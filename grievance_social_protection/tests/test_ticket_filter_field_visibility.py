"""
A ticket list filter applies to every ticket on which the user sees the
filtered field. Tickets on which that field is hidden (restricted access
through their category or one of their flags) are left out of the result,
whatever their value, so the filter reveals nothing about them.
"""
from unittest.mock import MagicMock

from django.core.cache import cache
from django.test import TestCase
from graphene import Schema
from graphene.test import Client

from core.models.openimis_graphql_test_case import BaseTestContext
from core.test_helpers import create_test_interactive_user, create_test_role
from grievance_social_protection.apps import TicketConfig
from grievance_social_protection.gql_queries import TicketFilterSet
from grievance_social_protection.models import Ticket
from grievance_social_protection.schema import Query
from grievance_social_protection.tests.test_helpers import (
    assign_rights_to_user, get_rights, restore_grievance_config, setup_grievance_config,
)

PREFIX = 'FVIS-'
OPEN_CATEGORY = 'fvis_open'
PRV_CATEGORY = 'fvis_prv'
HIDDEN_CATEGORY = 'fvis_hidden'
FLAG = 'FVIS_SENSITIVE'

TICKETS_QUERY = '''
    query {
        tickets(code_Istartswith: "%s"%s) {
            edges { node { code } }
        }
    }
'''


class TicketFilterFieldVisibilityTest(TestCase):
    def setUp(self):
        self._snapshot = setup_grievance_config({
            'grievance_types': [
                OPEN_CATEGORY,
                {
                    'name': PRV_CATEGORY,
                    'permissions': ['restricted_read', 'read'],
                    'visible_fields': ['category', 'description', 'status', 'attending_staff'],
                },
                {'name': HIDDEN_CATEGORY, 'permissions': ['restricted_read', 'read']},
            ],
            'grievance_flags': [
                {'name': FLAG, 'permissions': ['restricted_read', 'read']},
            ],
        })
        self.addCleanup(restore_grievance_config, self._snapshot)
        prv_rights = get_rights('processed_categories', PRV_CATEGORY)
        hidden_rights = get_rights('processed_categories', HIDDEN_CATEGORY)
        flag_rights = get_rights('processed_flags', FLAG)
        base_rights = [int(r) for r in TicketConfig.gql_query_tickets_perms]

        self.restricted_reader = self._user(
            'fvis_restricted', base_rights + [prv_rights['restricted_read'], flag_rights['restricted_read']])
        self.reader = self._user(
            'fvis_reader', base_rights + [prv_rights['read'], flag_rights['read'], hidden_rights['read']])

        self._ticket('OPEN-LOW', OPEN_CATEGORY, priority='Low', channel='sms')
        self._ticket('OPEN-MEDIUM', OPEN_CATEGORY, priority='Medium', channel='telephone')
        self._ticket('PRV-LOW', PRV_CATEGORY, priority='Low', channel='sms', description='needle',
                     attending_staff=self.reader)
        self._ticket('PRV-MEDIUM', PRV_CATEGORY, priority='Medium', channel='telephone')
        self._ticket('FLAG-LOW', OPEN_CATEGORY, priority='Low', channel='sms', flags=FLAG,
                     attending_staff=self.reader)
        self._ticket('HIDDEN-LOW', HIDDEN_CATEGORY, priority='Low', channel='sms')
        self.schema = Schema(query=Query)

    def _user(self, username, rights):
        empty_role = create_test_role([], name=f'NoRights_{username}')
        user = create_test_interactive_user(username=username, roles=[empty_role.id])
        assign_rights_to_user(user, rights, f'Role_{username}')
        cache.clear()
        return user

    def _ticket(self, suffix, category, flags=None, description='description', **fields):
        Ticket(code=PREFIX + suffix, title='title', description=description, category=category,
               flags=flags, status='OPEN', **fields).save(user=self.reader)

    def _codes(self, user, arguments=''):
        query = TICKETS_QUERY % (PREFIX, ', ' + arguments if arguments else '')
        result = Client(self.schema).execute(query, context=BaseTestContext(user).get_request())
        self.assertNotIn('errors', result, result.get('errors'))
        return sorted(edge['node']['code'][len(PREFIX):] for edge in result['data']['tickets']['edges'])

    def test_restricted_reader_lists_the_tickets_of_the_categories_it_may_see(self):
        self.assertEqual(
            self._codes(self.restricted_reader),
            ['FLAG-LOW', 'OPEN-LOW', 'OPEN-MEDIUM', 'PRV-LOW', 'PRV-MEDIUM'])

    def test_priority_filter_applies_where_priority_is_visible(self):
        # PRV tickets hide priority to this reader; FLAG-LOW shows the basic fields.
        self.assertEqual(self._codes(self.restricted_reader, 'priority_Icontains: "Low"'),
                         ['FLAG-LOW', 'OPEN-LOW'])
        self.assertEqual(self._codes(self.restricted_reader, 'priority_Icontains: "Medium"'),
                         ['OPEN-MEDIUM'])

    def test_channel_filter_applies_where_channel_is_visible(self):
        self.assertEqual(self._codes(self.restricted_reader, 'channel_Icontains: "sms"'), ['OPEN-LOW'])

    def test_filter_on_a_category_visible_field_applies_to_that_category(self):
        self.assertEqual(self._codes(self.restricted_reader, 'description_Icontains: "needle"'), ['PRV-LOW'])

    def test_status_filter_applies_to_every_ticket_showing_status(self):
        self.assertEqual(
            self._codes(self.restricted_reader, 'status: OPEN'),
            ['FLAG-LOW', 'OPEN-LOW', 'OPEN-MEDIUM', 'PRV-LOW', 'PRV-MEDIUM'])

    def test_combined_filters_each_apply(self):
        self.assertEqual(
            self._codes(self.restricted_reader, 'priority_Icontains: "Low", channel_Icontains: "sms"'),
            ['OPEN-LOW'])

    def test_related_field_filter_follows_the_visibility_of_its_field(self):
        # attending_staff shows on PRV tickets to this reader, not on FLAG-LOW.
        self.assertEqual(
            self._codes(self.restricted_reader, 'attendingStaff_Username: "fvis_reader"'), ['PRV-LOW'])
        self.assertEqual(
            self._codes(self.reader, 'attendingStaff_Username: "fvis_reader"'), ['FLAG-LOW', 'PRV-LOW'])

    def test_code_filter_applies_to_every_ticket(self):
        self.assertEqual(self._codes(self.restricted_reader, 'code: "%sPRV-LOW"' % PREFIX), ['PRV-LOW'])

    def test_reader_filters_every_ticket(self):
        self.assertEqual(self._codes(self.reader, 'priority_Icontains: "Low"'),
                         ['FLAG-LOW', 'HIDDEN-LOW', 'OPEN-LOW', 'PRV-LOW'])
        self.assertEqual(self._codes(self.reader, 'channel_Icontains: "sms"'),
                         ['FLAG-LOW', 'HIDDEN-LOW', 'OPEN-LOW', 'PRV-LOW'])

    def test_anonymous_filter_returns_no_ticket(self):
        request = MagicMock()
        request.user = MagicMock(is_anonymous=True)
        filterset = TicketFilterSet(data={'priority__icontains': 'Low'},
                                    queryset=Ticket.objects.filter(code__startswith=PREFIX), request=request)
        self.assertTrue(filterset.form.is_valid(), filterset.form.errors)
        self.assertEqual(list(filterset.qs), [])
