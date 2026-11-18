"""
A category or flag name the loaded configuration does not know is denied:
_check_access refuses every access type on it and get_user_access_level
gives 'none', so a ticket created under a configuration this process has not
loaded is closed rather than open. A configuration without any category (or
any flag) leaves every name unrestricted.
"""
from django.core.cache import cache
from django.test import TestCase

from core.test_helpers import create_test_interactive_user, create_test_role
from grievance_social_protection.access_control import GrievanceAccessControl
from grievance_social_protection.apps import TicketConfig
from grievance_social_protection.tests.test_helpers import (
    assign_rights_to_user, restore_grievance_config, setup_grievance_config, save_grievance_config,
)

OPEN_CATEGORY = 'ucf_open'
OPEN_FLAG = 'UCF_OPEN'
UNKNOWN = 'ucf_unknown'


class UnknownCategoryFlagDeniedTest(TestCase):

    def setUp(self):
        super().setUp()
        snapshot = setup_grievance_config({
            'grievance_types': [OPEN_CATEGORY],
            'grievance_flags': [OPEN_FLAG],
        })
        self.addCleanup(restore_grievance_config, snapshot)
        role = create_test_role([], name='NoRights_ucf_user')
        self.user = create_test_interactive_user(username='ucf_user', roles=[role.id])
        assign_rights_to_user(self.user, [int(r) for r in TicketConfig.gql_query_tickets_perms
                                          + TicketConfig.gql_mutation_update_tickets_perms], 'Role_ucf_user')
        cache.clear()

    def test_an_unknown_category_is_denied(self):
        for access_type in (GrievanceAccessControl.PERM_READ, GrievanceAccessControl.PERM_UPDATE):
            with self.subTest(access_type=access_type):
                self.assertFalse(GrievanceAccessControl.check_category_access(self.user, UNKNOWN, access_type))
        self.assertEqual(GrievanceAccessControl.get_user_access_level(self.user, UNKNOWN),
                         GrievanceAccessControl.ACCESS_NONE)

    def test_an_unknown_flag_is_denied(self):
        self.assertFalse(GrievanceAccessControl.check_flag_access(self.user, UNKNOWN))
        self.assertEqual(GrievanceAccessControl.get_user_access_level(self.user, OPEN_CATEGORY, UNKNOWN),
                         GrievanceAccessControl.ACCESS_NONE)

    def test_configured_names_keep_their_access(self):
        self.assertTrue(GrievanceAccessControl.check_category_access(self.user, OPEN_CATEGORY))
        self.assertEqual(GrievanceAccessControl.get_user_access_level(self.user, OPEN_CATEGORY, OPEN_FLAG),
                         GrievanceAccessControl.ACCESS_FULL)

    def test_a_configuration_without_categories_or_flags_restricts_no_name(self):
        snapshot = save_grievance_config()
        self.addCleanup(restore_grievance_config, snapshot)
        TicketConfig.processed_categories = {}
        TicketConfig.processed_flags = {}
        TicketConfig.grievance_types = []
        TicketConfig.grievance_flags = []
        self.assertTrue(GrievanceAccessControl.check_category_access(self.user, UNKNOWN))
        self.assertEqual(GrievanceAccessControl.get_user_access_level(self.user, UNKNOWN, UNKNOWN),
                         GrievanceAccessControl.ACCESS_FULL)
