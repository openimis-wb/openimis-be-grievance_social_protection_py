from types import SimpleNamespace

from django.core.exceptions import PermissionDenied
from django.test import TestCase

from core.test_helpers import create_test_interactive_user
from grievance_social_protection.access_control import GrievanceAccessControl
from grievance_social_protection.gql_queries import TicketGQLType
from grievance_social_protection.models import Ticket
from grievance_social_protection.tests.test_helpers import (
    setup_grievance_config, restore_grievance_config,
    assign_rights_to_user, get_rights,
)


class FlagEncodingAccessTest(TestCase):
    """Ticket flags stored as a space-separated string or as a JSON array
    string get the same access decisions, in the queryset filter and in the
    per-ticket checks."""

    _config_snapshot = None

    TICKET_FLAGS = {
        'plain_sensitive': 'sensitive',
        'plain_multi': 'urgent special',
        'json_sensitive': '["sensitive"]',
        'json_special': '["special"]',
        'json_multi': '["sensitive", "special"]',
        'json_spaced': ' [ "special" ] ',
        'json_padded': '[" sensitive "]',
        'json_prefix': '["sensitive_x"]',
        'json_unrestricted': '["urgent"]',
        'json_empty': '[]',
        'null': None,
    }

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._config_snapshot = cls._setup_test_config()
        cls._create_test_users()
        cls._create_test_tickets()

    def setUp(self):
        self._per_test_snapshot = self._setup_test_config()

    def tearDown(self):
        restore_grievance_config(self._per_test_snapshot)

    @classmethod
    def tearDownClass(cls):
        Ticket.objects.filter(id__in=[t.id for t in cls.tickets.values()]).delete()
        if cls._config_snapshot:
            restore_grievance_config(cls._config_snapshot)
        super().tearDownClass()

    @classmethod
    def _setup_test_config(cls):
        cfg = {
            'grievance_types': ['open_category'],
            'grievance_flags': [
                'urgent',
                'sensitive_x',
                {
                    'name': 'sensitive',
                    'priority': 'High',
                    'permissions': ['restricted_read', 'read'],
                },
                {
                    'name': 'special',
                    'priority': 'Critical',
                    'permissions': ['read'],
                },
            ],
        }
        return setup_grievance_config(cfg)

    @classmethod
    def _create_test_users(cls):
        sensitive_rights = get_rights('processed_flags', 'sensitive')
        special_rights = get_rights('processed_flags', 'special')

        cls.user_no_flag = create_test_interactive_user(username='flag_enc_none', roles=[1])
        assign_rights_to_user(cls.user_no_flag, [127000], 'FlagEncNoFlagRole')

        cls.user_restricted = create_test_interactive_user(username='flag_enc_restricted', roles=[1])
        assign_rights_to_user(
            cls.user_restricted, [127000, sensitive_rights['restricted_read']], 'FlagEncRestrictedRole')

        cls.user_reader = create_test_interactive_user(username='flag_enc_reader', roles=[1])
        assign_rights_to_user(
            cls.user_reader, [127000, sensitive_rights['read'], special_rights['read']], 'FlagEncReaderRole')

    @classmethod
    def _create_test_tickets(cls):
        cls.tickets = {}
        for key, flags in cls.TICKET_FLAGS.items():
            ticket = Ticket(
                title=f'Flag encoding {key}',
                category='open_category',
                flags=flags,
                resolution='5,0',
            )
            ticket.save(user=cls.user_reader)
            cls.tickets[key] = ticket

    def _visible_keys(self, user):
        queryset = Ticket.objects.filter(id__in=[t.id for t in self.tickets.values()])
        visible_ids = set(
            GrievanceAccessControl.filter_ticket_queryset(queryset, user).values_list('id', flat=True))
        return {key for key, ticket in self.tickets.items() if ticket.id in visible_ids}

    def test_parse_flags_encodings(self):
        parse = GrievanceAccessControl.parse_flags
        self.assertEqual(parse('sensitive special'), ['sensitive', 'special'])
        self.assertEqual(parse('["sensitive"]'), ['sensitive'])
        self.assertEqual(parse('["sensitive", "special"]'), ['sensitive', 'special'])
        self.assertEqual(parse(' [ "special" ] '), ['special'])
        self.assertEqual(parse('[" sensitive "]'), ['sensitive'])
        self.assertEqual(parse('[sensitive'), ['[sensitive'])
        self.assertEqual(parse('[]'), [])
        self.assertEqual(parse(''), [])
        self.assertEqual(parse(None), [])
        self.assertEqual(parse(['sensitive', 'special']), ['sensitive', 'special'])

    def test_access_level_json_array_flags(self):
        level = GrievanceAccessControl.get_user_access_level
        self.assertEqual(level(self.user_no_flag, 'open_category', '["sensitive"]'), 'none')
        self.assertEqual(level(self.user_restricted, 'open_category', '["sensitive"]'), 'restricted')
        self.assertEqual(level(self.user_restricted, 'open_category', '["sensitive", "special"]'), 'none')
        self.assertEqual(level(self.user_reader, 'open_category', '["sensitive", "special"]'), 'read')
        self.assertEqual(level(self.user_no_flag, 'open_category', '["sensitive_x"]'), 'full')

    def test_queryset_excludes_json_array_flags(self):
        self.assertEqual(
            self._visible_keys(self.user_no_flag),
            {'json_prefix', 'json_unrestricted', 'json_empty', 'null'},
        )
        self.assertEqual(
            self._visible_keys(self.user_restricted),
            {'plain_sensitive', 'json_sensitive', 'json_padded', 'json_prefix', 'json_unrestricted',
             'json_empty', 'null'},
        )
        self.assertEqual(self._visible_keys(self.user_reader), set(self.TICKET_FLAGS))

    def test_queryset_matches_access_level(self):
        for user in (self.user_no_flag, self.user_restricted, self.user_reader):
            visible = self._visible_keys(user)
            for key, ticket in self.tickets.items():
                level = GrievanceAccessControl.get_user_access_level(user, ticket.category, ticket.flags)
                self.assertEqual(
                    key in visible, level != GrievanceAccessControl.ACCESS_NONE,
                    f"user={user.username} ticket={key} flags={ticket.flags!r} level={level}",
                )

    def test_validate_ticket_access_json_array_flags(self):
        with self.assertRaises(PermissionDenied):
            GrievanceAccessControl.validate_ticket_access(
                self.user_no_flag, 'open_category', '["sensitive"]', GrievanceAccessControl.PERM_READ)
        GrievanceAccessControl.validate_ticket_access(
            self.user_reader, 'open_category', '["sensitive", "special"]', GrievanceAccessControl.PERM_READ)

    def test_effective_priority_json_array_flags(self):
        self.assertEqual(
            GrievanceAccessControl.get_effective_priority('open_category', '["special"]'), 'Critical')
        self.assertEqual(
            GrievanceAccessControl.get_effective_priority('open_category', '["sensitive"]'), 'High')

    def test_field_masking_json_array_flags(self):
        ticket = self.tickets['json_sensitive']
        info_restricted = SimpleNamespace(context=SimpleNamespace(user=self.user_restricted))
        info_reader = SimpleNamespace(context=SimpleNamespace(user=self.user_reader))
        self.assertTrue(TicketGQLType._should_restrict_field('description', ticket, info_restricted))
        self.assertFalse(TicketGQLType._should_restrict_field('description', ticket, info_reader))
