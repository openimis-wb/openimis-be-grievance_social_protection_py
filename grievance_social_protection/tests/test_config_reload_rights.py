import copy
import json
from unittest import mock

from django.core.exceptions import ValidationError
from django.test import TestCase

from core.models import ModuleConfiguration
from grievance_social_protection import apps as grievance_apps
from grievance_social_protection.access_control import GrievanceAccessControl
from grievance_social_protection.apps import TicketConfig
from grievance_social_protection.models import Ticket
from grievance_social_protection.tests.test_helpers import (
    assign_rights_to_user,
    create_test_grievance_user,
    get_rights,
    setup_grievance_config,
)

MODULE = 'grievance_social_protection'

CONFIG = {
    'grievance_types': [
        'general',
        {'name': 'VBG', 'permissions': ['restricted_read', 'read', 'create', 'update']},
    ],
    'grievance_flags': [
        'public',
        {'name': 'confidential', 'permissions': ['read']},
    ],
}

# CONFIG plus a restricted category that has no permission rows yet.
CONFIG_WITH_NEW_CATEGORY = {
    'grievance_types': CONFIG['grievance_types'] + [
        {'name': 'Fraude', 'permissions': ['read']},
    ],
    'grievance_flags': CONFIG['grievance_flags'],
}

# Rejected by validation: the default flag is not among grievance_flags.
INVALID_CONFIG = {
    'grievance_types': [
        {'name': 'VBG', 'permissions': ['read'], 'default_flags': ['unknown']},
    ],
    'grievance_flags': ['public'],
}

_STATE_TYPES = (list, dict, str, int, bool, type(None))


def _ticket_config_state():
    return {
        name: copy.deepcopy(value)
        for name, value in vars(TicketConfig).items()
        if not name.startswith('_') and isinstance(value, _STATE_TYPES)
    }


class ReloadRightsTestBase(TestCase):
    """Configuration state handling shared by the reload tests."""

    @classmethod
    def setUpTestData(cls):
        cls.user_without_vbg = create_test_grievance_user(username='reload_rights_no_vbg')
        cls.user_with_vbg = create_test_grievance_user(username='reload_rights_vbg')

    def setUp(self):
        self._state = _ticket_config_state()
        self._default_cfg = copy.deepcopy(grievance_apps.DEFAULT_CFG)

    def tearDown(self):
        for name in set(_ticket_config_state()) - set(self._state):
            delattr(TicketConfig, name)
        for name, value in self._state.items():
            setattr(TicketConfig, name, value)
        grievance_apps.DEFAULT_CFG.clear()
        grievance_apps.DEFAULT_CFG.update(self._default_cfg)

    def _apply_with_rights(self, cfg):
        setup_grievance_config(cfg)
        assign_rights_to_user(
            self.user_with_vbg,
            list(get_rights('processed_categories', 'VBG').values()),
            'ReloadRightsVBG',
        )

    @staticmethod
    def _apply_without_rights(cfg):
        cfg = copy.deepcopy(cfg)
        TicketConfig._TicketConfig__process_unified_categories(cfg)
        TicketConfig._TicketConfig__process_unified_flags(cfg)
        TicketConfig._TicketConfig__load_config(cfg)

    def _save_configuration(self, cfg):
        configuration = ModuleConfiguration(
            module=MODULE, layer='be', version='1', config=json.dumps(cfg))
        with self.captureOnCommitCallbacks(execute=True):
            configuration.save()

    def _ticket(self, category, flags=''):
        ticket = Ticket(title='Reload rights', category=category, flags=flags, priority='High')
        ticket.save(user=self.user_with_vbg)
        return ticket

    @staticmethod
    def _visible_ids(user):
        return set(GrievanceAccessControl.filter_ticket_queryset(Ticket.objects.all(), user)
                   .values_list('id', flat=True))


class ConfigReloadRightsTest(ReloadRightsTestBase):
    """
    Saving the module configuration through ModuleConfiguration keeps the
    category and flag restrictions in force in the saving process.
    """

    def test_saved_configuration_keeps_vbg_closed_to_user_without_right(self):
        self._apply_with_rights(CONFIG)
        ticket = self._ticket('VBG')
        self.assertFalse(GrievanceAccessControl.check_category_access(self.user_without_vbg, 'VBG'))
        self.assertNotIn(ticket.id, self._visible_ids(self.user_without_vbg))

        self._save_configuration(CONFIG)

        self.assertFalse(GrievanceAccessControl.check_category_access(self.user_without_vbg, 'VBG'))
        self.assertFalse(GrievanceAccessControl.can_view_category(self.user_without_vbg, 'VBG'))
        self.assertNotIn(ticket.id, self._visible_ids(self.user_without_vbg))

    def test_saved_configuration_regenerates_category_rights(self):
        self._apply_with_rights(CONFIG)
        ticket = self._ticket('VBG')
        rights_before = get_rights('processed_categories', 'VBG')
        self.assertTrue(rights_before)

        self._save_configuration(CONFIG)

        self.assertEqual(get_rights('processed_categories', 'VBG'), rights_before)
        self.assertTrue(GrievanceAccessControl.check_category_access(self.user_with_vbg, 'VBG'))
        self.assertIn(ticket.id, self._visible_ids(self.user_with_vbg))

    def test_saved_configuration_generates_rights_of_a_new_category(self):
        self._apply_with_rights(CONFIG)
        self.assertNotIn('Fraude', TicketConfig.processed_categories)

        self._save_configuration(CONFIG_WITH_NEW_CATEGORY)

        self.assertIn('read', get_rights('processed_categories', 'Fraude'))
        self.assertFalse(GrievanceAccessControl.check_category_access(self.user_without_vbg, 'Fraude'))

    def test_validating_a_configuration_leaves_the_live_one_untouched(self):
        self._apply_with_rights(CONFIG)
        categories = TicketConfig.processed_categories
        flags = TicketConfig.processed_flags
        rights_before = get_rights('processed_categories', 'VBG')

        ModuleConfiguration(
            module=MODULE, layer='be', version='1', config=json.dumps(CONFIG_WITH_NEW_CATEGORY)).clean()
        with self.assertRaises(ValidationError):
            ModuleConfiguration(
                module=MODULE, layer='be', version='1', config=json.dumps(INVALID_CONFIG)).clean()

        self.assertIs(TicketConfig.processed_categories, categories)
        self.assertIs(TicketConfig.processed_flags, flags)
        self.assertEqual(get_rights('processed_categories', 'VBG'), rights_before)
        self.assertFalse(GrievanceAccessControl.check_category_access(self.user_without_vbg, 'VBG'))

    def test_category_with_permissions_and_no_generated_rights_is_closed(self):
        self._apply_without_rights(CONFIG)
        vbg_ticket = self._ticket('VBG')
        general_ticket = self._ticket('general')

        for access_type in ('restricted_read', 'read', 'create', 'update', 'delete'):
            self.assertFalse(
                GrievanceAccessControl.check_category_access(self.user_without_vbg, 'VBG', access_type),
                access_type)
        self.assertTrue(GrievanceAccessControl.has_category_restrictions('VBG'))
        self.assertEqual(
            GrievanceAccessControl.get_user_access_level(self.user_without_vbg, 'VBG'),
            GrievanceAccessControl.ACCESS_NONE)
        visible = self._visible_ids(self.user_without_vbg)
        self.assertNotIn(vbg_ticket.id, visible)
        self.assertIn(general_ticket.id, visible)
        self.assertTrue(GrievanceAccessControl.check_category_access(self.user_without_vbg, 'general'))

    def test_flag_with_permissions_and_no_generated_rights_is_closed(self):
        self._apply_without_rights(CONFIG)
        confidential_ticket = self._ticket('general', 'confidential')
        public_ticket = self._ticket('general', 'public')

        self.assertFalse(GrievanceAccessControl.check_flag_access(self.user_without_vbg, 'confidential'))
        self.assertTrue(GrievanceAccessControl.has_flag_restrictions('confidential'))
        self.assertEqual(
            GrievanceAccessControl.get_user_access_level(self.user_without_vbg, None, ['confidential']),
            GrievanceAccessControl.ACCESS_NONE)
        visible = self._visible_ids(self.user_without_vbg)
        self.assertNotIn(confidential_ticket.id, visible)
        self.assertIn(public_ticket.id, visible)


class MissingRightsLoggingTest(ReloadRightsTestBase):
    """A denial caused by missing generated rights is reported once per
    category or flag, however many checks hit it."""

    def setUp(self):
        super().setUp()
        getattr(GrievanceAccessControl, '_missing_rights_reported', set()).clear()

    def test_category_denial_is_logged_once_across_checks(self):
        self._apply_without_rights(CONFIG)

        with self.assertLogs('grievance_social_protection.access_control', level='ERROR') as logs:
            for access_type in ('read', 'create', 'update', 'delete', 'restricted_read'):
                self.assertFalse(
                    GrievanceAccessControl.check_category_access(self.user_without_vbg, 'VBG', access_type))
            GrievanceAccessControl.filter_ticket_queryset(Ticket.objects.all(), self.user_without_vbg)

        vbg_records = [record for record in logs.records if "'VBG'" in record.getMessage()]
        self.assertEqual(len(vbg_records), 1)

    def test_each_category_and_flag_is_logged_once(self):
        self._apply_without_rights(CONFIG)

        with self.assertLogs('grievance_social_protection.access_control', level='ERROR') as logs:
            for _ in range(3):
                GrievanceAccessControl.check_category_access(self.user_without_vbg, 'VBG')
                GrievanceAccessControl.check_flag_access(self.user_without_vbg, 'confidential')

        self.assertEqual(len(logs.records), 2)

    def test_denial_is_logged_again_after_rights_were_present(self):
        self._apply_without_rights(CONFIG)
        with self.assertLogs('grievance_social_protection.access_control', level='ERROR'):
            GrievanceAccessControl.check_category_access(self.user_without_vbg, 'VBG')

        self._apply_with_rights(CONFIG)
        GrievanceAccessControl.check_category_access(self.user_without_vbg, 'VBG')
        self._apply_without_rights(CONFIG)

        with self.assertLogs('grievance_social_protection.access_control', level='ERROR') as logs:
            GrievanceAccessControl.check_category_access(self.user_without_vbg, 'VBG')
        self.assertEqual(len(logs.records), 1)
