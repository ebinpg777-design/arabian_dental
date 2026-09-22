# -*- coding: utf-8 -*-
"""Pins the backup fixes of 2026-09-15: who may read the stored credentials, where an
uploaded key may be written, what retention may delete, and that a failed upload is
recorded as a failure. No test reaches Google, Dropbox, S3 or an FTP/SFTP server."""
import base64
import datetime
import os
import subprocess
import tempfile
import zipfile
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError
from dropbox.exceptions import ApiError
from dropbox.files import FileMetadata

import odoo
from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged
from odoo.tools import mute_logger

MODELS = 'odoo.addons.auto_odoo_db_and_file_backup.models.models'
OLD = '2020-01-01_00_00_00'
OLD_EPOCH = 1000000


@tagged('post_install', '-at_install')
class TestBackupSafety(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.dbname = cls.env.cr.dbname
        cls.config = cls.env['auto.database.backup'].create({
            'name': 'Test backups',
            'autoremove': True,
            'days_to_keep': 1,
            'bkup_email': False,
            'bkup_fail_email': False,
        })
        cls.Status = cls.env['auto.database.backup.status']

    def setUp(self):
        super().setUp()
        self.status_floor = self.Status.search([], order='id desc', limit=1).id or 0

    # ------------------------------------------------------------------ helpers
    def _rule(self, destination, **vals):
        values = {
            'backup_id': self.config.id,
            'backup_destination': destination,
            'backup_type': 'zip',
            'backup': 'db_only',
        }
        values.update(vals)
        return self.env['database.backup'].create(values)

    def _new_statuses(self):
        return self.Status.search([('id', '>', self.status_floor)], order='id').mapped('name')

    def _tmp_file(self, content=b'backup'):
        fd, path = tempfile.mkstemp()
        with os.fdopen(fd, 'wb') as handle:
            handle.write(content)
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        return path

    def _write_old(self, folder, name):
        path = os.path.join(folder, name)
        with open(path, 'wb') as handle:
            handle.write(b'x')
        os.utime(path, (OLD_EPOCH, OLD_EPOCH))
        return path

    # ------------------------------------------------------------------ access
    def test_only_settings_administrators_reach_the_backup_models(self):
        """base.group_no_one had full CRUD on all four models - 110 users on staging."""
        system = self.env.ref('base.group_system')
        for model in ('database.backup', 'auto.database.backup', 'auto.database.backup.status',
                      'dropbox.auth.refresh.token.wiz'):
            accesses = self.env['ir.model.access'].search([('model_id.model', '=', model)])
            self.assertTrue(accesses, model)
            self.assertEqual(accesses.mapped('group_id'), system, model)

    def test_stored_secrets_are_restricted_to_settings_administrators(self):
        backup_fields = self.env['database.backup']._fields
        for name in ('ftp_pwd', 'sftp_keyfilepath', 'upload_file', 's3_app_key_id', 's3_secret_key_id',
                     'd_app_secret', 'dropbox_token', 'dropbox_refresh_token', 'dropbox_authorization_code',
                     'dropbox_code_verifier', 'google_drive_refresh_token', 'google_drive_authorization_code'):
            self.assertEqual(backup_fields[name].groups, 'base.group_system', name)

    def test_an_internal_user_cannot_read_the_ftp_password(self):
        rule = self._rule('ftp', ftp_pwd='hunter2')
        user = new_test_user(self.env, login='backup_nosy', groups='base.group_user,base.group_no_one')
        with self.assertRaises(AccessError):
            rule.with_user(user).read(['ftp_pwd'])

    # ------------------------------------------------------------------ key upload
    def test_an_uploaded_key_cannot_leave_the_private_key_folder(self):
        with tempfile.TemporaryDirectory() as data_dir, \
                patch.dict(odoo.tools.config.options, {'data_dir': data_dir}):
            rule = self.env['database.backup'].new({
                'upload_file': base64.b64encode(b'-----BEGIN OPENSSH PRIVATE KEY-----'),
                'file_name': '../../escape.pem',
            })
            rule.onchange_upload_file()
            expected = os.path.join(data_dir, 'backup_keys', self.dbname, 'escape.pem')
            self.assertEqual(rule.sftp_keyfilepath, expected)
            with open(expected, 'rb') as key_file:
                self.assertEqual(key_file.read(), b'-----BEGIN OPENSSH PRIVATE KEY-----')
            self.assertEqual(os.stat(expected).st_mode & 0o777, 0o600)
            self.assertFalse(os.path.exists(os.path.join(data_dir, 'escape.pem')))

    # ------------------------------------------------------------------ names
    def test_retention_recognises_only_this_rules_own_backup_names(self):
        with tempfile.TemporaryDirectory() as files_root:
            files_path = os.path.join(files_root, 'attachments')
            rule = self._rule('folder', backup='db_and_files', files_path=files_path)
            own = ['%s_%s.zip' % (self.dbname, OLD), '%s_%s.dump' % (self.dbname, OLD),
                   'attachments_%s.zip' % OLD]
            foreign = ['%s_live_%s.zip' % (self.dbname, OLD), 'x%s_%s.zip' % (self.dbname, OLD),
                       '%s_%s.zip.part' % (self.dbname, OLD), '%s_notes.dump' % self.dbname,
                       'attachments_%s.dump' % OLD, 'old_attachments_%s.zip' % OLD]
            for name in own:
                self.assertTrue(rule._is_own_backup_name(name), name)
            for name in foreign:
                self.assertFalse(rule._is_own_backup_name(name), name)

    # ------------------------------------------------------------------ folder
    @mute_logger(MODELS)
    def test_a_failed_folder_backup_is_recorded_and_leaves_no_partial_dump(self):
        def half_dump(rule_self, db_name, stream, *args):
            stream.write(b'half a dump')
            raise UserError("pg_dump failed (exit 1): disk full")

        with tempfile.TemporaryDirectory() as root:
            rule = self._rule('folder', folder=root, foldername='bk')
            with patch.object(type(rule), '_take_dump', half_dump):
                rule._backup_to_folder()
            self.assertEqual(os.listdir(os.path.join(root, 'bk')), [])
        statuses = self._new_statuses()
        self.assertEqual(len(statuses), 1)
        self.assertIn('disk full', statuses[0])

    def test_folder_retention_spares_other_databases_in_a_folder_named_after_this_one(self):
        """The database name used to be matched against the full path, so a backup folder
        named after the database made every file in it look like this database's."""
        def dump(rule_self, db_name, stream, *args):
            stream.write(b'dump')

        with tempfile.TemporaryDirectory() as root:
            rule = self._rule('folder', folder=root, foldername=self.dbname)
            backups = os.path.join(root, self.dbname)
            os.makedirs(backups)
            mine = ['%s_%s.zip' % (self.dbname, OLD), '%s_%s.dump' % (self.dbname, OLD)]
            theirs = ['%s_live_%s.zip' % (self.dbname, OLD), 'other_%s.zip' % OLD,
                      '%s_notes.zip' % self.dbname]
            for name in mine + theirs:
                self._write_old(backups, name)
            with patch.object(type(rule), '_take_dump', dump):
                rule._backup_to_folder()
            left = set(os.listdir(backups))
            for name in mine:
                self.assertNotIn(name, left)
            for name in theirs:
                self.assertIn(name, left)
            self.assertEqual(len([name for name in left if rule._is_own_backup_name(name)]), 1,
                             "today's backup stays")
        self.assertEqual(self._new_statuses(), ['Local: Success'])

    # ------------------------------------------------------------------ temp files
    @mute_logger(MODELS)
    def test_a_failed_dump_leaves_no_temporary_files(self):
        rule = self._rule('AWSs3')
        with tempfile.TemporaryDirectory() as scratch, patch.object(tempfile, 'tempdir', scratch), \
                patch.object(type(rule), '_take_dump', side_effect=UserError('pg_dump failed')):
            result = rule.get_content_files(rule)
            self.assertEqual(os.listdir(scratch), [])
        self.assertIn('pg_dump failed', str(result[4]))

    def test_the_files_archive_is_built_from_disk_and_holds_no_copy_of_itself(self):
        def dump(rule_self, db_name, stream, *args):
            with open(stream, 'wb') as handle:
                handle.write(b'dump')

        with tempfile.TemporaryDirectory() as scratch, tempfile.TemporaryDirectory() as files_root:
            files_path = os.path.join(files_root, 'attachments')
            os.makedirs(files_path)
            with open(os.path.join(files_path, 'a.txt'), 'w') as handle:
                handle.write('a')
            rule = self._rule('AWSs3', backup='db_and_files', files_path=files_path)
            with patch.object(tempfile, 'tempdir', scratch), patch.object(type(rule), '_take_dump', dump):
                bkp_file, db_path, bkp_folder, folder_path, err, _date, db_content, files_content = \
                    rule.get_content_files(rule)
            self.assertEqual(err, "")
            self.assertIsNone(db_content, "the dump is no longer read into memory")
            self.assertIsNone(files_content)
            with zipfile.ZipFile(folder_path) as archive:
                self.assertEqual(archive.namelist(), ['attachments/a.txt'])
            self.assertEqual(sorted(os.listdir(scratch)),
                             sorted([os.path.basename(db_path), os.path.basename(folder_path)]),
                             "two files and no directory")
            rule._remove_backup_temp_files(db_path, folder_path)
            self.assertEqual(os.listdir(scratch), [])

    # ------------------------------------------------------------------ dump format
    def test_a_failing_pg_dump_raises_instead_of_leaving_a_broken_dump(self):
        cron_user = self.env.ref('auto_odoo_db_and_file_backup.auto_db_backup_scheduler').user_id
        rule = self._rule('AWSs3', backup_type='dump').with_user(cron_user)
        path = self._tmp_file(b'')
        boom = subprocess.CalledProcessError(1, ['pg_dump'], stderr=b'pg_dump: error: connection refused')
        with patch(MODELS + '.subprocess.run', side_effect=boom) as run, \
                patch('odoo.tools.misc.find_pg_tool', return_value='/usr/bin/pg_dump'):
            with self.assertRaises(UserError) as caught:
                rule._take_dump(self.dbname, path, 'database.backup', 'AWSs3', 'dump')
        self.assertIn('connection refused', str(caught.exception))
        self.assertTrue(run.call_args.kwargs['check'])
        self.assertEqual(run.call_args.kwargs['stdout'].name, path, "pg_dump writes straight into the file")

    # ------------------------------------------------------------------ S3
    @mute_logger(MODELS)
    def test_an_s3_upload_error_fails_the_run_before_success_and_pruning(self):
        rule = self._rule('AWSs3', s3_bucket_name='bucket', s3_app_key_id='key', s3_secret_key_id='secret')
        client = MagicMock()
        client.upload_fileobj.side_effect = ClientError(
            {'Error': {'Code': 'AccessDenied', 'Message': 'denied'}}, 'PutObject')
        with patch(MODELS + '.boto3.client', return_value=client):
            rule.AWSs3_upload(rule, self._tmp_file(), '%s_%s.zip' % (self.dbname, OLD), '', '', '',
                              fields.Datetime.now(), None, None)
        statuses = self._new_statuses()
        self.assertEqual(len(statuses), 1)
        self.assertIn('Failed', statuses[0])
        client.get_paginator.assert_not_called()
        client.delete_objects.assert_not_called()

    def test_s3_retention_pages_through_the_bucket_and_matches_names_exactly(self):
        rule = self._rule('AWSs3', s3_bucket_name='bucket')
        old = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)
        second_page_own = '%s_2020-01-02_00_00_00.dump' % self.dbname
        pages = [
            {'Contents': [{'Key': '%s_%s.zip' % (self.dbname, OLD), 'LastModified': old},
                          {'Key': '%s_live_%s.zip' % (self.dbname, OLD), 'LastModified': old}]},
            {'Contents': [{'Key': second_page_own, 'LastModified': old},
                          {'Key': '%s_notes' % self.dbname, 'LastModified': old}]},
            {},
        ]
        client = MagicMock()
        client.get_paginator.return_value.paginate.return_value = pages
        self.assertEqual(rule._prune_s3(client), 2)
        client.get_paginator.assert_called_once_with('list_objects_v2')
        client.get_paginator.return_value.paginate.assert_called_once_with(
            Bucket='bucket', Prefix=self.dbname + '_')
        deleted = [item['Key'] for call in client.delete_objects.call_args_list
                   for item in call.kwargs['Delete']['Objects']]
        self.assertEqual(deleted, ['%s_%s.zip' % (self.dbname, OLD), second_page_own])

    # ------------------------------------------------------------------ Dropbox
    @mute_logger(MODELS)
    def test_dropbox_without_a_refresh_token_records_a_failure(self):
        rule = self._rule('dropbox', dropbox_refresh_token=False)
        with patch(MODELS + '.dropbox.Dropbox') as client:
            landed = rule.dropbox_upload(rule, self._tmp_file(), 'x.zip', 'x.zip', '', 1,
                                         fields.Datetime.now(), None, None)
        self.assertFalse(landed)
        client.assert_not_called()
        statuses = self._new_statuses()
        self.assertEqual(len(statuses), 1)
        self.assertIn('refresh token', statuses[0])

    @mute_logger(MODELS)
    def test_a_dropbox_api_error_is_recorded_not_a_process_exit(self):
        rule = self._rule('dropbox', dropbox_refresh_token='refresh', d_app_key='app')
        dbx = MagicMock()
        dbx.files_upload.side_effect = ApiError('request-id', None, 'Quota exceeded', 'en')
        with patch(MODELS + '.dropbox.Dropbox') as client:
            client.return_value.__enter__.return_value = dbx
            landed = rule.dropbox_upload(rule, self._tmp_file(), 'x.zip', 'x.zip', '', 1,
                                         fields.Datetime.now(), None, None)
        self.assertFalse(landed)
        statuses = self._new_statuses()
        self.assertEqual(len(statuses), 1)
        self.assertIn('Quota exceeded', statuses[0])

    def test_a_large_dropbox_upload_goes_up_in_a_session(self):
        rule = self._rule('dropbox')
        path = self._tmp_file(b'0123456789')
        dbx = MagicMock()
        dbx.files_upload_session_start.return_value = SimpleNamespace(session_id='session')
        with patch(MODELS + '.DROPBOX_CHUNK_SIZE', 4):
            rule._dropbox_upload_file(dbx, path, '/backup.zip')
        dbx.files_upload.assert_not_called()
        dbx.files_upload_session_start.assert_called_once_with(b'0123')
        dbx.files_upload_session_append_v2.assert_called_once()
        self.assertEqual(dbx.files_upload_session_append_v2.call_args.args[0], b'4567')
        data, cursor, commit = dbx.files_upload_session_finish.call_args.args
        self.assertEqual(data, b'89')
        self.assertEqual((cursor.session_id, cursor.offset), ('session', 8))
        self.assertEqual(commit.path, '/backup.zip')

    def test_dropbox_retention_reads_every_page_and_deletes_only_own_backups(self):
        rule = self._rule('dropbox')
        old = datetime.datetime(2020, 1, 1)

        def entry(name):
            item = MagicMock(spec=FileMetadata)
            item.name = name
            item.path_lower = '/' + name.lower()
            item.client_modified = old
            return item

        own = entry('%s_%s.zip' % (self.dbname, OLD))
        dbx = MagicMock()
        dbx.files_list_folder.return_value = SimpleNamespace(
            entries=[entry('%s_live_%s.zip' % (self.dbname, OLD))], has_more=True, cursor='next')
        dbx.files_list_folder_continue.return_value = SimpleNamespace(
            entries=[own, entry('%s notes.zip' % self.dbname)], has_more=False, cursor='end')
        rule._prune_dropbox(dbx)
        dbx.files_list_folder_continue.assert_called_once_with('next')
        dbx.files_delete_v2.assert_called_once_with(own.path_lower)

    # ------------------------------------------------------------------ FTP / SFTP
    def test_ftp_uses_the_configured_port_and_leaves_the_working_directory_alone(self):
        rule = self._rule('ftp', ftp_address='ftp.example.com', ftp_port=2121, ftp_usrnm='user',
                          ftp_pwd='password', ftp_path='/backups')
        name = '%s_2026-09-15_01_00_00.zip' % self.dbname
        ftp = MagicMock()
        ftp.mlsd.return_value = [
            ('%s_%s.zip' % (self.dbname, OLD), {'modify': '20200101000000'}),
            ('%s_live_%s.zip' % (self.dbname, OLD), {'modify': '20200101000000'}),
            ('readme.txt', {'modify': '20200101000000'}),
        ]
        with patch(MODELS + '.ftplib.FTP', return_value=ftp), patch(MODELS + '.os.chdir') as chdir:
            rule.ftp_upload(rule, self._tmp_file(), name, '', '', '', fields.Datetime.now(), None, None)
        ftp.connect.assert_called_once_with('ftp.example.com', 2121)
        chdir.assert_not_called()
        self.assertEqual(ftp.storbinary.call_args.args[0], 'STOR ' + name)
        ftp.delete.assert_called_once_with('%s_%s.zip' % (self.dbname, OLD))
        self.assertEqual(self._new_statuses(), ['FTP: Success'])

    def test_sftp_keeps_the_leading_slash_of_the_remote_path(self):
        rule = self._rule('sftp', sftp_host='sftp.example.com', sftp_user='user',
                          sftp_file_path='/odoo/backups/')
        name = '%s_2026-09-15_01_00_00.zip' % self.dbname
        path = self._tmp_file()
        client, sftp = MagicMock(), MagicMock()
        sftp.listdir_attr.return_value = []
        with patch.object(type(rule), '_open_sftp_connection', return_value=(client, sftp)):
            rule.sftp_upload(rule, path, name, '', '', '', fields.Datetime.now(), None, None)
        sftp.put.assert_called_once_with(path, '/odoo/backups/' + name)
        sftp.listdir_attr.assert_called_once_with('/odoo/backups')

    # ------------------------------------------------------------------ Google Drive
    def test_drive_retention_deletes_only_this_rules_backups(self):
        rule = self._rule('g_drive', backup_type='dump')
        old_created = '2020-01-01T00:00:00.000Z'
        drive = MagicMock()
        drive.ListFile.return_value.GetList.return_value = [
            {'id': 'own', 'title': '%s_%s.dump' % (self.dbname, OLD), 'createdDate': old_created},
            {'id': 'longer-db', 'title': '%s_live_%s.dump' % (self.dbname, OLD), 'createdDate': old_created},
            {'id': 'document', 'title': 'Notes on %s.docx' % self.dbname, 'createdDate': old_created},
        ]
        with patch.object(type(rule), 'authorize_drive', return_value=drive):
            landed = rule.google_drive_upload(rule, self._tmp_file(), '%s_2026-09-15_01_00_00.dump' % self.dbname,
                                              '', '', 1, fields.Datetime.now(), None, None)
        self.assertTrue(landed)
        deleted = [call.args[0] for call in drive.CreateFile.call_args_list if 'id' in call.args[0]]
        self.assertEqual(deleted, [{'id': 'own'}])
