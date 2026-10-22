from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestBackupHomeAlerts(TransactionCase):
    """The home menu warns administrators when the backup scheduler fails."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Backup = cls.env['db.backup.configure']
        Backup.search([]).active = False  # only the configurations of these tests
        # the configuration checks the master password of the server: not what is tested here
        with patch('odoo.addons.ebshel_auto_database_backup.models.db_backup_configure.odoo_db.verify_admin_password'):
            cls.config = Backup.create({
                'name': 'Nightly', 'db_name': cls.env.cr.dbname, 'master_pwd': 'x', 'active': True,
                'backup_format': 'dump', 'backup_destination': 'local', 'backup_path': '/tmp',
            })
        cls.cron = cls.env.ref('ebshel_auto_database_backup.ir_cron_auto_db_backup')
        cls.cron.write({'active': True, 'failure_count': 0, 'first_failure_date': False,
                        'nextcall': fields.Datetime.now() + timedelta(hours=3)})
        cls.admin = new_test_user(cls.env, login='backup_admin', groups='base.group_user,base.group_system',
                                  tz='Asia/Kolkata')
        cls.clerk = new_test_user(cls.env, login='backup_clerk', groups='base.group_user')

    def _alerts(self, user=None):
        return self.env['db.backup.configure'].with_user(user or self.admin)._home_alerts()

    def test_nothing_while_backups_work(self):
        self.assertEqual(self._alerts(), [])

    def test_a_failed_backup(self):
        self.config.write({'generated_exception': 'No space left on device',
                           'backup_filename': '%s_2026-10-05_20-30-00.dump' % self.env.cr.dbname})
        [alert] = self._alerts()
        self.assertEqual(alert['level'], 'danger')
        self.assertEqual(alert['title'], 'Database backup failed')
        self.assertIn('"Nightly" (Local Storage) failed on', alert['message'])
        self.assertIn('No space left on device', alert['message'])
        self.assertIn('6 Oct 2026, 2:00', alert['message'], "the attempt time, in the admin's timezone (20:30 UTC = 02:00 IST)")
        self.assertEqual((alert['action']['res_model'], alert['action']['res_id']), ('db.backup.configure', self.config.id))
        self.assertEqual(alert['action']['views'], [[False, 'form']])
        # a configuration that is archived is not watched
        self.config.active = False
        self.assertEqual(self._alerts(), [])

    def test_the_job_fails_or_stops(self):
        self.cron.write({'failure_count': 3, 'first_failure_date': fields.Datetime.now() - timedelta(days=2)})
        [alert] = self._alerts()
        self.assertEqual(alert['title'], 'The database backup job is failing')
        self.assertIn('failed 3 time(s) in a row', alert['message'])
        self.assertEqual((alert['action']['res_model'], alert['action']['res_id']), ('ir.cron', self.cron.id))

        self.cron.write({'failure_count': 0, 'first_failure_date': False, 'active': False})
        [alert] = self._alerts()
        self.assertEqual(alert['title'], 'Database backups are switched off')

        self.cron.write({'active': True, 'nextcall': fields.Datetime.now() - timedelta(days=3)})
        [alert] = self._alerts()
        self.assertEqual((alert['level'], alert['title']), ('warning', 'No database backup is being taken'))
        # a few hours late is not yet a stopped scheduler
        self.cron.nextcall = fields.Datetime.now() - timedelta(hours=5)
        self.assertEqual(self._alerts(), [])

    def test_only_administrators_are_warned(self):
        self.config.generated_exception = 'boom'
        self.cron.active = False
        self.assertEqual(self._alerts(self.clerk), [])
        self.assertEqual(len(self._alerts()), 2)
        # and the home menu gathers them (backend_theme), when it is installed
        if 'get_home_alerts' in dir(self.env['res.users']):
            titles = [a['title'] for a in self.env['res.users'].with_user(self.admin).get_home_alerts()]
            self.assertIn('Database backup failed', titles)
            self.assertEqual(self.env['res.users'].with_user(self.clerk).get_home_alerts(), [])
