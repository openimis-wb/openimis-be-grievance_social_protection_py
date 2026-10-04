"""
Fields listed in grievance_anonymized_fields are hidden from every user but
superusers: under the default key on every ticket, under a category on its
tickets and on those of its sub-categories. The ticket resolvers mask them and
a filter on them leaves those tickets out, and an update leaves them unchanged.
"""
import datetime
from unittest import mock

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
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

PREFIX = 'ANON-'
OPEN_CATEGORY = 'anon_open'
PARENT_CATEGORY = 'anon_parent'
SUB_CATEGORY = 'anon_parent > anon_sub'
ANONYMIZED = {'Default': ['channel'], PARENT_CATEGORY: ['description', 'reporter']}

TICKETS_QUERY = '''
    query {
        tickets(code_Istartswith: "%s"%s) {
            edges { node {
                code title description channel reporter reporterId reporterType
                reporterFirstName reporterLastName reporterDob
            } }
        }
    }
'''


class AnonymizedFieldsTest(TestCase):
    def setUp(self):
        self._snapshot = setup_grievance_config({
            'grievance_types': [OPEN_CATEGORY, {'name': PARENT_CATEGORY, 'children': ['anon_sub']}],
            'grievance_flags': [],
        })
        self.addCleanup(restore_grievance_config, self._snapshot)
        for patcher in (
                mock.patch.object(TicketConfig, 'grievance_anonymized_fields', ANONYMIZED),
                # Row filters other installed modules register are not under test.
                mock.patch.object(GrievanceAccessControl, 'ticket_queryset_filters', [])):
            patcher.start()
            self.addCleanup(patcher.stop)

        self.admin = create_test_interactive_user(username='anon_admin')
        empty_role = create_test_role([], name='NoRights_anon_reader')
        self.reader = create_test_interactive_user(username='anon_reader', roles=[empty_role.id])
        assign_rights_to_user(
            self.reader,
            [int(r) for r in TicketConfig.gql_query_tickets_perms + TicketConfig.gql_mutation_update_tickets_perms],
            'Role_anon_reader')
        cache.clear()

        individual_model = apps.get_model('individual', 'Individual')
        individual = individual_model(
            first_name='AnonFN', last_name='AnonLN', dob=datetime.date(1985, 3, 4), json_ext={})
        individual.save(user=self.admin)
        reporter = {'reporter_type': ContentType.objects.get_for_model(individual_model),
                    'reporter_id': str(individual.id)}
        self._ticket('OPEN', OPEN_CATEGORY, **reporter)
        self._ticket('PARENT', PARENT_CATEGORY, **reporter)
        self._ticket('SUB', SUB_CATEGORY, **reporter)
        self.schema = Schema(query=Query)

    def _ticket(self, suffix, category, **fields):
        Ticket(code=PREFIX + suffix, title=f'title {suffix}', description=f'needle {suffix}',
               category=category, channel='sms', status='OPEN', **fields).save(user=self.admin)

    def _nodes(self, user, arguments=''):
        query = TICKETS_QUERY % (PREFIX, ', ' + arguments if arguments else '')
        result = Client(self.schema).execute(query, context=BaseTestContext(user).get_request())
        self.assertNotIn('errors', result, result.get('errors'))
        return {edge['node']['code'][len(PREFIX):]: edge['node'] for edge in result['data']['tickets']['edges']}

    def test_users_are_what_the_test_expects(self):
        self.assertTrue(self.admin.is_superuser)
        self.assertFalse(self.reader.is_superuser)
        self.assertIsNone(GrievanceAccessControl.get_visible_fields(self.reader, PARENT_CATEGORY))

    def test_default_entry_hides_the_field_on_every_ticket(self):
        nodes = self._nodes(self.reader)
        self.assertEqual(sorted(nodes), ['OPEN', 'PARENT', 'SUB'])
        for node in nodes.values():
            self.assertEqual(node['channel'], '[Restricted]')

    def test_category_entry_hides_the_field_on_the_category_and_its_sub_categories(self):
        nodes = self._nodes(self.reader)
        self.assertEqual(nodes['OPEN']['description'], 'needle OPEN')
        self.assertEqual(nodes['PARENT']['description'], '[Restricted]')
        self.assertEqual(nodes['SUB']['description'], '[Restricted]')
        self.assertEqual(nodes['SUB']['title'], 'title SUB')

    def test_reporter_entry_hides_every_reporter_field(self):
        nodes = self._nodes(self.reader)
        self.assertEqual(nodes['OPEN']['reporterFirstName'], 'AnonFN')
        self.assertIsNotNone(nodes['OPEN']['reporter'])
        self.assertIsNotNone(nodes['OPEN']['reporterId'])
        for code in ('PARENT', 'SUB'):
            node = nodes[code]
            self.assertIsNone(node['reporter'])
            self.assertIsNone(node['reporterId'])
            self.assertIsNone(node['reporterType'])
            self.assertEqual(node['reporterFirstName'], '[Restricted]')
            self.assertEqual(node['reporterLastName'], '[Restricted]')
            self.assertIsNone(node['reporterDob'])

    def test_superuser_reads_anonymized_fields(self):
        nodes = self._nodes(self.admin)
        for code in ('OPEN', 'PARENT', 'SUB'):
            self.assertEqual(nodes[code]['channel'], 'sms')
            self.assertEqual(nodes[code]['description'], f'needle {code}')
            self.assertEqual(nodes[code]['reporterFirstName'], 'AnonFN')

    def test_filter_on_an_anonymized_field_leaves_out_the_tickets_hiding_it(self):
        self.assertEqual(sorted(self._nodes(self.reader, 'description_Icontains: "needle"')), ['OPEN'])
        self.assertEqual(sorted(self._nodes(self.reader, 'channel_Icontains: "sms"')), [])
        self.assertEqual(sorted(self._nodes(self.admin, 'channel_Icontains: "sms"')), ['OPEN', 'PARENT', 'SUB'])

    def test_attname_entry_names_its_field(self):
        with mock.patch.object(TicketConfig, 'grievance_anonymized_fields', {'Default': ['reporter_type_id']}):
            nodes = self._nodes(self.reader)
        self.assertIsNone(nodes['OPEN']['reporterType'])
        self.assertIsNotNone(nodes['OPEN']['reporterId'])

    def test_no_entry_hides_nothing(self):
        with mock.patch.object(TicketConfig, 'grievance_anonymized_fields', {}):
            nodes = self._nodes(self.reader)
        self.assertEqual(nodes['PARENT']['channel'], 'sms')
        self.assertEqual(nodes['PARENT']['description'], 'needle PARENT')
        self.assertEqual(nodes['PARENT']['reporterFirstName'], 'AnonFN')

    def test_update_leaves_the_anonymized_fields_unchanged(self):
        ticket = Ticket.objects.get(code=PREFIX + 'SUB')
        result = TicketService(self.reader).update({
            'id': ticket.id, 'title': 'new title', 'description': '[Restricted]', 'channel': '[Restricted]'})
        self.assertTrue(result['success'], result)

        ticket.refresh_from_db()
        self.assertEqual(ticket.title, 'new title')
        self.assertEqual(ticket.description, 'needle SUB')
        self.assertEqual(ticket.channel, 'sms')

    def test_superuser_update_writes_every_field(self):
        ticket = Ticket.objects.get(code=PREFIX + 'SUB')
        result = TicketService(self.admin).update({
            'id': ticket.id, 'description': 'new description', 'channel': 'telephone'})
        self.assertTrue(result['success'], result)

        ticket.refresh_from_db()
        self.assertEqual(ticket.description, 'new description')
        self.assertEqual(ticket.channel, 'telephone')
