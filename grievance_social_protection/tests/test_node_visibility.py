"""
node(id:) and the nested comment connection return a ticket or a comment only
to a user who could list it: the ticket read right and the category rights
through filter_ticket_queryset for tickets, the comment read right (or being
attending staff) and filter_comment_queryset for comments. A restricted
reader gets the ticket with its restricted fields masked.
"""
import graphene
from django.core.cache import cache
from django.test import TestCase
from graphene import Schema
from graphene.test import Client
from graphql_relay import to_global_id

from core.models.openimis_graphql_test_case import BaseTestContext
from core.test_helpers import create_test_interactive_user, create_test_role
from grievance_social_protection.apps import TicketConfig
from grievance_social_protection.gql_queries import RESTRICTED_VALUE
from grievance_social_protection.models import Comment, Ticket
from grievance_social_protection.schema import Query
from grievance_social_protection.tests.test_helpers import (
    assign_rights_to_user, get_rights, restore_grievance_config, setup_grievance_config,
)

OPEN_CATEGORY = 'nv_open'
VBG_CATEGORY = 'nv_vbg'
UNRELATED_RIGHT = 1799399


class NodeQuery(Query):
    node = graphene.relay.Node.Field()


TICKET_NODE = '''
    query { node(id: "%s") { ... on TicketGQLType { code title description } } }
'''
COMMENT_NODE = '''
    query { node(id: "%s") { ... on CommentGQLType { comment ticket { code } } } }
'''
TICKET_COMMENTS = '''
    query {
        tickets(code_Istartswith: "NV-") {
            edges { node { code commentSet { totalCount edges { node { comment } } } } }
        }
    }
'''

TICKET_HISTORY = '''
    query { tickets(showHistory: true, code_Istartswith: "NV-") { edges { node { code title } } } }
'''


class NodeVisibilityTest(TestCase):

    def setUp(self):
        super().setUp()
        snapshot = setup_grievance_config({
            'grievance_types': [
                OPEN_CATEGORY,
                {'name': VBG_CATEGORY, 'permissions': ['restricted_read', 'read']},
            ],
            'grievance_flags': [],
        })
        self.addCleanup(restore_grievance_config, snapshot)
        vbg = get_rights('processed_categories', VBG_CATEGORY)
        ticket_read = [int(r) for r in TicketConfig.gql_query_tickets_perms]
        comment_read = [int(r) for r in TicketConfig.gql_query_comments_perms]

        self.nobody = self._user('nv_nobody', [UNRELATED_RIGHT])
        self.ticket_reader = self._user('nv_ticket_reader', ticket_read)
        self.restricted_reader = self._user('nv_restricted', ticket_read + [vbg['restricted_read']])
        self.full_reader = self._user('nv_full', ticket_read + comment_read + [vbg['read']])
        self.comment_reader = self._user('nv_comment_reader', comment_read)

        self.open_ticket, self.open_comment = self._ticket_with_comment('NV-OPEN', OPEN_CATEGORY)
        self.vbg_ticket, self.vbg_comment = self._ticket_with_comment('NV-VBG', VBG_CATEGORY)

    def _user(self, username, rights):
        role = create_test_role([], name=f'NoRights_{username}')
        user = create_test_interactive_user(username=username, roles=[role.id])
        assign_rights_to_user(user, rights, f'Role_{username}')
        cache.clear()
        return user

    def _ticket_with_comment(self, code, category):
        ticket = Ticket(code=code, title=f'title {code}', description=f'secret {code}', category=category)
        ticket.save(user=self.full_reader)
        comment = Comment(ticket_id=ticket.id, comment=f'comment on {code}')
        comment.save(user=self.full_reader)
        return ticket, comment

    def _execute(self, user, query):
        result = Client(Schema(query=NodeQuery)).execute(query, context=BaseTestContext(user).get_request())
        self.assertNotIn('errors', result, result.get('errors'))
        return result['data']

    def _ticket_node(self, user, ticket):
        return self._execute(user, TICKET_NODE % to_global_id('TicketGQLType', str(ticket.id)))['node']

    def _comment_node(self, user, comment):
        return self._execute(user, COMMENT_NODE % to_global_id('CommentGQLType', str(comment.id)))['node']

    def test_ticket_node_is_null_without_the_ticket_read_right(self):
        for user in (self.nobody, self.comment_reader):
            for ticket in (self.open_ticket, self.vbg_ticket):
                with self.subTest(user=user.username, ticket=ticket.code):
                    self.assertIsNone(self._ticket_node(user, ticket))

    def test_ticket_node_is_null_for_a_category_the_user_cannot_view(self):
        self.assertIsNone(self._ticket_node(self.ticket_reader, self.vbg_ticket))
        self.assertEqual(self._ticket_node(self.ticket_reader, self.open_ticket),
                         {'code': 'NV-OPEN', 'title': 'title NV-OPEN', 'description': 'secret NV-OPEN'})

    def test_restricted_reader_gets_the_ticket_masked(self):
        self.assertEqual(self._ticket_node(self.restricted_reader, self.vbg_ticket),
                         {'code': 'NV-VBG', 'title': RESTRICTED_VALUE, 'description': RESTRICTED_VALUE})

    def test_full_reader_gets_the_ticket_in_clear(self):
        self.assertEqual(self._ticket_node(self.full_reader, self.vbg_ticket),
                         {'code': 'NV-VBG', 'title': 'title NV-VBG', 'description': 'secret NV-VBG'})

    def test_comment_node_is_null_without_the_comment_read_right(self):
        for user in (self.nobody, self.ticket_reader):
            with self.subTest(user=user.username):
                self.assertIsNone(self._comment_node(user, self.open_comment))

    def test_comment_node_is_null_on_a_ticket_the_user_cannot_view(self):
        self.assertIsNone(self._comment_node(self.comment_reader, self.vbg_comment))
        self.assertEqual(self._comment_node(self.comment_reader, self.open_comment),
                         {'comment': 'comment on NV-OPEN', 'ticket': {'code': 'NV-OPEN'}})

    def test_full_reader_gets_the_comment_node(self):
        self.assertEqual(self._comment_node(self.full_reader, self.vbg_comment),
                         {'comment': 'comment on NV-VBG', 'ticket': {'code': 'NV-VBG'}})

    def test_nested_comments_of_a_ticket_need_the_comment_read_right(self):
        edges = self._execute(self.ticket_reader, TICKET_COMMENTS)['tickets']['edges']
        self.assertEqual([(e['node']['code'], e['node']['commentSet']['totalCount']) for e in edges],
                         [('NV-OPEN', 0)])

    def test_full_reader_gets_the_nested_comments(self):
        edges = self._execute(self.full_reader, TICKET_COMMENTS)['tickets']['edges']
        self.assertEqual(
            sorted((e['node']['code'], [c['node']['comment'] for c in e['node']['commentSet']['edges']])
                   for e in edges),
            [('NV-OPEN', ['comment on NV-OPEN']), ('NV-VBG', ['comment on NV-VBG'])])

    def test_ticket_history_goes_through_the_same_visibility(self):
        self.vbg_ticket.title = 'title NV-VBG v2'
        self.vbg_ticket.save(user=self.full_reader)
        for user, expected in (
                (self.full_reader, [('NV-OPEN', 'title NV-OPEN'), ('NV-VBG', 'title NV-VBG'),
                                    ('NV-VBG', 'title NV-VBG v2')]),
                (self.restricted_reader, [('NV-OPEN', 'title NV-OPEN'), ('NV-VBG', RESTRICTED_VALUE),
                                          ('NV-VBG', RESTRICTED_VALUE)]),
                (self.ticket_reader, [('NV-OPEN', 'title NV-OPEN')])):
            with self.subTest(user=user.username):
                edges = self._execute(user, TICKET_HISTORY)['tickets']['edges']
                self.assertEqual(sorted((e['node']['code'], e['node']['title']) for e in edges), expected)
