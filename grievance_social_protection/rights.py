"""
Rights Manager for grievance categories and flags.
"""
import logging
import re

from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from .apps import DEFAULT_CFG
from .models import Ticket

logger = logging.getLogger(__name__)


class GrievanceRightsManager:
    """Manages automatic rights generation for grievance categories and flags"""

    # Reserve 127100-127999 for dynamic grievance permissions
    GRIEVANCE_RIGHT_BASE = 127100
    GRIEVANCE_RIGHT_MAX = 127999
    
    # Permission fields limits
    CODENAME_MAX_LENGTH = 100
    PERMISSION_NAME_MAX_LENGTH = 255
    
    # Permission type mappings
    PERM_TYPE_MAPPING = {
        'restricted_read': 'gql_query_restricted',
        'read': 'gql_query',
        'create': 'gql_mutation_create',
        'update': 'gql_mutation_update',
        'delete': 'gql_mutation_delete'
    }
    
    # Permission ID suffix mapping
    PERM_TYPE_SUFFIX = {
        'read': 0,
        'create': 1,
        'update': 2,
        'delete': 3,
        'restricted_read': 4,
    }
    
    # Regular expressions
    PARENTHESES_CONTENT_RE = re.compile(r'\s*\([^)]*\)')
    NON_ALPHANUMERIC_RE = re.compile(r'[^a-z0-9_]')

    @classmethod
    def _process_permissions(cls, item_name, item_info, is_flag, existing_by_codename, used_ids, ct, all_rights):
        """
        Process permissions for a single category or flag.
        
        Args:
            item_name: Name of the category or flag
            item_info: Dictionary containing item configuration
            is_flag: Whether this is a flag (True) or category (False)
            existing_by_codename: Map of existing permissions by codename
            used_ids: Set of used permission IDs
            ct: ContentType for Ticket model
            all_rights: Dictionary to collect all rights
        """
        permissions = item_info.get('permissions', [])
        if not permissions:
            return
            
        item_info['generated_rights'] = {}
        
        for perm_type in permissions:
            # Generate codename with length limit
            codename, permission_name, right_name = cls._generate_permission_fields(
                perm_type, item_name, is_flag=is_flag
            )
            
            # Check if permission already exists
            if codename in existing_by_codename:
                perm = existing_by_codename[codename]
                item_info['generated_rights'][perm_type] = perm.id
                logger.info(f"Using existing permission: {codename} (ID: {perm.id})")
            else:
                new_id = cls._get_next_available_id(perm_type, used_ids)
                
                # Create new permission
                perm = Permission.objects.create(
                    id=new_id,
                    codename=codename,
                    name=permission_name,
                    content_type=ct
                )
                
                item_info['generated_rights'][perm_type] = perm.id
                used_ids.add(new_id)
                logger.info(f"Created new permission: {codename} (ID: {new_id})")
            
            all_rights[right_name] = item_info['generated_rights'][perm_type]

    @classmethod
    def generate_automatic_rights(cls, app_config):
        """
        Generate automatic rights using Django's auth_permission table.
        Permissions are stored persistently to maintain stable IDs across config changes.
        """
        
        # Get content type for Ticket model
        ct = ContentType.objects.get_for_model(Ticket)
        
        # Get all existing grievance permissions from the reserved range
        existing_perms = Permission.objects.filter(
            content_type=ct,
            codename__endswith='_grievance',
            id__range=(cls.GRIEVANCE_RIGHT_BASE, cls.GRIEVANCE_RIGHT_MAX)
        )
        
        # Build map of existing permissions by codename
        existing_by_codename = {perm.codename: perm for perm in existing_perms}
        
        # Get used IDs in our range
        used_ids = set(Permission.objects.filter(
            id__range=(cls.GRIEVANCE_RIGHT_BASE, cls.GRIEVANCE_RIGHT_MAX)
        ).values_list('id', flat=True))
        
        all_rights = {}  # Collect all rights
        
        # Process categories
        processed_categories = getattr(app_config, 'processed_categories', {})
        for category_name, category_info in processed_categories.items():
            cls._process_permissions(
                category_name, category_info, False, 
                existing_by_codename, used_ids, ct, all_rights
            )
        
        # Process flags
        processed_flags = getattr(app_config, 'processed_flags', {})
        for flag_name, flag_info in processed_flags.items():
            cls._process_permissions(
                flag_name, flag_info, True,
                existing_by_codename, used_ids, ct, all_rights
            )
        
        # Store generated rights for documentation
        app_config.generated_rights = all_rights
        
        # Log summary of permissions
        if all_rights:
            logger.info(f"Generated/verified {len(all_rights)} grievance permissions")
            
            # Group rights by permission name to match OpenIMIS convention
            grouped_rights = {}
            for right_name, right_id in all_rights.items():
                if right_name not in grouped_rights:
                    grouped_rights[right_name] = []
                grouped_rights[right_name].append(right_id)
            
            # Set as class attribute
            for right_name, right_ids in grouped_rights.items():
                DEFAULT_CFG[right_name] = right_ids
                setattr(app_config, right_name, right_ids)

    @classmethod
    def _get_next_available_id(cls, perm_type, used_ids):
        suffix = cls.PERM_TYPE_SUFFIX[perm_type]
        
        # Start from base and find next available with correct suffix
        candidate = cls.GRIEVANCE_RIGHT_BASE + suffix
        while candidate in used_ids and candidate <= cls.GRIEVANCE_RIGHT_MAX:
            candidate += 10  # Maintain suffix while incrementing
            
        if candidate > cls.GRIEVANCE_RIGHT_MAX:
            raise ValueError(f"No available ID for permission type {perm_type}")
            
        return candidate
    
    @staticmethod
    def truncate_with_template(template, variable, max_length):
        """Generate string from template, truncating variable if needed"""
        base = template.format(variable=variable)
        if len(base) <= max_length:
            return base
        
        # Calculate space available for variable
        prefix_suffix_len = len(template.format(variable=''))
        max_var_len = max_length - prefix_suffix_len
        truncated_var = variable[:max_var_len]
        return template.format(variable=truncated_var)
    
    @classmethod
    def clean_name(cls, name):
        """Remove parentheses and their contents from a name"""
        return cls.PARENTHESES_CONTENT_RE.sub('', name).strip()

    @classmethod
    def generate_safe_name(cls, name):
        """
        Generate a safe name suitable for use in codenames.
        
        Args:
            name: The original category or flag name
            
        Returns:
            str: The sanitized safe name
        """
        cleaned = cls.clean_name(name)
        # First convert to lowercase, then replace any non-alphanumeric character with underscore
        return cls.NON_ALPHANUMERIC_RE.sub('_', cleaned.lower())

    @classmethod
    def _generate_permission_fields(cls, perm_type, original_name, is_flag=False):
        """
        Generate permission fields with proper length limits for Django.
        
        Args:
            perm_type: The permission type (e.g., 'read', 'create')
            original_name: The original unsanitized name for the permission description
            is_flag: Whether this is for a flag (True) or category (False)
            
        Returns:
            tuple: (codename, permission_name, right_name) where:
                - codename is limited to 100 chars
                - permission_name is limited to 255 chars
                - right_name is the OpenIMIS permission name
        """
        perm_type_display = perm_type.replace('_', ' ')
        safe_name = cls.generate_safe_name(original_name)
        
        # Generate templates based on whether it's a flag or category
        ticket_suffix = "flagged tickets" if is_flag else "tickets"
        right_suffix = "flagged_tickets_perms" if is_flag else "category_tickets_perms"
        
        # Build codename template
        if is_flag:
            codename_template = f"{perm_type}_flag_{{variable}}_grievance"
        else:
            codename_template = f"{perm_type}_{{variable}}_grievance"
        
        # Build permission name template
        name_template = f"Can {perm_type_display} {{variable}} {ticket_suffix}"
        
        # Generate right name using mapping
        prefix = cls.PERM_TYPE_MAPPING[perm_type]
        right_name = f"{prefix}_{safe_name}_{right_suffix}" if prefix else ''
        
        # Generate final codename and permission name with truncation
        codename = cls.truncate_with_template(codename_template, safe_name, cls.CODENAME_MAX_LENGTH)
        permission_name = cls.truncate_with_template(name_template, cls.clean_name(original_name), cls.PERMISSION_NAME_MAX_LENGTH)
        
        return codename, permission_name, right_name
