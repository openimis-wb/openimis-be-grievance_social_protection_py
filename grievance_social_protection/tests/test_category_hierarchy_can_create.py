"""
Each node of the category hierarchy carries can_create, true only when
TicketService.create accepts a ticket in that category for the user.
"""
import json

from django.core.exceptions import PermissionDenied
from graphene import Schema
from graphene.test import Client

from core.models.openimis_graphql_test_case import openIMISGraphQLTestCase, BaseTestContext
from core.test_helpers import create_test_interactive_user
from grievance_social_protection.access_control import GrievanceAccessControl
from grievance_social_protection.schema import Query
from grievance_social_protection.tests.test_helpers import (
    setup_grievance_config, restore_grievance_config,
    assign_rights_to_user, get_rights,
)

gql_query_categories_json = """
query {
  grievanceConfig {
    grievanceCategoriesJson
  }
}
"""

BASE_QUERY = 127000
BASE_CREATE = 127001
BASE_DELETE = 127003


def _index(nodes, acc=None):
    acc = {} if acc is None else acc
    for node in nodes:
        acc[node['full_name']] = node
        _index(node.get('children') or [], acc)
    return acc


class CategoryHierarchyCanCreateTest(openIMISGraphQLTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.field_operator = create_test_interactive_user(username='hier_field_operator', roles=[1])
        cls.case_manager = create_test_interactive_user(username='hier_case_manager', roles=[1])
        cls.gql_client = Client(Schema(query=Query))

    def setUp(self):
        super().setUp()
        self._snapshot = setup_grievance_config({
            'grievance_types': [
                {
                    'name': 'sensitive',
                    'permissions': ['restricted_read', 'read', 'create', 'update'],
                    'children': ['assault'],
                },
                {
                    'name': 'with_default_flag',
                    'default_flags': ['confidential'],
                },
                {
                    'name': 'mixed',
                    'permissions': ['read', 'create'],
                    'children': [{'name': 'open_child', 'permissions': []}],
                },
                'public',
            ],
            'grievance_flags': [
                {'name': 'confidential', 'permissions': ['restricted_read', 'read', 'create']},
            ],
        })
        sensitive = get_rights('processed_categories', 'sensitive')
        assault = get_rights('processed_categories', 'sensitive > assault')
        mixed = get_rights('processed_categories', 'mixed')
        confidential = get_rights('processed_flags', 'confidential')

        assign_rights_to_user(
            self.field_operator,
            [BASE_QUERY, BASE_CREATE, BASE_DELETE,
             sensitive['restricted_read'], assault['restricted_read'],
             mixed['read'], confidential['restricted_read']],
            'HierFieldOperatorRole',
        )
        assign_rights_to_user(
            self.case_manager,
            [BASE_QUERY, BASE_CREATE,
             *sensitive.values(), *assault.values(), *mixed.values(), *confidential.values()],
            'HierCaseManagerRole',
        )

    def tearDown(self):
        restore_grievance_config(self._snapshot)
        super().tearDown()

    def _hierarchy_via_gql(self, user):
        result = self.gql_client.execute(
            gql_query_categories_json, context=BaseTestContext(user).get_request())
        self.assertIsNone(result.get('errors'), result.get('errors'))
        raw = result['data']['grievanceConfig']['grievanceCategoriesJson']
        return _index(json.loads(raw) if isinstance(raw, str) else raw)

    def test_viewable_but_not_creatable_categories_are_flagged(self):
        nodes = self._hierarchy_via_gql(self.field_operator)
        self.assertIn('sensitive', nodes)
        self.assertIn('sensitive > assault', nodes)
        self.assertFalse(nodes['sensitive']['can_create'])
        self.assertFalse(nodes['sensitive > assault']['can_create'])

    def test_category_whose_default_flag_is_not_creatable_is_flagged(self):
        nodes = self._hierarchy_via_gql(self.field_operator)
        self.assertFalse(nodes['with_default_flag']['can_create'])

    def test_unrestricted_categories_are_creatable(self):
        nodes = self._hierarchy_via_gql(self.field_operator)
        self.assertTrue(nodes['public']['can_create'])
        self.assertTrue(nodes['mixed > open_child']['can_create'])
        self.assertFalse(nodes['mixed']['can_create'])

    def test_holder_of_create_rights_can_create_everywhere(self):
        nodes = self._hierarchy_via_gql(self.case_manager)
        for name in ('sensitive', 'sensitive > assault', 'with_default_flag', 'mixed',
                     'mixed > open_child', 'public'):
            self.assertTrue(nodes[name]['can_create'], name)

    def test_can_create_matches_create_validation(self):
        for user in (self.field_operator, self.case_manager):
            for name, node in _index(GrievanceAccessControl.get_category_hierarchy(user)).items():
                defaults = GrievanceAccessControl.get_category_defaults(name)
                try:
                    GrievanceAccessControl.validate_ticket_access(
                        user, name, defaults['default_flags'], GrievanceAccessControl.PERM_CREATE)
                    accepted = True
                except PermissionDenied:
                    accepted = False
                self.assertEqual(node['can_create'], accepted, f'{user.username} {name}')
