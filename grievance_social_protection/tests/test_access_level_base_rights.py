"""
The access level of a restricted category comes from its generated rights: the
module's base ticket rights (e.g. 127003 delete) must not raise a restricted
reader to 'full'.
"""
from django.core.cache import cache
from django.test import TestCase

from core.test_helpers import create_test_interactive_user, create_test_role
from grievance_social_protection.access_control import GrievanceAccessControl
from grievance_social_protection.apps import TicketConfig
from grievance_social_protection.tests.test_helpers import (
    assign_rights_to_user, get_rights, restore_grievance_config, setup_grievance_config,
)

CATEGORY = 'vbg_base_rights'


class BaseRightsDoNotGrantFullAccessTest(TestCase):
    def setUp(self):
        self._snapshot = setup_grievance_config({
            'grievance_types': [
                {
                    'name': CATEGORY,
                    'permissions': ['restricted_read', 'read', 'create', 'update'],
                    'visible_fields': ['category', 'status'],
                },
            ],
        })
        self.addCleanup(restore_grievance_config, self._snapshot)
        self.rights = get_rights('processed_categories', CATEGORY)
        base_rights = (
            TicketConfig.gql_query_tickets_perms
            + TicketConfig.gql_mutation_create_tickets_perms
            + TicketConfig.gql_mutation_update_tickets_perms
            + TicketConfig.gql_mutation_delete_tickets_perms
        )
        self.base_rights = [int(r) for r in base_rights]

    def _user(self, username, rights):
        empty_role = create_test_role([], name=f'NoRights_{username}')
        user = create_test_interactive_user(username=username, roles=[empty_role.id])
        assign_rights_to_user(user, rights, f'Role_{username}')
        cache.clear()
        return user

    def test_category_has_no_generated_delete_right(self):
        self.assertNotIn('delete', self.rights)

    def test_restricted_reader_with_base_rights_stays_restricted(self):
        user = self._user('base_rights_restricted', self.base_rights + [self.rights['restricted_read']])
        self.assertEqual(
            GrievanceAccessControl.get_user_access_level(user, CATEGORY),
            GrievanceAccessControl.ACCESS_RESTRICTED,
        )
        self.assertEqual(
            GrievanceAccessControl.get_visible_fields(user, CATEGORY), ['category', 'status'],
        )

    def test_generated_update_right_gives_full_access(self):
        user = self._user('base_rights_updater', [self.rights['update']])
        self.assertEqual(
            GrievanceAccessControl.get_user_access_level(user, CATEGORY),
            GrievanceAccessControl.ACCESS_FULL,
        )

    def test_generated_read_right_gives_read_access(self):
        user = self._user('base_rights_reader', self.base_rights + [self.rights['read']])
        self.assertEqual(
            GrievanceAccessControl.get_user_access_level(user, CATEGORY),
            GrievanceAccessControl.ACCESS_READ,
        )

    def test_base_delete_still_authorises_the_delete_check(self):
        user = self._user('base_rights_deleter', self.base_rights)
        self.assertTrue(
            GrievanceAccessControl.check_category_access(user, CATEGORY, GrievanceAccessControl.PERM_DELETE)
        )
