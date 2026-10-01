"""
canUpdate reports whether updateTicket accepts the ticket for the user: the
module update right and the update right of the ticket's category and of
each of its flags.
"""
from django.core.cache import cache
from django.test import TestCase
from graphene import Schema
from graphene.test import Client

from core.models.openimis_graphql_test_case import BaseTestContext
from core.test_helpers import create_test_interactive_user, create_test_role
from grievance_social_protection.apps import TicketConfig
from grievance_social_protection.models import Ticket
from grievance_social_protection.schema import Query
from grievance_social_protection.tests.test_helpers import (
    assign_rights_to_user, get_rights, restore_grievance_config, setup_grievance_config,
)

OPEN_CATEGORY = 'cu_open'
GUARDED_CATEGORY = 'cu_guarded'
FLAG = 'CU_FLAG'

CAN_UPDATE_QUERY = '''
    query {
        tickets(code_Istartswith: "CU-") {
            edges {
                node {
                    code
                    canUpdate
                }
            }
        }
    }
'''


class TicketCanUpdateTest(TestCase):

    def setUp(self):
        super().setUp()
        self._snapshot = setup_grievance_config({
            'grievance_types': [
                OPEN_CATEGORY,
                {'name': GUARDED_CATEGORY, 'permissions': ['read', 'update']},
            ],
            'grievance_flags': [
                {'name': FLAG, 'permissions': ['read', 'update']},
            ],
        })
        self.addCleanup(restore_grievance_config, self._snapshot)
        category_rights = get_rights('processed_categories', GUARDED_CATEGORY)
        flag_rights = get_rights('processed_flags', FLAG)
        query_rights = [int(r) for r in TicketConfig.gql_query_tickets_perms]
        update_rights = [int(r) for r in TicketConfig.gql_mutation_update_tickets_perms]
        read_rights = [category_rights['read'], flag_rights['read']]
        item_update_rights = [category_rights['update'], flag_rights['update']]

        self.updater = self._user('cu_updater', query_rights + update_rights + read_rights + item_update_rights)
        self.reader = self._user('cu_reader', query_rights + update_rights + read_rights)
        self.without_module_right = self._user(
            'cu_without_module_right', query_rights + read_rights + item_update_rights)

        for code, category, flags in (
                ('CU-OPEN', OPEN_CATEGORY, None),
                ('CU-GUARDED', GUARDED_CATEGORY, None),
                ('CU-FLAGGED', OPEN_CATEGORY, FLAG),
                ('CU-FLAGGED-JSON', OPEN_CATEGORY, f'["{FLAG}"]'),
        ):
            Ticket(code=code, title='Can update', category=category, flags=flags).save(user=self.updater)

    def _user(self, username, rights):
        empty_role = create_test_role([], name=f'NoRights_{username}')
        user = create_test_interactive_user(username=username, roles=[empty_role.id])
        assign_rights_to_user(user, rights, f'Role_{username}')
        cache.clear()
        return user

    def _can_update(self, user):
        result = Client(Schema(query=Query)).execute(
            CAN_UPDATE_QUERY, context=BaseTestContext(user).get_request())
        self.assertNotIn('errors', result, result.get('errors'))
        return {edge['node']['code']: edge['node']['canUpdate'] for edge in result['data']['tickets']['edges']}

    def test_updater_can_update_every_ticket(self):
        self.assertEqual(self._can_update(self.updater), {
            'CU-OPEN': True, 'CU-GUARDED': True, 'CU-FLAGGED': True, 'CU-FLAGGED-JSON': True,
        })

    def test_reader_can_update_only_the_unrestricted_ticket(self):
        self.assertEqual(self._can_update(self.reader), {
            'CU-OPEN': True, 'CU-GUARDED': False, 'CU-FLAGGED': False, 'CU-FLAGGED-JSON': False,
        })

    def test_user_without_module_update_right_can_update_no_ticket(self):
        self.assertEqual(self._can_update(self.without_module_right), {
            'CU-OPEN': False, 'CU-GUARDED': False, 'CU-FLAGGED': False, 'CU-FLAGGED-JSON': False,
        })
