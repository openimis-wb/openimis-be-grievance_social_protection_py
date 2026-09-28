from uuid import uuid4

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from graphene import Schema
from graphene.test import Client

from core.datetimes.ad_datetime import datetime
from core.models.openimis_graphql_test_case import BaseTestContext
from grievance_social_protection.apps import DEFAULT_CFG
from grievance_social_protection.models import Ticket
from grievance_social_protection.schema import Query
from grievance_social_protection.tests.test_helpers import (
    create_test_grievance_user, restore_grievance_config, setup_grievance_config,
)

TICKET_REPORTER_QUERY = '''
    query {
        tickets(code: "%s") {
            edges {
                node {
                    reporterFirstName
                    reporterLastName
                    reporterDob
                }
            }
        }
    }
'''


class GQLTicketReporterFieldsTest(TestCase):
    """reporterFirstName/LastName/Dob resolve from the reporter row, and to null, without error, when it is absent."""

    def setUp(self):
        super().setUp()
        self.addCleanup(restore_grievance_config, setup_grievance_config(DEFAULT_CFG))
        self.user = create_test_grievance_user(username='reporter_fields_user')
        individual_model = apps.get_model('individual', 'Individual')
        self.individual_type = ContentType.objects.get_for_model(individual_model)
        self.individual = individual_model(first_name='ReporterFN', last_name='ReporterLN', dob=datetime(1985, 3, 4))
        self.individual.save(username=self.user.username)

    def _ticket(self, reporter_id):
        ticket = Ticket(code=f'RPT-{uuid4().hex[:10]}', title='Reporter', category='Default',
                        reporter_type=self.individual_type, reporter_id=reporter_id)
        ticket.save(user=self.user)
        return ticket

    def _reporter_fields(self, ticket):
        result = Client(Schema(query=Query)).execute(
            TICKET_REPORTER_QUERY % ticket.code, context=BaseTestContext(self.user).get_request())
        self.assertNotIn('errors', result, result.get('errors'))
        edges = result['data']['tickets']['edges']
        self.assertEqual(len(edges), 1)
        return edges[0]['node']

    def test_null_reporter_id_resolves_to_null(self):
        node = self._reporter_fields(self._ticket(None))
        self.assertEqual(node, {'reporterFirstName': None, 'reporterLastName': None, 'reporterDob': None})

    def test_missing_reporter_row_resolves_to_null(self):
        node = self._reporter_fields(self._ticket(str(uuid4())))
        self.assertEqual(node, {'reporterFirstName': None, 'reporterLastName': None, 'reporterDob': None})

    def test_individual_reporter_resolves_to_its_fields(self):
        node = self._reporter_fields(self._ticket(str(self.individual.id)))
        self.assertEqual(node['reporterFirstName'], 'ReporterFN')
        self.assertEqual(node['reporterLastName'], 'ReporterLN')
        self.assertEqual(node['reporterDob'], '1985-03-04')
