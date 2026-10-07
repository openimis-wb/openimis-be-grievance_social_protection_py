"""
Every column of a ticket follows the visible-field rule in the ticket list:
at the 'restricted' level a column outside the category's visible_fields (or
the basic fields) is masked, whatever its type. Only the columns of
_ALWAYS_FILTERABLE are returned at every access level.
"""
import datetime
import uuid

from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.test import TestCase
from graphene import Schema
from graphene.test import Client
from graphene.utils.str_converters import to_camel_case
from graphql_relay import from_global_id

from core.models.openimis_graphql_test_case import BaseTestContext
from core.test_helpers import create_test_interactive_user, create_test_role
from grievance_social_protection.access_control import GrievanceAccessControl
from grievance_social_protection.apps import TicketConfig
from grievance_social_protection.gql_queries import RESTRICTED_VALUE, _ALWAYS_FILTERABLE
from grievance_social_protection.models import Ticket
from grievance_social_protection.schema import Query
from grievance_social_protection.tests.test_helpers import (
    assign_rights_to_user, get_rights, restore_grievance_config, setup_grievance_config,
)

BASIC_CATEGORY = 'tcol_basic'
LISTED_CATEGORY = 'tcol_listed'
LISTED_FIELDS = ['category', 'status', 'code', 'key', 'date_updated', 'user_created', 'user_updated']

# Ticket columns that are foreign keys to a user, queried through their id.
USER_COLUMNS = ('attending_staff', 'user_created', 'user_updated')


def _columns():
    """Concrete columns of Ticket as TicketGQLType names them."""
    return [field.name for field in Ticket._meta.concrete_fields]


def _selection(column):
    name = to_camel_case(column)
    return f'{name} {{ id }}' if column in USER_COLUMNS else name


TICKETS_QUERY = 'query { tickets { edges { node { %s } } } }' % ' '.join(_selection(c) for c in _columns())


class TicketColumnVisibilityTest(TestCase):
    def setUp(self):
        self._snapshot = setup_grievance_config({
            'grievance_types': [
                {'name': BASIC_CATEGORY, 'permissions': ['restricted_read', 'read']},
                {'name': LISTED_CATEGORY, 'permissions': ['restricted_read', 'read'],
                 'visible_fields': LISTED_FIELDS},
            ],
            'grievance_flags': [],
        })
        self.addCleanup(restore_grievance_config, self._snapshot)
        basic = get_rights('processed_categories', BASIC_CATEGORY)
        listed = get_rights('processed_categories', LISTED_CATEGORY)
        base_rights = [int(r) for r in TicketConfig.gql_query_tickets_perms]
        self.restricted_reader = self._user(
            'tcol_restricted', base_rights + [basic['restricted_read'], listed['restricted_read']])
        self.reader = self._user('tcol_reader', base_rights + [basic['read'], listed['read']])

        self.basic_ticket = self._ticket('TCOL-BASIC', BASIC_CATEGORY)
        self.listed_ticket = self._ticket('TCOL-LISTED', LISTED_CATEGORY)
        self.schema = Schema(query=Query)

    def _user(self, username, rights):
        empty_role = create_test_role([], name=f'NoRights_{username}')
        user = create_test_interactive_user(username=username, roles=[empty_role.id])
        assign_rights_to_user(user, rights, f'Role_{username}')
        cache.clear()
        return user

    def _ticket(self, code, category):
        ticket = Ticket(
            code=code, key=f'key-{code}', title='title', description='description', category=category,
            flags='', status='OPEN', priority='High', channel='telephone', resolution='resolution',
            reporter_type=ContentType.objects.get_for_model(Ticket), reporter_id='reporter', attending_staff=self.reader, date_of_incident=datetime.date(2026, 1, 2),
            due_date=datetime.date(2026, 2, 3), json_ext={'note': 'value'}, replacement_uuid=uuid.uuid4(),
        )
        ticket.save(user=self.reader)
        return ticket

    def _nodes(self, user):
        result = Client(self.schema).execute(TICKETS_QUERY, context=BaseTestContext(user).get_request())
        self.assertNotIn('errors', result, result.get('errors'))
        return {from_global_id(edge['node']['id'])[1]: edge['node'] for edge in result['data']['tickets']['edges']}

    def test_restricted_reader_gets_every_column_outside_the_basic_fields_masked(self):
        node = self._nodes(self.restricted_reader)[str(self.basic_ticket.id)]
        for column in _columns():
            if column in _ALWAYS_FILTERABLE or column in GrievanceAccessControl.BASIC_VISIBLE_FIELDS:
                continue
            with self.subTest(column=column):
                self.assertIn(node[to_camel_case(column)], (None, RESTRICTED_VALUE))

    def test_restricted_reader_gets_the_metadata_columns_masked(self):
        node = self._nodes(self.restricted_reader)[str(self.basic_ticket.id)]

        self.assertEqual((node['code'], node['key']), (RESTRICTED_VALUE, RESTRICTED_VALUE))
        for name in ('dateUpdated', 'userCreated', 'userUpdated', 'dateValidFrom', 'dateValidTo',
                     'replacementUuid', 'isDeleted'):
            with self.subTest(field=name):
                self.assertIsNone(node[name])

    def test_identifiers_are_returned_at_every_level(self):
        node = self._nodes(self.restricted_reader)[str(self.basic_ticket.id)]

        self.assertEqual(node['version'], self.basic_ticket.version)
        self.assertEqual(from_global_id(node['id'])[1], str(self.basic_ticket.id))

    def test_columns_listed_in_visible_fields_are_returned_at_the_restricted_level(self):
        node = self._nodes(self.restricted_reader)[str(self.listed_ticket.id)]

        self.assertEqual((node['code'], node['key']), ('TCOL-LISTED', 'key-TCOL-LISTED'))
        self.assertIsNotNone(node['dateUpdated'])
        self.assertEqual(node['userCreated']['id'], node['userUpdated']['id'])
        self.assertIsNotNone(node['userCreated']['id'])
        self.assertIsNone(node['dateValidFrom'])
        self.assertIsNone(node['isDeleted'])

    def test_reader_gets_every_column(self):
        node = self._nodes(self.reader)[str(self.basic_ticket.id)]

        for column in _columns():
            if column == 'date_valid_to':
                continue  # a ticket with date_valid_to set is a past version, outside the list
            with self.subTest(column=column):
                self.assertNotIn(node[to_camel_case(column)], (None, RESTRICTED_VALUE))
        self.assertEqual(node['code'], 'TCOL-BASIC')
        self.assertIs(node['isDeleted'], False)
