"""
A process reloads the grievance configuration when the stored row differs
from the one it loaded, at the start of its next request or task and at its
next access check, after CONFIG_CHECK_INTERVAL_SECONDS: a configuration saved
by another process (another web worker, a task worker) takes effect without a
restart, including for code that reads TicketConfig before any access check.
Within the interval an access check reads nothing from the database.

The other process is simulated by writing the row without the reloader that
ModuleConfiguration.save runs in the saving process only.
"""
import json
import threading
from unittest import mock

from celery.signals import task_prerun
from django.apps import apps
from django.core.signals import request_started
from django.db import close_old_connections, connection
from django.test import SimpleTestCase
from django.test.utils import CaptureQueriesContext

from core.models import ModuleConfiguration
from grievance_social_protection import apps as grievance_apps
from grievance_social_protection.access_control import GrievanceAccessControl
from grievance_social_protection.apps import TicketConfig
from grievance_social_protection.models import Ticket
from grievance_social_protection.services import TicketService
from grievance_social_protection.tests.data import service_add_ticket_payload
from grievance_social_protection.tests.test_config_reload_rights import MODULE, ReloadRightsTestBase

# 'secret' is an open flag: a flag the loaded configuration does not list is closed.
LOADED = {
    'grievance_types': ['general'],
    'grievance_flags': ['public', 'secret'],
}
# Saved by the other process: 'general' restricted, the flag 'secret' restricted.
SAVED_ELSEWHERE = {
    'grievance_types': [{'name': 'general', 'permissions': ['read']}],
    'grievance_flags': ['public', {'name': 'secret', 'permissions': ['read']}],
}
# Saved by the other process: a new open category 'fraude'.
NEW_CATEGORY_ELSEWHERE = {
    'grievance_types': ['general', 'fraude'],
    'grievance_flags': ['public'],
}


class ConfigChangeAcrossProcessesTest(ReloadRightsTestBase):

    def setUp(self):
        super().setUp()
        for attribute in ('_config_checked_at', 'loaded_config_text'):
            patcher = mock.patch.object(TicketConfig, attribute, None)
            patcher.start()
            self.addCleanup(patcher.stop)
        ModuleConfiguration.objects.filter(module=MODULE, layer='be').delete()
        self.row = ModuleConfiguration.objects.create(
            module=MODULE, layer='be', version='1', config=json.dumps(LOADED))
        # This process loaded LOADED from the row.
        apps.get_app_config(MODULE)._reload_module_config(self.row)
        self.general = self._ticket('general')
        self.secret = self._ticket('general', 'secret')
        self.clock = mock.patch.object(grievance_apps.time, 'monotonic', return_value=1000.0)
        self.monotonic = self.clock.start()
        self.addCleanup(self.clock.stop)
        TicketConfig.reload_if_changed()

    def _saved_by_another_process(self, cfg):
        ModuleConfiguration.objects.filter(id=self.row.id).update(config=json.dumps(cfg))

    def _after(self, seconds):
        self.monotonic.return_value = 1000.0 + seconds

    def test_a_configuration_saved_elsewhere_is_enforced_after_the_interval(self):
        self.assertTrue(GrievanceAccessControl.check_category_access(self.user_without_vbg, 'general'))
        self.assertIn(self.secret.id, self._visible_ids(self.user_without_vbg))

        self._saved_by_another_process(SAVED_ELSEWHERE)
        self._after(grievance_apps.CONFIG_CHECK_INTERVAL_SECONDS)

        self.assertFalse(GrievanceAccessControl.check_category_access(self.user_without_vbg, 'general'))
        self.assertFalse(GrievanceAccessControl.check_flag_access(self.user_without_vbg, 'secret'))
        self.assertNotIn(self.secret.id, self._visible_ids(self.user_without_vbg))
        self.assertNotIn(self.general.id, self._visible_ids(self.user_without_vbg))

    def test_a_check_within_the_interval_reads_nothing(self):
        self._saved_by_another_process(SAVED_ELSEWHERE)
        self._after(grievance_apps.CONFIG_CHECK_INTERVAL_SECONDS / 2)
        with CaptureQueriesContext(connection) as queries:
            GrievanceAccessControl.check_category_access(self.user_without_vbg, 'general')
            TicketConfig.reload_if_changed()
        self.assertEqual(len(queries), 0)

    def test_an_unchanged_row_is_not_reloaded(self):
        self._after(grievance_apps.CONFIG_CHECK_INTERVAL_SECONDS)
        with mock.patch.object(grievance_apps.TicketConfig, '_reload_module_config') as reload:
            TicketConfig.reload_if_changed()
        reload.assert_not_called()

    def _create_in_new_category(self):
        return TicketService(self.user_without_vbg).create(
            {**service_add_ticket_payload, 'category': 'fraude', 'flags': 'public'})

    def test_a_request_start_brings_a_configuration_saved_elsewhere_into_force(self):
        self._saved_by_another_process(NEW_CATEGORY_ELSEWHERE)
        self._after(grievance_apps.CONFIG_CHECK_INTERVAL_SECONDS)
        # As django.test.Client does: closing the connection would end the test transaction.
        request_started.disconnect(close_old_connections)
        self.addCleanup(request_started.connect, close_old_connections)
        request_started.send(sender=self.__class__, environ={})
        result = self._create_in_new_category()
        self.assertTrue(result['success'], result)
        self.assertTrue(Ticket.objects.filter(uuid=result['data']['uuid'], category='fraude').exists())

    def test_a_task_start_brings_a_configuration_saved_elsewhere_into_force(self):
        self._saved_by_another_process(NEW_CATEGORY_ELSEWHERE)
        self._after(grievance_apps.CONFIG_CHECK_INTERVAL_SECONDS)
        task_prerun.send(sender=None, task_id='grievance-config-test', task=None, args=(), kwargs={})
        result = self._create_in_new_category()
        self.assertTrue(result['success'], result)

    def test_a_deleted_row_brings_the_default_configuration_back(self):
        ModuleConfiguration.objects.filter(id=self.row.id).delete()
        self._after(grievance_apps.CONFIG_CHECK_INTERVAL_SECONDS)
        TicketConfig.reload_if_changed()
        self.assertEqual(TicketConfig.grievance_flags, grievance_apps.DEFAULT_CFG['grievance_flags'])


class ConfigCheckWithoutDatabaseTest(SimpleTestCase):
    """Where the database cannot be read, such as in a test without database
    access, the access checks keep the loaded configuration."""

    def setUp(self):
        for attribute in ('_config_checked_at', 'processed_categories', 'processed_flags'):
            patcher = mock.patch.object(TicketConfig, attribute, None if attribute == '_config_checked_at' else {})
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_the_check_keeps_the_loaded_configuration(self):
        user = mock.Mock(is_anonymous=False)
        with self.assertLogs('grievance_social_protection.apps', level='WARNING'):
            self.assertTrue(GrievanceAccessControl.check_category_access(user, 'general'))
        self.assertEqual(TicketConfig.processed_categories, {})


class NestedConfigCheckTest(SimpleTestCase):
    """A configuration check started while a reload runs in the same thread,
    such as by an access check of the reload, returns instead of waiting for
    the lock that thread holds."""

    def setUp(self):
        self.now = 1000.0
        for attribute, value in (('_config_checked_at', None), ('loaded_config_text', 'loaded')):
            patcher = mock.patch.object(TicketConfig, attribute, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for patcher in (
                mock.patch.object(grievance_apps.time, 'monotonic', side_effect=lambda: self.now),
                mock.patch.object(TicketConfig, '_stored_config_row',
                                  return_value=mock.Mock(config='saved elsewhere'))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_a_check_within_a_reload_returns(self):
        reloads = []

        def reload_checking_again(app_config, instance):
            reloads.append(instance.config)
            if len(reloads) == 1:
                self.now += 3 * grievance_apps.CONFIG_CHECK_INTERVAL_SECONDS
                TicketConfig.reload_if_changed()

        with mock.patch.object(TicketConfig, '_reload_module_config', reload_checking_again):
            worker = threading.Thread(target=TicketConfig.reload_if_changed, daemon=True)
            worker.start()
            worker.join(timeout=5)
        self.assertFalse(worker.is_alive(), 'reload_if_changed waits for the lock its own thread holds')
        self.assertEqual(reloads, ['saved elsewhere', 'saved elsewhere'])
