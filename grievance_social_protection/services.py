from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError, PermissionDenied
from django.db.models import Max
from django.db import transaction

from core.services import BaseService
from core.signals import register_service_signal
from core.services.utils import check_authentication as check_authentication, output_exception, \
    model_representation, output_result_success
from grievance_social_protection.models import Ticket, Comment
from grievance_social_protection.validations import (
    TicketValidation,
    CommentValidation,
    validate_resolution
)
from grievance_social_protection.access_control import GrievanceAccessControl
from grievance_social_protection.apps import TicketConfig


class TicketService(BaseService):
    OBJECT_TYPE = Ticket

    def __init__(self, user, validation_class=TicketValidation):
        super().__init__(user, validation_class)

    @register_service_signal('ticket_service.create')
    def create(self, obj_data):
        self._get_content_type(obj_data)
        self._generate_code(obj_data)
        # Assign default category before access control so permission
        # checks always have a category to validate against.
        if not obj_data.get('category'):
            obj_data['category'] = TicketConfig.default_grievance_type
        self._validate_access_control(obj_data, access_type=GrievanceAccessControl.PERM_CREATE)
        self._apply_category_defaults(obj_data)
        # Re-validate after defaults may have added restricted flags
        self._validate_access_control(obj_data, access_type=GrievanceAccessControl.PERM_CREATE)
        resolution_error = validate_resolution(obj_data)
        if resolution_error:
            raise ValidationError(resolution_error)
        return super().create(obj_data)

    @register_service_signal('ticket_service.update')
    def update(self, obj_data):
        self._get_content_type(obj_data)
        self._validate_existing_ticket_access(obj_data, access_type=GrievanceAccessControl.PERM_UPDATE)
        self._validate_access_control(obj_data, access_type=GrievanceAccessControl.PERM_UPDATE)
        self._apply_category_defaults(obj_data)
        # Re-validate after defaults may have added restricted flags
        self._validate_access_control(obj_data, access_type=GrievanceAccessControl.PERM_UPDATE)
        resolution_error = validate_resolution(obj_data)
        if resolution_error:
            raise ValidationError(resolution_error)
        return super().update(obj_data)

    @register_service_signal('ticket_service.delete')
    def delete(self, obj_data):
        self._validate_existing_ticket_access(obj_data, access_type=GrievanceAccessControl.PERM_DELETE)
        return super().delete(obj_data)

    def _check_access_or_raise(self, category, flags, access_type):
        """Validate ticket access and convert PermissionDenied to ValidationError"""
        try:
            GrievanceAccessControl.validate_ticket_access(
                self.user, category, flags, access_type
            )
        except PermissionDenied as e:
            raise ValidationError(str(e))

    def _validate_existing_ticket_access(self, obj_data, access_type):
        """Validate user has permission for the existing ticket's category and flags"""
        ticket_uuid = obj_data.get('uuid')
        ticket_id = obj_data.get('id')
        if not ticket_uuid and not ticket_id:
            return

        ticket = None
        base_qs = Ticket.filter_queryset()
        if ticket_uuid:
            ticket = base_qs.filter(uuid=ticket_uuid).first()
        if not ticket and ticket_id:
            if isinstance(ticket_id, int) or (isinstance(ticket_id, str) and ticket_id.isdigit()):
                ticket = base_qs.filter(id=ticket_id).first()
            else:
                ticket = base_qs.filter(uuid=ticket_id).first()
        if not ticket:
            raise ValidationError("Ticket does not exist.")

        self._check_access_or_raise(ticket.category, ticket.flags, access_type)

    @register_service_signal('ticket_service.reopen_ticket')
    @check_authentication
    def reopen_ticket(self, obj_data):
        try:
            with transaction.atomic():
                self.validation_class.validate_update(self.user, **obj_data)
                self._validate_existing_ticket_access(obj_data, access_type=GrievanceAccessControl.PERM_UPDATE)
                ticket_id = obj_data.get('id')
                ticket = Ticket.objects.filter(id=ticket_id).first()
                ticket.status = Ticket.TicketStatus.OPEN
                self._check_if_comment_resolution(ticket_id)
                ticket.save(user=self.user)
                return {
                    "success": True,
                    "message": "Ok",
                    "detail": "reopen_ticket",
                }
        except Exception as exc:
            return output_exception(model_name=self.OBJECT_TYPE.__name__, method="reopen_ticket", exception=exc)

    @transaction.atomic
    def _check_if_comment_resolution(self, ticket_id):
        comment_queryset = Comment.objects.filter(ticket_id=ticket_id, is_resolution=True)
        if comment_queryset.exists():
            comment = comment_queryset.first()
            comment.is_resolution = False
            comment.save(user=self.user)

    def _get_content_type(self, obj_data):
        if 'reporter_type' in obj_data:
            content_type = ContentType.objects.get(model=obj_data['reporter_type'].lower())
            obj_data['reporter_type'] = content_type

    def _generate_code(self, obj_data):
        if not obj_data.get('code'):
            last_ticket_code = Ticket.objects.filter(code__startswith='GRS').aggregate(Max('code')).get('code__max')
            if last_ticket_code is None:
                last_ticket_code_numeric = 0
            else:
                last_ticket_code_numeric = int(last_ticket_code[3:])

            new_ticket_code = f'GRS{last_ticket_code_numeric + 1:08}'
            obj_data['code'] = new_ticket_code

    def _validate_access_control(self, obj_data, access_type=GrievanceAccessControl.PERM_CREATE):
        """Validate user has permission to use selected category and flags"""
        category = obj_data.get('category')
        if category and TicketConfig.grievance_types:
            if category not in TicketConfig.grievance_types:
                raise ValidationError(
                    f"Unknown category: '{category}'. "
                    f"Must be one of the configured grievance types."
                )
        flags = obj_data.get('flags')
        if flags and TicketConfig.grievance_flags:
            flag_list = GrievanceAccessControl.parse_flags(flags)
            for flag in flag_list:
                if flag not in TicketConfig.grievance_flags:
                    raise ValidationError(
                        f"Unknown flag: '{flag}'. "
                        f"Must be one of the configured grievance flags."
                    )
        self._check_access_or_raise(
            obj_data.get('category'), obj_data.get('flags'), access_type
        )

    def _apply_category_defaults(self, obj_data):
        """Apply category defaults (flags, priority) if not already set"""
        category = obj_data.get('category')
        if not category:
            return

        # Get category defaults
        defaults = GrievanceAccessControl.get_category_defaults(category)

        # Apply default flags
        default_flags = defaults.get('default_flags', [])
        if default_flags:
            existing_flags = GrievanceAccessControl.parse_flags(obj_data.get('flags'))
            for flag in default_flags:
                if flag not in existing_flags:
                    existing_flags.append(flag)
            obj_data['flags'] = ' '.join(existing_flags)

        # Get effective priority if not set
        if not obj_data.get('priority'):
            obj_data['priority'] = GrievanceAccessControl.get_effective_priority(
                category, obj_data.get('flags')
            )


class CommentService:
    OBJECT_TYPE = Comment

    def __init__(self, user, validation_class=CommentValidation):
        self.user = user
        self.validation_class = validation_class

    @register_service_signal('comment_service.create')
    @check_authentication
    def create(self, obj_data):
        try:
            with transaction.atomic():
                self._get_content_type(obj_data)
                ticket_id = obj_data.get('ticket_id')
                self.validation_class.validate_create(self.user, **obj_data)

                comment_obj = self.OBJECT_TYPE(**obj_data)
                response_data = self.save_instance(comment_obj)
                self._update_ticket_comment_ids(ticket_id, response_data['data']['id'])

                return response_data

        except Exception as exc:
            return output_exception(
                model_name=self.OBJECT_TYPE.__name__,
                method="create",
                exception=exc
            )

    @transaction.atomic
    def _update_ticket_comment_ids(self, ticket_id, comment_id):
        ticket = Ticket.objects.filter(id=ticket_id).first()
        if ticket:
            json_ext = ticket.json_ext or {}
            comment_ids = json_ext.get('comment_ids', [])
            comment_ids.append(comment_id)
            json_ext['comment_ids'] = comment_ids
            ticket.json_ext = json_ext
            ticket.save(user=self.user)

    @register_service_signal('comment_service.resolve_grievance_by_comment')
    @check_authentication
    def resolve_grievance_by_comment(self, obj_data):
        try:
            with transaction.atomic():
                self.validation_class.validate_resolve_grievance_by_comment(self.user, **obj_data)
                comment = Comment.objects.filter(id=obj_data.get('id')).first()
                ticket = comment.ticket
                self._validate_ticket_update_access(ticket)
                ticket.status = Ticket.TicketStatus.CLOSED
                comment.is_resolution = True
                ticket.save(user=self.user)
                comment.save(user=self.user)
                return {
                    "success": True,
                    "message": "Ok",
                    "detail": "resolve_grievance_by_comment",
                }
        except Exception as exc:
            return output_exception(
                model_name=self.OBJECT_TYPE.__name__,
                method="resolve_grievance_by_comment",
                exception=exc
            )

    def _validate_ticket_update_access(self, ticket):
        """Validate the user may update a ticket of this category and these flags."""
        try:
            GrievanceAccessControl.validate_ticket_access(
                self.user, ticket.category, ticket.flags, GrievanceAccessControl.PERM_UPDATE
            )
        except PermissionDenied as e:
            raise ValidationError(str(e))

    def save_instance(self, obj_):
        obj_.save(user=self.user)
        dict_repr = model_representation(obj_)
        return output_result_success(dict_representation=dict_repr)

    def _get_content_type(self, obj_data):
        if 'commenter_type' in obj_data:
            content_type = ContentType.objects.get(model=obj_data['commenter_type'].lower())
            obj_data['commenter_type'] = content_type
