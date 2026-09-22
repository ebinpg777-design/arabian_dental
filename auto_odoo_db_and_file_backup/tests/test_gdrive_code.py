# -*- coding: utf-8 -*-
"""Google Drive sign-in: the pasted authorization code is saved, swapped for a refresh token
once, on save, and the form has somewhere to paste it. No test reaches Google. (2026-09-15)"""
from unittest.mock import patch

from lxml import etree

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

MODELS = 'odoo.addons.auto_odoo_db_and_file_backup.models.models'


@tagged('post_install', '-at_install')
class TestGdriveCode(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.config = cls.env['auto.database.backup'].create({
            'name': 'Drive backups',
            'bkup_email': False,
            'bkup_fail_email': False,
        })
        cls.rule = cls.env['database.backup'].create({
            'backup_id': cls.config.id,
            'backup_destination': 'g_drive',
            'backup_type': 'dump',
            'backup': 'db_only',
        })

    def _swap(self, **kwargs):
        return patch('%s.DatabaseBackup._generate_gdrive_refresh_token' % MODELS, autospec=True, **kwargs)

    def test_the_pasted_address_is_saved_as_the_bare_code_and_swapped_on_save(self):
        with self._swap(return_value='refresh-1') as swap:
            self.rule.write({'google_drive_authorization_code':
                             ' http://localhost/?code=4%2F0AbCd-Ef&scope=https://www.googleapis.com/auth/drive '})
        self.assertEqual(self.rule.google_drive_authorization_code, '4/0AbCd-Ef')
        self.assertEqual(swap.call_count, 1)
        self.assertEqual(swap.call_args.args[1], '4/0AbCd-Ef', "Google is sent the decoded code, not the URL")
        self.assertEqual(self.rule.google_drive_refresh_token, 'refresh-1')
        self.assertTrue(self.rule.google_drive_connected)

    def test_an_encoded_code_on_its_own_is_decoded(self):
        with self._swap(return_value='refresh-2'):
            self.rule.write({'google_drive_authorization_code': '4%2F0XyZ'})
        self.assertEqual(self.rule.google_drive_authorization_code, '4/0XyZ')

    def test_a_code_already_swapped_is_never_sent_to_google_again(self):
        with self._swap(return_value='refresh-1') as swap:
            self.rule.write({'google_drive_authorization_code': '4/0First'})
            self.rule.write({'google_drive_authorization_code': '4/0First'})
            self.rule.write({'interval_number': 2})
        self.assertEqual(swap.call_count, 1)

    def test_a_new_code_replaces_the_token(self):
        with self._swap(side_effect=['refresh-1', 'refresh-2']) as swap:
            self.rule.write({'google_drive_authorization_code': '4/0First'})
            self.rule.write({'google_drive_authorization_code': '4/0Second'})
        self.assertEqual(swap.call_count, 2)
        self.assertEqual(self.rule.google_drive_refresh_token, 'refresh-2')

    def test_a_code_google_refuses_saves_nothing(self):
        with self._swap(side_effect=UserError('Could not generate Google Drive refresh token: invalid_grant')):
            with self.assertRaisesRegex(UserError, 'invalid_grant'):
                self.rule.write({'google_drive_authorization_code': '4/0Stale'})
        self.assertFalse(self.rule.google_drive_connected)

    def test_a_rule_created_with_a_code_is_connected(self):
        with self._swap(return_value='refresh-3') as swap:
            rule = self.env['database.backup'].create({
                'backup_id': self.config.id,
                'backup_destination': 'g_drive',
                'backup_type': 'dump',
                'backup': 'db_only',
                'google_drive_authorization_code': 'http://localhost/?code=4%2F0New',
            })
        self.assertEqual(swap.call_count, 1)
        self.assertTrue(rule.google_drive_connected)

    def test_no_code_means_not_connected_and_no_call(self):
        with self._swap() as swap:
            self.rule.write({'interval_number': 3})
        swap.assert_not_called()
        self.assertFalse(self.rule.google_drive_connected)

    def test_the_form_shows_the_code_field_for_google_drive_and_never_the_token(self):
        arch = self.env['database.backup'].get_view(
            self.env.ref('auto_odoo_db_and_file_backup.view_autobackup_config_form').id, 'form')['arch']
        doc = etree.fromstring(arch)
        code = doc.xpath("//field[@name='google_drive_authorization_code']")
        self.assertEqual(len(code), 1, "the form must have a field to paste the code into")
        hidden = [node for node in code[0].iterancestors() if node.get('invisible') in ('1', 'True', 'true')]
        self.assertFalse(hidden, "the code field must not sit inside an always-invisible container")
        for node in [code[0]] + list(code[0].iterancestors()):
            if node.get('invisible'):
                self.assertIn('g_drive', node.get('invisible'))
        self.assertEqual(len(doc.xpath("//field[@name='google_drive_connected']")), 1)
        self.assertFalse(doc.xpath("//field[@name='google_drive_refresh_token']"),
                         "the refresh token itself must not be put on the screen")
