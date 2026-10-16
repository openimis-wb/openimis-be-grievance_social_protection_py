"""
default_visible_fields lists the fields shown at the restricted level on a
ticket whose category sets no visible_fields or that has no category. When it
lists none, the module's basic fields apply. The read and full levels show
every field whatever it lists.
"""
import json
from types import SimpleNamespace
from unittest import mock

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import TestCase

from core.models import ModuleConfiguration
from core.test_helpers import create_test_interactive_user, create_test_role
from grievance_social_protection.access_control import GrievanceAccessControl
from grievance_social_protection.apps import TicketConfig
from grievance_social_protection.gql_queries import RESTRICTED_VALUE, TicketGQLType
from grievance_social_protection.models import Ticket
from grievance_social_protection.tests.test_helpers import (
    assign_rights_to_user, get_rights, restore_grievance_config, save_grievance_config, setup_grievance_config,
)

PREFIX = 'DVF-'
ALL_PERMISSIONS = ['restricted_read', 'read', 'create', 'update', 'delete']
FLAG = 'DVF_FLAG'
LISTED = 'dvf_listed'
UNLISTED = 'dvf_unlisted'
LISTED_FIELDS = ['category', 'status', 'code']
BASIC = list(GrievanceAccessControl.BASIC_VISIBLE_FIELDS)
CONFIGURED = BASIC + ['code']


class DefaultVisibleFieldsTest(TestCase):
    def setUp(self):
        self._snapshot = setup_grievance_config({
            'grievance_types': [
                {'name': LISTED, 'permissions': ALL_PERMISSIONS, 'visible_fields': LISTED_FIELDS},
                {'name': UNLISTED, 'permissions': ALL_PERMISSIONS},
            ],
            'grievance_flags': [{'name': FLAG, 'permissions': ['restricted_read', 'read']}],
        })
        self.addCleanup(restore_grievance_config, self._snapshot)

        base_rights = [int(r) for r in TicketConfig.gql_query_tickets_perms]
        listed, unlisted = get_rights('processed_categories', LISTED), get_rights('processed_categories', UNLISTED)
        flag = get_rights('processed_flags', FLAG)
        self.restricted_reader = self._user('dvf_restricted', base_rights + [
            int(listed['restricted_read']), int(unlisted['restricted_read']), int(flag['restricted_read'])])
        self.full_reader = self._user('dvf_full', base_rights + [
            int(listed['read']), int(unlisted['read']), int(flag['read'])])
        cache.clear()

        admin = create_test_interactive_user(username='dvf_admin')
        self.tickets = {}
        for suffix, category in (('LISTED', LISTED), ('UNLISTED', UNLISTED), ('BLANK', None)):
            ticket = Ticket(code=PREFIX + suffix, title=f'title {suffix}', description=f'description {suffix}',
                            category=category or LISTED, flags=FLAG, channel='sms', status='OPEN')
            ticket.save(user=admin)
            if category is None:
                Ticket.objects.filter(id=ticket.id).update(category=None)
                ticket.refresh_from_db()
            self.tickets[suffix] = ticket

    def _user(self, username, right_ids):
        empty_role = create_test_role([], name=f'NoRights_{username}')
        user = create_test_interactive_user(username=username, roles=[empty_role.id])
        assign_rights_to_user(user, right_ids, f'Role_{username}')
        return user

    def _default(self, fields):
        patcher = mock.patch.object(TicketConfig, 'default_visible_fields', fields)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _fields(self, user, category):
        return GrievanceAccessControl.get_visible_fields(user, category, FLAG)

    def _hiding(self, user, field):
        hidden = GrievanceAccessControl.hidden_field_q(user, field)
        if hidden is None:
            return []
        tickets = Ticket.objects.filter(code__startswith=PREFIX).filter(hidden)
        return sorted(t.code[len(PREFIX):] for t in tickets)

    def test_without_the_setting_the_basic_fields_apply(self):
        self._default([])

        self.assertEqual(self._fields(self.restricted_reader, UNLISTED), BASIC)
        self.assertEqual(self._fields(self.restricted_reader, None), BASIC)
        self.assertEqual(self._fields(self.restricted_reader, LISTED), LISTED_FIELDS)
        self.assertEqual(self._hiding(self.restricted_reader, 'code'), ['BLANK', 'UNLISTED'])

    def test_the_configured_fields_apply_to_a_category_without_visible_fields(self):
        self._default(CONFIGURED)

        self.assertEqual(self._fields(self.restricted_reader, UNLISTED), CONFIGURED)
        self.assertEqual(self._fields(self.restricted_reader, LISTED), LISTED_FIELDS)

    def test_the_configured_fields_apply_to_a_ticket_without_category(self):
        self._default(CONFIGURED)

        self.assertEqual(self._fields(self.restricted_reader, None), CONFIGURED)
        self.assertEqual(self._fields(self.restricted_reader, ''), CONFIGURED)

    def test_ticket_filters_and_resolvers_follow_the_configured_fields(self):
        info = SimpleNamespace(context=SimpleNamespace(user=self.restricted_reader))
        blank = self.tickets['BLANK']
        self._default([])
        self.assertEqual(TicketGQLType.resolve_code(blank, info), RESTRICTED_VALUE)

        self._default(CONFIGURED)

        self.assertEqual(self._hiding(self.restricted_reader, 'code'), [])
        self.assertEqual(self._hiding(self.restricted_reader, 'description'), ['BLANK', 'LISTED', 'UNLISTED'])
        self.assertEqual(TicketGQLType.resolve_code(blank, info), PREFIX + 'BLANK')
        self.assertEqual(TicketGQLType.resolve_description(blank, info), RESTRICTED_VALUE)

    def test_read_and_full_levels_show_every_field(self):
        self._default(['status'])

        for category in (LISTED, UNLISTED, None):
            with self.subTest(category=category):
                self.assertIsNone(self._fields(self.full_reader, category))
        self.assertEqual(self._hiding(self.full_reader, 'description'), [])


class DefaultVisibleFieldsValidationTest(TestCase):
    def validate(self, cfg):
        return TicketConfig._TicketConfig__validate_default_visible_fields(cfg)

    def test_ticket_fields_are_accepted(self):
        self.validate({'default_visible_fields': [*BASIC, 'code', 'reporter', 'reporter_first_name',
                                                  'attending_staff', 'json_ext']})
        self.validate({'default_visible_fields': []})
        self.validate({})

    def test_an_unknown_field_is_refused(self):
        with self.assertRaisesMessage(ValidationError, "unknown ticket fields ['nope']"):
            self.validate({'default_visible_fields': ['code', 'nope']})

    def test_a_value_that_is_not_a_list_of_field_names_is_refused(self):
        for value in ('code', [1], {'code': True}):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                self.validate({'default_visible_fields': value})

    def test_saving_a_configuration_with_an_unknown_field_is_refused(self):
        snapshot = save_grievance_config()
        self.addCleanup(restore_grievance_config, snapshot)
        config = ModuleConfiguration(module='grievance_social_protection', layer='be', version='1',
                                     config=json.dumps({'default_visible_fields': ['code', 'nope']}))

        with self.assertRaises(ValidationError):
            config.clean()
