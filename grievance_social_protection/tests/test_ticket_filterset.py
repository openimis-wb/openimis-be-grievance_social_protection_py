"""
Tests for TicketFilterSet — verifies that filter-level field restrictions
prevent information inference attacks by users with restricted_read access.
"""
from unittest.mock import MagicMock

from django.test import TestCase

from core.test_helpers import create_test_interactive_user

from grievance_social_protection.gql_queries import TicketFilterSet
from grievance_social_protection.models import Ticket
from grievance_social_protection.tests.test_helpers import (
    setup_grievance_config, restore_grievance_config,
    assign_rights_to_user, get_rights,
)


class TicketFilterSetFilterQuerysetTest(TestCase):
    """Test that filter_queryset leaves out tickets whose filtered field is hidden."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user_restricted = create_test_interactive_user(
            username='fq_restricted', roles=[1]
        )
        cls.user_full = create_test_interactive_user(
            username='fq_full', roles=[1]
        )

    def setUp(self):
        super().setUp()
        self._snapshot = self._setup_config()
        self._create_tickets()

    def tearDown(self):
        Ticket.objects.filter(code__startswith='FSTEST').delete()
        restore_grievance_config(self._snapshot)
        super().tearDown()

    def _setup_config(self):
        cfg = {
            'grievance_types': [
                {
                    'name': 'filter_cat',
                    'permissions': ['restricted_read', 'read'],
                    'visible_fields': ['id', 'status', 'category', 'priority'],
                },
            ],
            'grievance_flags': [],
        }
        snapshot = setup_grievance_config(cfg)

        cat_rights = get_rights('processed_categories', 'filter_cat')

        if cat_rights.get('restricted_read'):
            assign_rights_to_user(
                self.user_restricted,
                [127000, cat_rights['restricted_read']],
                role_name='FQRestrictedRole',
            )

        if cat_rights.get('read'):
            assign_rights_to_user(
                self.user_full,
                [127000, cat_rights['read']],
                role_name='FQFullRole',
            )

        return snapshot

    def _create_tickets(self):
        self.ticket_match = Ticket(
            code='FSTEST001',
            title='Sensitive Title',
            description='secret information',
            category='filter_cat',
            status='OPEN',
            priority='High',
        )
        self.ticket_match.save(user=self.user_full)

        self.ticket_other = Ticket(
            code='FSTEST002',
            title='Other Title',
            description='other info',
            category='filter_cat',
            status='OPEN',
            priority='Low',
        )
        self.ticket_other.save(user=self.user_full)

    def _build_filterset(self, user, data):
        """Build a TicketFilterSet with a mock request carrying the given user."""
        request = MagicMock()
        request.user = user
        qs = Ticket.objects.filter(code__startswith='FSTEST')
        fs = TicketFilterSet(data=data, queryset=qs, request=request)
        # Trigger form validation so cleaned_data is populated
        fs.form.is_valid()
        return fs

    def test_full_user_can_filter_on_description(self):
        """Full access user can filter on description and get matching results."""
        fs = self._build_filterset(
            self.user_full, {'description__icontains': 'secret'}
        )
        result = fs.filter_queryset(fs.queryset)
        codes = list(result.values_list('code', flat=True))
        self.assertIn('FSTEST001', codes)
        self.assertNotIn('FSTEST002', codes)

    def test_restricted_user_description_filter_leaves_out_hidden_tickets(self):
        """description is hidden on both tickets: neither is returned, matching or not."""
        for value in ('secret', 'other'):
            fs = self._build_filterset(
                self.user_restricted, {'description__icontains': value}
            )
            result = fs.filter_queryset(fs.queryset)
            self.assertEqual(list(result.values_list('code', flat=True)), [])

    def test_restricted_user_can_filter_on_status(self):
        """Restricted user can still filter on visible fields like status."""
        fs = self._build_filterset(
            self.user_restricted, {'status': 'OPEN'}
        )
        result = fs.filter_queryset(fs.queryset)
        codes = list(result.values_list('code', flat=True))
        self.assertIn('FSTEST001', codes)
        self.assertIn('FSTEST002', codes)

    def test_restricted_user_can_filter_on_priority(self):
        """Restricted user can filter on priority (in visible_fields)."""
        fs = self._build_filterset(
            self.user_restricted, {'priority': 'High'}
        )
        result = fs.filter_queryset(fs.queryset)
        codes = list(result.values_list('code', flat=True))
        self.assertIn('FSTEST001', codes)
        self.assertNotIn('FSTEST002', codes)

    def test_restricted_user_title_filter_leaves_out_hidden_tickets(self):
        """title is not in visible_fields: no ticket is returned, matching or not."""
        for value in ('Sensitive', 'Other'):
            fs = self._build_filterset(
                self.user_restricted, {'title__icontains': value}
            )
            result = fs.filter_queryset(fs.queryset)
            self.assertEqual(list(result.values_list('code', flat=True)), [])

    def test_full_user_title_filter_works(self):
        """Full access user CAN filter on title."""
        fs = self._build_filterset(
            self.user_full, {'title__icontains': 'Sensitive'}
        )
        result = fs.filter_queryset(fs.queryset)
        codes = list(result.values_list('code', flat=True))
        self.assertIn('FSTEST001', codes)
        self.assertNotIn('FSTEST002', codes)
