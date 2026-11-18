"""
A superuser keeps access to a category or flag name the loaded configuration
does not know: the ticket check gives it every access type and level 'full',
the SQL rules list the ticket and hide none of its fields, and it can update
the ticket. Any other account stays denied.
"""
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import TestCase

from core.test_helpers import create_test_interactive_user, create_test_role
from grievance_social_protection.access_control import GrievanceAccessControl
from grievance_social_protection.apps import TicketConfig
from grievance_social_protection.models import Ticket
from grievance_social_protection.services import TicketService
from grievance_social_protection.tests.test_helpers import (
    assign_rights_to_user, restore_grievance_config, setup_grievance_config,
)

OPEN_CATEGORY = 'uns_open'
OPEN_FLAG = 'UNS_OPEN'
UNKNOWN_FLAG = 'UNS_UNKNOWN'
UNKNOWN_CATEGORY = 'uns_unknown'


class UnknownNameSuperuserTest(TestCase):

    def setUp(self):
        super().setUp()
        snapshot = setup_grievance_config({
            'grievance_types': [OPEN_CATEGORY],
            'grievance_flags': [OPEN_FLAG],
        })
        self.addCleanup(restore_grievance_config, snapshot)
        self.admin = create_test_interactive_user(username='uns_admin')
        empty_role = create_test_role([], name='NoRights_uns_user')
        self.user = create_test_interactive_user(username='uns_user', roles=[empty_role.id])
        assign_rights_to_user(self.user, [int(r) for r in TicketConfig.gql_query_tickets_perms
                                          + TicketConfig.gql_mutation_update_tickets_perms], 'Role_uns_user')
        cache.clear()
        self.assertTrue(self.admin.is_superuser)
        self.assertFalse(self.user.is_superuser)
        self.flagged = self._ticket('UNS-FLAG', OPEN_CATEGORY, f'{OPEN_FLAG} {UNKNOWN_FLAG}')
        self.categorised = self._ticket('UNS-CAT', UNKNOWN_CATEGORY)

    def _ticket(self, code, category, flags=None):
        ticket = Ticket(code=code, title='uns title', category=category, flags=flags, status='OPEN')
        ticket.save(user=self.admin)
        return ticket

    def _ids(self, queryset):
        return set(queryset.filter(code__startswith='UNS-').values_list('id', flat=True))

    def test_a_superuser_reads_a_ticket_with_an_unknown_name(self):
        self.assertTrue(GrievanceAccessControl.check_flag_access(self.admin, UNKNOWN_FLAG))
        self.assertTrue(GrievanceAccessControl.check_category_access(self.admin, UNKNOWN_CATEGORY))
        for ticket in (self.flagged, self.categorised):
            self.assertEqual(GrievanceAccessControl.get_user_access_level(self.admin, ticket.category, ticket.flags),
                             GrievanceAccessControl.ACCESS_FULL)
        all_tickets = {self.flagged.id, self.categorised.id}
        self.assertEqual(self._ids(GrievanceAccessControl.filter_ticket_queryset(Ticket.objects.all(), self.admin)),
                         all_tickets)
        self.assertEqual(self._ids(GrievanceAccessControl.filter_comment_ticket_queryset(
            Ticket.objects.all(), self.admin)), all_tickets)
        self.assertIsNone(GrievanceAccessControl.no_access_q(self.admin))
        self.assertIsNone(GrievanceAccessControl.hidden_field_q(self.admin, 'title'))

    def test_a_superuser_updates_a_ticket_with_an_unknown_name(self):
        for ticket in (self.flagged, self.categorised):
            result = TicketService(self.admin).update({'id': ticket.id, 'title': 'uns updated'})
            self.assertTrue(result['success'], result)
            ticket.refresh_from_db()
            self.assertEqual(ticket.title, 'uns updated')

    def test_another_account_stays_denied(self):
        for ticket in (self.flagged, self.categorised):
            self.assertEqual(GrievanceAccessControl.get_user_access_level(self.user, ticket.category, ticket.flags),
                             GrievanceAccessControl.ACCESS_NONE)
            with self.assertRaises(ValidationError):
                TicketService(self.user).update({'id': ticket.id, 'title': 'uns updated'})
            ticket.refresh_from_db()
            self.assertEqual(ticket.title, 'uns title')
        self.assertEqual(self._ids(GrievanceAccessControl.filter_ticket_queryset(Ticket.objects.all(), self.user)),
                         set())
        self.assertEqual(self._ids(Ticket.objects.filter(GrievanceAccessControl.no_access_q(self.user))),
                         {self.flagged.id, self.categorised.id})
