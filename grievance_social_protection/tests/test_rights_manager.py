"""
Tests for GrievanceRightsManager class.

This module provides comprehensive test coverage for:
- generate_automatic_rights(): Permission creation and management
- _get_next_available_id(): ID generation with suffix pattern
- _generate_permission_fields(): Codename and permission name generation
"""
from datetime import datetime

from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase

from core.models import Role, RoleRight
from grievance_social_protection.models import Ticket
from grievance_social_protection.rights import GrievanceRightsManager
from grievance_social_protection.apps import TicketConfig
import grievance_social_protection


class TestGetNextAvailableId(TestCase):
    """Tests for GrievanceRightsManager._get_next_available_id()"""

    def test_read_permission_suffix_zero(self):
        """Test that read permission gets suffix 0"""
        used_ids = set()
        next_id = GrievanceRightsManager._get_next_available_id('read', used_ids)

        self.assertEqual(next_id % 10, 0)
        self.assertEqual(next_id, GrievanceRightsManager.GRIEVANCE_RIGHT_BASE + 0)

    def test_create_permission_suffix_one(self):
        """Test that create permission gets suffix 1"""
        used_ids = set()
        next_id = GrievanceRightsManager._get_next_available_id('create', used_ids)

        self.assertEqual(next_id % 10, 1)
        self.assertEqual(next_id, GrievanceRightsManager.GRIEVANCE_RIGHT_BASE + 1)

    def test_update_permission_suffix_two(self):
        """Test that update permission gets suffix 2"""
        used_ids = set()
        next_id = GrievanceRightsManager._get_next_available_id('update', used_ids)

        self.assertEqual(next_id % 10, 2)
        self.assertEqual(next_id, GrievanceRightsManager.GRIEVANCE_RIGHT_BASE + 2)

    def test_delete_permission_suffix_three(self):
        """Test that delete permission gets suffix 3"""
        used_ids = set()
        next_id = GrievanceRightsManager._get_next_available_id('delete', used_ids)

        self.assertEqual(next_id % 10, 3)
        self.assertEqual(next_id, GrievanceRightsManager.GRIEVANCE_RIGHT_BASE + 3)

    def test_restricted_read_permission_suffix_four(self):
        """Test that restricted_read permission gets suffix 4"""
        used_ids = set()
        next_id = GrievanceRightsManager._get_next_available_id('restricted_read', used_ids)

        self.assertEqual(next_id % 10, 4)
        self.assertEqual(next_id, GrievanceRightsManager.GRIEVANCE_RIGHT_BASE + 4)

    def test_skips_used_ids_maintains_suffix(self):
        """Test that when base ID is used, it increments by 10 to maintain suffix"""
        # Mark the base read ID as used
        base_read_id = GrievanceRightsManager.GRIEVANCE_RIGHT_BASE + 0  # 127100
        used_ids = {base_read_id}

        next_id = GrievanceRightsManager._get_next_available_id('read', used_ids)

        # Should be 127110 (base + 10), maintaining suffix 0
        self.assertEqual(next_id, 127110)
        self.assertEqual(next_id % 10, 0)

    def test_skips_multiple_used_ids(self):
        """Test that multiple used IDs are properly skipped"""
        # Mark first three read slots as used
        used_ids = {127100, 127110, 127120}

        next_id = GrievanceRightsManager._get_next_available_id('read', used_ids)

        self.assertEqual(next_id, 127130)
        self.assertEqual(next_id % 10, 0)

    def test_mixed_used_ids_different_suffixes(self):
        """Test with mixed IDs from different permission types"""
        # Mix of read (0), create (1), update (2) used IDs
        used_ids = {127100, 127101, 127102, 127110, 127111}

        # Read should skip to 127120
        read_id = GrievanceRightsManager._get_next_available_id('read', used_ids)
        self.assertEqual(read_id, 127120)

        # Create should skip to 127121
        create_id = GrievanceRightsManager._get_next_available_id('create', used_ids)
        self.assertEqual(create_id, 127121)

        # Delete (suffix 3) should get 127103 since it's not used
        delete_id = GrievanceRightsManager._get_next_available_id('delete', used_ids)
        self.assertEqual(delete_id, 127103)

    def test_raises_error_when_range_exhausted(self):
        """Test that ValueError is raised when no IDs available"""
        # Create set of all possible IDs with suffix 0 (read) in the range
        used_ids = set()
        current = GrievanceRightsManager.GRIEVANCE_RIGHT_BASE + 0
        while current <= GrievanceRightsManager.GRIEVANCE_RIGHT_MAX:
            used_ids.add(current)
            current += 10

        with self.assertRaises(ValueError) as context:
            GrievanceRightsManager._get_next_available_id('read', used_ids)

        self.assertIn('No available ID', str(context.exception))
        self.assertIn('read', str(context.exception))

    def test_boundary_id_at_max(self):
        """Test behavior when approaching the maximum ID"""
        # Use all IDs except the last valid one
        used_ids = set()
        current = GrievanceRightsManager.GRIEVANCE_RIGHT_BASE + 0
        # Leave room for exactly one more ID with suffix 0
        while current <= GrievanceRightsManager.GRIEVANCE_RIGHT_MAX - 10:
            used_ids.add(current)
            current += 10

        # Should still find the last available ID
        next_id = GrievanceRightsManager._get_next_available_id('read', used_ids)
        self.assertLessEqual(next_id, GrievanceRightsManager.GRIEVANCE_RIGHT_MAX)
        self.assertEqual(next_id % 10, 0)


class TestGeneratePermissionFields(TestCase):
    """Tests for GrievanceRightsManager._generate_permission_fields()"""

    def test_category_read_permission(self):
        """Test generating read permission fields for a category"""
        codename, permission_name, right_name = GrievanceRightsManager._generate_permission_fields(
            'read', 'complaint', is_flag=False
        )

        self.assertEqual(codename, 'read_complaint_grievance')
        self.assertEqual(permission_name, 'Can read complaint tickets')
        self.assertEqual(right_name, 'gql_query_complaint_category_tickets_perms')

    def test_category_create_permission(self):
        """Test generating create permission fields for a category"""
        codename, permission_name, right_name = GrievanceRightsManager._generate_permission_fields(
            'create', 'feedback', is_flag=False
        )

        self.assertEqual(codename, 'create_feedback_grievance')
        self.assertEqual(permission_name, 'Can create feedback tickets')
        self.assertEqual(right_name, 'gql_mutation_create_feedback_category_tickets_perms')

    def test_category_update_permission(self):
        """Test generating update permission fields for a category"""
        codename, permission_name, right_name = GrievanceRightsManager._generate_permission_fields(
            'update', 'appeal', is_flag=False
        )

        self.assertEqual(codename, 'update_appeal_grievance')
        self.assertEqual(permission_name, 'Can update appeal tickets')
        self.assertEqual(right_name, 'gql_mutation_update_appeal_category_tickets_perms')

    def test_category_delete_permission(self):
        """Test generating delete permission fields for a category"""
        codename, permission_name, right_name = GrievanceRightsManager._generate_permission_fields(
            'delete', 'test_category', is_flag=False
        )

        self.assertEqual(codename, 'delete_test_category_grievance')
        self.assertEqual(permission_name, 'Can delete test_category tickets')
        self.assertEqual(right_name, 'gql_mutation_delete_test_category_category_tickets_perms')

    def test_category_restricted_read_permission(self):
        """Test generating restricted_read permission fields for a category"""
        codename, permission_name, right_name = GrievanceRightsManager._generate_permission_fields(
            'restricted_read', 'sensitive', is_flag=False
        )

        self.assertEqual(codename, 'restricted_read_sensitive_grievance')
        self.assertEqual(permission_name, 'Can restricted read sensitive tickets')
        self.assertEqual(right_name, 'gql_query_restricted_sensitive_category_tickets_perms')

    def test_flag_read_permission(self):
        """Test generating read permission fields for a flag"""
        codename, permission_name, right_name = GrievanceRightsManager._generate_permission_fields(
            'read', 'urgent', is_flag=True
        )

        self.assertEqual(codename, 'read_flag_urgent_grievance')
        self.assertEqual(permission_name, 'Can read urgent flagged tickets')
        self.assertEqual(right_name, 'gql_query_urgent_flagged_tickets_perms')

    def test_flag_create_permission(self):
        """Test generating create permission fields for a flag"""
        codename, permission_name, right_name = GrievanceRightsManager._generate_permission_fields(
            'create', 'confidential', is_flag=True
        )

        self.assertEqual(codename, 'create_flag_confidential_grievance')
        self.assertEqual(permission_name, 'Can create confidential flagged tickets')
        self.assertEqual(right_name, 'gql_mutation_create_confidential_flagged_tickets_perms')

    def test_name_with_spaces_normalized(self):
        """Test that names with spaces are normalized properly"""
        codename, permission_name, right_name = GrievanceRightsManager._generate_permission_fields(
            'read', 'Category With Spaces', is_flag=False
        )

        # Codename should have underscores instead of spaces
        self.assertEqual(codename, 'read_category_with_spaces_grievance')
        # Permission name keeps the readable form
        self.assertEqual(permission_name, 'Can read Category With Spaces tickets')
        # Right name should have underscores
        self.assertEqual(right_name, 'gql_query_category_with_spaces_category_tickets_perms')

    def test_name_with_special_characters(self):
        """Test that special characters are removed from codename"""
        codename, permission_name, right_name = GrievanceRightsManager._generate_permission_fields(
            'read', 'Category-A (Type 1)', is_flag=False
        )

        # Special chars and parenthetical content removed
        self.assertEqual(codename, 'read_category_a_grievance')
        # Permission name has parenthetical content removed
        self.assertEqual(permission_name, 'Can read Category-A tickets')
        # Right name normalized
        self.assertEqual(right_name, 'gql_query_category_a_category_tickets_perms')

    def test_name_with_parentheses_stripped(self):
        """Test that parenthetical content is removed"""
        codename, permission_name, right_name = GrievanceRightsManager._generate_permission_fields(
            'read', 'VBG Complaint (Violence)', is_flag=False
        )

        # Parentheses content should be removed
        self.assertNotIn('violence', codename.lower())
        self.assertNotIn('(', permission_name)

    def test_hierarchical_category_name(self):
        """Test permission generation for hierarchical category names"""
        codename, permission_name, right_name = GrievanceRightsManager._generate_permission_fields(
            'read', 'parent > child', is_flag=False
        )

        # Separator should become underscore in codename, kept readable in label
        self.assertEqual(codename, 'read_parent_child_grievance')
        self.assertEqual(permission_name, 'Can read parent > child tickets')

    def test_codename_truncation_for_long_names(self):
        """Test that long names are truncated to fit Django's 100 char limit"""
        # Create a very long category name
        long_name = 'a' * 150

        codename, permission_name, right_name = GrievanceRightsManager._generate_permission_fields(
            'read', long_name, is_flag=False
        )

        # Codename should not exceed 100 characters
        self.assertLessEqual(len(codename), GrievanceRightsManager.CODENAME_MAX_LENGTH)
        # Should still end with _grievance
        self.assertTrue(codename.endswith('_grievance'))

    def test_permission_name_truncation_for_long_names(self):
        """Test that permission names are truncated to fit Django's 255 char limit"""
        # Create a very long category name
        long_name = 'a' * 300

        codename, permission_name, right_name = GrievanceRightsManager._generate_permission_fields(
            'read', long_name, is_flag=False
        )

        # Permission name should not exceed 255 characters
        self.assertLessEqual(len(permission_name), GrievanceRightsManager.PERMISSION_NAME_MAX_LENGTH)

    def test_underscore_in_permission_type_display(self):
        """Test that underscore in permission type is replaced with space in display"""
        codename, permission_name, right_name = GrievanceRightsManager._generate_permission_fields(
            'restricted_read', 'test', is_flag=False
        )

        # Permission name should have space instead of underscore
        self.assertIn('restricted read', permission_name)


class TestCleanNameAndGenerateSafeName(TestCase):
    """Tests for helper methods clean_name and generate_safe_name"""

    def test_clean_name_removes_parentheses(self):
        """Test clean_name removes parenthetical content"""
        result = GrievanceRightsManager.clean_name('Category (Type A)')
        self.assertEqual(result, 'Category')

    def test_clean_name_removes_multiple_parentheses(self):
        """Test clean_name removes multiple parenthetical sections"""
        result = GrievanceRightsManager.clean_name('Category (Type A) (Subtype B)')
        self.assertEqual(result, 'Category')

    def test_clean_name_with_no_parentheses(self):
        """Test clean_name with no parentheses returns unchanged"""
        result = GrievanceRightsManager.clean_name('Simple Category')
        self.assertEqual(result, 'Simple Category')

    def test_generate_safe_name_lowercase(self):
        """Test generate_safe_name converts to lowercase"""
        result = GrievanceRightsManager.generate_safe_name('UPPERCASE')
        self.assertEqual(result, 'uppercase')

    def test_generate_safe_name_replaces_spaces(self):
        """Test generate_safe_name replaces spaces with underscores"""
        result = GrievanceRightsManager.generate_safe_name('with spaces')
        self.assertEqual(result, 'with_spaces')

    def test_generate_safe_name_replaces_special_chars(self):
        """Test generate_safe_name replaces special characters"""
        result = GrievanceRightsManager.generate_safe_name('test-name.with!special@chars')
        self.assertEqual(result, 'test_name_with_special_chars')

    def test_generate_safe_name_removes_parentheses_first(self):
        """Test generate_safe_name removes parenthetical content before sanitizing"""
        result = GrievanceRightsManager.generate_safe_name('Name (With Info)')
        self.assertEqual(result, 'name')


class TestTruncateWithTemplate(TestCase):
    """Tests for truncate_with_template helper method"""

    def test_no_truncation_needed(self):
        """Test that short strings are not truncated"""
        result = GrievanceRightsManager.truncate_with_template(
            'prefix_{variable}_suffix',
            'short',
            100
        )
        self.assertEqual(result, 'prefix_short_suffix')

    def test_truncation_applied(self):
        """Test that long strings are truncated"""
        long_var = 'a' * 100
        result = GrievanceRightsManager.truncate_with_template(
            'prefix_{variable}_suffix',
            long_var,
            50
        )

        self.assertLessEqual(len(result), 50)
        self.assertTrue(result.startswith('prefix_'))
        self.assertTrue(result.endswith('_suffix'))

    def test_truncation_preserves_template_structure(self):
        """Test that truncation preserves the template prefix and suffix"""
        long_var = 'x' * 200
        template = 'start_{variable}_end'

        result = GrievanceRightsManager.truncate_with_template(template, long_var, 30)

        self.assertTrue(result.startswith('start_'))
        self.assertTrue(result.endswith('_end'))


class TestGenerateAutomaticRights(TestCase):
    """Tests for GrievanceRightsManager.generate_automatic_rights()"""

    def setUp(self):
        """Set up test fixtures"""
        self.ct = ContentType.objects.get_for_model(Ticket)

    def tearDown(self):
        """Clean up created test permissions"""
        Permission.objects.filter(
            content_type=self.ct,
            codename__endswith='_grievance',
            id__range=(GrievanceRightsManager.GRIEVANCE_RIGHT_BASE, GrievanceRightsManager.GRIEVANCE_RIGHT_MAX)
        ).delete()

    def _create_app_config(self):
        """Create a fresh app config for testing"""
        app_config = TicketConfig('grievance_social_protection', grievance_social_protection)
        app_config.processed_categories = {}
        app_config.processed_flags = {}
        app_config.generated_rights = {}
        return app_config

    def test_creates_permissions_for_categories(self):
        """Test that permissions are created for categories with permissions defined"""
        app_config = self._create_app_config()
        app_config.processed_categories = {
            'test_cat': {
                'permissions': ['read', 'create'],
                'generated_rights': {}
            }
        }

        GrievanceRightsManager.generate_automatic_rights(app_config)

        # Check permissions were created
        self.assertIn('read', app_config.processed_categories['test_cat']['generated_rights'])
        self.assertIn('create', app_config.processed_categories['test_cat']['generated_rights'])

        # Verify permission exists in database
        read_id = app_config.processed_categories['test_cat']['generated_rights']['read']
        perm = Permission.objects.get(id=read_id)
        self.assertEqual(perm.codename, 'read_test_cat_grievance')

    def test_creates_permissions_for_flags(self):
        """Test that permissions are created for flags with permissions defined"""
        app_config = self._create_app_config()
        app_config.processed_flags = {
            'urgent_flag': {
                'permissions': ['read'],
                'generated_rights': {}
            }
        }

        GrievanceRightsManager.generate_automatic_rights(app_config)

        # Check permission was created
        self.assertIn('read', app_config.processed_flags['urgent_flag']['generated_rights'])

        # Verify permission exists with flag naming
        read_id = app_config.processed_flags['urgent_flag']['generated_rights']['read']
        perm = Permission.objects.get(id=read_id)
        self.assertEqual(perm.codename, 'read_flag_urgent_flag_grievance')

    def test_skips_categories_without_permissions(self):
        """Test that categories without permissions don't get rights generated"""
        app_config = self._create_app_config()
        app_config.processed_categories = {
            'no_perms_cat': {
                'permissions': [],
                'generated_rights': {}
            }
        }

        GrievanceRightsManager.generate_automatic_rights(app_config)

        # generated_rights should be empty
        self.assertEqual(app_config.processed_categories['no_perms_cat']['generated_rights'], {})

    def test_reuses_existing_permissions(self):
        """Test that existing permissions are reused instead of creating duplicates"""
        # First, create a permission manually (use update_or_create for --keepdb safety)
        existing_perm, _ = Permission.objects.update_or_create(
            id=127100,
            defaults={
                'codename': 'read_existing_cat_grievance',
                'name': 'Can read existing_cat tickets',
                'content_type': self.ct
            }
        )

        app_config = self._create_app_config()
        app_config.processed_categories = {
            'existing_cat': {
                'permissions': ['read'],
                'generated_rights': {}
            }
        }

        GrievanceRightsManager.generate_automatic_rights(app_config)

        # Should reuse the existing permission ID
        self.assertEqual(
            app_config.processed_categories['existing_cat']['generated_rights']['read'],
            existing_perm.id
        )

        # Verify no duplicate was created
        count = Permission.objects.filter(codename='read_existing_cat_grievance').count()
        self.assertEqual(count, 1)

    def test_generates_all_permission_types(self):
        """Test that all permission types can be generated"""
        app_config = self._create_app_config()
        app_config.processed_categories = {
            'full_perms_cat': {
                'permissions': ['read', 'create', 'update', 'delete', 'restricted_read'],
                'generated_rights': {}
            }
        }

        GrievanceRightsManager.generate_automatic_rights(app_config)

        gen_rights = app_config.processed_categories['full_perms_cat']['generated_rights']
        self.assertEqual(len(gen_rights), 5)
        self.assertIn('read', gen_rights)
        self.assertIn('create', gen_rights)
        self.assertIn('update', gen_rights)
        self.assertIn('delete', gen_rights)
        self.assertIn('restricted_read', gen_rights)

    def test_sets_generated_rights_attribute(self):
        """Test that generated_rights is set on the app config"""
        app_config = self._create_app_config()
        app_config.processed_categories = {
            'attr_test': {
                'permissions': ['read'],
                'generated_rights': {}
            }
        }

        GrievanceRightsManager.generate_automatic_rights(app_config)

        # Check that app_config.generated_rights is populated
        self.assertTrue(hasattr(app_config, 'generated_rights'))
        self.assertGreater(len(app_config.generated_rights), 0)

    def test_permission_ids_follow_suffix_pattern(self):
        """Test that generated permission IDs follow the suffix pattern"""
        app_config = self._create_app_config()
        app_config.processed_categories = {
            'suffix_test': {
                'permissions': ['read', 'create', 'update', 'delete', 'restricted_read'],
                'generated_rights': {}
            }
        }

        GrievanceRightsManager.generate_automatic_rights(app_config)

        gen_rights = app_config.processed_categories['suffix_test']['generated_rights']

        # Verify suffixes
        self.assertEqual(gen_rights['read'] % 10, 0)
        self.assertEqual(gen_rights['create'] % 10, 1)
        self.assertEqual(gen_rights['update'] % 10, 2)
        self.assertEqual(gen_rights['delete'] % 10, 3)
        self.assertEqual(gen_rights['restricted_read'] % 10, 4)

    def test_multiple_categories_get_unique_ids(self):
        """Test that multiple categories get unique permission IDs"""
        app_config = self._create_app_config()
        app_config.processed_categories = {
            'cat_one': {
                'permissions': ['read'],
                'generated_rights': {}
            },
            'cat_two': {
                'permissions': ['read'],
                'generated_rights': {}
            }
        }

        GrievanceRightsManager.generate_automatic_rights(app_config)

        id_one = app_config.processed_categories['cat_one']['generated_rights']['read']
        id_two = app_config.processed_categories['cat_two']['generated_rights']['read']

        self.assertNotEqual(id_one, id_two)

    def test_sets_rights_as_app_config_attribute(self):
        """Test that generated rights are set as attributes on app_config"""
        app_config = self._create_app_config()
        app_config.processed_categories = {
            'cfg_test': {
                'permissions': ['read'],
                'generated_rights': {}
            }
        }

        # Track the key that will be generated
        expected_key = 'gql_query_cfg_test_category_tickets_perms'

        GrievanceRightsManager.generate_automatic_rights(app_config)

        # Verify the right was set as attribute on app_config
        self.assertTrue(hasattr(app_config, expected_key))

    def test_handles_mixed_categories_and_flags(self):
        """Test processing both categories and flags together"""
        app_config = self._create_app_config()
        app_config.processed_categories = {
            'mixed_cat': {
                'permissions': ['read', 'create'],
                'generated_rights': {}
            }
        }
        app_config.processed_flags = {
            'mixed_flag': {
                'permissions': ['read'],
                'generated_rights': {}
            }
        }

        GrievanceRightsManager.generate_automatic_rights(app_config)

        # Both should have generated rights
        self.assertIn('read', app_config.processed_categories['mixed_cat']['generated_rights'])
        self.assertIn('create', app_config.processed_categories['mixed_cat']['generated_rights'])
        self.assertIn('read', app_config.processed_flags['mixed_flag']['generated_rights'])

    def test_permissions_stored_in_database(self):
        """Test that created permissions are actually stored in the database"""
        app_config = self._create_app_config()
        app_config.processed_categories = {
            'db_test': {
                'permissions': ['read'],
                'generated_rights': {}
            }
        }

        GrievanceRightsManager.generate_automatic_rights(app_config)

        # Query the database directly
        perms = Permission.objects.filter(
            content_type=self.ct,
            codename='read_db_test_grievance'
        )
        self.assertEqual(perms.count(), 1)
        self.assertEqual(perms.first().name, 'Can read db_test tickets')

    def test_permissions_within_reserved_range(self):
        """Test that all generated permissions fall within the reserved ID range"""
        app_config = self._create_app_config()
        app_config.processed_categories = {
            f'range_test_{i}': {
                'permissions': ['read', 'create'],
                'generated_rights': {}
            }
            for i in range(5)
        }

        GrievanceRightsManager.generate_automatic_rights(app_config)

        # Verify all generated IDs are within range
        for cat_name, cat_info in app_config.processed_categories.items():
            for perm_type, perm_id in cat_info['generated_rights'].items():
                self.assertGreaterEqual(perm_id, GrievanceRightsManager.GRIEVANCE_RIGHT_BASE)
                self.assertLessEqual(perm_id, GrievanceRightsManager.GRIEVANCE_RIGHT_MAX)


class TestProcessPermissions(TestCase):
    """Tests for GrievanceRightsManager._process_permissions()"""

    def setUp(self):
        """Set up test fixtures"""
        self.ct = ContentType.objects.get_for_model(Ticket)
        # Pre-populate used_ids from DB for --keepdb safety
        self.db_used_ids = set(Permission.objects.filter(
            id__range=(GrievanceRightsManager.GRIEVANCE_RIGHT_BASE, GrievanceRightsManager.GRIEVANCE_RIGHT_MAX)
        ).values_list('id', flat=True))

    def tearDown(self):
        """Clean up created test permissions"""
        Permission.objects.filter(
            content_type=self.ct,
            codename__endswith='_grievance',
            id__range=(GrievanceRightsManager.GRIEVANCE_RIGHT_BASE, GrievanceRightsManager.GRIEVANCE_RIGHT_MAX)
        ).delete()

    def test_process_category_permissions(self):
        """Test processing permissions for a category"""
        item_info = {
            'permissions': ['read', 'create'],
            'generated_rights': {}
        }
        existing_by_codename = {}
        used_ids = self.db_used_ids.copy()
        all_rights = {}

        GrievanceRightsManager._process_permissions(
            'test_item', item_info, False,
            existing_by_codename, used_ids, self.ct, all_rights
        )

        # Verify generated_rights populated
        self.assertIn('read', item_info['generated_rights'])
        self.assertIn('create', item_info['generated_rights'])

        # Verify all_rights populated
        self.assertIn('gql_query_test_item_category_tickets_perms', all_rights)
        self.assertIn('gql_mutation_create_test_item_category_tickets_perms', all_rights)

    def test_process_flag_permissions(self):
        """Test processing permissions for a flag"""
        item_info = {
            'permissions': ['read'],
            'generated_rights': {}
        }
        existing_by_codename = {}
        used_ids = self.db_used_ids.copy()
        all_rights = {}

        GrievanceRightsManager._process_permissions(
            'test_flag', item_info, True,  # is_flag=True
            existing_by_codename, used_ids, self.ct, all_rights
        )

        # Verify flag naming in all_rights
        self.assertIn('gql_query_test_flag_flagged_tickets_perms', all_rights)

    def test_skips_empty_permissions(self):
        """Test that empty permissions list results in no processing"""
        item_info = {
            'permissions': [],
            'generated_rights': {}
        }
        existing_by_codename = {}
        used_ids = self.db_used_ids.copy()
        all_rights = {}

        GrievanceRightsManager._process_permissions(
            'empty_test', item_info, False,
            existing_by_codename, used_ids, self.ct, all_rights
        )

        # generated_rights should be empty since permissions is empty
        self.assertEqual(item_info['generated_rights'], {})
        self.assertEqual(len(all_rights), 0)

    def test_reuses_existing_permission(self):
        """Test that existing permissions are reused"""
        # Create existing permission (use update_or_create for --keepdb safety)
        existing_perm, _ = Permission.objects.update_or_create(
            id=127150,
            defaults={
                'codename': 'read_reuse_test_grievance',
                'name': 'Can read reuse_test tickets',
                'content_type': self.ct
            }
        )

        item_info = {
            'permissions': ['read'],
            'generated_rights': {}
        }
        existing_by_codename = {'read_reuse_test_grievance': existing_perm}
        used_ids = self.db_used_ids | {127150}
        all_rights = {}

        GrievanceRightsManager._process_permissions(
            'reuse_test', item_info, False,
            existing_by_codename, used_ids, self.ct, all_rights
        )

        # Should use the existing permission ID
        self.assertEqual(item_info['generated_rights']['read'], 127150)

    def test_updates_used_ids(self):
        """Test that used_ids is updated when creating new permissions"""
        item_info = {
            'permissions': ['read'],
            'generated_rights': {}
        }
        existing_by_codename = {}
        used_ids = self.db_used_ids.copy()
        initial_count = len(used_ids)
        all_rights = {}

        GrievanceRightsManager._process_permissions(
            'used_ids_test', item_info, False,
            existing_by_codename, used_ids, self.ct, all_rights
        )

        # used_ids should now contain more IDs than before
        self.assertGreater(len(used_ids), initial_count)
        new_id = item_info['generated_rights']['read']
        self.assertIn(new_id, used_ids)


class TestGeneratedIdsSkipGrantedIds(TestCase):
    """Generated ids skip ids that live role grants still reference."""

    CATEGORY = 'granted_id_cat'

    def setUp(self):
        self.ct = ContentType.objects.get_for_model(Ticket)
        self.role, _ = Role.objects.get_or_create(
            name='GrantedIdTestRole',
            defaults={'is_system': 0, 'is_blocked': False, 'audit_user_id': -1},
        )
        reserved_range = (GrievanceRightsManager.GRIEVANCE_RIGHT_BASE, GrievanceRightsManager.GRIEVANCE_RIGHT_MAX)
        self.used_ids = set(Permission.objects.filter(
            id__range=reserved_range
        ).values_list('id', flat=True)) | set(RoleRight.filter_queryset().filter(
            right_id__range=reserved_range
        ).values_list('right_id', flat=True))
        self.granted_id = GrievanceRightsManager._get_next_available_id('read', self.used_ids)

    def _generate(self):
        app_config = TicketConfig('grievance_social_protection', grievance_social_protection)
        app_config.processed_categories = {self.CATEGORY: {'permissions': ['read'], 'generated_rights': {}}}
        app_config.processed_flags = {}
        app_config.generated_rights = {}
        GrievanceRightsManager.generate_automatic_rights(app_config)
        return app_config.processed_categories[self.CATEGORY]['generated_rights']['read']

    def test_skips_id_held_by_live_role_grant(self):
        RoleRight.objects.create(role=self.role, right_id=self.granted_id, audit_user_id=-1)
        expected_id = GrievanceRightsManager._get_next_available_id(
            'read', self.used_ids | {self.granted_id})

        new_id = self._generate()

        self.assertNotEqual(new_id, self.granted_id)
        self.assertEqual(new_id, expected_id)
        self.assertEqual(new_id % 10, self.granted_id % 10)

    def test_logs_orphan_grant(self):
        RoleRight.objects.create(role=self.role, right_id=self.granted_id, audit_user_id=-1)

        with self.assertLogs('grievance_social_protection.rights', level='WARNING') as logs:
            self._generate()

        self.assertTrue(any(
            str(self.granted_id) in line and str(self.role.id) in line for line in logs.output
        ))

    def test_revoked_grant_does_not_block_id(self):
        RoleRight.objects.create(
            role=self.role, right_id=self.granted_id, audit_user_id=-1, validity_to=datetime.now())

        self.assertEqual(self._generate(), self.granted_id)
