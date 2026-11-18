"""
tickets(showHistory:) and tickets(ticketVersion:) apply to a version of a
ticket the most restrictive rule of the version and of the ticket as it
stands now: a version is listed only when the user may list the ticket now,
its fields are masked when the version or the ticket now hides them, and a
filter or an ordering on a field the ticket now hides reveals nothing about
the value of its versions.
"""
from unittest import mock

from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from graphene import Schema
from graphene.test import Client
from graphql_relay import from_global_id

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

OPEN_CATEGORY = 'thca_open'
SECRET_CATEGORY = 'thca_secret'
FLAG = 'THCA_FLAG'

HISTORY = '''
    query { tickets(%s) { edges { node { id title description jsonExt accessLevel } } } }
'''


class TicketHistoryCurrentAccessTest(TestCase):

    def setUp(self):
        super().setUp()
        snapshot = setup_grievance_config({
            'grievance_types': [
                OPEN_CATEGORY,
                {'name': SECRET_CATEGORY, 'permissions': ['restricted_read', 'read']},
            ],
            'grievance_flags': [
                {'name': FLAG, 'permissions': ['restricted_read', 'read']},
            ],
        })
        self.addCleanup(restore_grievance_config, snapshot)
        secret = get_rights('processed_categories', SECRET_CATEGORY)
        flag = get_rights('processed_flags', FLAG)
        ticket_read = [int(r) for r in TicketConfig.gql_query_tickets_perms]

        self.ticket_reader = self._user('thca_reader', ticket_read)
        self.restricted_reader = self._user(
            'thca_restricted', ticket_read + [secret['restricted_read'], flag['restricted_read']])
        self.full_reader = self._user('thca_full', ticket_read + [secret['read'], flag['read']])

        self.reclassified = self._ticket('THCA-CAT', 'aaa first title')
        self.reclassified.category = SECRET_CATEGORY
        self.reclassified.save(user=self.full_reader)

        self.flagged = self._ticket('THCA-FLAG', 'zzz first title')
        self.flagged.flags = FLAG
        self.flagged.save(user=self.full_reader)

        self.open_ticket = self._ticket('THCA-OPEN', 'open title')

    def _user(self, username, rights):
        role = create_test_role([], name=f'NoRights_{username}')
        user = create_test_interactive_user(username=username, roles=[role.id])
        assign_rights_to_user(user, rights, f'Role_{username}')
        cache.clear()
        return user

    def _ticket(self, code, title):
        ticket = Ticket(code=code, title=title, description=f'secret {code}', category=OPEN_CATEGORY,
                        json_ext={'reporter_phone': '79000000'})
        ticket.save(user=self.full_reader)
        return ticket

    def _rows(self, user, arguments):
        """(ticket code, title, description, jsonExt, accessLevel) of the
        returned rows of the tickets of this test, in the returned order."""
        result = Client(Schema(query=Query)).execute(
            HISTORY % arguments, context=BaseTestContext(user).get_request())
        self.assertNotIn('errors', result, result.get('errors'))
        codes = {str(ticket_id): code for ticket_id, code in
                 Ticket.objects.filter(code__startswith='THCA-').values_list('id', 'code')}
        rows = []
        for edge in result['data']['tickets']['edges']:
            node = edge['node']
            ticket_id = from_global_id(node['id'])[1]
            if ticket_id in codes:
                rows.append((codes[ticket_id], node['title'], node['description'], node['jsonExt'],
                             node['accessLevel']))
        return rows

    def test_no_version_of_a_ticket_the_user_cannot_list_now(self):
        for arguments in ('showHistory: true', 'ticketVersion: 1'):
            with self.subTest(arguments=arguments):
                self.assertEqual(
                    sorted(row[:2] for row in self._rows(self.ticket_reader, arguments)),
                    [('THCA-OPEN', 'open title')])

    def test_versions_are_masked_by_the_ticket_as_it_stands_now(self):
        masked = (RESTRICTED_VALUE, RESTRICTED_VALUE, None, GrievanceAccessControl.ACCESS_RESTRICTED)
        for arguments, versions in (('showHistory: true', 2), ('ticketVersion: 1', 1)):
            with self.subTest(arguments=arguments):
                rows = self._rows(self.restricted_reader, arguments)
                for code in ('THCA-CAT', 'THCA-FLAG'):
                    self.assertEqual([row[1:] for row in rows if row[0] == code], [masked] * versions)

    def test_full_reader_reads_every_version(self):
        rows = self._rows(self.full_reader, 'showHistory: true')
        self.assertEqual(sorted(row[:2] for row in rows if row[0] == 'THCA-CAT'),
                         [('THCA-CAT', 'aaa first title'), ('THCA-CAT', 'aaa first title')])

    def test_filter_on_a_field_hidden_now_skips_the_versions(self):
        rows = self._rows(self.restricted_reader, 'showHistory: true, title_Icontains: "first title"')
        self.assertEqual(rows, [])

    def test_ordering_on_a_field_hidden_now_ignores_its_version_values(self):
        def order(direction):
            rows = self._rows(self.restricted_reader, f'ticketVersion: 1, orderBy: ["{direction}title"]')
            return [row[0] for row in rows if row[0] in ('THCA-CAT', 'THCA-FLAG')]
        self.assertEqual(order(''), order('-'))

    def test_queries_do_not_grow_with_the_versions_of_a_ticket(self):
        def count():
            # The first query of the user loads its rights; the second is counted.
            self._rows(self.restricted_reader, 'showHistory: true')
            # The periodic read of the stored configuration is not under test here.
            with mock.patch.object(TicketConfig, 'reload_if_changed'), \
                    CaptureQueriesContext(connection) as queries:
                self._rows(self.restricted_reader, 'showHistory: true')
            return len(queries)
        before = count()
        for version in range(3):
            self.reclassified.title = f'aaa title {version}'
            self.reclassified.save(user=self.full_reader)
        self.assertEqual(count(), before)
