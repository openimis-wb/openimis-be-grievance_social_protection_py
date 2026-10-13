"""
A ticket whose sub-category is stored 'parent|child', as earlier versions of
the module named sub-categories, follows the rights, defaults and visible
fields of 'parent > child': the ticket lists keep it for the users who may
read that category, and a user without its rights does not get full access.
"""
from django.core.cache import cache
from django.test import TestCase

from core.test_helpers import create_test_interactive_user, create_test_role
from grievance_social_protection.access_control import GrievanceAccessControl
from grievance_social_protection.apps import TicketConfig
from grievance_social_protection.models import Ticket
from grievance_social_protection.tests.test_helpers import (
    assign_rights_to_user, get_rights, restore_grievance_config, setup_grievance_config,
)

PREFIX = 'PACL-'
ARROW = 'pipe_secret > pipe_sub'
PIPE = 'pipe_secret|pipe_sub'
CATEGORIES = {'OPEN': 'pipe_open', 'ARROW': ARROW, 'PIPE': PIPE, 'LOOKALIKE': 'pipe_secretx|pipe_sub'}
ALL_PERMISSIONS = ['restricted_read', 'read', 'create', 'update', 'delete']


class PipeSubCategoryAccessTest(TestCase):
    def setUp(self):
        self._snapshot = setup_grievance_config({
            'grievance_types': [
                'pipe_open',
                {'name': 'pipe_secret', 'priority': 'High', 'permissions': ALL_PERMISSIONS,
                 'children': ['pipe_sub']},
            ],
            'grievance_flags': [],
        })
        self.addCleanup(restore_grievance_config, self._snapshot)

        self.admin = create_test_interactive_user(username='pipeacl_admin')
        base_rights = [int(r) for r in TicketConfig.gql_query_tickets_perms]
        sub_rights = get_rights('processed_categories', ARROW)
        self.base_reader = self._user('pipeacl_base', base_rights)
        self.restricted_reader = self._user(
            'pipeacl_restricted', base_rights + [int(sub_rights['restricted_read'])])
        self.full_reader = self._user('pipeacl_full', base_rights + [int(sub_rights['read'])])
        cache.clear()

        self.tickets = {}
        for suffix, category in CATEGORIES.items():
            ticket = Ticket(code=PREFIX + suffix, title=f'title {suffix}', description=f'needle {suffix}',
                            category=category, channel='sms', status='OPEN')
            ticket.save(user=self.admin)
            self.tickets[suffix] = ticket

    def _user(self, username, right_ids):
        empty_role = create_test_role([], name=f'NoRights_{username}')
        user = create_test_interactive_user(username=username, roles=[empty_role.id])
        assign_rights_to_user(user, right_ids, f'Role_{username}')
        return user

    def _codes(self, queryset):
        return sorted(t.code[len(PREFIX):] for t in queryset)

    def _listed(self, user):
        return self._codes(GrievanceAccessControl.filter_ticket_queryset(
            Ticket.objects.filter(code__startswith=PREFIX), user))

    def test_ticket_list_keeps_a_pipe_sub_category_for_its_readers(self):
        self.assertEqual(self._listed(self.full_reader), ['ARROW', 'OPEN', 'PIPE'])
        self.assertEqual(self._listed(self.restricted_reader), ['ARROW', 'OPEN', 'PIPE'])
        self.assertEqual(self._listed(self.admin), ['ARROW', 'OPEN', 'PIPE'])

    def test_ticket_list_leaves_out_a_pipe_sub_category_the_user_may_not_read(self):
        self.assertEqual(self._listed(self.base_reader), ['OPEN'])

    def test_access_level_of_a_pipe_sub_category_is_the_configured_one(self):
        for user, level in ((self.base_reader, 'none'), (self.restricted_reader, 'restricted'),
                            (self.full_reader, 'read')):
            self.assertEqual(GrievanceAccessControl.get_user_access_level(user, PIPE), level, user.username)
            self.assertEqual(GrievanceAccessControl.get_user_access_level(user, ARROW), level, user.username)
        self.assertFalse(GrievanceAccessControl.check_category_access(self.base_reader, PIPE, 'read'))
        self.assertTrue(GrievanceAccessControl.check_category_access(self.full_reader, PIPE, 'read'))

    def test_visible_fields_of_a_pipe_sub_category_are_the_configured_ones(self):
        self.assertEqual(GrievanceAccessControl.get_visible_fields(self.restricted_reader, PIPE),
                         list(GrievanceAccessControl.BASIC_VISIBLE_FIELDS))
        self.assertEqual(GrievanceAccessControl.get_visible_fields(self.base_reader, PIPE), [])

    def test_defaults_of_a_pipe_sub_category_are_the_configured_ones(self):
        self.assertEqual(GrievanceAccessControl.get_category_defaults(PIPE)['priority'], 'High')
        self.assertEqual(GrievanceAccessControl.get_effective_priority(PIPE), 'High')

    def test_queryset_conditions_treat_pipe_and_arrow_alike(self):
        tickets = Ticket.objects.filter(code__startswith=PREFIX)
        self.assertEqual(
            self._codes(tickets.filter(GrievanceAccessControl.no_access_q(self.base_reader))), ['ARROW', 'PIPE'])
        self.assertEqual(
            self._codes(tickets.filter(GrievanceAccessControl.hidden_field_q(self.restricted_reader, 'description'))),
            ['ARROW', 'PIPE'])
        self.assertEqual(
            self._codes(tickets.filter(GrievanceAccessControl.hidden_field_q(self.full_reader, 'description'))), [])
