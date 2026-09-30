from django.test import TestCase

from core.test_helpers import create_test_interactive_user
from grievance_social_protection.access_control import GrievanceAccessControl
from grievance_social_protection.models import Ticket


class TicketQuerysetFiltersTest(TestCase):
    """Filters registered by other modules restrict filter_ticket_queryset."""

    @classmethod
    def setUpTestData(cls):
        cls.user = create_test_interactive_user(username='tqf_admin')
        cls.kept = Ticket(title='kept', json_ext={'location': {'province_code': 'P1'}})
        cls.kept.save(username=cls.user.username)
        cls.dropped = Ticket(title='dropped', json_ext={'location': {'province_code': 'P2'}})
        cls.dropped.save(username=cls.user.username)

    def setUp(self):
        registered = list(GrievanceAccessControl.ticket_queryset_filters)
        self.addCleanup(setattr, GrievanceAccessControl, 'ticket_queryset_filters', registered)

    def _titles(self):
        tickets = Ticket.objects.filter(id__in=[self.kept.id, self.dropped.id])
        return sorted(GrievanceAccessControl.filter_ticket_queryset(tickets, self.user)
                      .values_list('title', flat=True))

    def test_a_registered_filter_applies_once(self):
        def province_p1(queryset, user):
            return queryset.filter(json_ext__location__province_code='P1')

        self.assertEqual(self._titles(), ['dropped', 'kept'])
        GrievanceAccessControl.register_ticket_queryset_filter(province_p1)
        GrievanceAccessControl.register_ticket_queryset_filter(province_p1)
        self.assertEqual(GrievanceAccessControl.ticket_queryset_filters.count(province_p1), 1)
        self.assertEqual(self._titles(), ['kept'])

    def test_the_filter_receives_the_user(self):
        seen = []

        def remember(queryset, user):
            seen.append(user)
            return queryset

        GrievanceAccessControl.register_ticket_queryset_filter(remember)
        self._titles()
        self.assertEqual(seen, [self.user])
