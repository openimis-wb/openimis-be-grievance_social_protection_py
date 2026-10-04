"""
A ticket whose sub-category is stored 'parent|child', as earlier versions of
the module named sub-categories, takes the grievance_anonymized_fields
entries of its parent and of 'parent > child', as one stored
'parent > child' does. The ticket lists leave such a ticket out for a user
who is not a superuser (its category is not a configured name), but a
schema nesting TicketGQLType under another type resolves it with the
ticket resolvers.
"""
from types import SimpleNamespace
from unittest import mock

from django.core.cache import cache
from django.test import TestCase

from core.test_helpers import create_test_interactive_user, create_test_role
from grievance_social_protection.access_control import GrievanceAccessControl
from grievance_social_protection.apps import TicketConfig
from grievance_social_protection.gql_queries import TicketGQLType
from grievance_social_protection.models import Ticket
from grievance_social_protection.tests.test_helpers import (
    assign_rights_to_user, restore_grievance_config, setup_grievance_config,
)

PREFIX = 'PIPE-'
PARENT_CATEGORY = 'pipe_parent'
ANONYMIZED = {PARENT_CATEGORY: ['description'], 'pipe_parent > pipe_sub': ['channel']}
CATEGORIES = {'OPEN': 'pipe_open', 'ARROW': 'pipe_parent > pipe_sub', 'PIPE': 'pipe_parent|pipe_sub',
              'LOOKALIKE': 'pipe_parentx|pipe_sub'}


class PipeSubCategoryAnonymizedFieldsTest(TestCase):
    def setUp(self):
        self._snapshot = setup_grievance_config({
            'grievance_types': ['pipe_open', {'name': PARENT_CATEGORY, 'children': ['pipe_sub']}],
            'grievance_flags': [],
        })
        self.addCleanup(restore_grievance_config, self._snapshot)
        patcher = mock.patch.object(TicketConfig, 'grievance_anonymized_fields', ANONYMIZED)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.admin = create_test_interactive_user(username='pipe_admin')
        empty_role = create_test_role([], name='NoRights_pipe_reader')
        self.reader = create_test_interactive_user(username='pipe_reader', roles=[empty_role.id])
        assign_rights_to_user(self.reader, [int(r) for r in TicketConfig.gql_query_tickets_perms],
                              'Role_pipe_reader')
        cache.clear()

        self.tickets = {}
        for suffix, category in CATEGORIES.items():
            ticket = Ticket(code=PREFIX + suffix, title=f'title {suffix}', description=f'needle {suffix}',
                            category=category, channel='sms', status='OPEN')
            ticket.save(user=self.admin)
            self.tickets[suffix] = ticket

    def _resolved(self, user, code):
        info = SimpleNamespace(context=SimpleNamespace(user=user))
        ticket = self.tickets[code]
        return (TicketGQLType.resolve_description(ticket, info), TicketGQLType.resolve_channel(ticket, info),
                TicketGQLType.resolve_title(ticket, info))

    def test_anonymized_fields_of_a_pipe_sub_category(self):
        self.assertEqual(GrievanceAccessControl.anonymized_fields(self.reader, 'pipe_parent|pipe_sub'),
                         {'description', 'channel'})
        self.assertEqual(GrievanceAccessControl.anonymized_fields(self.reader, 'pipe_parentx|pipe_sub'), set())

    def test_the_ticket_resolvers_mask_the_fields_of_a_pipe_sub_category(self):
        for code in ('ARROW', 'PIPE'):
            self.assertEqual(self._resolved(self.reader, code), ('[Restricted]', '[Restricted]', f'title {code}'))
        for code in ('OPEN', 'LOOKALIKE'):
            self.assertEqual(self._resolved(self.reader, code), (f'needle {code}', 'sms', f'title {code}'))

    def test_superuser_reads_the_fields_of_a_pipe_sub_category(self):
        self.assertEqual(self._resolved(self.admin, 'PIPE'), ('needle PIPE', 'sms', 'title PIPE'))

    def test_hidden_field_condition_matches_a_pipe_sub_category(self):
        tickets = Ticket.objects.filter(code__startswith=PREFIX)
        for field in ('description', 'channel'):
            hiding = tickets.filter(GrievanceAccessControl.hidden_field_q(self.reader, field))
            self.assertEqual(sorted(t.code[len(PREFIX):] for t in hiding), ['ARROW', 'PIPE'], field)
