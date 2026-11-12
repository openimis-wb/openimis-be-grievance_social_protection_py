import threading
import time
from unittest import mock

from django.apps import apps as global_apps
from django.core.exceptions import ValidationError
from django.db import DEFAULT_DB_ALIAS, OperationalError, ProgrammingError, connection
from django.db.models.signals import post_migrate

from grievance_social_protection.access_control import GrievanceAccessControl
from grievance_social_protection.apps import DEFAULT_GRIEVANCE_TYPE, TicketConfig
from grievance_social_protection.rights import GrievanceRightsManager
from grievance_social_protection.services import TicketService
from grievance_social_protection.tests.test_config_reload_rights import (
    MODULE,
    ReloadRightsTestBase,
)
from grievance_social_protection.tests.test_helpers import get_rights

GENERATE = 'grievance_social_protection.rights.GrievanceRightsManager.generate_automatic_rights'


class RightsGenerationAfterMigrateTest(ReloadRightsTestBase):
    """
    On a database whose tables do not exist when the application starts,
    ready() skips the rights generation; the restricted categories and flags
    get their rights after migrate, or on the first access check.
    """

    def _start_without_tables(self):
        # ready() of a process started before the migrations: the rights
        # tables are missing, so the generation raises a database error.
        with mock.patch(GENERATE, side_effect=ProgrammingError('relation does not exist')):
            global_apps.get_app_config(MODULE).ready()

    @staticmethod
    def _send_post_migrate():
        app_config = global_apps.get_app_config(MODULE)
        post_migrate.send(
            sender=app_config, app_config=app_config, verbosity=0, interactive=False,
            using=DEFAULT_DB_ALIAS, apps=global_apps, plan=[])

    def _create_uncategorized(self, user):
        return TicketService(user).create({
            'category': DEFAULT_GRIEVANCE_TYPE,
            'title': 'Uncategorized after migrate',
            'resolution': '5,0',
        })

    def test_startup_without_tables_leaves_rights_empty_and_pending(self):
        self._start_without_tables()

        self.assertIn(DEFAULT_GRIEVANCE_TYPE, TicketConfig.processed_categories)
        self.assertEqual(get_rights('processed_categories', DEFAULT_GRIEVANCE_TYPE), {})
        self.assertTrue(TicketConfig.rights_generation_pending)

    def test_post_migrate_generates_the_skipped_rights(self):
        self._start_without_tables()

        self._send_post_migrate()

        rights = get_rights('processed_categories', DEFAULT_GRIEVANCE_TYPE)
        self.assertEqual(set(rights), {'read', 'update'})
        self.assertFalse(TicketConfig.rights_generation_pending)

    def test_base_right_creates_uncategorized_ticket_after_migrate(self):
        self._start_without_tables()
        self._send_post_migrate()

        with mock.patch(GENERATE) as retry:
            result = self._create_uncategorized(self.user_without_vbg)

        retry.assert_not_called()
        self.assertTrue(result.get('success'), result)

    def test_post_migrate_skips_generation_when_rights_tables_are_missing(self):
        self._start_without_tables()

        with mock.patch(
                'grievance_social_protection.apps.connections') as connections, \
                mock.patch(GENERATE) as generate:
            connections.__getitem__.return_value.introspection.table_names.return_value = []
            self._send_post_migrate()

        generate.assert_not_called()
        self.assertTrue(TicketConfig.rights_generation_pending)

    def test_first_access_check_generates_the_skipped_rights(self):
        self._start_without_tables()

        with mock.patch(GENERATE, wraps=GrievanceRightsManager.generate_automatic_rights) as generate:
            allowed = GrievanceAccessControl.check_category_access(
                self.user_without_vbg, DEFAULT_GRIEVANCE_TYPE, GrievanceAccessControl.PERM_CREATE)
            GrievanceAccessControl.check_category_access(
                self.user_without_vbg, DEFAULT_GRIEVANCE_TYPE, GrievanceAccessControl.PERM_CREATE)

        self.assertTrue(allowed)
        self.assertEqual(generate.call_count, 1)
        self.assertEqual(set(get_rights('processed_categories', DEFAULT_GRIEVANCE_TYPE)), {'read', 'update'})
        self.assertFalse(TicketConfig.rights_generation_pending)

    def test_base_right_creates_uncategorized_ticket_after_first_access_retry(self):
        self._start_without_tables()

        result = self._create_uncategorized(self.user_without_vbg)

        self.assertTrue(result.get('success'), result)

    def test_failed_retry_stays_denied_and_is_not_repeated(self):
        self._start_without_tables()

        with mock.patch(GENERATE, side_effect=OperationalError('database unavailable')) as generate:
            first = GrievanceAccessControl.check_category_access(
                self.user_without_vbg, DEFAULT_GRIEVANCE_TYPE, GrievanceAccessControl.PERM_CREATE)
            second = GrievanceAccessControl.check_category_access(
                self.user_without_vbg, DEFAULT_GRIEVANCE_TYPE, GrievanceAccessControl.PERM_CREATE)
            with self.assertRaises(ValidationError):
                self._create_uncategorized(self.user_without_vbg)

        self.assertFalse(first)
        self.assertFalse(second)
        self.assertEqual(generate.call_count, 1)
        self.assertEqual(get_rights('processed_categories', DEFAULT_GRIEVANCE_TYPE), {})

    def test_failed_retry_clears_rights_assigned_before_the_failure(self):
        self._start_without_tables()

        def fail_after_assigning(app_config):
            # The manager writes each entry's rights before the transaction
            # commits; the rollback removes the rows behind these ids.
            app_config.processed_categories[DEFAULT_GRIEVANCE_TYPE]['generated_rights'] = {
                'read': 127100, 'update': 127102, 'create': 127101}
            raise OperationalError('connection lost')

        with mock.patch(GENERATE, side_effect=fail_after_assigning):
            allowed = GrievanceAccessControl.check_category_access(
                self.user_without_vbg, DEFAULT_GRIEVANCE_TYPE, GrievanceAccessControl.PERM_CREATE)

        self.assertFalse(allowed)
        self.assertEqual(get_rights('processed_categories', DEFAULT_GRIEVANCE_TYPE), {})
        self.assertFalse(GrievanceAccessControl.check_category_access(
            self.user_without_vbg, DEFAULT_GRIEVANCE_TYPE, GrievanceAccessControl.PERM_CREATE))

    def test_concurrent_access_checks_retry_once(self):
        self._start_without_tables()
        calls = []

        def slow_generation(app_config):
            calls.append(threading.get_ident())
            time.sleep(0.05)

        def check_in_thread():
            try:
                TicketConfig.generate_skipped_rights()
            finally:
                connection.close()

        with mock.patch(GENERATE, side_effect=slow_generation):
            threads = [threading.Thread(target=check_in_thread) for _ in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        self.assertEqual(len(calls), 1)
        self.assertFalse(TicketConfig.rights_generation_pending)
