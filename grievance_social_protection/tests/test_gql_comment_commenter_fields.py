from uuid import uuid4

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from graphene import Schema
from graphene.test import Client

from core.datetimes.ad_datetime import datetime
from core.models import User
from core.models.openimis_graphql_test_case import openIMISGraphQLTestCase, BaseTestContext
from grievance_social_protection.models import Comment
from grievance_social_protection.schema import Query, Mutation
from grievance_social_protection.tests.test_helpers import create_ticket, create_test_grievance_user

gql_query_comment_commenter_fields = """
query {
  comments(id: "%s") {
    edges {
      node {
        comment
        commenterFirstName
        commenterLastName
        commenterDob
      }
    }
  }
}
"""


class GQLCommentCommenterFieldsTestCase(openIMISGraphQLTestCase):
    """commenterFirstName/LastName/Dob resolve to null, without error, when the commenter row is absent."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = create_test_grievance_user(username='commenter_fields_user')
        cls.ticket = create_ticket(cls.user)

        individual_model = apps.get_model('individual', 'Individual')
        cls.individual = individual_model(first_name='CommenterFN', last_name='CommenterLN', dob=datetime(1990, 1, 2))
        cls.individual.save(username=cls.user.username)
        user_type = ContentType.objects.get_for_model(User)
        individual_type = ContentType.objects.get_for_model(individual_model)

        cls.comment_null_id = cls._create_comment('null id', user_type, None)
        cls.comment_missing_row = cls._create_comment('missing row', individual_type, str(uuid4()))
        cls.comment_individual = cls._create_comment('individual', individual_type, str(cls.individual.id))
        cls.comment_user = cls._create_comment('user', user_type, str(cls.user.id))

        cls.gql_client = Client(Schema(query=Query, mutation=Mutation))
        cls.gql_context = BaseTestContext(cls.user)

    @classmethod
    def _create_comment(cls, text, commenter_type, commenter_id):
        comment = Comment(ticket_id=cls.ticket.id, comment=text,
                          commenter_type=commenter_type, commenter_id=commenter_id)
        comment.save(user=cls.user)
        return comment

    def _commenter_fields(self, comment):
        result = self.gql_client.execute(
            gql_query_comment_commenter_fields % comment.id, context=self.gql_context.get_request())
        self.assertIsNone(result.get('errors'), result.get('errors'))
        edges = result['data']['comments']['edges']
        self.assertEqual(len(edges), 1)
        return edges[0]['node']

    def test_null_commenter_id_resolves_to_null(self):
        node = self._commenter_fields(self.comment_null_id)
        self.assertIsNone(node['commenterFirstName'])
        self.assertIsNone(node['commenterLastName'])
        self.assertIsNone(node['commenterDob'])

    def test_missing_commenter_row_resolves_to_null(self):
        node = self._commenter_fields(self.comment_missing_row)
        self.assertIsNone(node['commenterFirstName'])
        self.assertIsNone(node['commenterLastName'])
        self.assertIsNone(node['commenterDob'])

    def test_individual_commenter_resolves_names_and_dob(self):
        node = self._commenter_fields(self.comment_individual)
        self.assertEqual(node['commenterFirstName'], 'CommenterFN')
        self.assertEqual(node['commenterLastName'], 'CommenterLN')
        self.assertEqual(node['commenterDob'], '1990-01-02')

    def test_user_commenter_resolves_to_null(self):
        node = self._commenter_fields(self.comment_user)
        self.assertIsNone(node['commenterFirstName'])
        self.assertIsNone(node['commenterLastName'])
        self.assertIsNone(node['commenterDob'])
