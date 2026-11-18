"""
A comment is created only on a ticket the user may see: a non-deleted ticket
that filter_ticket_queryset keeps for the user, at an access level other than
'none' (the tickets whose comments the comments query returns), or a
non-deleted ticket the user attends. The rule applies to every caller of
CommentService.create, the holder of the bypass right included. A ticket the
user may not see is refused with the message of a ticket that does not exist.
"""
import re
from uuid import uuid4

from django.core.cache import cache
from django.test import TestCase
from graphene import Schema
from graphene.test import Client

from core.models import MutationLog
from core.models.openimis_graphql_test_case import BaseTestContext
from core.test_helpers import create_test_interactive_user, create_test_role
from grievance_social_protection.apps import TicketConfig
from grievance_social_protection.models import Comment, Ticket
from grievance_social_protection.schema import Query, Mutation
from grievance_social_protection.services import CommentService
from grievance_social_protection.tests.gql_payloads import gql_mutation_create_comment_anonymous_user
from grievance_social_protection.tests.test_helpers import (
    assign_rights_to_user, get_rights, restore_grievance_config, setup_grievance_config,
)

OPEN_CATEGORY = 'ccv_open'
SECRET_CATEGORY = 'ccv_secret'
FLAG = 'CCV_FLAG'
# The ticket id in a refusal message, in any rendering of the UUID.
ID_IN_MESSAGE = re.compile(r'[0-9a-f-]{16,}')


class CommentCreateTicketVisibilityTest(TestCase):

    def setUp(self):
        super().setUp()
        snapshot = setup_grievance_config({
            'grievance_types': [
                OPEN_CATEGORY,
                {'name': SECRET_CATEGORY, 'permissions': ['read']},
            ],
            'grievance_flags': [
                {'name': FLAG, 'permissions': ['read']},
            ],
        })
        self.addCleanup(restore_grievance_config, snapshot)
        category_rights = get_rights('processed_categories', SECRET_CATEGORY)
        flag_rights = get_rights('processed_flags', FLAG)
        bypass = [int(r) for r in TicketConfig.gql_mutation_delete_tickets_perms]

        self.commenter = self._user('ccv_commenter', bypass)
        self.full_reader = self._user(
            'ccv_full_reader', bypass + [category_rights['read'], flag_rights['read']])
        self.staff = self._user('ccv_staff', [int(r) for r in TicketConfig.gql_query_tickets_perms])

        self.open_ticket = self._ticket('CCV-OPEN', OPEN_CATEGORY)
        self.secret_ticket = self._ticket('CCV-SECRET', SECRET_CATEGORY)
        self.flagged_ticket = self._ticket('CCV-FLAGGED', OPEN_CATEGORY, FLAG)
        self.attended_secret_ticket = self._ticket('CCV-ATTENDED', SECRET_CATEGORY, attending_staff=self.staff)
        self.deleted_ticket = self._ticket('CCV-DELETED', OPEN_CATEGORY)
        Ticket.objects.filter(id=self.deleted_ticket.id).update(is_deleted=True)

    def _user(self, username, rights):
        empty_role = create_test_role([], name=f'NoRights_{username}')
        user = create_test_interactive_user(username=username, roles=[empty_role.id])
        assign_rights_to_user(user, rights, f'Role_{username}')
        cache.clear()
        return user

    def _ticket(self, code, category, flags=None, attending_staff=None):
        ticket = Ticket(code=code, title='Visibility', category=category, flags=flags,
                        attending_staff=attending_staff)
        ticket.save(user=self.full_reader)
        return ticket

    def _mutate(self, user, ticket_id):
        mutation_id = str(uuid4())
        payload = gql_mutation_create_comment_anonymous_user % ('visibility comment', ticket_id, mutation_id)
        Client(Schema(query=Query, mutation=Mutation)).execute(
            payload, context=BaseTestContext(user).get_request())
        return MutationLog.objects.get(client_mutation_id=mutation_id)

    def _comments(self, ticket):
        return Comment.objects.filter(ticket_id=ticket.id).count()

    def test_bypass_right_comments_on_a_visible_ticket(self):
        self.assertFalse(self._mutate(self.commenter, self.open_ticket.id).error)
        self.assertEqual(self._comments(self.open_ticket), 1)

    def test_bypass_right_without_the_category_right_is_refused(self):
        self.assertTrue(self._mutate(self.commenter, self.secret_ticket.id).error)
        self.assertEqual(self._comments(self.secret_ticket), 0)

    def test_bypass_right_without_the_flag_right_is_refused(self):
        self.assertTrue(self._mutate(self.commenter, self.flagged_ticket.id).error)
        self.assertEqual(self._comments(self.flagged_ticket), 0)

    def test_attending_staff_without_the_category_right_comments(self):
        self.assertFalse(self._mutate(self.staff, self.attended_secret_ticket.id).error)
        self.assertEqual(self._comments(self.attended_secret_ticket), 1)

    def test_staff_attending_another_ticket_is_refused(self):
        self.assertTrue(self._mutate(self.staff, self.secret_ticket.id).error)
        self.assertEqual(self._comments(self.secret_ticket), 0)

    def test_a_deleted_ticket_is_refused(self):
        self.assertTrue(self._mutate(self.commenter, self.deleted_ticket.id).error)
        self.assertEqual(self._comments(self.deleted_ticket), 0)

    def test_holder_of_the_category_and_flag_rights_comments(self):
        for ticket in (self.secret_ticket, self.flagged_ticket):
            with self.subTest(ticket=ticket.code):
                self.assertFalse(self._mutate(self.full_reader, ticket.id).error)
                self.assertEqual(self._comments(ticket), 1)

    def test_service_refuses_a_hidden_ticket_as_a_missing_one(self):
        hidden = CommentService(self.commenter).create(
            {'ticket_id': self.secret_ticket.id, 'comment': 'service comment'})
        missing_id = uuid4()
        missing = CommentService(self.commenter).create({'ticket_id': missing_id, 'comment': 'service comment'})
        self.assertFalse(hidden['success'])
        self.assertEqual(self._comments(self.secret_ticket), 0)
        self.assertEqual((hidden['message'], ID_IN_MESSAGE.sub('<id>', hidden['detail'])),
                         (missing['message'], ID_IN_MESSAGE.sub('<id>', missing['detail'])))
