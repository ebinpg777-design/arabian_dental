# -*- coding: utf-8 -*-
"""Google returns to this server after Drive access is approved, not to localhost.

The auth link named the first redirect address in client_secrets.json - http://localhost
or the site root - so the browser landed on "This site can't be reached" and the code was
lost. A Web application client now returns to /auto_backup/gdrive/callback with the rule
it is for, signed, and the code is saved there. No test reaches Google. (2026-09-15)
"""
from unittest.mock import patch

from odoo.tests import HttpCase, tagged

MODELS = 'odoo.addons.auto_odoo_db_and_file_backup.models.models'


class GdriveCallbackCase:

    @classmethod
    def _rule(cls):
        config = cls.env['auto.database.backup'].create({
            'name': 'Drive callback backups', 'bkup_email': False, 'bkup_fail_email': False})
        return cls.env['database.backup'].create({
            'backup_id': config.id, 'backup_destination': 'g_drive',
            'backup_type': 'dump', 'backup': 'db_only'})

    def _swap(self, **kwargs):
        return patch('%s.DatabaseBackup._generate_gdrive_refresh_token' % MODELS,
                     autospec=True, **kwargs)

    def _client_type(self, kind):
        return patch('%s.DatabaseBackup._gdrive_client_type' % MODELS, return_value=kind)


@tagged('post_install', '-at_install')
class TestGdriveCallback(GdriveCallbackCase, HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.rule = cls._rule()

    def test_the_signed_state_finds_its_rule_and_a_forged_one_does_not(self):
        Rule = self.env['database.backup']
        state = self.rule._gdrive_state()
        self.assertEqual(Rule._gdrive_rule_from_state(state), self.rule)
        rule_id, _sep, signature = state.partition('.')
        for forged in ('%s.%s' % (int(rule_id) + 1, signature), rule_id + '.x', rule_id, '', None):
            self.assertFalse(Rule._gdrive_rule_from_state(forged), forged)

    def test_a_web_client_returns_to_this_server(self):
        with self._client_type('web'):
            uri = self.rule._gdrive_redirect_uri()
        self.assertEqual(uri, self.rule.get_base_url().rstrip('/') + '/auto_backup/gdrive/callback')
        self.assertEqual(self.rule.google_drive_redirect_uri, uri)

    def test_the_callback_saves_the_code_on_the_rule_and_returns_to_it(self):
        self.authenticate('admin', 'admin')
        with self._client_type('web'), self._swap(return_value='refresh-cb') as swap:
            response = self.url_open('/auto_backup/gdrive/callback?code=4%%2F0Cb-1&state=%s'
                                     % self.rule._gdrive_state(), allow_redirects=False)
        self.assertIn(response.status_code, (302, 303))
        self.assertIn('model=database.backup', response.headers.get('Location', ''))
        self.assertEqual(swap.call_count, 1)
        self.rule.invalidate_recordset()
        self.assertEqual(self.rule.google_drive_refresh_token, 'refresh-cb')

    def test_a_link_for_no_rule_changes_nothing(self):
        self.authenticate('admin', 'admin')
        with self._swap(return_value='never') as swap:
            response = self.url_open('/auto_backup/gdrive/callback?code=abc&state=999999.bad',
                                     allow_redirects=False)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(swap.call_count, 0)
