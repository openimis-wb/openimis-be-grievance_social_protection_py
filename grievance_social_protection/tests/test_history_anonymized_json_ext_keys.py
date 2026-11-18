"""
A version of the ticket history returns jsonExt without the keys that
grievance_anonymized_fields hides on the version's category or on the
category of the ticket as it stands now.
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
from grievance_social_protection.tests.test_helpers import (
    assign_rights_to_user, restore_grievance_config, setup_grievance_config,
)

OPEN_CATEGORY = 'hjx_open'
ANONYMIZING_CATEGORY = 'hjx_anonymizing'
ANONYMIZED = {ANONYMIZING_CATEGORY: ['json_ext.reporter_phone']}
CLEAR = {'reporter_phone': '79000000', 'location': {'colline': 'C1'}}
HIDDEN = {'location': {'colline': 'C1'}}

HISTORY = '''
    query { tickets(code_Istartswith: "HJX-", %s) { edges { node { code jsonExt } } } }
'''


class HistoryAnonymizedJsonExtKeysTest(TestCase):

    def setUp(self):
        super().setUp()
        snapshot = setup_grievance_config({
            'grievance_types': [OPEN_CATEGORY, ANONYMIZING_CATEGORY],
            'grievance_flags': [],
        })
        self.addCleanup(restore_grievance_config, snapshot)
        for patcher in (
                mock.patch.object(TicketConfig, 'grievance_anonymized_fields', ANONYMIZED),
                # Row filters other installed modules register are not under test.
                mock.patch.object(GrievanceAccessControl, 'ticket_queryset_filters', [])):
            patcher.start()
            self.addCleanup(patcher.stop)

        self.admin = create_test_interactive_user(username='hjx_admin')
        role = create_test_role([], name='NoRights_hjx_reader')
        self.reader = create_test_interactive_user(username='hjx_reader', roles=[role.id])
        assign_rights_to_user(self.reader, [int(r) for r in TicketConfig.gql_query_tickets_perms], 'Role_hjx_reader')
        cache.clear()

        self.reclassified = self._ticket('HJX-MOVED', OPEN_CATEGORY)
        self.reclassified.category = ANONYMIZING_CATEGORY
        self.reclassified.save(user=self.admin)
        self.declassified = self._ticket('HJX-BACK', ANONYMIZING_CATEGORY)
        self.declassified.category = OPEN_CATEGORY
        self.declassified.save(user=self.admin)
        self.open_ticket = self._ticket('HJX-OPEN', OPEN_CATEGORY)

    def _ticket(self, code, category):
        ticket = Ticket(code=code, title='History', category=category, json_ext=dict(CLEAR))
        ticket.save(user=self.admin)
        return ticket

    def _json_ext(self, user, arguments):
        result = Client(Schema(query=Query)).execute(
            HISTORY % arguments, context=BaseTestContext(user).get_request())
        self.assertNotIn('errors', result, result.get('errors'))
        rows = {}
        for edge in result['data']['tickets']['edges']:
            value = edge['node']['jsonExt']
            rows.setdefault(edge['node']['code'], []).append(
                json.loads(value) if isinstance(value, str) else value)
        return rows

    def test_versions_hide_the_keys_of_the_current_category(self):
        for arguments, versions in (('showHistory: true', 2), ('ticketVersion: 1', 1)):
            with self.subTest(arguments=arguments):
                rows = self._json_ext(self.reader, arguments)
                self.assertEqual(rows['HJX-MOVED'], [HIDDEN] * versions)
                self.assertEqual(rows['HJX-OPEN'], [CLEAR])

    def test_a_version_hides_the_keys_of_its_own_category(self):
        self.assertEqual(self._json_ext(self.reader, 'ticketVersion: 1')['HJX-BACK'], [HIDDEN])
        self.assertEqual(self._json_ext(self.reader, 'ticketVersion: 2')['HJX-BACK'], [CLEAR])

    def test_superuser_reads_the_keys_of_every_version(self):
        rows = self._json_ext(self.admin, 'showHistory: true')
        self.assertEqual(rows['HJX-MOVED'], [CLEAR] * 2)
