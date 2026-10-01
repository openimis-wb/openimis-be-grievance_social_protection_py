"""
reopenTicket and resolveGrievanceByComment change the ticket status, so they
require the update right of the ticket's category and of each of its flags,
as updateTicket does, on top of the module rights 127002 and 127006.
"""
from uuid import uuid4

from django.core.cache import cache
from graphene import Schema
from graphene.test import Client

from core.models import MutationLog
from core.models.openimis_graphql_test_case import openIMISGraphQLTestCase, BaseTestContext
from core.test_helpers import create_test_interactive_user, create_test_role
from grievance_social_protection.apps import TicketConfig
from grievance_social_protection.models import Comment, Ticket
from grievance_social_protection.schema import Query, Mutation
from grievance_social_protection.tests.gql_payloads import (
    gql_mutation_reopen_ticket,
    gql_mutation_resolve_ticket_by_comment,
)
from grievance_social_protection.tests.test_helpers import (
    assign_rights_to_user, get_rights, restore_grievance_config, setup_grievance_config,
)

OPEN_CATEGORY = 'stc_open'
GUARDED_CATEGORY = 'stc_guarded'
FLAG = 'STC_FLAG'


class TicketStatusChangeAccessTest(openIMISGraphQLTestCase):

    def setUp(self):
        super().setUp()
        self._snapshot = setup_grievance_config({
            'grievance_types': [
                OPEN_CATEGORY,
                {'name': GUARDED_CATEGORY, 'permissions': ['read', 'update']},
            ],
            'grievance_flags': [
                {'name': FLAG, 'permissions': ['read', 'update']},
            ],
        })
        self.addCleanup(restore_grievance_config, self._snapshot)
        category_rights = get_rights('processed_categories', GUARDED_CATEGORY)
        flag_rights = get_rights('processed_flags', FLAG)
        base_rights = [
            int(r) for r in (
                TicketConfig.gql_query_tickets_perms
                + TicketConfig.gql_query_comments_perms
                + TicketConfig.gql_mutation_update_tickets_perms
                + TicketConfig.gql_mutation_resolve_grievance_perms
            )
        ]
        self.without_update_right = self._user(
            'stc_without_update', base_rights + [category_rights['read'], flag_rights['read']])
        self.with_update_right = self._user(
            'stc_with_update', base_rights + [
                category_rights['read'], category_rights['update'],
                flag_rights['read'], flag_rights['update'],
            ])
        self.gql_client = Client(Schema(query=Query, mutation=Mutation))

    def _user(self, username, rights):
        empty_role = create_test_role([], name=f'NoRights_{username}')
        user = create_test_interactive_user(username=username, roles=[empty_role.id])
        assign_rights_to_user(user, rights, f'Role_{username}')
        cache.clear()
        return user

    def _ticket(self, category, flags=None, status=Ticket.TicketStatus.OPEN, resolved_comment=False):
        ticket = Ticket(
            code=f'STC-{uuid4().hex[:10]}', title='Status change', category=category,
            flags=flags, status=status,
        )
        ticket.save(user=self.with_update_right)
        comment = Comment(ticket_id=ticket.id, comment='resolution', is_resolution=resolved_comment)
        comment.save(user=self.with_update_right)
        return ticket, comment

    def _closed_ticket(self, category, flags=None):
        return self._ticket(category, flags, status=Ticket.TicketStatus.CLOSED, resolved_comment=True)

    def _run(self, payload, object_id, user):
        mutation_id = str(uuid4())
        self.gql_client.execute(payload % (object_id, mutation_id), context=BaseTestContext(user).get_request())
        return MutationLog.objects.get(client_mutation_id=mutation_id)

    def _assert_unchanged(self, ticket, comment, status, is_resolution):
        self.assertEqual(Ticket.objects.get(id=ticket.id).status, status)
        self.assertEqual(Comment.objects.get(id=comment.id).is_resolution, is_resolution)

    def test_reopen_refused_without_category_update_right(self):
        ticket, comment = self._closed_ticket(GUARDED_CATEGORY)
        mutation_log = self._run(gql_mutation_reopen_ticket, ticket.id, self.without_update_right)
        self.assertTrue(mutation_log.error)
        self.assertIn(GUARDED_CATEGORY, mutation_log.error)
        self._assert_unchanged(ticket, comment, Ticket.TicketStatus.CLOSED, True)

    def test_reopen_refused_without_flag_update_right(self):
        ticket, comment = self._closed_ticket(OPEN_CATEGORY, FLAG)
        mutation_log = self._run(gql_mutation_reopen_ticket, ticket.id, self.without_update_right)
        self.assertTrue(mutation_log.error)
        self.assertIn(FLAG, mutation_log.error)
        self._assert_unchanged(ticket, comment, Ticket.TicketStatus.CLOSED, True)

    def test_reopen_allowed_with_category_and_flag_update_rights(self):
        ticket, comment = self._closed_ticket(GUARDED_CATEGORY, FLAG)
        mutation_log = self._run(gql_mutation_reopen_ticket, ticket.id, self.with_update_right)
        self.assertFalse(mutation_log.error)
        self._assert_unchanged(ticket, comment, Ticket.TicketStatus.OPEN, False)

    def test_reopen_allowed_on_unrestricted_category(self):
        ticket, comment = self._closed_ticket(OPEN_CATEGORY)
        mutation_log = self._run(gql_mutation_reopen_ticket, ticket.id, self.without_update_right)
        self.assertFalse(mutation_log.error)
        self._assert_unchanged(ticket, comment, Ticket.TicketStatus.OPEN, False)

    def test_resolve_by_comment_refused_without_category_update_right(self):
        ticket, comment = self._ticket(GUARDED_CATEGORY)
        mutation_log = self._run(gql_mutation_resolve_ticket_by_comment, comment.id, self.without_update_right)
        self.assertTrue(mutation_log.error)
        self.assertIn(GUARDED_CATEGORY, mutation_log.error)
        self._assert_unchanged(ticket, comment, Ticket.TicketStatus.OPEN, False)

    def test_resolve_by_comment_refused_without_flag_update_right(self):
        ticket, comment = self._ticket(OPEN_CATEGORY, FLAG)
        mutation_log = self._run(gql_mutation_resolve_ticket_by_comment, comment.id, self.without_update_right)
        self.assertTrue(mutation_log.error)
        self.assertIn(FLAG, mutation_log.error)
        self._assert_unchanged(ticket, comment, Ticket.TicketStatus.OPEN, False)

    def test_resolve_by_comment_allowed_with_category_and_flag_update_rights(self):
        ticket, comment = self._ticket(GUARDED_CATEGORY, FLAG)
        mutation_log = self._run(gql_mutation_resolve_ticket_by_comment, comment.id, self.with_update_right)
        self.assertFalse(mutation_log.error)
        self._assert_unchanged(ticket, comment, Ticket.TicketStatus.CLOSED, True)

    def test_resolve_by_comment_allowed_on_unrestricted_category(self):
        ticket, comment = self._ticket(OPEN_CATEGORY)
        mutation_log = self._run(gql_mutation_resolve_ticket_by_comment, comment.id, self.without_update_right)
        self.assertFalse(mutation_log.error)
        self._assert_unchanged(ticket, comment, Ticket.TicketStatus.CLOSED, True)
