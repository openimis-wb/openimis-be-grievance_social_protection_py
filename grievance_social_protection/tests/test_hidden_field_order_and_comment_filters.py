"""
A field hidden from the user on a ticket (outside the visible_fields of its
category at the 'restricted' level, or listed in grievance_anonymized_fields)
can be neither ordered on nor filtered through comments:
- an ordering on that field, from orderBy or from the resolver, orders the
  tickets hiding it as if it were null, so their order reveals nothing;
- a comment filter on that ticket field leaves out the comments of the
  tickets hiding it, as a ticket filter leaves out those tickets;
- a comment ordering on that ticket field is treated as a ticket ordering;
- an ordering with a masked term ends with id, so the rows tied on that term
  keep one order from page to page.
"""
from unittest import mock

from django.core.cache import cache
from django.test import TestCase
from graphene import Schema
from graphene.test import Client
from graphql_relay import from_global_id

from core.models.openimis_graphql_test_case import BaseTestContext
from core.test_helpers import create_test_interactive_user, create_test_role
from grievance_social_protection.access_control import GrievanceAccessControl
from grievance_social_protection.apps import TicketConfig
from grievance_social_protection.gql_queries import COMMENT_TICKET_PREFIX, order_without_hidden_fields
from grievance_social_protection.models import Comment, Ticket
from grievance_social_protection.schema import Query
from grievance_social_protection.tests.test_helpers import (
    assign_rights_to_user, get_rights, restore_grievance_config, setup_grievance_config,
)

PREFIX = 'HFO-'
OPEN_CATEGORY = 'hfo_open'
PRV_CATEGORY = 'hfo_prv'
ANON_CATEGORY = 'hfo_anon'
ANONYMIZED = {ANON_CATEGORY: ['channel', 'attending_staff', 'json_ext']}

PRV = ['PRV-1', 'PRV-2']
ANON = ['ANON-1', 'ANON-2']

# Tickets are selected by category and told apart by id: both stay readable
# at the restricted level, where code is hidden like any column the
# category does not list in visible_fields.
TICKETS_QUERY = '''
    query {
        %s(category_Istartswith: "%s"%s) {
            edges { node { id } }
        }
    }
'''

COMMENTS_QUERY = '''
    query {
        comments(ticket_Category_Istartswith: "%s"%s) {
            totalCount
            edges { node { comment } }
        }
    }
'''

COMMENT_SET_QUERY = '''
    query {
        tickets(id: "%s") {
            edges { node { commentSet(%s) { edges { node { comment } } } } }
        }
    }
'''

CATEGORY_PREFIX = 'hfo_'


class HiddenFieldOrderAndCommentFiltersTest(TestCase):
    def setUp(self):
        self._ticket_ids = {}
        self._snapshot = setup_grievance_config({
            'grievance_types': [
                OPEN_CATEGORY,
                ANON_CATEGORY,
                {
                    'name': PRV_CATEGORY,
                    'permissions': ['restricted_read', 'read'],
                    'visible_fields': ['category', 'description', 'status'],
                },
            ],
            'grievance_flags': [],
        })
        self.addCleanup(restore_grievance_config, self._snapshot)
        for patcher in (
                mock.patch.object(TicketConfig, 'grievance_anonymized_fields', ANONYMIZED),
                # Row filters other installed modules register are not under test.
                mock.patch.object(GrievanceAccessControl, 'ticket_queryset_filters', [])):
            patcher.start()
            self.addCleanup(patcher.stop)

        prv_rights = get_rights('processed_categories', PRV_CATEGORY)
        base_rights = [int(r) for r in TicketConfig.gql_query_tickets_perms + TicketConfig.gql_query_comments_perms]
        self.admin = create_test_interactive_user(username='hfo_admin')
        self.restricted_reader = self._user('hfo_restricted', base_rights + [prv_rights['restricted_read']])
        self.reader = self._user('hfo_reader', base_rights + [prv_rights['read']])
        staff_a = create_test_interactive_user(username='hfo_staff_a')
        staff_z = create_test_interactive_user(username='hfo_staff_z')

        # Each hidden pair's values order it against its codes.
        self._ticket('OPEN-A', OPEN_CATEGORY, title='mm', priority='A1', channel='c2', rank=2)
        self._ticket('OPEN-B', OPEN_CATEGORY, title='nn', priority='B1', channel='c3', rank=3)
        self._ticket('PRV-1', PRV_CATEGORY, title='zz', priority='Z', description='needle')
        self._ticket('PRV-2', PRV_CATEGORY, title='aa', priority='A0')
        self._ticket('ANON-1', ANON_CATEGORY, title='oo', channel='c9', attending_staff=staff_z, rank=9)
        self._ticket('ANON-2', ANON_CATEGORY, title='pp', channel='c0', attending_staff=staff_a, rank=0)
        self.schema = Schema(query=Query)
        self.ticket_ids = {suffix: str(ticket_id) for suffix, ticket_id in self._ticket_ids.items()}
        self.suffix_by_id = {ticket_id: suffix for suffix, ticket_id in self.ticket_ids.items()}

    def _user(self, username, rights):
        empty_role = create_test_role([], name=f'NoRights_{username}')
        user = create_test_interactive_user(username=username, roles=[empty_role.id])
        assign_rights_to_user(user, rights, f'Role_{username}')
        cache.clear()
        return user

    def _ticket(self, suffix, category, description='description', rank=None, **fields):
        ticket = Ticket(code=PREFIX + suffix, description=description, category=category, status='OPEN',
                        json_ext={'rank': rank} if rank is not None else {}, **fields)
        ticket.save(user=self.admin)
        self._ticket_ids[suffix] = ticket.id
        Comment(ticket_id=ticket.id, comment=f'comment on {suffix}').save(user=self.admin)

    def _execute(self, user, query):
        result = Client(self.schema).execute(query, context=BaseTestContext(user).get_request())
        self.assertNotIn('errors', result, result.get('errors'))
        return result['data']

    def _ticket_codes(self, user, arguments='', field='tickets'):
        query = TICKETS_QUERY % (field, CATEGORY_PREFIX, ', ' + arguments if arguments else '')
        return [self.suffix_by_id[from_global_id(edge['node']['id'])[1]]
                for edge in self._execute(user, query)[field]['edges']]

    def _comment_codes(self, user, arguments=''):
        data = self._execute(user, COMMENTS_QUERY % (CATEGORY_PREFIX, ', ' + arguments if arguments else ''))['comments']
        codes = [edge['node']['comment'][len('comment on '):] for edge in data['edges']]
        self.assertEqual(data['totalCount'], len(codes))
        return codes

    def _by_id(self, suffixes):
        return sorted(suffixes, key=lambda suffix: self.ticket_ids[suffix])

    @staticmethod
    def _among(codes, subset):
        return [code for code in codes if code in subset]

    def _assert_unordered_by_hidden_field(self, ascending, descending, hidden):
        # Ordered by the field then by id: the tickets hiding the field come
        # in id order whatever the direction.
        self.assertEqual(self._among(ascending, hidden), self._by_id(hidden))
        self.assertEqual(self._among(descending, hidden), self._by_id(hidden))

    def test_ordering_on_a_restricted_hidden_field_leaves_those_tickets_unordered_by_it(self):
        ascending = self._ticket_codes(self.restricted_reader, 'orderBy: ["priority", "id"]')
        descending = self._ticket_codes(self.restricted_reader, 'orderBy: ["-priority", "id"]')

        self._assert_unordered_by_hidden_field(ascending, descending, PRV)
        self.assertEqual(self._among(ascending, ['OPEN-A', 'OPEN-B']), ['OPEN-A', 'OPEN-B'])
        self.assertEqual(self._among(descending, ['OPEN-A', 'OPEN-B']), ['OPEN-B', 'OPEN-A'])

    def test_reader_seeing_the_field_orders_every_ticket_by_it(self):
        ascending = self._ticket_codes(self.reader, 'orderBy: ["priority", "id"]')
        self.assertEqual(self._among(ascending, PRV + ['OPEN-A', 'OPEN-B']), ['PRV-2', 'OPEN-A', 'OPEN-B', 'PRV-1'])

    def test_ordering_on_an_anonymized_field_leaves_those_tickets_unordered_by_it(self):
        ascending = self._ticket_codes(self.reader, 'orderBy: ["channel", "id"]')
        descending = self._ticket_codes(self.reader, 'orderBy: ["-channel", "id"]')

        self._assert_unordered_by_hidden_field(ascending, descending, ANON)
        self.assertEqual(self._among(ascending, ['OPEN-A', 'OPEN-B']), ['OPEN-A', 'OPEN-B'])

    def test_superuser_orders_anonymized_fields(self):
        ascending = self._ticket_codes(self.admin, 'orderBy: ["channel", "id"]')
        self.assertEqual(self._among(ascending, ANON + ['OPEN-A']), ['ANON-2', 'OPEN-A', 'ANON-1'])

    def test_related_path_ordering_follows_the_visibility_of_its_field(self):
        ascending = self._ticket_codes(self.reader, 'orderBy: ["attendingStaff__username", "id"]')
        descending = self._ticket_codes(self.reader, 'orderBy: ["-attendingStaff__username", "id"]')

        self._assert_unordered_by_hidden_field(ascending, descending, ANON)

    def test_json_ext_key_ordering_follows_the_visibility_of_json_ext(self):
        ascending = self._ticket_codes(self.reader, 'orderBy: ["jsonExt__rank", "id"]')
        descending = self._ticket_codes(self.reader, 'orderBy: ["-jsonExt__rank", "id"]')

        self._assert_unordered_by_hidden_field(ascending, descending, ANON)
        self.assertEqual(self._among(ascending, ['OPEN-A', 'OPEN-B']), ['OPEN-A', 'OPEN-B'])
        self.assertEqual(self._among(descending, ['OPEN-A', 'OPEN-B']), ['OPEN-B', 'OPEN-A'])

    def test_ordering_on_a_visible_field_is_unchanged(self):
        self.assertEqual(
            self._ticket_codes(self.restricted_reader, 'orderBy: ["-category", "id"]'),
            self._by_id(PRV) + self._by_id(['OPEN-A', 'OPEN-B']) + self._by_id(ANON))

    def test_resolver_ordering_on_a_hidden_field_leaves_those_tickets_unordered_by_it(self):
        # ticketDetails orders by title, which PRV tickets hide from this reader.
        codes = self._ticket_codes(self.restricted_reader, field='ticketDetails')
        self.assertEqual(codes[:4], ['OPEN-A', 'OPEN-B', 'ANON-1', 'ANON-2'])
        self.assertEqual(codes[4:], self._by_id(PRV))

        self.assertEqual(self._ticket_codes(self.reader, field='ticketDetails')[0], 'PRV-2')

    def test_comment_filter_on_a_hidden_ticket_field_leaves_out_the_comments_of_tickets_hiding_it(self):
        self.assertEqual(self._comment_codes(self.restricted_reader, 'ticket_Priority_Icontains: "Z"'), [])
        self.assertEqual(self._comment_codes(self.reader, 'ticket_Priority_Icontains: "Z"'), ['PRV-1'])

    def test_comment_filter_on_a_ticket_field_shown_by_the_category_applies(self):
        self.assertEqual(
            self._comment_codes(self.restricted_reader, 'ticket_Description_Icontains: "needle"'), ['PRV-1'])

    def test_comment_filter_on_an_anonymized_ticket_field_leaves_out_those_comments(self):
        self.assertEqual(self._comment_codes(self.reader, 'ticket_Channel_Icontains: "c9"'), [])
        self.assertEqual(self._comment_codes(self.reader, 'ticket_Channel_Icontains: "c2"'), ['OPEN-A'])
        self.assertEqual(self._comment_codes(self.admin, 'ticket_Channel_Icontains: "c9"'), ['ANON-1'])

    def test_comment_filter_on_a_related_ticket_field_follows_its_visibility(self):
        self.assertEqual(
            self._comment_codes(self.reader, 'ticket_AttendingStaff_Username: "hfo_staff_z"'), [])

    def test_comment_ordering_on_a_hidden_ticket_field_leaves_those_comments_unordered_by_it(self):
        ascending = self._comment_codes(self.restricted_reader, 'orderBy: ["ticket__priority", "ticket__id"]')
        descending = self._comment_codes(self.restricted_reader, 'orderBy: ["-ticket__priority", "ticket__id"]')

        self._assert_unordered_by_hidden_field(ascending, descending, PRV)

    def test_ticket_comment_set_filter_on_a_hidden_ticket_field_leaves_out_its_comments(self):
        query = COMMENT_SET_QUERY % (self.ticket_ids['PRV-1'], 'ticket_Priority_Icontains: "Z"')

        def comments(user):
            edges = self._execute(user, query)['tickets']['edges']
            self.assertEqual(len(edges), 1)
            return [edge['node']['comment'] for edge in edges[0]['node']['commentSet']['edges']]

        self.assertEqual(comments(self.restricted_reader), [])
        self.assertEqual(comments(self.reader), ['comment on PRV-1'])

    def test_masked_ordering_ends_with_id(self):
        ordered = order_without_hidden_fields(Ticket.objects.order_by('-date_created'), self.restricted_reader)
        self.assertEqual(ordered.query.order_by[-1], 'id')
        self.assertEqual(len(ordered.query.order_by), 2)

        comments = order_without_hidden_fields(
            Comment.objects.order_by('ticket__priority'), self.restricted_reader, COMMENT_TICKET_PREFIX)
        self.assertEqual(comments.query.order_by[-1], 'id')

    def test_masked_ordering_already_ending_with_id_is_not_extended(self):
        ordered = order_without_hidden_fields(Ticket.objects.order_by('priority', 'id'), self.restricted_reader)
        self.assertEqual(ordered.query.order_by[-1], 'id')
        self.assertEqual(len(ordered.query.order_by), 2)

    def test_ordering_without_a_masked_term_is_left_as_is(self):
        queryset = Ticket.objects.order_by('-version')
        self.assertIs(order_without_hidden_fields(queryset, self.restricted_reader), queryset)
