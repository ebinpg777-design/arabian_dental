# -*- coding: utf-8 -*-
"""The warning when the last backup did not work. (client, 2026-09-24)

A backup that has started failing is otherwise silent: it writes a row into a
log under a second menu and waits to be looked for, which happens on the one
day it is already too late. So the configuration screen says so.

What is asserted here is mostly about which way the test runs. The module
writes its status as free text, and the SUCCESS wording is the closed set it
controls ("Local: Success", "SFTP: Success", ... ) while the failure wording is
open-ended. So success is matched and everything else is a failure - a shape
nobody thought of must raise the alarm rather than pass quietly.
"""
from datetime import datetime, timedelta

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestBackupWarning(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Status = cls.env['auto.database.backup.status']
        cls.config = cls.env['auto.database.backup'].search([], limit=1)
        if not cls.config:
            cls.config = cls.env['auto.database.backup'].create({'name': 'Test backups'})
        cls.Status.search([]).unlink()
        cls.now = datetime(2026, 9, 24, 9, 0, 0)

    def _status(self, name, minutes_ago=0):
        return self.Status.create({
            'name': name, 'date': self.now - timedelta(minutes=minutes_ago)})

    def _failed(self):
        self.config.invalidate_recordset()
        return self.config.last_status_failed

    # ------------------------------------------------------- which way it reads
    def test_every_wording_this_module_calls_success_is_a_success(self):
        for wording in ("Success", "Local: Success", "SFTP: Success",
                        "AWS S3: Success", "Dropbox: Success", "FTP: Success",
                        "Google Drive: Success"):
            self.Status.search([]).unlink()
            self._status(wording)
            self.assertFalse(self._failed(), wording)

    def test_a_failure_is_a_failure(self):
        self._status("Failed (Error: could not reach the server)")
        self.assertTrue(self._failed())

    def test_a_wording_nobody_thought_of_raises_the_alarm(self):
        """The direction that matters. Matching 'Failed' instead would let an
        unknown shape pass as though the backup had worked."""
        self._status("Aborted: disk full")
        self.assertTrue(self._failed())

    def test_a_status_with_no_wording_at_all_is_not_taken_as_success(self):
        self._status("")
        self.assertTrue(self._failed())

    # -------------------------------------------------------------- the latest
    def test_the_latest_by_date_decides_and_not_the_newest_row(self):
        """"Latest" is the date on the row, not the order it was written: a
        catch-up run can be inserted after an older one."""
        self._status("Failed (Error: old news)", minutes_ago=600)
        self._status("Local: Success", minutes_ago=5)
        self.assertFalse(self._failed(), "the most recent run worked")

    def test_a_failure_after_a_success_warns(self):
        self._status("Local: Success", minutes_ago=600)
        self._status("Failed (Error: disk full)", minutes_ago=5)
        self.assertTrue(self._failed())

    def test_two_runs_at_the_same_moment_are_broken_by_the_row(self):
        """Two destinations finishing in the same second still have an order."""
        self._status("Failed (Error: gdrive refused)")
        self._status("Local: Success")
        self.assertFalse(self._failed(), "the later row of the two wins")

    def test_nothing_recorded_yet_is_not_a_failure(self):
        """A fresh install has never run; that is not the same as a bad run."""
        self.Status.search([]).unlink()
        self.assertFalse(self._failed())
        self.config.invalidate_recordset()
        self.assertFalse(self.config.last_status_name)

    # ------------------------------------------------------------- what it says
    def test_the_banner_carries_the_run_it_is_complaining_about(self):
        self._status("Failed (Error: disk full)", minutes_ago=5)
        self.config.invalidate_recordset()
        self.assertIn("disk full", self.config.last_status_name)
        self.assertEqual(self.config.last_status_date, self.now - timedelta(minutes=5))

    def test_the_log_flags_its_own_bad_rows(self):
        good = self._status("Local: Success", minutes_ago=10)
        bad = self._status("Failed (Error: nope)", minutes_ago=5)
        self.assertFalse(good.failed)
        self.assertTrue(bad.failed, "stored, so the list can colour the row")

    def test_the_log_reads_newest_first(self):
        self._status("Local: Success", minutes_ago=600)
        newest = self._status("Failed (Error: latest)", minutes_ago=1)
        self.assertEqual(self.Status.search([])[0], newest)

    def test_the_form_shows_the_warning_and_the_list_the_result(self):
        form = self.env['auto.database.backup'].get_view(
            self.env.ref('auto_odoo_db_and_file_backup.view_auto_backup_config_form').id,
            'form')['arch']
        self.assertIn('last_status_failed', form)
        self.assertIn('alert-danger', form)
        tree = self.env['auto.database.backup'].get_view(
            self.env.ref('auto_odoo_db_and_file_backup.view_auto_backup_config_tree').id,
            'list')['arch']
        self.assertIn('last_status_name', tree)

    # ------------------------------------------------------- on the home screen
    # The same fact, said on the screen every user lands on. The strip itself
    # belongs to lab_home; the wording belongs here, with the module that writes
    # the log and knows what its own rows mean.
    def test_a_failed_run_puts_a_warning_on_the_home_screen(self):
        self._status("Failed (Error: could not reach the server)", minutes_ago=3)
        alert = self.Status._home_alert()
        self.assertTrue(alert)
        self.assertEqual(alert['level'], 'danger')
        self.assertIn("could not reach the server", alert['detail'])
        self.assertEqual(alert['action'],
                         'auto_odoo_db_and_file_backup.action_autobackup_status')

    def test_a_backup_that_worked_puts_nothing_on_the_home_screen(self):
        self._status("Local: Success", minutes_ago=3)
        self.assertIsNone(self.Status._home_alert())

    def test_no_backup_has_ever_run_puts_nothing_on_the_home_screen(self):
        """Not a failure: a lab that has not set backups up yet would be told
        every morning that something broke, and would stop reading the strip."""
        self.Status.search([]).unlink()
        self.assertIsNone(self.Status._home_alert())

    def test_the_home_screen_warning_names_the_run_by_its_own_reckoning_of_latest(self):
        self._status("Failed (Error: old one)", minutes_ago=600)
        self._status("Local: Success", minutes_ago=2)
        self.assertIsNone(self.Status._home_alert(),
                          "the newest run worked, so there is nothing to say")
