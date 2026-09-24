"""
Tests for the ticketsStr query arguments.
"""
from core.test_helpers import create_test_interactive_user
from core.models.openimis_graphql_test_case import openIMISGraphQLTestCase, BaseTestContext
from graphene import Schema
from graphene.test import Client

from grievance_social_protection.models import Ticket
from grievance_social_protection.schema import Query
from grievance_social_protection.tests.test_helpers import (
    setup_grievance_config, restore_grievance_config, assign_rights_to_user,
)


class TicketsStrQueryTest(openIMISGraphQLTestCase):
    """ticketsStr exposes only arguments that its resolver or filterset applies."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = create_test_interactive_user(username='tickets_str_viewer', roles=[1])
        cls.schema = Schema(query=Query)

    def setUp(self):
        super().setUp()
        self._snapshot = setup_grievance_config({
            'grievance_types': ['public_feedback'],
            'grievance_flags': ['public'],
        })
        assign_rights_to_user(self.user, [127000], 'TicketsStrViewerRole')
        self.tickets = []
        for code, title in (('STRQ001', 'Water point broken'), ('STRQ002', 'Payment delayed')):
            ticket = Ticket(
                code=code,
                title=title,
                category='public_feedback',
                flags='public',
                status='OPEN',
                channel='Web',
            )
            ticket.save(user=self.user.user)
            self.tickets.append(ticket)

    def tearDown(self):
        for ticket in self.tickets:
            ticket.delete(user=self.user.user)
        restore_grievance_config(self._snapshot)
        super().tearDown()

    def _execute(self, query):
        client = Client(self.schema)
        context = BaseTestContext(self.user)
        return client.execute(query, context=context.get_request())

    def _codes(self, result):
        return sorted(edge['node']['code'] for edge in result['data']['ticketsStr']['edges'])

    def test_str_argument_is_not_declared(self):
        args = Query._meta.fields['ticketsStr'].args
        self.assertNotIn('str', list(args))

    def test_str_argument_is_rejected(self):
        result = self._execute('''
            query {
                ticketsStr(str: "Water") {
                    totalCount
                }
            }
        ''')
        self.assertIn('errors', result)
        messages = [error['message'] for error in result['errors']]
        self.assertTrue(
            any('Unknown argument' in message and '"str"' in message for message in messages),
            messages,
        )

    def test_query_without_arguments_returns_visible_tickets(self):
        result = self._execute('''
            query {
                ticketsStr {
                    totalCount
                    edges { node { code } }
                }
            }
        ''')
        self.assertNotIn('errors', result)
        self.assertEqual(self._codes(result), ['STRQ001', 'STRQ002'])

    def test_filterset_arguments_still_filter(self):
        result = self._execute('''
            query {
                ticketsStr(title_Icontains: "water") {
                    totalCount
                    edges { node { code } }
                }
            }
        ''')
        self.assertNotIn('errors', result)
        self.assertEqual(self._codes(result), ['STRQ001'])
