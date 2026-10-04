"""
An entry json_ext.<key> of grievance_anonymized_fields hides that top-level
key of the ticket's json_ext from every user but superusers: jsonExt is
returned without it, an ordering on a path under it orders the tickets
hiding it as if it were null, and an update leaves its stored value
unchanged. The other json_ext keys stay readable.
"""
import json
from unittest import mock

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
from grievance_social_protection.services import TicketService
from grievance_social_protection.tests.test_helpers import (
    assign_rights_to_user, restore_grievance_config, setup_grievance_config,
)

PREFIX = 'JXK-'
OPEN_CATEGORY = 'jxk_open'
PARENT_CATEGORY = 'jxk_parent'
SUB_CATEGORY = 'jxk_parent > jxk_sub'
ANONYMIZED = {PARENT_CATEGORY: ['json_ext.reporter']}
HIDDEN = ['PARENT-1', 'PARENT-2', 'SUB']

TICKETS_QUERY = '''
    query {
        tickets(code_Istartswith: "%s"%s) {
            edges { node { code jsonExt } }
        }
    }
'''


class AnonymizedJsonExtKeysTest(TestCase):
    def setUp(self):
        self._snapshot = setup_grievance_config({
            'grievance_types': [OPEN_CATEGORY, {'name': PARENT_CATEGORY, 'children': ['jxk_sub']}],
            'grievance_flags': [],
        })
        self.addCleanup(restore_grievance_config, self._snapshot)
        for patcher in (
                mock.patch.object(TicketConfig, 'grievance_anonymized_fields', ANONYMIZED),
                # Row filters other installed modules register are not under test.
                mock.patch.object(GrievanceAccessControl, 'ticket_queryset_filters', [])):
            patcher.start()
            self.addCleanup(patcher.stop)

        self.admin = create_test_interactive_user(username='jxk_admin')
        empty_role = create_test_role([], name='NoRights_jxk_reader')
        self.reader = create_test_interactive_user(username='jxk_reader', roles=[empty_role.id])
        assign_rights_to_user(
            self.reader,
            [int(r) for r in TicketConfig.gql_query_tickets_perms + TicketConfig.gql_mutation_update_tickets_perms],
            'Role_jxk_reader')
        cache.clear()

        # Each hidden pair's reporter names order it against its codes.
        self._ticket('OPEN-A', OPEN_CATEGORY, 'mm', rank=2)
        self._ticket('OPEN-B', OPEN_CATEGORY, 'nn', rank=3)
        self._ticket('PARENT-1', PARENT_CATEGORY, 'zz', rank=9)
        self._ticket('PARENT-2', PARENT_CATEGORY, 'aa', rank=0)
        self._ticket('SUB', SUB_CATEGORY, 'bb', rank=1)
        self.schema = Schema(query=Query)

    def _ticket(self, suffix, category, name, rank):
        Ticket(code=PREFIX + suffix, title=f'title {suffix}', category=category, status='OPEN',
               json_ext={'reporter': {'name': name, 'gender': 'F'}, 'location': {'colline': 'C1'},
                         'rank': rank}).save(user=self.admin)

    def _nodes(self, user, arguments=''):
        query = TICKETS_QUERY % (PREFIX, ', ' + arguments if arguments else '')
        result = Client(self.schema).execute(query, context=BaseTestContext(user).get_request())
        self.assertNotIn('errors', result, result.get('errors'))
        return [(edge['node']['code'][len(PREFIX):], edge['node']['jsonExt'])
                for edge in result['data']['tickets']['edges']]

    def _json_ext(self, user):
        return {code: json.loads(value) if isinstance(value, str) else value
                for code, value in self._nodes(user)}

    def _codes(self, user, arguments):
        return [code for code, _ in self._nodes(user, arguments)]

    @staticmethod
    def _among(codes, subset):
        return [code for code in codes if code in subset]

    def test_the_key_is_left_out_of_json_ext_on_the_category_and_its_sub_categories(self):
        json_ext = self._json_ext(self.reader)

        for code in HIDDEN:
            self.assertNotIn('reporter', json_ext[code], code)
            self.assertEqual(json_ext[code]['location'], {'colline': 'C1'})
        self.assertEqual(json_ext['OPEN-A']['reporter'], {'name': 'mm', 'gender': 'F'})

    def test_superuser_reads_the_key(self):
        json_ext = self._json_ext(self.admin)

        self.assertEqual(json_ext['PARENT-1']['reporter'], {'name': 'zz', 'gender': 'F'})

    def test_access_control_names_the_hidden_keys(self):
        self.assertEqual(GrievanceAccessControl.anonymized_json_ext_keys(self.reader, SUB_CATEGORY), {'reporter'})
        self.assertEqual(GrievanceAccessControl.anonymized_json_ext_keys(self.reader, OPEN_CATEGORY), set())
        self.assertEqual(GrievanceAccessControl.anonymized_json_ext_keys(self.admin, SUB_CATEGORY), set())

    def test_ordering_under_the_key_leaves_the_tickets_hiding_it_unordered_by_it(self):
        ascending = self._codes(self.reader, 'orderBy: ["jsonExt__reporter__name", "code"]')
        descending = self._codes(self.reader, 'orderBy: ["-jsonExt__reporter__name", "code"]')

        self.assertEqual(self._among(ascending, HIDDEN), sorted(HIDDEN))
        self.assertEqual(self._among(descending, HIDDEN), sorted(HIDDEN))
        self.assertEqual(self._among(ascending, ['OPEN-A', 'OPEN-B']), ['OPEN-A', 'OPEN-B'])
        self.assertEqual(self._among(descending, ['OPEN-A', 'OPEN-B']), ['OPEN-B', 'OPEN-A'])

    def test_ordering_on_another_key_is_unchanged(self):
        ascending = self._codes(self.reader, 'orderBy: ["jsonExt__rank", "code"]')

        self.assertEqual(ascending, ['PARENT-2', 'SUB', 'OPEN-A', 'OPEN-B', 'PARENT-1'])

    def test_superuser_orders_under_the_key(self):
        ascending = self._codes(self.admin, 'orderBy: ["jsonExt__reporter__name", "code"]')

        self.assertEqual(ascending, ['PARENT-2', 'SUB', 'OPEN-A', 'OPEN-B', 'PARENT-1'])

    def test_update_keeps_the_stored_key(self):
        ticket = Ticket.objects.get(code=PREFIX + 'SUB')
        result = TicketService(self.reader).update({
            'id': ticket.id, 'json_ext': {'location': {'colline': 'C2'}, 'rank': 5}})
        self.assertTrue(result['success'], result)

        ticket.refresh_from_db()
        self.assertEqual(ticket.json_ext, {'reporter': {'name': 'bb', 'gender': 'F'},
                                           'location': {'colline': 'C2'}, 'rank': 5})

    def test_update_ignores_a_value_sent_for_the_key(self):
        ticket = Ticket.objects.get(code=PREFIX + 'SUB')
        result = TicketService(self.reader).update({
            'id': ticket.id, 'json_ext': {'reporter': None, 'rank': 5}})
        self.assertTrue(result['success'], result)

        ticket.refresh_from_db()
        self.assertEqual(ticket.json_ext, {'reporter': {'name': 'bb', 'gender': 'F'}, 'rank': 5})

    def test_update_does_not_add_the_key_absent_from_the_stored_ticket(self):
        ticket = Ticket.objects.get(code=PREFIX + 'SUB')
        Ticket.objects.filter(id=ticket.id).update(json_ext={'rank': 1})
        result = TicketService(self.reader).update({
            'id': ticket.id, 'json_ext': {'reporter': {'name': 'new'}, 'rank': 5}})
        self.assertTrue(result['success'], result)

        ticket.refresh_from_db()
        self.assertEqual(ticket.json_ext, {'rank': 5})

    def test_superuser_update_writes_the_key(self):
        ticket = Ticket.objects.get(code=PREFIX + 'SUB')
        result = TicketService(self.admin).update({
            'id': ticket.id, 'json_ext': {'reporter': {'name': 'new'}}})
        self.assertTrue(result['success'], result)

        ticket.refresh_from_db()
        self.assertEqual(ticket.json_ext, {'reporter': {'name': 'new'}})
