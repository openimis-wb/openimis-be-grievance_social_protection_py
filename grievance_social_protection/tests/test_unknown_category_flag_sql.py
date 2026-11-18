"""
The SQL rules put a ticket whose stored category or flags carry a name the
loaded configuration does not know at the 'none' level, as _check_access
does ticket by ticket: filter_ticket_queryset leaves it out, no_access_q and
hidden_field_q match it. So the comments query does not return its comments,
createComment refuses it and a ticket filter on one of its fields does not
select it. The rules read both flag encodings accepted by parse_flags; a
stored value starting with '[' that is not an array of strings counts as
carrying an unknown flag.
"""
from uuid import uuid4

from django.core.cache import cache
from django.test import TestCase
from graphene import Schema
from graphene.test import Client
from graphql_relay import from_global_id

from core.models import MutationLog
from core.models.openimis_graphql_test_case import BaseTestContext
from core.test_helpers import create_test_interactive_user, create_test_role
from grievance_social_protection.access_control import GrievanceAccessControl
from grievance_social_protection.apps import TicketConfig
from grievance_social_protection.models import Comment, Ticket
from grievance_social_protection.schema import Mutation, Query
from grievance_social_protection.tests.gql_payloads import gql_mutation_create_comment_anonymous_user
from grievance_social_protection.tests.test_helpers import (
    assign_rights_to_user, get_rights, restore_grievance_config, setup_grievance_config,
)

OPEN_CATEGORY = 'ucs_open'
OPEN_FLAG = 'UCS_OPEN'
OTHER_FLAG = 'UCS_OTHER'
ACCENTED_FLAG = 'UCS_Sécurité'
DOTTED_FLAG = 'UCS.DOT'
UNKNOWN_FLAG = 'UCS_UNKNOWN'
UNKNOWN_CATEGORY = 'ucs_unknown'
NEEDLE = 'ucs-needle'

COMMENTS_QUERY = '''
    query {
        comments(ticket_Category_Istartswith: "%s") {
            edges { node { comment } }
        }
    }
'''

TICKETS_QUERY = '''
    query {
        tickets(title_Icontains: "%s") {
            edges { node { id } }
        }
    }
'''

# Stored flags of a ticket of OPEN_CATEGORY, in the encodings parse_flags reads.
STORED_FLAGS = [
    None,
    '',
    '   ',
    OPEN_FLAG,
    f'{OPEN_FLAG}  {OTHER_FLAG}',
    f' {OTHER_FLAG} {OPEN_FLAG} ',
    f'{OPEN_FLAG} {UNKNOWN_FLAG}',
    f'{OPEN_FLAG}X',
    f'X{OPEN_FLAG}',
    'UCSxDOT',
    DOTTED_FLAG,
    ACCENTED_FLAG,
    f'["{OPEN_FLAG}"]',
    f'[ "{OPEN_FLAG}" , "{OTHER_FLAG}" ]',
    f'["{OPEN_FLAG}", " {OTHER_FLAG} "]',
    f'["{OPEN_FLAG}", ""]',
    f'["{OPEN_FLAG}", "{UNKNOWN_FLAG}"]',
    f'["{UNKNOWN_FLAG}", "{OPEN_FLAG}"]',
    f'["{OPEN_FLAG}X"]',
    '["UCS_S\\u00e9curit\\u00e9"]',
    f'["{ACCENTED_FLAG}"]',
    '[]',
    f'["{OPEN_FLAG}"',
    f'[{OPEN_FLAG}]',
    '[1]',
]


class UnknownCategoryFlagSqlTest(TestCase):

    def setUp(self):
        super().setUp()
        snapshot = setup_grievance_config({
            'grievance_types': [OPEN_CATEGORY],
            'grievance_flags': [OPEN_FLAG, OTHER_FLAG, ACCENTED_FLAG, DOTTED_FLAG],
        })
        self.addCleanup(restore_grievance_config, snapshot)
        self.admin = create_test_interactive_user(username='ucs_admin')
        bypass = [int(r) for r in TicketConfig.gql_mutation_delete_tickets_perms]
        # The default category's read right keeps the tickets without a category listed.
        default_read = get_rights('processed_categories', TicketConfig.default_grievance_type)['read']
        self.user = self._user('ucs_user', [int(r) for r in TicketConfig.gql_query_tickets_perms
                                            + TicketConfig.gql_query_comments_perms] + bypass + [default_read])
        self.open_ticket = self._ticket('UCS-OPEN', OPEN_CATEGORY, OPEN_FLAG)
        self.unknown_flag_ticket = self._ticket('UCS-UNK-FLAG', OPEN_CATEGORY, f'{OPEN_FLAG} {UNKNOWN_FLAG}')
        self.unknown_category_ticket = self._ticket('UCS-UNK-CAT', UNKNOWN_CATEGORY)

    def _user(self, username, rights):
        empty_role = create_test_role([], name=f'NoRights_{username}')
        user = create_test_interactive_user(username=username, roles=[empty_role.id])
        assign_rights_to_user(user, rights, f'Role_{username}')
        cache.clear()
        return user

    def _ticket(self, code, category, flags=None, title=NEEDLE):
        ticket = Ticket(code=code, title=title, category=category, flags=flags, status='OPEN')
        ticket.save(user=self.admin)
        Comment(ticket_id=ticket.id, comment=f'comment on {code}').save(user=self.admin)
        return ticket

    def _execute(self, query, mutation=False):
        schema = Schema(query=Query, mutation=Mutation) if mutation else Schema(query=Query)
        result = Client(schema).execute(query, context=BaseTestContext(self.user).get_request())
        self.assertNotIn('errors', result, result.get('errors'))
        return result['data']

    def _create_comment(self, ticket):
        mutation_id = str(uuid4())
        self._execute(gql_mutation_create_comment_anonymous_user % ('ucs comment', ticket.id, mutation_id),
                      mutation=True)
        return MutationLog.objects.get(client_mutation_id=mutation_id)

    def test_the_comments_query_leaves_out_a_ticket_with_an_unknown_flag(self):
        comments = {edge['node']['comment'] for edge in self._execute(COMMENTS_QUERY % 'ucs_')['comments']['edges']}
        self.assertEqual(comments, {'comment on UCS-OPEN'})

    def test_create_comment_refuses_a_ticket_with_an_unknown_flag(self):
        self.assertFalse(self._create_comment(self.open_ticket).error)
        self.assertTrue(self._create_comment(self.unknown_flag_ticket).error)
        self.assertEqual(Comment.objects.filter(ticket_id=self.unknown_flag_ticket.id).count(), 1)

    def test_a_title_filter_does_not_select_a_ticket_with_an_unknown_flag(self):
        ids = {from_global_id(edge['node']['id'])[1]
               for edge in self._execute(TICKETS_QUERY % NEEDLE)['tickets']['edges']}
        self.assertEqual(ids, {str(self.open_ticket.id)})

    def test_no_access_q_and_hidden_field_q_match_unknown_names(self):
        no_access = set(Ticket.objects.filter(GrievanceAccessControl.no_access_q(self.user)).values_list('id', flat=True))
        hidden_title = set(Ticket.objects.filter(
            GrievanceAccessControl.hidden_field_q(self.user, 'title')).values_list('id', flat=True))
        for ids in (no_access, hidden_title):
            self.assertIn(self.unknown_flag_ticket.id, ids)
            self.assertIn(self.unknown_category_ticket.id, ids)
            self.assertNotIn(self.open_ticket.id, ids)

    def test_the_sql_level_matches_the_ticket_level_on_every_stored_encoding(self):
        tickets = {}
        for index, flags in enumerate(STORED_FLAGS):
            tickets[flags] = self._ticket(f'UCS-E{index}', OPEN_CATEGORY, flags, title='encoding')
        for category in ('', None):
            ticket = self._ticket(f'UCS-B{category!r}', OPEN_CATEGORY, title='encoding')
            Ticket.objects.filter(id=ticket.id).update(category=category)
        for code, category in (('UCS-C-UNK', UNKNOWN_CATEGORY), ('UCS-C-OPEN', OPEN_CATEGORY)):
            self._ticket(code, category, title='encoding')

        no_access = set(Ticket.objects.filter(GrievanceAccessControl.no_access_q(self.user)).values_list('id', flat=True))
        listed = set(GrievanceAccessControl.filter_ticket_queryset(Ticket.objects.all(), self.user)
                     .values_list('id', flat=True))
        for ticket in Ticket.objects.filter(title='encoding'):
            with self.subTest(category=ticket.category, flags=ticket.flags):
                level = GrievanceAccessControl.get_user_access_level(self.user, ticket.category or None, ticket.flags)
                denied = level == GrievanceAccessControl.ACCESS_NONE
                self.assertEqual(ticket.id in no_access, denied)
                self.assertEqual(ticket.id in listed, not denied)
