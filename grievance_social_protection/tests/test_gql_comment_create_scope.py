from uuid import uuid4

from graphene import Schema
from graphene.test import Client

from core.models import MutationLog
from core.models.openimis_graphql_test_case import openIMISGraphQLTestCase, BaseTestContext
from core.test_helpers import create_test_interactive_user, create_test_role
from grievance_social_protection.models import Comment, Ticket
from grievance_social_protection.schema import Query, Mutation
from grievance_social_protection.tests.gql_payloads import gql_mutation_create_comment_anonymous_user
from grievance_social_protection.tests.data import service_add_ticket_payload
from grievance_social_protection.tests.test_helpers import create_test_grievance_user


class GQLCommentCreateScopeTestCase(openIMISGraphQLTestCase):
    """createComment without the bypass right is limited to the tickets the user attends."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin = create_test_grievance_user(username='comment_scope_admin')
        staff_role = create_test_role(perm_names=["gql_query_tickets_perms"], name="CommentScopeStaffRole")
        cls.staff = create_test_interactive_user(username='comment_scope_staff', roles=[staff_role.id])
        cls.outsider = create_test_interactive_user(username='comment_scope_outsider', roles=[staff_role.id])

        cls.own_ticket = cls._create_ticket('SCOPE-OWN', attending_staff=cls.staff)
        cls.other_ticket = cls._create_ticket('SCOPE-OTHER')

        cls.gql_client = Client(Schema(query=Query, mutation=Mutation))

    @classmethod
    def _create_ticket(cls, code, attending_staff=None):
        ticket = Ticket(**{**service_add_ticket_payload, 'code': code, 'attending_staff': attending_staff})
        ticket.save(user=cls.admin)
        return ticket

    def _create_comment(self, user, ticket):
        mutation_id = str(uuid4())
        payload = gql_mutation_create_comment_anonymous_user % ("scope test comment", ticket.id, mutation_id)
        self.gql_client.execute(payload, context=BaseTestContext(user).get_request())
        return MutationLog.objects.get(client_mutation_id=mutation_id)

    def test_attending_staff_comments_on_own_ticket(self):
        mutation_log = self._create_comment(self.staff, self.own_ticket)
        self.assertFalse(mutation_log.error)
        self.assertTrue(Comment.objects.filter(ticket_id=self.own_ticket.id).exists())

    def test_attending_staff_cannot_comment_on_other_ticket(self):
        mutation_log = self._create_comment(self.staff, self.other_ticket)
        self.assertTrue(mutation_log.error)
        self.assertFalse(Comment.objects.filter(ticket_id=self.other_ticket.id).exists())

    def test_user_attending_no_ticket_cannot_comment(self):
        mutation_log = self._create_comment(self.outsider, self.own_ticket)
        self.assertTrue(mutation_log.error)
        self.assertFalse(Comment.objects.filter(ticket_id=self.own_ticket.id).exists())

    def test_bypass_right_comments_on_any_ticket(self):
        mutation_log = self._create_comment(self.admin, self.other_ticket)
        self.assertFalse(mutation_log.error)
        self.assertTrue(Comment.objects.filter(ticket_id=self.other_ticket.id).exists())
