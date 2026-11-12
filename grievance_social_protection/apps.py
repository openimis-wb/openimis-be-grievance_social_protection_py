import copy
import logging
import sys
import os

from django.apps import AppConfig
from django.db import OperationalError, ProgrammingError
from django.core.exceptions import ValidationError

logger = logging.getLogger(__name__)

MODULE_NAME = "grievance_social_protection"

DEFAULT_STRING = 'Default'
# CRON timedelta: {days},{hours}
DEFAULT_TIME_RESOLUTION = '5,0'
DEFAULT_GRIEVANCE_TYPE = 'uncategorized'
VALID_PERMISSION_TYPES = frozenset({'restricted_read', 'read', 'create', 'update', 'delete'})
CATEGORY_SEPARATOR = ' > '
# Ticket fields TicketGQLType resolves from the reporter row, besides the model's own.
REPORTER_ATTRIBUTE_FIELDS = ('reporter_first_name', 'reporter_last_name', 'reporter_dob')

DEFAULT_CFG = {
    "default_validations_disabled": False,
    "default_grievance_type": DEFAULT_GRIEVANCE_TYPE,
    "gql_query_tickets_perms": ["127000"],
    "gql_query_comments_perms": ["127004"],
    "gql_mutation_create_tickets_perms": ["127001"],
    "gql_mutation_update_tickets_perms": ["127002"],
    "gql_mutation_delete_tickets_perms": ["127003"],
    "gql_mutation_create_comment_perms": ["127005"],
    "gql_mutation_resolve_grievance_perms": ["127006"],
    "tickets_attachments_root_path": None,

    "grievance_types": [DEFAULT_STRING, 'Category A', 'Category B'],
    "grievance_flags": [DEFAULT_STRING, 'Flag A', 'Flag B'],
    "grievance_channels": [DEFAULT_STRING, 'Channel A', 'Channel B'],
    "default_responses": {DEFAULT_STRING: DEFAULT_STRING},
    "grievance_anonymized_fields": {DEFAULT_STRING: []},
    # Fields shown at the restricted level on a ticket whose category sets no
    # visible_fields or that has no category; empty: the basic fields
    # (GrievanceAccessControl.BASIC_VISIBLE_FIELDS).
    "default_visible_fields": [],
    # CRON timedelta: {days},{hours}
    "resolution_times": DEFAULT_TIME_RESOLUTION,
    "default_resolution": {DEFAULT_STRING: DEFAULT_TIME_RESOLUTION, 'Category A': '4,0', 'Category B': '6,12'},

    "attending_staff_role_ids": [],
    "default_attending_staff_role_ids": {DEFAULT_STRING: [1, 2]},
}


class TicketConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = MODULE_NAME
    gql_query_tickets_perms = []
    gql_query_comments_perms = []
    gql_mutation_create_tickets_perms = []
    gql_mutation_update_tickets_perms = []
    gql_mutation_delete_tickets_perms = []
    gql_mutation_resolve_grievance_perms = []
    gql_mutation_create_comment_perms = []
    tickets_attachments_root_path = None

    grievance_types = []
    grievance_flags = []
    grievance_channels = []
    default_responses = {}
    grievance_anonymized_fields = {}
    default_visible_fields = []
    resolution_times = {}
    default_resolution = {}
    default_grievance_type = DEFAULT_GRIEVANCE_TYPE
    attending_staff_role_ids = []
    default_attending_staff_role_ids = {}

    # Processed structures for enhanced configurations
    processed_categories = {}
    processed_flags = {}
    unified_resolution_times = {}
    generated_rights = {}  # Store dynamically generated rights

    @classmethod
    def get_all_permissions(cls):
        """
        Get all permissions including dynamically generated ones.
        This method can be used by external systems to get the complete permission list.
        """
        all_perms = {}

        # Start with static permissions from DEFAULT_CFG
        for key, value in DEFAULT_CFG.items():
            if key.endswith('_perms'):
                all_perms[key] = value

        # Add dynamically generated permissions
        for right_name, right_id in cls.generated_rights.items():
            if right_name not in all_perms:
                all_perms[right_name] = []
            if right_id not in all_perms[right_name]:
                all_perms[right_name].append(right_id)

        return all_perms

    def ready(self):
        from core.models import ModuleConfiguration
        from core.module_config_registry import register_validator, register_reloader

        cfg = ModuleConfiguration.get_or_default(MODULE_NAME, DEFAULT_CFG)
        self.__process_config(cfg)
        self.__validate_config(cfg)
        self.__load_config(cfg)

        # Generate rights only when the database is available and ready.
        # Skip during migrations, when NO_DATABASE is set, or when
        # core tables (django_content_type, auth_permission) don't exist yet.
        if os.environ.get("NO_DATABASE") != "True" and 'migrate' not in sys.argv and 'makemigrations' not in sys.argv:
            try:
                from .rights import GrievanceRightsManager
                GrievanceRightsManager.generate_automatic_rights(TicketConfig)
            except (OperationalError, ProgrammingError):
                logger.info("Database tables not ready, skipping automatic rights generation.")

        register_validator(MODULE_NAME, self._validate_module_config)
        register_reloader(MODULE_NAME, self._reload_module_config)

    def __process_config(self, cfg):
        # Writes the processed structures into cfg only: validation runs it on
        # configurations that are never loaded, so it leaves TicketConfig as it
        # is and __load_config publishes the result.
        self.__process_unified_categories(cfg)
        self.__process_unified_flags(cfg)

    def __validate_config(self, cfg):
        self.__validate_category_default_flags(cfg)
        self.__validate_grievance_dict_fields(cfg, 'default_responses')
        self.__validate_grievance_dict_fields(cfg, 'grievance_anonymized_fields')
        self.__validate_grievance_dict_fields(cfg, 'default_resolution')
        self.__validate_default_visible_fields(cfg)
        self.__validate_grievance_default_resolution_time(cfg)

    def _merge_with_defaults(self, instance):
        return {**copy.deepcopy(DEFAULT_CFG), **instance._cfg}

    def _validate_module_config(self, instance):
        cfg = self._merge_with_defaults(instance)
        self.__process_config(cfg)
        self.__validate_config(cfg)

    def _reload_module_config(self, instance):
        from .rights import GrievanceRightsManager

        cfg = self._merge_with_defaults(instance)
        self.__process_config(cfg)
        self.__build_unified_resolution_times(cfg)
        previous = {
            field: getattr(TicketConfig, field)
            for field in (*cfg, 'generated_rights') if hasattr(TicketConfig, field)
        }
        self.__load_config(cfg)
        # The reloaded categories and flags carry empty generated_rights until
        # this runs; GrievanceAccessControl denies them in the meantime.
        try:
            GrievanceRightsManager.generate_automatic_rights(TicketConfig)
        except Exception:
            # The configuration in force stays the one whose rights exist; a
            # category that never had rights stays denied by the access control.
            for field, value in previous.items():
                setattr(TicketConfig, field, value)
            logger.exception(
                "Rights generation failed for the saved configuration; "
                "the previous configuration and rights remain in force.")

    @classmethod
    def __validate_grievance_dict_fields(cls, cfg, field_name):
        def get_grievance_type_options_msg(types):
            types_string = ", ".join(types)
            return logger.info(f'Available grievance types: {types_string}')

        dict_field = cfg.get(field_name, {})
        if not dict_field:
            return

        grievance_types = cfg.get('grievance_types', [])
        if not grievance_types:
            logger.warning('Please specify grievance_types if you want to setup %s.', field_name)

        if not isinstance(dict_field, dict):
            get_grievance_type_options_msg(grievance_types)
            return

        for field_key in dict_field.keys():
            if field_key not in grievance_types:
                logger.warning('%s in %s not in grievance_types', field_key, field_name)
                get_grievance_type_options_msg(grievance_types)

    @classmethod
    def __validate_default_visible_fields(cls, cfg):
        """default_visible_fields is a list of ticket field names: the model's
        fields and the reporter attributes TicketGQLType resolves."""
        fields = cfg.get('default_visible_fields')
        if not fields:
            return
        if not isinstance(fields, (list, tuple)) or not all(isinstance(field, str) for field in fields):
            raise ValidationError("'default_visible_fields' must be a list of ticket field names.")
        from .models import Ticket
        known = {field.name for field in Ticket._meta.get_fields() if field.concrete or not field.auto_created}
        known.update(REPORTER_ATTRIBUTE_FIELDS)
        unknown = sorted(set(fields) - known)
        if unknown:
            raise ValidationError(
                f"'default_visible_fields' lists unknown ticket fields {unknown}. "
                f"Available fields: {sorted(known)}"
            )

    @classmethod
    def __validate_grievance_default_resolution_time(cls, cfg):
        """
        Validate resolution times from both legacy default_resolution and new category-specific resolution_times.
        This method also builds a unified resolution times mapping that considers:
        1. Category-specific resolution_times (from processed_categories)
        2. Legacy default_resolution configuration
        3. Global resolution_times fallback
        """
        # First, validate legacy default_resolution
        dict_field = cfg.get("default_resolution", {})
        if dict_field:
            for key in dict_field:
                value = dict_field[key]
                if value in ['', None]:
                    raise ValidationError(
                        f"'{key}' in 'default_resolution' has no value. "
                        f"Expected format: 'days,hours' (e.g. '{DEFAULT_TIME_RESOLUTION}')."
                    )
                else:
                    cls.__validate_resolution_time_format(value, f"default_resolution[{key}]")

        # Then, validate resolution_times in processed categories
        processed_categories = cfg.get("processed_categories", {})
        for category_name, category_info in processed_categories.items():
            resolution_time = category_info.get('resolution_times')
            if resolution_time:
                cls.__validate_resolution_time_format(resolution_time, f"category '{category_name}'")

        cls.__build_unified_resolution_times(cfg)

    @classmethod
    def __build_unified_resolution_times(cls, cfg):
        """
        Store in cfg the resolution times by category: the legacy
        default_resolution entries, overridden by the category-specific
        resolution_times of the processed categories.
        """
        unified_resolution = dict(cfg.get("default_resolution") or {})
        for category_name, category_info in cfg.get("processed_categories", {}).items():
            resolution_time = category_info.get('resolution_times')
            if resolution_time:
                unified_resolution[category_name] = resolution_time

        cfg['unified_resolution_times'] = unified_resolution

    @classmethod
    def __validate_resolution_time_format(cls, value, context=""):
        if ',' not in value:
            raise ValidationError(
                f"Invalid resolution time format for {context}. "
                "Configuration should contain two integers representing days and hours, "
                "separated by a comma."
            )

        try:
            parts = value.split(',')
            days = int(parts[0])
            hours = int(parts[1])
        except (ValueError, IndexError):
            raise ValidationError(
                f"Invalid resolution time format for {context}. "
                "Expected format: 'days,hours' where both are integers."
            )

        if not (0 <= days < 99 and 0 <= hours < 24):
            raise ValidationError(
                f"Invalid resolution time values for {context}. "
                "Days must be 0-98 and hours must be 0-23."
            )

    @classmethod
    def __validate_category_default_flags(cls, cfg):
        """
        Validate that every default_flags entry in grievance_types
        references a flag defined in grievance_flags.
        """
        grievance_flags = cfg.get('grievance_flags', [])
        processed_categories = cfg.get('processed_categories', {})
        for category_name, category_info in processed_categories.items():
            for flag in category_info.get('default_flags', []):
                if flag not in grievance_flags:
                    raise ValidationError(
                        f"Category '{category_name}' references default flag '{flag}' "
                        f"which is not defined in grievance_flags. "
                        f"Available flags: {grievance_flags}"
                    )

    @classmethod
    def __process_unified_categories(cls, cfg):
        """
        Process grievance_types configuration supporting both string and dict formats
        """
        categories = cfg.get('grievance_types', [])
        processed_categories = {}
        flat_types = []

        # Ensure default_grievance_type is in the categories list
        default_grievance_type = cfg.get('default_grievance_type', DEFAULT_GRIEVANCE_TYPE)
        if default_grievance_type:
            # Check if default_grievance_type exists in categories
            def get_category_name(cat):
                if isinstance(cat, str):
                    return cat
                if isinstance(cat, dict):
                    return cat.get('name')
                return None

            type_exists = any(get_category_name(cat) == default_grievance_type for cat in categories)

            # If not found, add it with default permissions so uncategorized
            # tickets are subject to access control like all other categories.
            if not type_exists:
                categories.insert(0, {
                    'name': default_grievance_type,
                    'permissions': ['read', 'update']
                })
                logger.info(
                    f"Added default grievance type '{default_grievance_type}' with permissions ['read', 'update']")

        def process_category_item(item, parent_name=None, parent_info=None):
            """Process a single category item (string or dict)"""
            parent = parent_info or {}

            if isinstance(item, str):
                # Simple string format (backward compatible)
                full_name = f"{parent_name}{CATEGORY_SEPARATOR}{item}" if parent_name else item
                processed_categories[full_name] = {
                    'priority': parent.get('priority', 'Medium'),
                    'permissions': list(parent.get('permissions', [])),
                    'default_flags': list(parent.get('default_flags', [])),
                    'resolution_times': parent.get('resolution_times'),
                    'visible_fields': list(parent.get('visible_fields', [])),
                    'parent': parent_name,
                    'children': {},
                    'generated_rights': {}
                }
                flat_types.append(full_name)
                return full_name

            elif isinstance(item, dict):
                # Enhanced dict format with permissions
                cat_name = item.get('name')
                if not cat_name:
                    raise ValidationError("Each category dict in 'grievance_types' must have a 'name' field.")

                full_name = f"{parent_name}{CATEGORY_SEPARATOR}{cat_name}" if parent_name else cat_name

                # Process permissions — copy to avoid mutating parent's list
                permissions = list(item.get('permissions', parent.get('permissions', [])))
                invalid = set(permissions) - VALID_PERMISSION_TYPES
                if invalid:
                    raise ValidationError(
                        f"Category '{cat_name}' has invalid permission types: {invalid}. "
                        f"Allowed: {sorted(VALID_PERMISSION_TYPES)}"
                    )

                # Process visible_fields with inheritance constraints
                visible_fields = item.get('visible_fields', [])
                if visible_fields:
                    # If visible_fields is defined, ensure restricted_read and read permissions exist
                    required_permissions = ['restricted_read', 'read']
                    for perm in required_permissions:
                        if perm not in permissions:
                            permissions.append(perm)
                            logger.info(
                                f"Auto-adding '{perm}' permission to category '{cat_name}' due to visible_fields")

                    # Validate against parent's visible_fields
                    parent_visible_fields = parent.get('visible_fields')
                    if parent_visible_fields:
                        parent_visible = set(parent_visible_fields)
                        child_visible = set(visible_fields)

                        # Check if child tries to expose fields hidden by parent
                        invalid_fields = child_visible - parent_visible
                        if invalid_fields:
                            raise ValidationError(
                                f"Category '{cat_name}' cannot make fields {invalid_fields} visible — "
                                f"they are not in parent '{parent_name}' visible_fields."
                            )
                elif parent.get('visible_fields'):
                    # Inherit parent's visible_fields if not specified
                    visible_fields = parent['visible_fields'].copy()

                # Inherit from parent if not specified
                priority = item.get('priority', parent.get('priority', 'Medium'))
                default_flags = list(item.get('default_flags', parent.get('default_flags', [])))

                # Handle resolution_times - inherit from parent if not specified
                resolution_times = item.get('resolution_times')
                if not resolution_times:
                    resolution_times = parent.get('resolution_times')

                category_info = {
                    'priority': priority,
                    'permissions': permissions,
                    'default_flags': default_flags,
                    'resolution_times': resolution_times,
                    'visible_fields': visible_fields,
                    'parent': parent_name,
                    'children': {},
                    'generated_rights': {}  # Will be populated by rights generation
                }

                processed_categories[full_name] = category_info
                flat_types.append(full_name)

                # Process children recursively
                children = item.get('children', [])
                for child in children:
                    child_full_name = process_category_item(child, full_name, category_info)
                    if child_full_name:
                        child_short_name = child_full_name.split(CATEGORY_SEPARATOR)[-1]
                        category_info['children'][child_short_name] = child_full_name

                return full_name

            return None

        # Process all top-level categories
        for category in categories:
            process_category_item(category)

        # Store processed data
        cfg['processed_categories'] = processed_categories
        # Update flat list for backward compatibility
        cfg['grievance_types'] = flat_types

    @classmethod
    def __process_unified_flags(cls, cfg):
        """
        Process grievance_flags configuration supporting both string and dict formats
        """
        flags = cfg.get('grievance_flags', [])
        processed_flags = {}
        flat_flags = []

        for flag in flags:
            if isinstance(flag, str):
                # Simple string format (backward compatible)
                processed_flags[flag] = {
                    'priority': 'Medium',
                    'permissions': [],  # No restrictions - empty list
                    'generated_rights': {}
                }
                flat_flags.append(flag)

            elif isinstance(flag, dict):
                # Enhanced dict format with permissions
                flag_name = flag.get('name')
                if not flag_name:
                    raise ValidationError("Each flag dict in 'grievance_flags' must have a 'name' field.")

                # Process permissions
                permissions = flag.get('permissions', [])
                invalid = set(permissions) - VALID_PERMISSION_TYPES
                if invalid:
                    raise ValidationError(
                        f"Flag '{flag_name}' has invalid permission types: {invalid}. "
                        f"Allowed: {sorted(VALID_PERMISSION_TYPES)}"
                    )

                processed_flags[flag_name] = {
                    'priority': flag.get('priority', 'Medium'),
                    'permissions': permissions,
                    'generated_rights': {}
                }
                flat_flags.append(flag_name)

        # Store processed data
        cfg['processed_flags'] = processed_flags
        # Update flat list for backward compatibility
        cfg['grievance_flags'] = flat_flags

    @classmethod
    def __load_config(cls, cfg):
        """
        Load all config fields that match current AppConfig class fields, all custom fields have to be loaded separately
        """
        for field in cfg:
            if hasattr(TicketConfig, field):
                setattr(TicketConfig, field, cfg[field])
