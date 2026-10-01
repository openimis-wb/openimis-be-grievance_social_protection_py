"""
The access level of a restricted category or flag reaches 'full' only through a
create, update or delete right generated for that item. The module's base
ticket rights (gql_query/create/update/delete_tickets_perms) keep governing the
operation checks for permission types the item does not list.
"""
from django.core.cache import cache
from django.test import TestCase
from graphene import Schema
from graphene.test import Client

from core.models.openimis_graphql_test_case import BaseTestContext
from core.test_helpers import create_test_interactive_user, create_test_role
from grievance_social_protection.access_control import GrievanceAccessControl
from grievance_social_protection.apps import TicketConfig
from grievance_social_protection.models import Ticket
from grievance_social_protection.schema import Query
from grievance_social_protection.tests.test_helpers import (
    assign_rights_to_user, get_rights, restore_grievance_config, setup_grievance_config,
)

RESTRICTED_CATEGORY = 'access_level_restricted_cat'
OPEN_CATEGORY = 'access_level_open_cat'
READ_ONLY_FLAG = 'access_level_read_only_flag'
EDITABLE_FLAG = 'access_level_editable_flag'
CATEGORY_VISIBLE_FIELDS = ['id', 'status', 'category']

TICKETS_QUERY = '''
    query {
        tickets {
            edges {
                node {
                    code
                    description
                    resolution
                    accessLevel
                }
            }
        }
    }
'''


def base_ticket_rights():
    rights = (
        TicketConfig.gql_query_tickets_perms
        + TicketConfig.gql_mutation_create_tickets_perms
        + TicketConfig.gql_mutation_update_tickets_perms
        + TicketConfig.gql_mutation_delete_tickets_perms
    )
    return [int(right) for right in rights]


class AccessLevelGeneratedRightsTest(TestCase):

    def setUp(self):
        snapshot = setup_grievance_config({
            'grievance_types': [
                OPEN_CATEGORY,
                {
                    'name': RESTRICTED_CATEGORY,
                    'permissions': ['restricted_read', 'read', 'create', 'update'],
                    'visible_fields': CATEGORY_VISIBLE_FIELDS,
                },
            ],
            'grievance_flags': [
                {
                    'name': READ_ONLY_FLAG,
                    'permissions': ['restricted_read', 'read'],
                },
                {
                    'name': EDITABLE_FLAG,
                    'permissions': ['restricted_read', 'read', 'update'],
                },
            ],
        })
        self.addCleanup(restore_grievance_config, snapshot)
        self.category_rights = get_rights('processed_categories', RESTRICTED_CATEGORY)
        self.read_only_flag_rights = get_rights('processed_flags', READ_ONLY_FLAG)
        self.editable_flag_rights = get_rights('processed_flags', EDITABLE_FLAG)
        self.base_rights = base_ticket_rights()

    def _user(self, username, rights):
        empty_role = create_test_role([], name=f'NoRights_{username}')
        user = create_test_interactive_user(username=username, roles=[empty_role.id])
        assign_rights_to_user(user, rights, f'Role_{username}')
        cache.clear()
        return user

    def _ticket(self, code, category, flags):
        admin = create_test_interactive_user(username='access_level_admin')
        ticket = Ticket(
            code=code,
            title='Access level ticket',
            description='Sensitive description',
            resolution='Sensitive resolution',
            category=category,
            flags=flags,
            status='OPEN',
            channel='Web',
        )
        ticket.save(user=admin)
        return ticket

    def _query_ticket(self, user, code):
        result = Client(Schema(query=Query)).execute(
            TICKETS_QUERY, context=BaseTestContext(user).get_request())
        self.assertNotIn('errors', result)
        nodes = [edge['node'] for edge in result['data']['tickets']['edges']]
        return next(node for node in nodes if node['code'] == code)

    def test_items_generate_no_delete_right(self):
        self.assertNotIn('delete', self.category_rights)
        self.assertNotIn('create', self.read_only_flag_rights)
        self.assertNotIn('update', self.read_only_flag_rights)
        self.assertNotIn('delete', self.read_only_flag_rights)

    def test_category_restricted_reader_with_base_rights_is_restricted(self):
        user = self._user('al_cat_restricted', self.base_rights + [self.category_rights['restricted_read']])
        self.assertEqual(
            GrievanceAccessControl.get_user_access_level(user, RESTRICTED_CATEGORY),
            GrievanceAccessControl.ACCESS_RESTRICTED,
        )
        self.assertEqual(
            GrievanceAccessControl.get_visible_fields(user, RESTRICTED_CATEGORY), CATEGORY_VISIBLE_FIELDS,
        )

    def test_category_restricted_reader_with_base_rights_sees_masked_fields(self):
        self._ticket('AL-CAT-1', RESTRICTED_CATEGORY, '')
        user = self._user('al_cat_restricted_gql', self.base_rights + [self.category_rights['restricted_read']])
        node = self._query_ticket(user, 'AL-CAT-1')
        self.assertEqual(node['accessLevel'], GrievanceAccessControl.ACCESS_RESTRICTED)
        self.assertEqual(node['description'], '[Restricted]')
        self.assertEqual(node['resolution'], '[Restricted]')

    def test_category_generated_read_right_gives_read(self):
        user = self._user('al_cat_reader', self.base_rights + [self.category_rights['read']])
        self.assertEqual(
            GrievanceAccessControl.get_user_access_level(user, RESTRICTED_CATEGORY),
            GrievanceAccessControl.ACCESS_READ,
        )

    def test_category_generated_update_right_gives_full(self):
        user = self._user('al_cat_updater', self.base_rights + [self.category_rights['update']])
        self.assertEqual(
            GrievanceAccessControl.get_user_access_level(user, RESTRICTED_CATEGORY),
            GrievanceAccessControl.ACCESS_FULL,
        )

    def test_flag_restricted_reader_with_base_rights_is_restricted(self):
        user = self._user('al_flag_restricted', self.base_rights + [self.read_only_flag_rights['restricted_read']])
        self.assertEqual(
            GrievanceAccessControl.get_user_access_level(user, OPEN_CATEGORY, READ_ONLY_FLAG),
            GrievanceAccessControl.ACCESS_RESTRICTED,
        )

    def test_flag_restricted_reader_with_base_rights_sees_masked_fields(self):
        self._ticket('AL-FLAG-1', OPEN_CATEGORY, READ_ONLY_FLAG)
        user = self._user(
            'al_flag_restricted_gql', self.base_rights + [self.read_only_flag_rights['restricted_read']])
        node = self._query_ticket(user, 'AL-FLAG-1')
        self.assertEqual(node['accessLevel'], GrievanceAccessControl.ACCESS_RESTRICTED)
        self.assertEqual(node['description'], '[Restricted]')
        self.assertEqual(node['resolution'], '[Restricted]')

    def test_flag_generated_update_right_gives_full(self):
        self._ticket('AL-FLAG-2', OPEN_CATEGORY, EDITABLE_FLAG)
        user = self._user('al_flag_updater', self.base_rights + [
            self.editable_flag_rights['read'], self.editable_flag_rights['update']])
        self.assertEqual(
            GrievanceAccessControl.get_user_access_level(user, OPEN_CATEGORY, EDITABLE_FLAG),
            GrievanceAccessControl.ACCESS_FULL,
        )
        node = self._query_ticket(user, 'AL-FLAG-2')
        self.assertEqual(node['accessLevel'], GrievanceAccessControl.ACCESS_FULL)
        self.assertEqual(node['description'], 'Sensitive description')

    def test_base_rights_still_authorise_unlisted_operations(self):
        user = self._user('al_base_only', self.base_rights)
        self.assertTrue(GrievanceAccessControl.check_category_access(
            user, RESTRICTED_CATEGORY, GrievanceAccessControl.PERM_DELETE))
        self.assertTrue(GrievanceAccessControl.check_flag_access(
            user, READ_ONLY_FLAG, GrievanceAccessControl.PERM_CREATE))
        self.assertTrue(GrievanceAccessControl.check_flag_access(
            user, READ_ONLY_FLAG, GrievanceAccessControl.PERM_UPDATE))
        self.assertFalse(GrievanceAccessControl.check_category_access(
            user, RESTRICTED_CATEGORY, GrievanceAccessControl.PERM_CREATE))
