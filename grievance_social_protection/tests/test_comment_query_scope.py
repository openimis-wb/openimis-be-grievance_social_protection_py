"""
The comments query returns the comments of the tickets the user may see:
the tickets filter_ticket_queryset keeps for the user, at an access level
other than 'none'.
"""
from django.core.cache import cache
from django.test import TestCase
from graphene import Schema
from graphene.test import Client

from core.models.openimis_graphql_test_case import BaseTestContext
from core.test_helpers import create_test_interactive_user, create_test_role
from grievance_social_protection.apps import TicketConfig
from grievance_social_protection.models import Comment, Ticket
from grievance_social_protection.schema import Query
from grievance_social_protection.tests.test_helpers import (
    assign_rights_to_user, get_rights, restore_grievance_config, setup_grievance_config,
)

OPEN_CATEGORY = 'csq_open'
SECRET_CATEGORY = 'csq_secret'
FLAG = 'CSQ_FLAG'

COMMENTS_QUERY = '''
    query {
        comments(ticket_Code_Istartswith: "CSQ-") {
            totalCount
            edges {
                node {
                    comment
                    ticket { code }
                }
            }
        }
    }
'''


class CommentQueryScopeTest(TestCase):

    def setUp(self):
        super().setUp()
        self._snapshot = setup_grievance_config({
            'grievance_types': [
                OPEN_CATEGORY,
                {'name': SECRET_CATEGORY, 'permissions': ['read']},
            ],
            'grievance_flags': [
                {'name': FLAG, 'permissions': ['read']},
            ],
        })
        self.addCleanup(restore_grievance_config, self._snapshot)
        category_rights = get_rights('processed_categories', SECRET_CATEGORY)
        flag_rights = get_rights('processed_flags', FLAG)
        comment_rights = [int(r) for r in TicketConfig.gql_query_comments_perms]

        self.no_access_user = self._user('csq_no_access', comment_rights)
        self.category_reader = self._user('csq_category_reader', comment_rights + [category_rights['read']])
        self.full_reader = self._user(
            'csq_full_reader', comment_rights + [category_rights['read'], flag_rights['read']])

        self._ticket_with_comment('CSQ-OPEN', OPEN_CATEGORY)
        self._ticket_with_comment('CSQ-SECRET', SECRET_CATEGORY)
        self._ticket_with_comment('CSQ-FLAGGED', OPEN_CATEGORY, FLAG)
        self._ticket_with_comment('CSQ-FLAGGED-JSON', OPEN_CATEGORY, f'["{FLAG}"]')
        deleted = self._ticket_with_comment('CSQ-DELETED', OPEN_CATEGORY)
        Ticket.objects.filter(id=deleted.id).update(is_deleted=True)

    def _user(self, username, rights):
        empty_role = create_test_role([], name=f'NoRights_{username}')
        user = create_test_interactive_user(username=username, roles=[empty_role.id])
        assign_rights_to_user(user, rights, f'Role_{username}')
        cache.clear()
        return user

    def _ticket_with_comment(self, code, category, flags=None):
        ticket = Ticket(code=code, title='Scope', description='secret', category=category, flags=flags)
        ticket.save(user=self.full_reader)
        Comment(ticket_id=ticket.id, comment=f'comment on {code}').save(user=self.full_reader)
        return ticket

    def _ticket_codes(self, user):
        result = Client(Schema(query=Query)).execute(
            COMMENTS_QUERY, context=BaseTestContext(user).get_request())
        self.assertNotIn('errors', result, result.get('errors'))
        edges = result['data']['comments']['edges']
        self.assertEqual(result['data']['comments']['totalCount'], len(edges))
        for edge in edges:
            self.assertEqual(edge['node']['comment'], f"comment on {edge['node']['ticket']['code']}")
        return sorted(edge['node']['ticket']['code'] for edge in edges)

    def test_user_without_category_or_flag_right_reads_unrestricted_ticket_comments_only(self):
        self.assertEqual(self._ticket_codes(self.no_access_user), ['CSQ-OPEN'])

    def test_category_reader_does_not_read_comments_of_tickets_with_unreadable_flag(self):
        self.assertEqual(self._ticket_codes(self.category_reader), ['CSQ-OPEN', 'CSQ-SECRET'])

    def test_full_reader_reads_every_non_deleted_ticket_comment(self):
        self.assertEqual(
            self._ticket_codes(self.full_reader),
            ['CSQ-FLAGGED', 'CSQ-FLAGGED-JSON', 'CSQ-OPEN', 'CSQ-SECRET'],
        )
