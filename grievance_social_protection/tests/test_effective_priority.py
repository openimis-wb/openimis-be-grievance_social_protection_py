"""
The effective priority of a new ticket starts from its category's configured
priority ('Medium' for an unconfigured category) and is raised by any flag
with a higher priority.
"""
from django.test import TestCase

from core.test_helpers import create_test_interactive_user
from grievance_social_protection.access_control import GrievanceAccessControl
from grievance_social_protection.services import TicketService
from grievance_social_protection.tests.test_helpers import (
    restore_grievance_config, setup_grievance_config,
)

LOW_CATEGORY = 'effprio_low'
HIGH_CATEGORY = 'effprio_high'
PLAIN_CATEGORY = 'effprio_plain'


class EffectivePriorityTest(TestCase):
    def setUp(self):
        self._snapshot = setup_grievance_config({
            'grievance_types': [
                {'name': LOW_CATEGORY, 'priority': 'Low', 'children': ['child']},
                {'name': HIGH_CATEGORY, 'priority': 'High'},
                PLAIN_CATEGORY,
            ],
            'grievance_flags': [
                {'name': 'EFFPRIO_HIGH', 'priority': 'High'},
                {'name': 'EFFPRIO_LOW', 'priority': 'Low'},
            ],
        })
        self.addCleanup(restore_grievance_config, self._snapshot)

    def test_low_category_keeps_low(self):
        self.assertEqual(GrievanceAccessControl.get_effective_priority(LOW_CATEGORY), 'Low')

    def test_low_subcategory_keeps_inherited_low(self):
        self.assertEqual(GrievanceAccessControl.get_effective_priority(f'{LOW_CATEGORY} > child'), 'Low')

    def test_low_flag_keeps_low_category_low(self):
        self.assertEqual(GrievanceAccessControl.get_effective_priority(LOW_CATEGORY, 'EFFPRIO_LOW'), 'Low')

    def test_higher_flag_raises_low_category(self):
        self.assertEqual(GrievanceAccessControl.get_effective_priority(LOW_CATEGORY, 'EFFPRIO_HIGH'), 'High')

    def test_low_flag_does_not_lower_high_category(self):
        self.assertEqual(GrievanceAccessControl.get_effective_priority(HIGH_CATEGORY, 'EFFPRIO_LOW'), 'High')

    def test_category_without_priority_is_medium(self):
        self.assertEqual(GrievanceAccessControl.get_effective_priority(PLAIN_CATEGORY), 'Medium')

    def test_unknown_category_is_medium(self):
        self.assertEqual(GrievanceAccessControl.get_effective_priority('effprio_unknown'), 'Medium')

    def test_ticket_service_applies_low_category_priority(self):
        user = create_test_interactive_user(username='effprio_user')
        obj_data = {'category': LOW_CATEGORY}
        TicketService(user)._apply_category_defaults(obj_data)
        self.assertEqual(obj_data['priority'], 'Low')

    def test_ticket_service_keeps_sent_priority(self):
        user = create_test_interactive_user(username='effprio_user')
        obj_data = {'category': LOW_CATEGORY, 'priority': 'Critical'}
        TicketService(user)._apply_category_defaults(obj_data)
        self.assertEqual(obj_data['priority'], 'Critical')
