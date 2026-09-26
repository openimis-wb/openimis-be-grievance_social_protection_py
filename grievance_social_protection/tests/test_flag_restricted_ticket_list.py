"""
A reader who holds only restricted_read on a flag sees tickets carrying that
flag at the 'restricted' level. The ticket list must render those tickets with
the restricted field set (basic fields, or the category's visible_fields) and
never resolve the non-null status field to null.
"""
from django.core.cache import cache
from django.test import TestCase
from graphene import Schema
from graphene.test import Client

from core.models.openimis_graphql_test_case import BaseTestContext
from core.test_helpers import create_test_interactive_user, create_test_role
from grievance_social_protection.access_control import GrievanceAccessControl
from grievance_social_protection.apps import TicketConfig
from grievance_social_protection.gql_queries import RESTRICTED_VALUE
from grievance_social_protection.models import Ticket
from grievance_social_protection.schema import Query
from grievance_social_protection.tests.test_helpers import (
    assign_rights_to_user, get_rights, restore_grievance_config, setup_grievance_config,
)

OPEN_CATEGORY = 'flagrl_open'
VBG_CATEGORY = 'flagrl_vbg'
FLAG = 'FLAGRL_SENSITIVE'
BASIC_FIELDS = ['id', 'status', 'category', 'priority', 'date_created']

TICKETS_QUERY = '''
    query {
        tickets(code_Istartswith: "FLAGRL") {
            totalCount
            edges {
                node {
                    code
                    status
                    title
                    description
                    category
                    priority
                    accessLevel
                }
            }
        }
    }
'''


class FlagRestrictedTicketListTest(TestCase):
    def setUp(self):
        self._snapshot = setup_grievance_config({
            'grievance_types': [
                OPEN_CATEGORY,
                {
                    'name': VBG_CATEGORY,
                    'priority': 'High',
                    'permissions': ['restricted_read', 'read', 'create', 'update'],
                    'visible_fields': ['category', 'description', 'status'],
                },
            ],
            'grievance_flags': [
                {'name': FLAG, 'priority': 'High', 'permissions': ['restricted_read', 'read']},
            ],
        })
        self.addCleanup(restore_grievance_config, self._snapshot)
        flag_rights = get_rights('processed_flags', FLAG)
        vbg_rights = get_rights('processed_categories', VBG_CATEGORY)
        base_rights = [
            int(r) for r in (
                TicketConfig.gql_query_tickets_perms
                + TicketConfig.gql_mutation_create_tickets_perms
                + TicketConfig.gql_mutation_update_tickets_perms
            )
        ]
        self.flag_restricted_reader = self._user(
            'flagrl_restricted', base_rights + [flag_rights['restricted_read'], vbg_rights['read']])
        self.flag_reader = self._user(
            'flagrl_reader', base_rights + [flag_rights['read'], vbg_rights['read']])

        self.open_ticket = self._ticket('FLAGRL-OPEN', OPEN_CATEGORY)
        self.vbg_ticket = self._ticket('FLAGRL-VBG', VBG_CATEGORY)
        self.schema = Schema(query=Query)

    def _user(self, username, rights):
        empty_role = create_test_role([], name=f'NoRights_{username}')
        user = create_test_interactive_user(username=username, roles=[empty_role.id])
        assign_rights_to_user(user, rights, f'Role_{username}')
        cache.clear()
        return user

    def _ticket(self, code, category):
        ticket = Ticket(
            code=code,
            title='Sensitive title',
            description='Sensitive description',
            category=category,
            flags=FLAG,
            priority='High',
            status='RESOLVED',
            channel='telephone',
        )
        ticket.save(user=self.flag_reader)
        return ticket

    def _nodes(self, user):
        result = Client(self.schema).execute(TICKETS_QUERY, context=BaseTestContext(user).get_request())
        self.assertNotIn('errors', result, result.get('errors'))
        return {edge['node']['code']: edge['node'] for edge in result['data']['tickets']['edges']}

    def test_flag_restricted_reader_is_restricted_on_open_category(self):
        self.assertEqual(
            GrievanceAccessControl.get_user_access_level(self.flag_restricted_reader, OPEN_CATEGORY, FLAG),
            GrievanceAccessControl.ACCESS_RESTRICTED,
        )

    def test_visible_fields_include_the_flag_level(self):
        self.assertIsNone(GrievanceAccessControl.get_visible_fields(self.flag_restricted_reader, OPEN_CATEGORY))
        self.assertEqual(
            GrievanceAccessControl.get_visible_fields(self.flag_restricted_reader, OPEN_CATEGORY, FLAG),
            BASIC_FIELDS,
        )
        self.assertEqual(
            GrievanceAccessControl.get_visible_fields(self.flag_restricted_reader, VBG_CATEGORY, FLAG),
            ['category', 'description', 'status'],
        )
        self.assertIsNone(GrievanceAccessControl.get_visible_fields(self.flag_reader, OPEN_CATEGORY, FLAG))

    def test_list_renders_flag_restricted_ticket_in_open_category(self):
        node = self._nodes(self.flag_restricted_reader)['FLAGRL-OPEN']
        self.assertEqual(node['status'], 'RESOLVED')
        self.assertEqual(node['category'], OPEN_CATEGORY)
        self.assertEqual(node['priority'], 'High')
        self.assertEqual(node['title'], RESTRICTED_VALUE)
        self.assertEqual(node['description'], RESTRICTED_VALUE)
        self.assertEqual(node['accessLevel'], GrievanceAccessControl.ACCESS_RESTRICTED)

    def test_list_applies_category_visible_fields_to_flag_restricted_ticket(self):
        node = self._nodes(self.flag_restricted_reader)['FLAGRL-VBG']
        self.assertEqual(node['status'], 'RESOLVED')
        self.assertEqual(node['category'], VBG_CATEGORY)
        self.assertEqual(node['description'], 'Sensitive description')
        self.assertEqual(node['title'], RESTRICTED_VALUE)
        self.assertIsNone(node['priority'])

    def test_flag_reader_sees_every_field(self):
        node = self._nodes(self.flag_reader)['FLAGRL-OPEN']
        self.assertEqual(node['status'], 'RESOLVED')
        self.assertEqual(node['title'], 'Sensitive title')
        self.assertEqual(node['description'], 'Sensitive description')
        self.assertEqual(node['accessLevel'], GrievanceAccessControl.ACCESS_READ)
