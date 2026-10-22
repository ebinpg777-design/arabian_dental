import json
import os
import re
import shutil
import tempfile
import zipfile
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.addons.mail.tests.common import MockEmail
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tools import config, mute_logger

MODEL = 'odoo.addons.ebshel_auto_database_backup.models.db_backup_configure'


@tagged('post_install', '-at_install')
class TestBackup(TransactionCase, MockEmail):
    """A backup is dumped, zipped with its manifest, stored and cleaned up
    without the database manager (list_db) and without a real pg_dump."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Backup = cls.env['db.backup.configure']
        cls.Backup.search([]).active = False
        cls.env.user.email = 'backup.admin@example.com'

    def setUp(self):
        super().setUp()
        # files do not roll back with the test: one directory per test
        self.backup_dir = tempfile.mkdtemp(prefix='backup_test_')
        self.addCleanup(shutil.rmtree, self.backup_dir, ignore_errors=True)
        with patch(MODEL + '.odoo_db.verify_admin_password'):
            self.config = self.Backup.create({
                'name': 'Nightly', 'db_name': self.env.cr.dbname,
                'master_pwd': 'x', 'active': True, 'backup_format': 'zip',
                'backup_destination': 'local', 'backup_path': self.backup_dir,
                'notify_user': True, 'user_id': self.env.user.id,
            })

    def _fake_pg_dump(self, cmd, env, stdout):
        """Stand-in for pg_dump: writes where the real one would."""
        file_arg = next((a for a in cmd if a.startswith('--file=')), None)
        if file_arg:
            with open(file_arg[len('--file='):], 'w') as fh:
                fh.write('-- SQL dump of %s\n' % cmd[-1])
        else:
            stdout.write(b'PGDMP custom dump')

    def _run(self, records, method='_run_backup'):
        """Run a backup with the fake pg_dump, catching the e-mails."""
        with patch(MODEL + '.find_pg_tool', return_value='pg_dump'), \
                patch.object(type(self.Backup), '_run_pg_dump',
                             side_effect=self._fake_pg_dump), \
                self.mock_mail_gateway():
            getattr(records, method)()

    def _sent(self, subject):
        return [mail for mail in self._mails if mail['subject'] == subject]

    def _backup_name(self, days_ago):
        stamp = (fields.Datetime.now() - timedelta(days=days_ago)).strftime(
            '%Y-%m-%d_%H-%M-%S')
        return '%s_%s.zip' % (self.env.cr.dbname, stamp)

    def test_zip_backup_without_list_db(self):
        with patch.dict(config.options, {'list_db': False}):
            self._run(self.Backup, '_schedule_auto_backup')
        self.assertFalse(self.config.generated_exception)
        self.assertRegex(self.config.backup_filename, r'^%s_\d{4}-\d{2}-\d{2}_'
                         r'\d{2}-\d{2}-\d{2}\.zip$' % re.escape(self.env.cr.dbname))
        path = os.path.join(self.backup_dir, self.config.backup_filename)
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            self.assertEqual(names[0], 'dump.sql', "restore reads dump.sql first")
            manifest = json.loads(archive.read('manifest.json'))
        self.assertEqual(manifest['db_name'], self.env.cr.dbname)
        self.assertEqual(manifest['major_version'], '20.0')
        self.assertIn('ebshel_auto_database_backup', manifest['modules'])
        # the success mail went out from the template
        [mail] = self._sent('Database Backup Successful: %s' % self.env.cr.dbname)
        self.assertEqual(mail['email_to'], ['"%s" <backup.admin@example.com>' % self.env.user.name])
        self.assertIn(self.config.backup_filename, mail['body'])
        self.assertIn('Local', mail['body'])

    def test_dump_format_and_old_backups(self):
        self.config.write({'backup_format': 'dump', 'auto_remove': True,
                           'days_to_remove': 7})
        old = self._backup_name(10).replace('.zip', '.dump')
        recent = self._backup_name(2)
        other_db = 'another_db_%s' % old.split('_', 1)[-1]
        for name in (old, recent, other_db, 'notes.txt'):
            open(os.path.join(self.backup_dir, name), 'w').close()
        self._run(self.config)
        self.assertFalse(self.config.generated_exception)
        left = set(os.listdir(self.backup_dir))
        self.assertNotIn(old, left, "a backup older than 7 days is removed")
        self.assertTrue({recent, other_db, 'notes.txt', self.config.backup_filename} <= left,
                        "recent backups and files that are not ours are kept")
        with open(os.path.join(self.backup_dir, self.config.backup_filename), 'rb') as fh:
            self.assertEqual(fh.read(), b'PGDMP custom dump')

    def test_failure_is_recorded_and_mailed(self):
        self.config.backup_path = '/proc/no_such_dir/backups'
        with mute_logger(MODEL):
            self._run(self.config)
        self.assertTrue(self.config.generated_exception)
        [mail] = self._sent('Database Backup Failed: %s' % self.env.cr.dbname)
        self.assertIn('/proc/no_such_dir', mail['body'])
        self.assertEqual(os.listdir(self.backup_dir), [])

    def test_backups_only_from_the_scheduler(self):
        """Only the cron's user may dump: an administrator calling the method
        over RPC gets nothing."""
        admin = self.env.ref('base.user_admin')
        admin.email = 'admin@example.com'
        with mute_logger(MODEL):
            self._run(self.config.with_user(admin))
        self.assertIn('Unauthorized database operation',
                      self.config.generated_exception)

    def test_credentials_are_checked(self):
        with self.assertRaisesRegex(ValidationError, 'Invalid Master Password'):
            self.config.master_pwd = 'not the master password'
        with patch(MODEL + '.odoo_db.verify_admin_password'), \
                self.assertRaisesRegex(ValidationError, 'Invalid Database Name'):
            self.config.db_name = 'no_such_database_here'

    def test_oauth_state_is_bound_to_record_and_user(self):
        state = self.config._get_oauth_state()
        self.assertEqual(self.Backup._get_record_from_oauth_state(state), self.config)
        self.assertFalse(self.Backup._get_record_from_oauth_state(state + 'x'))
        other = self.env.ref('base.user_admin')
        self.assertFalse(self.Backup.with_user(other)._get_record_from_oauth_state(state))
