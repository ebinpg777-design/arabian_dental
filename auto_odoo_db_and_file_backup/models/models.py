# -*- coding: utf-8 -*-
# imports of python libs
import datetime
import json
import os
import pytz
import re
import shutil
import tempfile
import time
import zipfile

try:
    from xmlrpc import client as xmlrpclib
except ImportError:
    import xmlrpclib

import socket
import requests
from odoo.addons.google_account.models.google_service import GOOGLE_TOKEN_ENDPOINT, TIMEOUT
import subprocess
from urllib.parse import parse_qs, unquote, urlparse
from dateutil.relativedelta import relativedelta
import dropbox
from dropbox.files import CommitInfo, FileMetadata, UploadSessionCursor, WriteMode
from dropbox.exceptions import ApiError, AuthError
from dropbox import DropboxOAuth2FlowNoRedirect
import http.client
import ftplib
import boto3
import paramiko
import base64
import httplib2
from oauth2client.client import GoogleCredentials
from pydrive.auth import GoogleAuth
from pydrive.drive import GoogleDrive
import logging

_logger = logging.getLogger(__name__)
# imports of odoo
import odoo
from odoo import models, fields, api, _
from odoo.exceptions import AccessDenied, RedirectWarning, UserError
from odoo.tools import consteq
from odoo.tools.misc import hmac as odoo_hmac

_intervalTypes = {
    'days': lambda interval: relativedelta(days=interval),
    'hours': lambda interval: relativedelta(hours=interval),
    'weeks': lambda interval: relativedelta(days=7 * interval),
    'months': lambda interval: relativedelta(months=interval),
    'minutes': lambda interval: relativedelta(minutes=interval),
}

# Backups are written as <database>_<YYYY-MM-DD_HH_MM_SS>.<zip|dump>, and the files archive as
# <files folder>_<same stamp>.zip. Retention deletes only names of exactly that shape: it used to
# delete anything whose name merely contained the database name, which on a shared destination
# included other databases' backups (database "lab" pruning "lab_live_...") and other files.
BACKUP_STAMP_RE = r'\d{4}-\d{2}-\d{2}_\d{2}_\d{2}_\d{2}'
# Dropbox takes at most 150 MB in one files_upload call; larger files go up in sessions.
DROPBOX_CHUNK_SIZE = 16 * 1024 * 1024
# Where Google returns after Drive access is approved (controllers/main.py), and the
# signing scope of the rule id carried there in the OAuth state.
GDRIVE_CALLBACK = '/auto_backup/gdrive/callback'
GDRIVE_STATE_SCOPE = 'auto_backup.gdrive_state'
# S3 delete_objects accepts at most 1,000 keys per request.
S3_DELETE_BATCH = 1000


def execute(connector, method, *args):
    res = False
    try:
        res = getattr(connector, method)(*args)
    except socket.error as error:
        _logger.critical('Error while executing the method "execute". Error: ' + str(error))
        raise error
    return res


def exec_pg_environ():
    env = os.environ.copy()
    config = odoo.tools.config
    pg_env_keys = {
        'db_host': 'PGHOST',
        'db_port': 'PGPORT',
        'db_user': 'PGUSER',
        'db_password': 'PGPASSWORD',
    }
    for config_key, env_key in pg_env_keys.items():
        value = config.get(config_key)
        if value:
            env[env_key] = str(value)
    return env


class AutoDatabaseBackupStatus(models.Model):
    _name = 'auto.database.backup.status'
    _description = 'Auto Database Backup Status'

    name = fields.Char("Status")
    date = fields.Datetime("Date")


class AutoDatabaseBackup(models.Model):
    _name = 'auto.database.backup'
    _description = 'Auto Database Backup configuration'

    bkup_email = fields.Char("Successful Backup Notification Email")
    bkup_fail_email = fields.Char("Failed Backup Notification Email")
    autoremove = fields.Boolean('Auto. Remove Backups',
                                help='If you check this option you can choose to automatically remove the backup '
                                     'after xx days')
    days_to_keep = fields.Integer('Remove after x days',
                                  help="Choose after how many days the backup should be deleted. For example:\n"
                                       "If you fill in 5 the backups will be removed after 5 days.",
                                  )
    name = fields.Char("Filename")
    bkpu_rules = fields.One2many('database.backup', 'backup_id', "Auto Database Backup Rules")


class DatabaseBackup(models.Model):
    _name = 'database.backup'
    _description = 'Auto Database Backup Rules'

    def _get_abs_file_path(self):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__))).split('/')
        p1 = path[: len(path) - 3]
        p2 = "/".join(p1)
        dirlist = [(os.path.join(p2, filename), os.path.join(p2, filename)) for filename in os.listdir(p2) if
                   os.path.isdir(os.path.join(p2, filename))]
        return dirlist

    def _get_abs_file_path2(self):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__))).split('/')
        p1 = path[: len(path) - 3]
        p2 = "/".join(p1)
        dirlist = [(os.path.join(p2, filename), os.path.join(p2, filename)) for filename in os.listdir(p2) if
                   os.path.isdir(os.path.join(p2, filename))]
        return dirlist

        # Columns for local server configuration

    backup_id = fields.Many2one("auto.database.backup", "Auto Backup")
    is_active = fields.Boolean("Active", )
    interval_number = fields.Integer("Interval Number", default=1, )
    interval_type = fields.Selection([('minutes', 'Minutes'),
                                      ('hours', 'Hours'),
                                      ('days', 'Days'),
                                      ('weeks', 'Weeks'),
                                      ('months', 'Months')], string='Interval Unit', default='days')
    backup_type = fields.Selection([('zip', 'Zip'), ('dump', 'Dump')], 'Backup Type', required=True, default='zip')
    backup_destination = fields.Selection([('folder', 'Folder'), ('g_drive', 'Google Drive'),
                                           ('dropbox', 'Dropbox'), ('ftp', 'FTP'), ('sftp', 'SFTP'),
                                           ('AWSs3', 'AWS S3')], 'Backup Destination', readonly=True, default='folder')
    next_exec_dt = fields.Datetime("Next Excecution Date", default=fields.Datetime.now, required=True, )
    backup = fields.Selection([('db_only', 'Database Only'), ('db_and_files', 'Database and Files')], 'Backup',
                              default='db_only')
    files_path = fields.Selection(selection=_get_abs_file_path, string='Files Path',
                                  help="Mention files path for the files, you want to take backup.")
    folder = fields.Selection(selection=_get_abs_file_path2, string='Backup Directory',
                              help='Absolute path for storing the backups')
    foldername = fields.Char("Foldername", help='Foldername for storing the backups', default="Backups")

    # fields for Google Drive uploads
    google_drive_uri = fields.Char(compute='_compute_drive_uri', string='URI',
                                   help="The URL to generate the authorization code from Google")
    google_drive_authorization_code = fields.Char(
        string='Google Authorization Code', groups='base.group_system',
        help="Filled in by itself when Google returns to this server (a Web application OAuth "
             "client). With a Desktop client Google opens http://localhost/?code=... instead: copy "
             "that whole address, paste it here and Save.")
    google_drive_redirect_uri = fields.Char(
        string='Redirect URI', compute='_compute_gdrive_redirect_uri',
        help="Add this address to the OAuth client's Authorized redirect URIs in Google Cloud "
             "Console.")
    google_drive_connected = fields.Boolean(
        string='Google Drive Connected', compute='_compute_google_drive_connected',
        help="Ticked once a pasted authorization code has been swapped for a refresh token.")
    google_drive_folder = fields.Char(
        string='Drive Folder',
        help="Where the backups are put in Google Drive: a folder name (Lab "
             "Backups), a path (Lab/Backups), the folder's id, or the address of "
             "the folder in Drive. Created if it is not there yet. Leave empty for "
             "the root of the Drive.")
    google_drive_refresh_token = fields.Text(string='google Drive Refresh Token', groups='base.group_system')
    google_drive_authorization_code_old = fields.Char(string='Old Google Authorization Code', groups='base.group_system')
    is_cred_avail = fields.Boolean("Credentials Available??", compute="get_is_cred_avail")

    # dropbox fields
    d_app_key = fields.Char("App key")
    d_app_secret = fields.Char("App secret", groups='base.group_system')
    dropbox_uri = fields.Char(string='Dropbox URI', help="The URL to generate the authorization code from Dropbox", )
    dropbox_authorization_code = fields.Char(string='Dropbox Authorization Code', groups='base.group_system')
    dropbox_token = fields.Text(string='Access Token', groups='base.group_system')
    dropbox_authorization_code_old = fields.Char(string='Old Dropbox Authorization Code', groups='base.group_system')
    dropbox_code_verifier = fields.Char("Dropbox Code Verifier", groups='base.group_system')
    dropbox_refresh_token = fields.Text(string='Dropbox Refresh Token', groups='base.group_system')
    # FTP fields
    ftp_address = fields.Char('FTP Address',
                              help='The IP address from your remote server. For example 192.168.0.1')
    ftp_port = fields.Integer('FTP Port', help='The port on the FTP server that accepts SSH/SFTP calls.')
    ftp_usrnm = fields.Char('FTP Username',
                            help='The username where the FTP connection should be made with. This is the user on the '
                                 'external server.')
    ftp_pwd = fields.Char('FTP Password',
                          help='The password from the user where the FTP connection should be made with. This '
                               'is the password from the user on the external server.',
                          groups='base.group_system')
    ftp_path = fields.Char('FTP Path',
                           help='The location to the folder where the dumps should be written to. For example '
                                '/odoo/backups/.\nFiles will then be written to /odoo/backups/ on your remote server.')
    # SFTP fields
    sftp_host = fields.Char('SFTP Host',
                            help='The IP address from your remote server. For example 192.168.0.1')
    sftp_user = fields.Char('SFTP User',
                            help='The username where the SFTP connection should be made with. This is the user on the '
                                 'external server.')
    sftp_keyfilepath = fields.Char("SFTP Key File Path(Use .pem File)",
                                   help='Add file path where key file for SFTP connection is present.',
                                   groups='base.group_system')
    sftp_file_path = fields.Char('SFTP Path',
                                 help='The location to the folder where the dumps should be written to. For example '
                                      '/odoo/backups/.\nFiles will then be written to /odoo/backups/ on your remote '
                                      'server.')
    upload_file = fields.Binary(string="Upload File", groups='base.group_system')
    file_name = fields.Char(string="File Name")
    is_pem_file_avail = fields.Boolean(".pem File Available??")
    sftp_port = fields.Integer('SFTP Port', help='The port on the FTP server that accepts SSH/SFTP calls.')
    # AWS S3
    s3_app_key_id = fields.Char("AWS S3 app key", groups='base.group_system')
    s3_secret_key_id = fields.Char("AWS S3 secret key", groups='base.group_system')
    s3_bucket_name = fields.Char("Bucket Name")

    def get_gdrive_auth_code(self):
        print()

    def _backup_key_dir(self):
        """The private folder uploaded SFTP keys are kept in: <data_dir>/backup_keys/<database>."""
        base_dir = os.path.join(odoo.tools.config['data_dir'], 'backup_keys')
        key_dir = os.path.join(base_dir, self.env.cr.dbname)
        for directory in (base_dir, key_dir):
            os.makedirs(directory, mode=0o700, exist_ok=True)
            os.chmod(directory, 0o700)
        return key_dir

    @api.onchange('upload_file')
    def onchange_upload_file(self):
        if not self.upload_file:
            return
        # The key used to be written to <addons parent dir>/<file name>, with the file name as the
        # browser sent it - so "../../anything" wrote wherever the server user could, beside the
        # code. It now goes into a private folder under the data dir, under its bare name, 0600.
        file_name = os.path.basename((self.file_name or '').replace('\\', '/'))
        if file_name in ('', '.', '..'):
            file_name = 'sftp_key.pem'
        key_dir = False
        try:
            key_dir = self._backup_key_dir()
            path1 = os.path.join(key_dir, file_name)
            content = base64.b64decode(self.upload_file)
            fd = os.open(path1, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, 'O_NOFOLLOW', 0), 0o600)
            with os.fdopen(fd, 'wb') as key_file:
                key_file.write(content)
            os.chmod(path1, 0o600)
            self.sftp_keyfilepath = path1
        except Exception as e:
            msg = str(e)
            if "Permission denied" in msg:
                msg = "Please give write permission to Directory: %s" % (key_dir or odoo.tools.config['data_dir'],)
            raise UserError(_(msg))

    def _get_cron_xmlids(self):
        return {
            'folder': 'auto_odoo_db_and_file_backup.auto_db_backup_scheduler',
            'g_drive': 'auto_odoo_db_and_file_backup.auto_db_backup_scheduler_Gdrive',
            'dropbox': 'auto_odoo_db_and_file_backup.auto_db_backup_scheduler_dropbox',
            'ftp': 'auto_odoo_db_and_file_backup.auto_db_backup_scheduler_ftp',
            'sftp': 'auto_odoo_db_and_file_backup.auto_db_backup_scheduler_sftp',
            'AWSs3': 'auto_odoo_db_and_file_backup.auto_db_backup_scheduler_AWSs3',
        }

    def _get_cron(self):
        self.ensure_one()
        xmlid = self._get_cron_xmlids().get(self.backup_destination)
        return self.env.ref(xmlid, raise_if_not_found=False) if xmlid else self.env['ir.cron']

    def _backup_tz(self):
        # The scheduled backup runs as a user with no timezone - and so do most users
        # here - and pytz.timezone(False) crashed every run before a dump was taken.
        # Fall back to the company's zone, then the lab's own.
        return (self.env.context.get('tz') or self.env.user.tz
                or self.env.company.partner_id.tz or 'Asia/Kolkata')

    def _get_next_execution_datetime(self):
        self.ensure_one()
        unit = self.interval_number or 1
        return fields.Datetime.now() + _intervalTypes[self.interval_type or 'days'](unit)

    def _sync_cron_from_rule(self, vals=None):
        for rec in self:
            cron = rec._get_cron()
            if not cron:
                continue
            cron_vals = {}
            source = vals or {}
            if 'is_active' in source:
                cron_vals['active'] = source.get('is_active')
            if 'interval_number' in source:
                cron_vals['interval_number'] = source.get('interval_number')
            if 'interval_type' in source:
                cron_vals['interval_type'] = source.get('interval_type')
            if 'next_exec_dt' in source:
                cron_vals['nextcall'] = source.get('next_exec_dt')
            if cron_vals:
                cron = cron.sudo()
                # A running cron is locked by the scheduler (or by "Run Manually"), and
                # Odoo 19 refuses to write it: "This cron task is currently being
                # executed". The usual writer is that very job - every scheduled backup
                # moves the rule's next date as it starts - and the scheduler sets the
                # cron's next call itself when the job ends, from the same interval. So
                # the next call is left to it; only real settings changes must wait.
                if not cron.try_lock_for_update(allow_referencing=True):
                    cron_vals.pop('nextcall', None)
                    if not cron_vals:
                        continue
                    raise UserError(_(
                        "The %s backup is running right now. Save these settings again "
                        "once it has finished.") % cron.cron_name)
                cron.write(cron_vals)

    def change_nextcall_datetime(self, rec):
        for rule in rec:
            rule.write({'next_exec_dt': rule._get_next_execution_datetime()})

    def _backup_name_patterns(self):
        """The file names this rule's backups are written under (see BACKUP_STAMP_RE)."""
        self.ensure_one()
        patterns = [re.compile(r'^%s_%s\.(zip|dump)$' % (re.escape(self.env.cr.dbname), BACKUP_STAMP_RE))]
        if self.backup == 'db_and_files' and self.files_path:
            fpath = self.files_path.split('/')[-1]
            patterns.append(re.compile(r'^%s_%s\.zip$' % (re.escape(fpath), BACKUP_STAMP_RE)))
        return patterns

    def _is_own_backup_name(self, name):
        """Whether a bare file name (no directory) is one of this rule's own backups."""
        self.ensure_one()
        return any(pattern.match(name or '') for pattern in self._backup_name_patterns())

    def _zip_files_path(self, target):
        """Archive the rule's files path into `target`, read straight from disk.

        The archive holds <files folder>/..., as the copy-then-zip it replaces did, without first
        copying the whole folder into a temporary directory that was never removed."""
        self.ensure_one()
        if self.files_path and os.path.isdir(self.files_path):
            odoo.tools.osutil.zip_dir(self.files_path, target, include_dir=True, fnct_sort=None)
        else:
            # A files path that has gone gave an empty archive before; it still does.
            with zipfile.ZipFile(target, 'w'):
                pass

    def _remove_backup_temp_files(self, *paths):
        for path in paths:
            if path and os.path.isfile(path):
                try:
                    os.remove(path)
                except OSError:
                    _logger.warning("Could not remove temporary backup file %s", path, exc_info=True)

    def _load_sftp_private_key(self, key_path):
        key_classes = [
            getattr(paramiko, 'RSAKey', None),
            getattr(paramiko, 'ECDSAKey', None),
            getattr(paramiko, 'Ed25519Key', None),
            getattr(paramiko, 'DSSKey', None),
        ]
        errors = []
        for key_class in [key_class for key_class in key_classes if key_class]:
            try:
                return key_class.from_private_key_file(key_path)
            except paramiko.ssh_exception.PasswordRequiredException:
                raise UserError(_(
                    "The SFTP private key is encrypted with a passphrase. "
                    "Upload an unencrypted private key or add passphrase support before testing the connection."
                ))
            except paramiko.ssh_exception.SSHException as error:
                errors.append(str(error))

        try:
            with open(key_path, 'r', encoding='utf-8') as key_file:
                key_text = key_file.read().strip()
        except Exception:
            key_text = ''
        if key_text.startswith('ssh-'):
            raise UserError(_(
                "The uploaded SFTP file is a public key, not a private key. "
                "Upload the matching private key file, usually starting with "
                "'-----BEGIN OPENSSH PRIVATE KEY-----' or '-----BEGIN RSA PRIVATE KEY-----'."
            ))
        if key_text.startswith('PuTTY-User-Key-File'):
            raise UserError(_(
                "The uploaded SFTP key is a PuTTY .ppk file. Convert it to OpenSSH private key format first, "
                "then upload the converted private key."
            ))
        raise UserError(_(
            "The uploaded SFTP private key could not be read by Paramiko. "
            "Use an unencrypted OpenSSH private key in RSA, ECDSA, or Ed25519 format. Details: %s"
        ) % "; ".join([error for error in errors if error]))

    def _get_sftp_public_key_line(self, rec):
        pkey = rec._load_sftp_private_key(rec.sftp_keyfilepath)
        return "%s %s" % (pkey.get_name(), pkey.get_base64())

    def _get_sftp_key_fingerprint(self, rec):
        pkey = rec._load_sftp_private_key(rec.sftp_keyfilepath)
        return pkey.get_fingerprint().hex()

    def _open_sftp_connection(self, rec):
        port = rec.sftp_port or 22
        connect_kwargs = {
            'hostname': rec.sftp_host,
            'port': port,
            'username': rec.sftp_user,
            'timeout': 10,
            'banner_timeout': 10,
            'auth_timeout': 10,
            'allow_agent': False,
            'look_for_keys': False,
        }
        attempts = [{}]
        if rec.is_pem_file_avail:
            pkey = rec._load_sftp_private_key(rec.sftp_keyfilepath)
            connect_kwargs['pkey'] = pkey
            if pkey.get_name() == 'ssh-rsa':
                attempts.extend([
                    {'disabled_algorithms': {'pubkeys': ['rsa-sha2-512', 'rsa-sha2-256']}},
                    {'disabled_algorithms': {'pubkeys': ['ssh-rsa']}},
                ])
        else:
            connect_kwargs['password'] = rec.sftp_keyfilepath

        last_error = False
        for attempt in attempts:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            kwargs = connect_kwargs.copy()
            kwargs.update(attempt)
            try:
                client.connect(**kwargs)
                return client, client.open_sftp()
            except paramiko.ssh_exception.AuthenticationException as error:
                last_error = error
                client.close()
            except TypeError as error:
                last_error = error
                client.close()
                if 'disabled_algorithms' not in str(error):
                    raise
            except Exception:
                client.close()
                raise
        raise last_error or UserError(_("SFTP authentication failed."))

    def _format_sftp_connection_error(self, rec, error):
        if isinstance(error, paramiko.ssh_exception.AuthenticationException):
            if rec.is_pem_file_avail:
                try:
                    public_key_line = rec._get_sftp_public_key_line(rec)
                    fingerprint = rec._get_sftp_key_fingerprint(rec)
                except Exception:
                    public_key_line = ""
                    fingerprint = ""
                return _(
                    "The private key was read, but the SFTP server rejected it for user '%s'. "
                    "Add the public key below to ~%s/.ssh/authorized_keys on the SFTP server, "
                    "then check remote ownership/permissions. Key fingerprint: %s\n\n%s"
                ) % (rec.sftp_user, rec.sftp_user, fingerprint, public_key_line)
            return _("The SFTP server rejected the password for user '%s'.") % rec.sftp_user
        return str(error)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('google_drive_authorization_code'):
                vals['google_drive_authorization_code'] = self._clean_gdrive_code(vals['google_drive_authorization_code'])
        records = super(DatabaseBackup, self).create(vals_list)
        records._setup_gdrive_token()
        return records

    def write(self, vals):
        if vals.get('google_drive_authorization_code'):
            vals = dict(vals, google_drive_authorization_code=self._clean_gdrive_code(vals['google_drive_authorization_code']))
        result = super(DatabaseBackup, self).write(vals)
        if vals.get('google_drive_authorization_code'):
            self._setup_gdrive_token()
        self._sync_cron_from_rule(vals)
        return result

    def trigger_direct(self):
        backup_destination = self.env.context.get('backup_destination')
        actid = self.env.context.get('id')
        rec = self.env['database.backup'].browse(actid) if actid else self
        if backup_destination == 'folder':
            cron_id = self.env.ref('auto_odoo_db_and_file_backup.auto_db_backup_scheduler')
            IrCron = self.env['ir.cron'].browse(cron_id.id)
            IrCron.with_user(IrCron.sudo().user_id).ir_actions_server_id.run()
            self.change_nextcall_datetime(rec)
        elif backup_destination == 'g_drive':
            cron_id = self.env.ref('auto_odoo_db_and_file_backup.auto_db_backup_scheduler_Gdrive')
            IrCron = self.env['ir.cron'].browse(cron_id.id)
            IrCron.with_user(IrCron.sudo().user_id).ir_actions_server_id.run()
            self.change_nextcall_datetime(rec)
        elif backup_destination == 'dropbox':
            cron_id = self.env.ref('auto_odoo_db_and_file_backup.auto_db_backup_scheduler_dropbox')
            IrCron = self.env['ir.cron'].browse(cron_id.id)
            IrCron.with_user(IrCron.sudo().user_id).ir_actions_server_id.run()
            self.change_nextcall_datetime(rec)
        elif backup_destination == 'ftp':
            cron_id = self.env.ref('auto_odoo_db_and_file_backup.auto_db_backup_scheduler_ftp')
            IrCron = self.env['ir.cron'].browse(cron_id.id)
            IrCron.with_user(IrCron.sudo().user_id).ir_actions_server_id.run()
            self.change_nextcall_datetime(rec)
        elif backup_destination == 'sftp':
            cron_id = self.env.ref('auto_odoo_db_and_file_backup.auto_db_backup_scheduler_sftp')
            IrCron = self.env['ir.cron'].browse(cron_id.id)
            IrCron.with_user(IrCron.sudo().user_id).ir_actions_server_id.run()
            self.change_nextcall_datetime(rec)
        else:
            cron_id = self.env.ref('auto_odoo_db_and_file_backup.auto_db_backup_scheduler_AWSs3')
            IrCron = self.env['ir.cron'].browse(cron_id.id)
            IrCron.with_user(IrCron.sudo().user_id).ir_actions_server_id.run()
            self.change_nextcall_datetime(rec)
        return True

    def test_sftp_connection(self, context=None):
        self.ensure_one()

        # Check if there is a success or fail and write messages
        message_title = ""
        message_content = ""
        error = ""
        has_failed = False

        for rec in self:
            ip_host = rec.sftp_host
            s = False

            # Connect with external server over SFTP, so we know sure that everything works.
            try:
                s, sftp = rec._open_sftp_connection(rec)
                sftp.close()
                message_title = _("Connection Test Succeeded!\nEverything seems properly set up for SFTP back-ups!")
            except Exception as e:
                error_msg = rec._format_sftp_connection_error(rec, e)
                _logger.critical('There was a problem connecting to the remote sftp: %s', error_msg)
                error += error_msg
                has_failed = True
                message_title = _("Connection Test Failed!")
                if ip_host and len(ip_host) < 8:
                    message_content += "\nYour IP address seems to be too short.\n"
                message_content += _("Here is what we got instead:\n")
            finally:
                if s:
                    s.close()

        if has_failed:
            raise UserError(message_title + '\n\n' + message_content + "%s" % str(error))
        else:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'message': message_title,
                    'type': 'success',
                    'sticky': False,
                }
            }

    def test_ftp_connection(self, context=None):
        self.ensure_one()

        # Check if there is a success or fail and write messages
        message_title = ""
        message_content = ""
        error = ""
        has_failed = False

        for rec in self:
            ip_host = rec.ftp_address
            port_host = rec.ftp_port
            username_login = rec.ftp_usrnm
            password_login = rec.ftp_pwd

            # Connect with external server over SFTP, so we know sure that everything works.
            try:
                server = ftplib.FTP()
                server.connect(ip_host, port_host)
                server.login(username_login, password_login)
                message_title = _("Connection Test Succeeded!\nEverything seems properly set up for FTP back-ups!")
            except Exception as e:
                _logger.critical('There was a problem connecting to the remote ftp: ' + str(e))
                error += str(e)
                has_failed = True
                message_title = _("Connection Test Failed!")
                if len(rec.ftp_address) < 8:
                    message_content += "\nYour IP address seems to be too short.\n"
                message_content += _("Here is what we got instead:\n")
            finally:
                if server:
                    server.close()

        if has_failed:
            raise UserError(message_title + '\n\n' + message_content + "%s" % str(error))
        else:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'message': message_title,
                    'type': 'success',
                    'sticky': False,
                }
            }

    def get_auth_code(self):
        if not self.d_app_key:
            raise UserError(_("App Key is required"))
        auth_flow = DropboxOAuth2FlowNoRedirect(self.d_app_key, use_pkce=True, token_access_type='offline')
        authorize_url = "https://www.dropbox.com/oauth2/authorize?response_type=code&client_id=%s&token_access_type=offline&code_challenge=%s&code_challenge_method=S256" % (
            self.d_app_key, auth_flow.code_challenge,)
        self.write({'dropbox_code_verifier': auth_flow.code_verifier, 'dropbox_uri': authorize_url})
        ctx = {'default_dropbox_uri': authorize_url, 'backup_id': self.id}
        return {
            'view_mode': 'form',
            'res_model': 'dropbox.auth.refresh.token.wiz',
            'view_type': 'form',
            'type': 'ir.actions.act_window',
            'context': ctx,
            'target': 'new',
        }

    def get_auth_refresh_token(self):
        conn = http.client.HTTPSConnection("api.dropbox.com")
        payload = "grant_type=authorization_code&client_id=%s&code_verifier=%s&code=%s" % (
            self.d_app_key, self.dropbox_code_verifier, self.dropbox_authorization_code,)
        headers = {'Accept': "application/json", 'content-type': "application/x-www-form-urlencoded"}
        conn.request("POST", "/oauth2/token", payload, headers)
        res = conn.getresponse()
        data = res.read()
        data = json.loads(data.decode("utf-8"))
        self.dropbox_refresh_token = data.get('refresh_token')

    @api.model
    def _clean_gdrive_code(self, value):
        """The authorization code as Google issued it.

        With the http://localhost redirect Google sends the browser to
        http://localhost/?code=4%2F0A...&scope=..., a page that does not load, so what gets
        pasted is often that whole address, or the code still URL-encoded (4%2F0A...).
        Google refuses both, so take the code parameter out and decode it.
        """
        value = (value or '').strip()
        if not value:
            return value
        if 'code=' in value:
            query = urlparse(value).query or value.split('?', 1)[-1]
            value = (parse_qs(query).get('code') or [value])[0]
        return unquote(value)

    def _setup_gdrive_token(self):
        """Swap a newly saved authorization code for a refresh token, on save.

        This used to run as an onchange writing a readonly field the form keeps hidden, and
        the web client does not send such a value on save - so the token was thrown away and
        every backup stopped with "refresh token is missing". A code is single-use, so it is
        swapped exactly once, here, and a code already swapped is never sent to Google again.
        (client, 2026-09-15)
        """
        for rec in self.sudo():
            code = rec.google_drive_authorization_code
            if not code:
                continue
            if rec.google_drive_refresh_token and code == rec.google_drive_authorization_code_old:
                continue
            refresh_token = rec._generate_gdrive_refresh_token(code)
            super(DatabaseBackup, rec).write({
                'google_drive_refresh_token': refresh_token,
                'google_drive_authorization_code_old': code,
            })

    @api.depends('google_drive_refresh_token')
    def _compute_google_drive_connected(self):
        for rec in self:
            rec.google_drive_connected = bool(rec.sudo().google_drive_refresh_token)

    @api.onchange('dropbox_authorization_code', 'd_app_key', 'd_app_secret')
    def action_setup_dropbox_token(self):
        for rec in self:
            if rec.d_app_key and rec.d_app_secret and rec.dropbox_authorization_code:
                if not rec.dropbox_token:
                    try:
                        token_url = "https://api.dropbox.com/oauth2/token"
                        params = {
                            "code": rec.dropbox_authorization_code,
                            "grant_type": "authorization_code",
                            "client_id": rec.d_app_key,
                            "client_secret": rec.d_app_secret
                        }
                        r = requests.post(token_url, data=params)
                        response = json.loads(r.text)
                        self.write({'dropbox_authorization_code_old': rec.dropbox_authorization_code})
                        rec.dropbox_token = response.get('access_token')
                    except Exception as e:
                        _logger.debug(e)
                        raise UserError(str(e))
                else:
                    if rec.dropbox_authorization_code != rec.dropbox_authorization_code_old:
                        try:
                            token_url = "https://api.dropbox.com/oauth2/token"
                            params = {
                                "code": rec.dropbox_authorization_code,
                                "grant_type": "authorization_code",
                                "client_id": rec.d_app_key,
                                "client_secret": rec.d_app_secret
                            }
                            r = requests.post(token_url, data=params)
                            response = json.loads(r.text)
                            self.write({'dropbox_authorization_code_old': rec.dropbox_authorization_code})
                            self.dropbox_token = response.get('access_token')
                        except Exception as e:
                            _logger.debug(e)
                            raise UserError(str(e))
                    else:
                        rec.dropbox_token = rec.dropbox_token
            else:
                rec.dropbox_token = rec.dropbox_token

    @api.depends('google_drive_authorization_code')
    def _compute_drive_uri(self):
        for config in self:
            config.google_drive_uri = config._get_gdrive_auth_url()

    def send_success_mail_notificaton(self, rec, bkp_file, bkp_folder):
        email_to = rec.backup_id.bkup_email
        BackupType = dict(self._fields['backup_type'].selection)
        BackupDest = dict(self._fields['backup_destination'].selection)
        if rec.backup_destination == 'folder':
            notification_template = self.env.ref('auto_odoo_db_and_file_backup.email_local_upload')
            folder = rec.folder
            if rec.folder.endswith('/'):
                folder = rec.folder.rstrip('/')
            submsg1 = folder + "/" + rec.foldername
            submsg2 = ""
            subject = "Folder Upload Successful"
        if rec.backup_destination == 'g_drive':
            notification_template = self.env.ref('auto_odoo_db_and_file_backup.email_google_drive_upload')

            submsg1 = "<a href='https://drive.google.com/drive/my-drive'>https://drive.google.com/drive/my-drive</a>"
            submsg2 = ""
            subject = "Google Drive Upload Successful"
        if rec.backup_destination == 'dropbox':
            notification_template = self.env.ref('auto_odoo_db_and_file_backup.email_dropbox_upload')
            submsg1 = "<a href='https://www.dropbox.com/home?preview=%s'>https://www.dropbox.com/home?preview=%s</a>" % (
                bkp_file, bkp_file)
            submsg2 = "<a href='https://www.dropbox.com/home?preview=%s'>https://www.dropbox.com/home?preview=%s</a>" % (
                bkp_folder, bkp_folder)
            subject = "Dropbox Upload Successful"
        if rec.backup_destination == 'ftp':
            notification_template = self.env.ref('auto_odoo_db_and_file_backup.email_ftp_upload')
            submsg1 = rec.ftp_path
            submsg2 = ""
            subject = "FTP Upload Successful"
        if rec.backup_destination == 'sftp':
            notification_template = self.env.ref('auto_odoo_db_and_file_backup.email_sftp_upload')
            submsg1 = rec.sftp_file_path
            submsg2 = ""
            subject = "SFTP Upload Successful"

        if rec.backup_destination == 'AWSs3':
            notification_template = self.env.ref('auto_odoo_db_and_file_backup.email_AWSs3_upload')
            submsg1 = "In Bucket " + rec.s3_bucket_name
            submsg2 = ""
            subject = "AWS S3 Upload Successful"

        if rec.backup == 'db_only':
            msg = "<h3>Backup Successfully Created!</h3>" \
                  "Please see below details. <br/> <br/> " \
                  "<p>Backup : Database Only </p>" \
                  "<p>Backup Type : %s" % (str(BackupType.get(rec.backup_type))) + "</p>" \
                                                                                   "<p>Backup Destination : %s" % (
                      str(BackupDest.get(rec.backup_destination))) + "</p>" \
                                                                     "<p>Backup Directory : %s" % (
                      str(submsg1)) + "</p>" \
                                      "<p>Filename : %s" % (str(bkp_file)) + "</p>"
        else:
            msg = "<h3>Backup Successfully Created!</h3>" \
                  "Please see below details. <br/> <br/> " \
                  "<p>Backup : Database and Files</p>" \
                  "<p>Backup Type : %s" % (str(BackupType.get(rec.backup_type))) + "</p>" \
                                                                                   "<p>Backup Destination : %s" % (
                      str(BackupDest.get(rec.backup_destination))) + "</p>" \
                                                                     "<p>Backup Directory : %s" % (
                      str(submsg1)) + "<br/>" + (str(submsg2)) + "</p>" \
                                                                 "<p>DB Filename : %s" % (str(bkp_file)) + "</p>" \
                                                                                                           "<p>Files : %s" % (
                      str(bkp_folder)) + "</p>"
        email_values = {
            'email_from': self.env['res.users'].browse(self.env.uid).company_id.email,
            'email_to': email_to, 'subject': subject, 'body_html': msg,
        }
        notification_template.send_mail(rec.id, force_send=True, email_values=email_values)

    def send_fail_mail_notificaton(self, rec, bkp_file, bkp_folder, error):
        email_to = rec.backup_id.bkup_fail_email

        if rec.backup_destination == 'folder':
            notification_template = self.env.ref('auto_odoo_db_and_file_backup.email_local_upload')
            subject = "Folder Upload Failed"
        if rec.backup_destination == 'g_drive':
            notification_template = self.env.ref('auto_odoo_db_and_file_backup.email_google_drive_upload')
            subject = "Google Drive Upload Failed"
        if rec.backup_destination == 'dropbox':
            notification_template = self.env.ref('auto_odoo_db_and_file_backup.email_dropbox_upload')
            subject = "Dropbox Upload Failed"
        if rec.backup_destination == 'ftp':
            notification_template = self.env.ref('auto_odoo_db_and_file_backup.email_ftp_upload')
            subject = "FTP Upload Failed"
        if rec.backup_destination == 'sftp':
            notification_template = self.env.ref('auto_odoo_db_and_file_backup.email_sftp_upload')
            subject = "SFTP Upload Failed"
        if rec.backup_destination == 'AWSs3':
            notification_template = self.env.ref('auto_odoo_db_and_file_backup.email_AWSs3_upload')
            subject = "AWS S3 Upload Failed"

        if rec.backup == 'db_only':
            msg = "<h3>Backup Upload Failed!</h3>" \
                  "Please see below details. <br/> <br/> " \
                  "<table style='width:100%'>" \
                  "<tr> " \
                  "<th align='left'>Backup</th>" \
                  "<td>" + (str(bkp_file)) + "</td></tr>" \
                                             "<tr> " \
                                             "<th align='left'>Error: </th>" \
                                             "<td>" + str(error) + "</td>" \
                                                                   "</tr>" \
                                                                   "</table>"
        else:
            msg = "<h3>Backup Upload Failed!</h3>" \
                  "Please see below details. <br/> <br/> " \
                  "<table style='width:100%'>" \
                  "<tr> " \
                  "<th align='left'>Backup</th>" \
                  "<td>" + (str(bkp_file)) + (str(bkp_folder)) + "</td></tr>" \
                                                                 "<tr> " \
                                                                 "<th align='left'>Error: </th>" \
                                                                 "<td>" + str(error) + "</td>" \
                                                                                       "</tr>" \
                                                                                       "</table>"
        email_values = {
            'email_from': self.env['res.users'].browse(self.env.uid).company_id.email,
            'email_to': email_to, 'subject': subject, 'body_html': msg,
        }
        notification_template.send_mail(rec.id, force_send=True, email_values=email_values)

    @api.model
    def schedule_auto_db_backup(self):
        # One pass per active rule: two active folder rules used to crash the whole job on a
        # singleton error before either was backed up.
        for conf_id in self.search([('is_active', '=', True), ('backup_destination', '=', 'folder')]):
            conf_id._backup_to_folder()

    def _backup_to_folder(self):
        conf_id = self
        conf_id.ensure_one()
        conf_id.change_nextcall_datetime(conf_id)
        user_tz = pytz.timezone(self._backup_tz())
        date_today = pytz.utc.localize(datetime.datetime.today()).astimezone(user_tz)
        StatusObj = self.env['auto.database.backup.status']
        # Defined before the try: the except block reports them, and an unbound name there raised
        # again - which rolled the "Failed" status back along with the cron.
        Folder_Path = ""
        bkp_file = ""
        bkp_folder = ""
        file_path = ""
        bkp_folder_path = ""
        written = False
        try:
            folder = conf_id.folder
            if conf_id.folder.endswith('/'):
                folder = conf_id.folder.rstrip('/')
            Folder_Path = folder + "/" + conf_id.foldername
            if not os.path.isdir(Folder_Path):
                os.makedirs(Folder_Path)
            # Create name for dumpfile.
            bkp_file = '%s_%s.%s' % (
                self.env.cr.dbname, date_today.strftime('%Y-%m-%d_%H_%M_%S'), conf_id.backup_type)
            file_path = os.path.join(Folder_Path, bkp_file)
            # try to backup database and write it away
            with open(file_path, 'wb') as fp:
                conf_id._take_dump(self.env.cr.dbname, fp, 'database.backup', conf_id.backup_destination,
                                   conf_id.backup_type)
            if conf_id.backup == 'db_and_files':
                fpath = conf_id.files_path.split('/')[-1]
                bkp_folder = '%s_%s.%s' % (fpath, date_today.strftime('%Y-%m-%d_%H_%M_%S'), "zip")
                bkp_folder_path = os.path.join(Folder_Path, bkp_folder)
                conf_id._zip_files_path(bkp_folder_path)
            written = True

            _logger.info("Backup Successfully Uploaded to Local.")
            StatusObj.create({'date': datetime.datetime.today(), 'name': "Local: Success"})
            if conf_id.backup_id.bkup_email:
                conf_id.send_success_mail_notificaton(conf_id, bkp_file, bkp_folder)

        except Exception as error:
            _logger.exception("Couldn't back up database %s to a folder", self.env.cr.dbname)
            if not written:
                # A half-written dump left in the backup folder looks like a backup.
                conf_id._remove_backup_temp_files(file_path, bkp_folder_path)
            StatusObj.create({'date': datetime.datetime.today(), 'name': "Failed (Error: %s)" % (str(error))})
            if conf_id.backup_id.bkup_fail_email:
                conf_id.send_fail_mail_notificaton(conf_id, bkp_file, bkp_folder, error)
        """
        Remove all old files (on local server) in case this is configured..
        """
        if conf_id.backup_id.autoremove and Folder_Path and os.path.isdir(Folder_Path):
            now = datetime.datetime.now()
            for f in os.listdir(Folder_Path):
                fullpath = os.path.join(Folder_Path, f)
                # Only this rule's own backups, matched on the whole file name. The database name
                # used to be looked for anywhere in the full path - directory included - so a
                # backup folder named after the database emptied every other database's backups.
                if not os.path.isfile(fullpath) or not conf_id._is_own_backup_name(f):
                    continue
                createtime = datetime.datetime.fromtimestamp(os.path.getmtime(fullpath))
                delta = now.date() - createtime.date()
                if delta.days >= conf_id.backup_id.days_to_keep:
                    _logger.info("Delete local out-of-date file: " + fullpath)
                    os.remove(fullpath)

    def get_content_files(self, rec):
        """Dump the database (and archive the files path) into temporary files.

        Returns the file names and paths; the caller uploads from the paths and must remove them
        (_remove_backup_temp_files). The two content slots are kept for the callers' signature but
        are always None: the uploads read from disk, where a ~750 MB dump used to be read whole into
        memory, once more with the files archive."""
        err = ""
        user_tz = pytz.timezone(self._backup_tz())
        date_today = pytz.utc.localize(datetime.datetime.today()).astimezone(user_tz)
        patht = ""
        bkp_folder_path = ""
        try:
            # Create name for dumpfile.
            bkp_file = '%s_%s.%s' % (self.env.cr.dbname, date_today.strftime('%Y-%m-%d_%H_%M_%S'), rec.backup_type)

            fd, patht = tempfile.mkstemp(bkp_file)  # can use anything
            os.close(fd)
            # A failed dump used to be printed and ignored, and the empty temp file was
            # then uploaded and recorded as "Success". Let it fail the backup instead.
            rec._take_dump(self.env.cr.dbname, patht, 'database.backup', rec.backup_destination, rec.backup_type)
            if not os.path.getsize(patht):
                raise UserError(_("The database dump came out empty; nothing was uploaded."))

            bkp_folder = ""
            if rec.backup == 'db_and_files':
                fpath = rec.files_path.split('/')[-1]
                bkp_folder = '%s_%s.%s' % (fpath, date_today.strftime('%Y-%m-%d_%H_%M_%S'), "zip")
                # A temporary file, not a folder: the archive used to be built in a mkdtemp() folder
                # holding a copy of the whole files path, and neither was ever removed.
                fd, bkp_folder_path = tempfile.mkstemp(bkp_folder)
                os.close(fd)
                rec._zip_files_path(bkp_folder_path)
            return bkp_file, patht, bkp_folder, bkp_folder_path, err, datetime.datetime.today(), None, None
        except Exception as error:
            _logger.exception("Couldn't back up database %s", self.env.cr.dbname)
            rec._remove_backup_temp_files(patht, bkp_folder_path)
            return "", "", "", "", error, datetime.datetime.today(), None, None

    @api.model
    def schedule_auto_db_backup_to_Gdrive(self):
        for conf_id in self.search([('is_active', '=', True), ('backup_destination', '=', 'g_drive')]):
            conf_id.change_nextcall_datetime(conf_id)
            cred_fp = os.path.join(os.path.dirname(os.path.abspath(__file__))) + "/client_secrets.json"
            if not os.path.exists(cred_fp):
                raise UserError(
                    _("client_secrets.json does not exist. First add credentials file in path auto_odoo_db_and_file_backup/models."))

            StatusObj = self.env['auto.database.backup.status']
            bkp_file, file_path2, bkp_folder, bkp_folder_path, err, date_today, db_content, dbfile_content = conf_id.get_content_files(
                conf_id)
            try:
                if err == "":
                    status = 1
                    uploaded = conf_id.google_drive_upload(conf_id, file_path2, bkp_file, bkp_file, bkp_folder, status,
                                                           date_today, db_content, dbfile_content)
                    status = 2
                    # The files archive only follows a database upload that landed: otherwise its
                    # upload recorded "Success" for a backup whose database never arrived.
                    if uploaded and conf_id.backup == 'db_and_files':
                        time.sleep(3)
                        conf_id.google_drive_upload(conf_id, bkp_folder_path, bkp_folder, bkp_file, bkp_folder, status,
                                                    date_today, db_content, dbfile_content)
                else:
                    StatusObj.create({'date': date_today, 'name': "Failed (Error: %s)" % (str(err))})
            finally:
                conf_id._remove_backup_temp_files(file_path2, bkp_folder_path)

    @api.model
    def schedule_auto_db_backup_to_dropbox(self):
        for conf_id in self.search([('is_active', '=', True), ('backup_destination', '=', 'dropbox')]):
            conf_id.change_nextcall_datetime(conf_id)
            StatusObj = self.env['auto.database.backup.status']
            bkp_file, file_path2, bkp_folder, bkp_folder_path, err, date_today, db_content, dbfile_content = conf_id.get_content_files(
                conf_id)
            try:
                if err == "":
                    status = 1
                    uploaded = conf_id.dropbox_upload(conf_id, file_path2, bkp_file, bkp_file, bkp_folder, status,
                                                      date_today, db_content, dbfile_content)
                    status = 2
                    if uploaded and conf_id.backup == 'db_and_files':
                        time.sleep(3)
                        conf_id.dropbox_upload(conf_id, bkp_folder_path, bkp_folder, bkp_file, bkp_folder, status,
                                               date_today, db_content, dbfile_content)
                else:
                    StatusObj.create({'date': date_today, 'name': "Failed (Error: %s)" % (str(err))})
            finally:
                conf_id._remove_backup_temp_files(file_path2, bkp_folder_path)

    @api.model
    def schedule_auto_db_backup_to_ftp(self):
        for conf_id in self.search([('is_active', '=', True), ('backup_destination', '=', 'ftp')]):
            conf_id.change_nextcall_datetime(conf_id)
            bkp_file, file_path2, bkp_folder, bkp_folder_path, err, date_today, db_content, dbfile_content = conf_id.get_content_files(
                conf_id)
            try:
                conf_id.ftp_upload(conf_id, file_path2, bkp_file, bkp_folder_path, bkp_folder, err, date_today,
                                   db_content, dbfile_content)
            finally:
                conf_id._remove_backup_temp_files(file_path2, bkp_folder_path)

    @api.model
    def schedule_auto_db_backup_to_sftp(self):
        for conf_id in self.search([('is_active', '=', True), ('backup_destination', '=', 'sftp')]):
            conf_id.change_nextcall_datetime(conf_id)
            bkp_file, file_path2, bkp_folder, bkp_folder_path, err, date_today, db_content, dbfile_content = conf_id.get_content_files(
                conf_id)
            try:
                conf_id.sftp_upload(conf_id, file_path2, bkp_file, bkp_folder_path, bkp_folder, err, date_today,
                                    db_content, dbfile_content)
            finally:
                conf_id._remove_backup_temp_files(file_path2, bkp_folder_path)

    @api.model
    def schedule_auto_db_backup_to_AWSs3(self):
        for conf_id in self.search([('is_active', '=', True), ('backup_destination', '=', 'AWSs3')]):
            conf_id.change_nextcall_datetime(conf_id)
            bkp_file, file_path2, bkp_folder, bkp_folder_path, err, date_today, db_content, dbfile_content = conf_id.get_content_files(
                conf_id)
            try:
                conf_id.AWSs3_upload(conf_id, file_path2, bkp_file, bkp_folder_path, bkp_folder, err, date_today,
                                     db_content, dbfile_content)
            finally:
                conf_id._remove_backup_temp_files(file_path2, bkp_folder_path)

    def get_datetime_format(self, date_time):
        # convert to datetime object
        date_time = datetime.datetime.strptime(date_time, "%Y%m%d%H%M%S").date()
        # convert to human readable date time string
        strdt = date_time.strftime("%Y-%m-%d")
        return datetime.datetime.strptime(strdt, "%Y-%m-%d").date()

    def sftp_upload(self, rec, file_path, bkp_file, bkp_folder_path, bkp_folder, err, date_today, db_content,
                    dbfile_content):
        StatusObj = self.env['auto.database.backup.status']
        if err == "":
            s = False
            sftp = False
            try:
                s, sftp = rec._open_sftp_connection(rec)
                remote = rec.sftp_file_path
                if remote.endswith('/'):
                    # rstrip, not strip: strip also took the leading slash and turned
                    # /odoo/backups/ into a path relative to the SFTP user's home.
                    remote = remote.rstrip('/')
                remoteFilePath = remote + "/" + bkp_file
                sftp.put(file_path, remoteFilePath)
                if os.path.exists(file_path):
                    os.remove(file_path)
                if rec.backup == 'db_and_files':
                    remoteFilePath2 = remote + "/" + bkp_folder
                    sftp.put(bkp_folder_path, remoteFilePath2)
                    if os.path.exists(bkp_folder_path):
                        os.remove(bkp_folder_path)
                _logger.info("Backup Successfully Uploaded to SFTP.")
                StatusObj.create({'date': date_today, 'name': "SFTP: Success"})
                if rec.backup_id.bkup_email:
                    self.send_success_mail_notificaton(rec, bkp_file, bkp_folder)
                # remove files after x days if auto remove is true
                if rec.backup_id.autoremove:
                    for entry in sftp.listdir_attr(remote or '/'):
                        if not rec._is_own_backup_name(entry.filename):
                            continue
                        timestamp = entry.st_mtime
                        createtime = datetime.datetime.fromtimestamp(timestamp).date()
                        date_today1 = datetime.datetime.today().date()
                        delta = date_today1 - createtime
                        if delta.days >= rec.backup_id.days_to_keep:
                            sftp.remove(remote + '/' + entry.filename)
                            _logger.info("Delete SFTP out-of-date file %s.", entry.filename)
            except Exception as err:
                error_msg = rec._format_sftp_connection_error(rec, err)
                _logger.debug("ERROR: %s" % (error_msg,))
                StatusObj.create({'date': date_today, 'name': "Failed (Error: %s)" % (error_msg)})
                if rec.backup_id.bkup_fail_email:
                    self.send_fail_mail_notificaton(rec, bkp_file, bkp_folder, error_msg)
            finally:
                if sftp:
                    sftp.close()
                if s:
                    s.close()
        else:
            StatusObj.create({'date': date_today, 'name': "Failed (Error: %s)" % (str(err))})

    def AWSs3_upload(self, rec, file_path, bkp_file, bkp_folder_path, bkp_folder, err, date_today, db_content,
                     dbfile_content):
        StatusObj = self.env['auto.database.backup.status']
        if err == "":
            try:
                s3_app_key_id = rec.s3_app_key_id
                s3_secret_key_id = rec.s3_secret_key_id
                client = boto3.client(
                    's3',
                    aws_access_key_id=s3_app_key_id,
                    aws_secret_access_key=s3_secret_key_id
                )
                # An upload error used to be logged and passed over: the run went on to record
                # "Success", send the success mail and prune the old backups. It fails the run now.
                with open(file_path, 'rb') as fp:
                    client.upload_fileobj(fp, rec.s3_bucket_name, bkp_file)
                if rec.backup == 'db_and_files':
                    with open(bkp_folder_path, 'rb') as fp1:
                        client.upload_fileobj(fp1, rec.s3_bucket_name, bkp_folder)
                _logger.info("Backup Successfully Uploaded to AWS S3.")
                StatusObj.create({'date': date_today, 'name': "AWS S3: Success"})
                if rec.backup_id.bkup_email:
                    self.send_success_mail_notificaton(rec, bkp_file, bkp_folder)
                # remove files after x days if auto remove is true
                if rec.backup_id.autoremove:
                    rec._prune_s3(client)

            except Exception as err:
                _logger.exception("Backup upload to AWS S3 failed")
                StatusObj.create({'date': date_today, 'name': "Failed (Error: %s)" % (str(err))})
                if rec.backup_id.bkup_fail_email:
                    self.send_fail_mail_notificaton(rec, bkp_file, bkp_folder, err)
        else:
            StatusObj.create({'date': date_today, 'name': "Failed (Error: %s)" % (str(err))})

    def _prune_s3(self, client):
        """Delete this rule's own out-of-date backups from the bucket; return how many.

        Listed page by page under the backups' own name prefixes - a single list_objects_v2 call
        sees only the first 1,000 keys - and each key must match the backup name exactly."""
        self.ensure_one()
        prefixes = {self.env.cr.dbname + '_'}
        if self.backup == 'db_and_files' and self.files_path:
            prefixes.add(self.files_path.split('/')[-1] + '_')
        today = datetime.datetime.today().date()
        keys = []
        paginator = client.get_paginator('list_objects_v2')
        for prefix in sorted(prefixes):
            for page in paginator.paginate(Bucket=self.s3_bucket_name, Prefix=prefix):
                for s3_object in page.get('Contents', []):
                    key = s3_object['Key']
                    if key in keys or not self._is_own_backup_name(key):
                        continue
                    createtime = s3_object['LastModified'].date()
                    if (today - createtime).days >= self.backup_id.days_to_keep:
                        keys.append(key)
        for start in range(0, len(keys), S3_DELETE_BATCH):
            client.delete_objects(Bucket=self.s3_bucket_name, Delete={
                'Objects': [{'Key': key} for key in keys[start:start + S3_DELETE_BATCH]]})
        if keys:
            _logger.info("Deleted %s AWS S3 out-of-date file(s).", len(keys))
        return len(keys)

    def ftp_upload(self, rec, file_path, bkp_file, bkp_folder_path, bkp_folder, err, date_today, db_content,
                   dbfile_content):
        StatusObj = self.env['auto.database.backup.status']
        if err == "":
            ftp = False
            try:
                filename = bkp_file
                # The configured port used to be ignored (always 21), and os.chdir("/tmp") moved the
                # working directory of the whole server process - every thread - to upload a file.
                # The files are sent from their absolute paths instead.
                ftp = ftplib.FTP(timeout=300)
                ftp.connect(rec.ftp_address, rec.ftp_port or 0)
                ftp.login(rec.ftp_usrnm, rec.ftp_pwd)
                ftp.encoding = "utf-8"
                ftp.cwd(rec.ftp_path)
                with open(file_path, 'rb') as db_document:
                    ftp.storbinary('STOR ' + filename, db_document)
                if rec.backup == 'db_and_files':
                    with open(bkp_folder_path, 'rb') as files_document:
                        ftp.storbinary('STOR ' + bkp_folder, files_document)

                _logger.info("Backup Successfully Uploaded to FTP.")
                StatusObj.create({'date': date_today, 'name': "FTP: Success"})

                if rec.backup_id.bkup_email:
                    self.send_success_mail_notificaton(rec, bkp_file, bkp_folder)
                # remove files after x days if auto remove is true
                if rec.backup_id.autoremove:
                    for file_data in list(ftp.mlsd()):
                        file_name, meta = file_data
                        if not rec._is_own_backup_name(file_name) or not meta.get("modify"):
                            continue
                        create_date = self.get_datetime_format(meta.get("modify")[:14])
                        date_today1 = datetime.datetime.today().date()
                        delta1 = date_today1 - create_date
                        if delta1.days >= rec.backup_id.days_to_keep:
                            ftp.delete(file_name)
                            _logger.info("Delete FTP out-of-date file %s.", file_name)
                ftp.quit()
            except Exception as err:
                _logger.exception("Backup upload to FTP failed")
                StatusObj.create({'date': date_today, 'name': "Failed (Error: %s)" % (str(err))})
                if rec.backup_id.bkup_fail_email:
                    self.send_fail_mail_notificaton(rec, bkp_file, bkp_folder, err)
            finally:
                if ftp:
                    ftp.close()
        else:
            StatusObj.create({'date': date_today, 'name': "Failed (Error: %s)" % (str(err))})

    def dropbox_upload(self, rec, file_path, bkp_file, bkp_file2, bkp_folder, status, date_today, db_content,
                       dbfile_content):
        """Upload one file of the backup to Dropbox; return whether it landed.

        Every failure is recorded as the run's status (and mailed). It used to call sys.exit()
        inside the cron worker on an API error, only log an authorisation error at debug level,
        and record nothing at all when the refresh token was missing."""
        StatusObj = self.env['auto.database.backup.status']
        try:
            if not rec.dropbox_refresh_token:
                raise UserError(_("Dropbox is not authorised: there is no refresh token. Get an authorization "
                                  "code and generate the refresh token again."))
            with dropbox.Dropbox(oauth2_refresh_token=rec.dropbox_refresh_token, app_key=rec.d_app_key) as dbx:
                # Check that the access token is valid
                try:
                    dbx.users_get_current_account()
                except AuthError as error:
                    raise UserError(_("Dropbox refused the stored authorisation: %s") % (error,)) from error

                try:
                    rec._dropbox_upload_file(dbx, file_path, "/" + bkp_file)
                except ApiError as error:
                    raise UserError(rec._dropbox_error_message(error)) from error
                _logger.info("Backup Successfully Uploaded to Dropbox.")
                if rec.backup == 'db_only' and status == 1:
                    StatusObj.create({'date': date_today, 'name': "Dropbox: Success"})
                if rec.backup == 'db_and_files' and status == 2:
                    StatusObj.create({'date': date_today, 'name': "Success"})
                if rec.backup_id.bkup_email:
                    if rec.backup == 'db_only' and status == 1:
                        self.send_success_mail_notificaton(rec, bkp_file, bkp_folder)
                    if rec.backup == 'db_and_files' and status == 2:
                        self.send_success_mail_notificaton(rec, bkp_file2, bkp_folder)

                # remove files after x days if auto remove is true
                if rec.backup_id.autoremove:
                    rec._prune_dropbox(dbx)
            return True
        except Exception as err:
            _logger.exception("Backup upload to Dropbox failed")
            StatusObj.create({'date': date_today, 'name': "Failed (Error: %s)" % (str(err))})
            if rec.backup_id.bkup_fail_email:
                self.send_fail_mail_notificaton(rec, bkp_file2, bkp_folder, err)
            return False

    def _dropbox_upload_file(self, dbx, file_path, dropbox_path):
        """Upload a file from disk, in an upload session when it is larger than one chunk.

        files_upload takes at most 150 MB and holds the whole file in memory; the database dump
        is several times that."""
        file_size = os.path.getsize(file_path)
        mode = WriteMode('overwrite')
        with open(file_path, 'rb') as f:
            if file_size <= DROPBOX_CHUNK_SIZE:
                return dbx.files_upload(f.read(), dropbox_path, mode=mode)
            session = dbx.files_upload_session_start(f.read(DROPBOX_CHUNK_SIZE))
            cursor = UploadSessionCursor(session_id=session.session_id, offset=f.tell())
            commit = CommitInfo(path=dropbox_path, mode=mode)
            while file_size - f.tell() > DROPBOX_CHUNK_SIZE:
                dbx.files_upload_session_append_v2(f.read(DROPBOX_CHUNK_SIZE), cursor)
                cursor.offset = f.tell()
            return dbx.files_upload_session_finish(f.read(DROPBOX_CHUNK_SIZE), cursor, commit)

    def _dropbox_error_message(self, error):
        detail = getattr(error, 'error', None)
        try:
            path_error = detail.get_path() if detail is not None and detail.is_path() else None
            # files_upload wraps the write error in .reason; an upload session reports it directly.
            write_error = getattr(path_error, 'reason', path_error)
            if write_error is not None and write_error.is_insufficient_space():
                return _("Cannot back up: the Dropbox account has insufficient space.")
        except Exception:
            pass
        return getattr(error, 'user_message_text', None) or str(error)

    def _prune_dropbox(self, dbx):
        """Delete this rule's own out-of-date backups from the Dropbox root, every page of it."""
        self.ensure_one()
        result = dbx.files_list_folder('')
        entries = list(result.entries)
        while result.has_more:
            result = dbx.files_list_folder_continue(result.cursor)
            entries.extend(result.entries)
        date_today1 = datetime.datetime.today().date()
        for entry in entries:
            if not isinstance(entry, FileMetadata) or not self._is_own_backup_name(entry.name):
                continue
            if (date_today1 - entry.client_modified.date()).days >= self.backup_id.days_to_keep:
                dbx.files_delete_v2(entry.path_lower)
                _logger.info("Delete Dropbox out-of-date file %s.", entry.name)

    def get_is_cred_avail(self):
        cred_fp = os.path.join(os.path.dirname(os.path.abspath(__file__))) + "/client_secrets.json"
        for rec in self:
            if os.path.exists(cred_fp):
                rec.is_cred_avail = True
            else:
                rec.is_cred_avail = False

    def get_gdrive_credentials(self):
        for rec in self:
            if not rec.is_cred_avail:
                raise UserError(
                    _("client_secrets.json does not exist. First generate & add credentials file in path auto_odoo_db_and_file_backup/models."))
            return {
                'type': 'ir.actions.act_url',
                'url': rec.google_drive_uri,
                'target': 'new',
            }

    def authenticate_gdrive(self):
        for rec in self:
            if not rec.is_cred_avail:
                raise UserError(
                    _("client_secrets.json does not exist. First generate & add credentials file in path auto_odoo_db_and_file_backup/models."))
            if not rec.google_drive_refresh_token:
                raise UserError(
                    _("Google Drive refresh token is missing. Click Get Credentials, approve access, paste the authorization code, then save the rule."))
            try:
                drive = rec.authorize_drive()
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'message': "Google drive authorized successfully!",
                        'type': 'success',
                        'sticky': False,
                    }
                }
            except Exception as e:
                raise UserError(_(str(e)))

    def _get_gdrive_client_config(self):
        cred_fp = os.path.join(os.path.dirname(os.path.abspath(__file__))) + "/client_secrets.json"
        if not os.path.exists(cred_fp):
            raise UserError(
                _("client_secrets.json does not exist. First generate & add credentials file in path auto_odoo_db_and_file_backup/models."))
        with open(cred_fp, 'r', encoding='utf-8') as credentials_file:
            credentials = json.load(credentials_file)
        client_config = credentials.get('installed') or credentials.get('web')
        if not client_config:
            raise UserError(_("Invalid client_secrets.json file. Expected an 'installed' or 'web' OAuth client."))
        return client_config

    def _gdrive_client_type(self):
        cred_fp = os.path.join(os.path.dirname(os.path.abspath(__file__))) + "/client_secrets.json"
        if not os.path.exists(cred_fp):
            return False
        with open(cred_fp, 'r', encoding='utf-8') as credentials_file:
            credentials = json.load(credentials_file)
        return 'web' if credentials.get('web') else 'installed' if credentials.get('installed') else False

    def _gdrive_redirect_uri(self):
        """Where Google must send the browser back.

        A Web application client returns to this server's own callback, which saves the code
        on the rule. A Desktop (installed) client may only use loopback addresses, so it keeps
        its own first redirect address and the code is pasted by hand.
        """
        if self._gdrive_client_type() == 'web':
            return self.get_base_url().rstrip('/') + GDRIVE_CALLBACK
        client_config = self._get_gdrive_client_config()
        return (client_config.get('redirect_uris') or ['http://localhost'])[0]

    def _compute_gdrive_redirect_uri(self):
        callback = self.get_base_url().rstrip('/') + GDRIVE_CALLBACK
        for rec in self:
            rec.google_drive_redirect_uri = callback

    def _gdrive_state(self):
        """The rule id, signed: the callback writes a code only on the rule that asked."""
        self.ensure_one()
        return '%s.%s' % (self.id, odoo_hmac(self.env(su=True), GDRIVE_STATE_SCOPE, str(self.id)))

    @api.model
    def _gdrive_rule_from_state(self, state):
        rule_id, _sep, signature = (state or '').partition('.')
        if not rule_id.isdigit() or not signature:
            return self.browse()
        expected = odoo_hmac(self.env(su=True), GDRIVE_STATE_SCOPE, rule_id)
        if not consteq(signature, expected):
            return self.browse()
        return self.browse(int(rule_id)).exists()

    GDRIVE_FOLDER_MIME = 'application/vnd.google-apps.folder'

    def _gdrive_folder_id(self, drive):
        """The Drive folder the backups go into, created if it is not there yet.

        Takes what people have to hand: the folder's name ("Lab Backups"), a
        path ("Lab/Backups"), the folder id, or the address of the folder in
        Drive. Empty means the root, which is where every backup went before
        there was a setting at all. (client, 2026-09-16)
        """
        self.ensure_one()
        value = (self.google_drive_folder or '').strip()
        if not value:
            return False
        if '/folders/' in value:
            value = value.split('/folders/', 1)[1].split('?', 1)[0].split('/', 1)[0]
        parent = 'root'
        for segment in [s.strip() for s in value.split('/') if s.strip()]:
            parent = self._gdrive_child_folder(drive, parent, segment)
        return parent if parent != 'root' else False

    @api.model
    def _gdrive_is_not_found(self, error):
        """True only when Google says there is no such file.

        Anything else - the Drive API switched off in the project, a token that may
        not read that folder, a network blip - is not an answer about the id and must
        not be treated as one. (client, 2026-09-16)
        """
        status = getattr(getattr(error, 'resp', None), 'status', None)
        if status is not None:
            return int(status) == 404
        text = str(error).lower()
        return 'not found' in text or 'no such file' in text or 'notfound' in text

    def _gdrive_child_folder(self, drive, parent, name):
        """One folder of the path: found by title, or created."""
        # A Drive id, not a name: long, and no spaces in it.
        if parent == 'root' and len(name) > 20 and ' ' not in name:
            try:
                found = drive.CreateFile({'id': name})
                found.FetchMetadata(fields='id,mimeType,title')
                if found.get('mimeType') == self.GDRIVE_FOLDER_MIME:
                    return found['id']
            except Exception as error:                                 # noqa: BLE001
                # Only "there is no such file" means this was a name all along. On any
                # other error the fallback would search for a folder TITLED with the id
                # and, finding none, create one - so the backups would quietly go to a
                # new folder called 1WvhbK4A... instead of the one that was asked for.
                if not self._gdrive_is_not_found(error):
                    raise
                # not an id after all - take it as a name
        # The title goes into a query string, so a quote in it must not end the
        # string and open a search of everything.
        safe = name.replace('\\', '\\\\').replace("'", "\\'")
        existing = drive.ListFile({'q': "'%s' in parents and trashed=false and "
                                        "mimeType='%s' and title='%s'"
                                        % (parent, self.GDRIVE_FOLDER_MIME, safe)}).GetList()
        if existing:
            return existing[0]['id']
        folder = drive.CreateFile({'title': name, 'mimeType': self.GDRIVE_FOLDER_MIME,
                                   'parents': [{'id': parent}]})
        folder.Upload()
        _logger.info("Google Drive: created backup folder %s", name)
        return folder['id']

    def _generate_gdrive_refresh_token(self, authorization_code):
        client_config = self._get_gdrive_client_config()
        payload = {
            'code': authorization_code,
            'client_id': client_config.get('client_id'),
            'client_secret': client_config.get('client_secret'),
            # The same address the auth link named, or Google refuses the code.
            'redirect_uri': self._gdrive_redirect_uri(),
            'grant_type': 'authorization_code',
        }
        response = requests.post(
            client_config.get('token_uri') or GOOGLE_TOKEN_ENDPOINT,
            data=payload,
            timeout=TIMEOUT,
        )
        try:
            response_data = response.json()
        except Exception:
            response_data = {}
        if response.status_code >= 400 or not response_data.get('refresh_token'):
            error = response_data.get('error_description') or response_data.get('error') or response.text
            raise UserError(_("Could not generate Google Drive refresh token: %s") % error)
        return response_data.get('refresh_token')

    def _get_gdrive_auth_url(self):
        try:
            cred_fp = os.path.join(os.path.dirname(os.path.abspath(__file__))) + "/client_secrets.json"
            if not os.path.exists(cred_fp):
                return "https://console.cloud.google.com/apis"
            gauth = GoogleAuth()
            gauth.DEFAULT_SETTINGS['client_config_file'] = cred_fp
            gauth.GetFlow()
            # prompt=consent is how Google now forces the consent screen, which is what makes
            # it return a refresh token. The old approval_prompt=force may not be sent with it:
            # Google refuses the pair with "Access blocked: Conflict params: approval_prompt and
            # prompt" (Error 400: invalid_request). It is removed whoever set it - PyDrive adds
            # it itself when its get_refresh_token setting is on. (client, 2026-09-15)
            gauth.flow.params.update({'access_type': 'offline', 'prompt': 'consent'})
            gauth.flow.params.pop('approval_prompt', None)
            # Back to this server's callback with the rule it is for, rather than to the first
            # address in the credentials file (http://localhost or the site root), where the
            # code was lost. (client, 2026-09-15)
            gauth.flow.redirect_uri = self._gdrive_redirect_uri()
            if isinstance(self.id, int):
                gauth.flow.params['state'] = self._gdrive_state()
            return gauth.GetAuthUrl()
        except Exception:
            return "https://console.cloud.google.com/apis"

    def authorize_drive(self):
        gauth = GoogleAuth()
        client_config = self._get_gdrive_client_config()
        refresh_token = self.google_drive_refresh_token
        if not refresh_token:
            raise UserError(
                _("Google Drive refresh token is missing. Click Get Credentials, approve access, paste the authorization code, then save the rule."))
        credentials = GoogleCredentials(
            access_token=None,
            client_id=client_config.get('client_id'),
            client_secret=client_config.get('client_secret'),
            refresh_token=refresh_token,
            token_expiry=None,
            token_uri=client_config.get('token_uri') or GOOGLE_TOKEN_ENDPOINT,
            user_agent='Odoo Auto DB Backup',
        )
        credentials.refresh(httplib2.Http(timeout=TIMEOUT))
        gauth.credentials = credentials
        return GoogleDrive(gauth)

    def google_drive_upload(self, rec, file_path, bkp_file, bkp_file2, bkp_folder, status, date_today, db_content,
                            dbfile_content):
        """Upload one file of the backup to Google Drive; return whether it landed."""
        StatusObj = self.env['auto.database.backup.status']
        # GOOGLE DRIVE UPLOAP
        if rec.backup_destination == "g_drive":
            try:
                drive = rec.authorize_drive()
                folder_id = rec._gdrive_folder_id(drive)

                def _new_drive_file(title, folder_id=folder_id, drive=drive):
                    meta = {'title': title}
                    if folder_id:
                        meta['parents'] = [{'id': folder_id}]
                    return drive.CreateFile(meta)

                if status == 1:  # db file
                    db_backup_file = _new_drive_file(bkp_file)
                    db_backup_file.SetContentFile(file_path)
                    db_backup_file.Upload()
                else:  # db filed & folders
                    db_backup_file2 = _new_drive_file(bkp_folder)
                    db_backup_file2.SetContentFile(file_path)
                    db_backup_file2.Upload()
                _logger.info("Backup Successfully Uploaded to Google Drive.")
                if os.path.exists(file_path):
                    os.remove(file_path)
                if rec.backup == 'db_only' and status == 1:
                    StatusObj.create({'date': date_today, 'name': "Google Drive: Success"})
                if rec.backup == 'db_and_files' and status == 2:
                    StatusObj.create({'date': date_today, 'name': "Success"})
                if rec.backup_id.bkup_email:
                    if rec.backup == 'db_only' and status == 1:
                        self.send_success_mail_notificaton(rec, bkp_file, bkp_folder)
                    if rec.backup == 'db_and_files' and status == 2:
                        self.send_success_mail_notificaton(rec, bkp_file2, bkp_folder)
                        # AUTO REMOVE UPLOADED FILE
                if rec.backup_id.autoremove:
                    # Pruned where they were put - the rule's own folder, or the root when it
                    # names none - and only files named exactly as this rule names its backups.
                    # Any title merely containing the database name, or the files folder's name,
                    # used to be deleted: other databases' backups and anyone's files.
                    file_list = drive.ListFile(
                        {'q': "'%s' in parents and trashed=false" % (folder_id or 'root')}).GetList()
                    date_today1 = datetime.datetime.today().date()
                    for item in file_list:
                        item_title = item['title']
                        if item.get('mimeType') == 'application/vnd.google-apps.folder' \
                                or not rec._is_own_backup_name(item_title):
                            continue
                        create_date = datetime.datetime.strptime(str(item['createdDate'])[0:10], '%Y-%m-%d').date()
                        delta = date_today1 - create_date
                        if delta.days >= rec.backup_id.days_to_keep:
                            # delete file from drive
                            drive.CreateFile({'id': item['id']}).Delete()
                            _logger.info("Deleted Google Drive out-of-date file %s." % (item_title,))
                return True
            except Exception as e:
                _logger.exception("Backup Upload to Google drive Failed")
                StatusObj.create({'date': date_today, 'name': "Failed (Error: %s)" % (str(e))})
                if rec.backup_id.bkup_fail_email:
                    self.send_fail_mail_notificaton(rec, bkp_file2, bkp_folder, str(e))
        return False

    def _take_dump(self, db_name, stream, model, backup_destination, backup_format='zip'):
        """Dump database `db` into file-like object `stream` if stream is None
        return a file object with the dump """

        cron_user_id = self.env.ref('auto_odoo_db_and_file_backup.auto_db_backup_scheduler').user_id.id
        if self._name != 'database.backup' or cron_user_id != self.env.user.id:
            _logger.error('Unauthorized database operation. Backups should only be available from the cron job.')
            raise AccessDenied()

        _logger.info('DUMP DB: %s format %s', db_name, backup_format)

        cmd = [odoo.tools.misc.find_pg_tool('pg_dump'), '--no-owner']
        cmd.append(db_name)
        env = exec_pg_environ()
        if backup_format == 'zip':
            with tempfile.TemporaryDirectory() as dump_dir:
                filestore = odoo.tools.config.filestore(db_name)
                if os.path.exists(filestore):
                    shutil.copytree(filestore, os.path.join(dump_dir, 'filestore'))
                with open(os.path.join(dump_dir, 'manifest.json'), 'w') as fh:
                    db = odoo.sql_db.db_connect(db_name)
                    with db.cursor() as cr:
                        json.dump(self._dump_db_manifest(cr), fh, indent=4)
                cmd.insert(-1, '--file=' + os.path.join(dump_dir, 'dump.sql'))
                subprocess.run(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, check=True)
                if stream:
                    odoo.tools.osutil.zip_dir(dump_dir, stream, include_dir=False,
                                              fnct_sort=lambda file_name: file_name != 'dump.sql')
                else:
                    t = tempfile.TemporaryFile()
                    odoo.tools.osutil.zip_dir(dump_dir, t, include_dir=False,
                                              fnct_sort=lambda file_name: file_name != 'dump.sql')
                    t.seek(0)
                    return t
        else:
            cmd.insert(-1, '--format=c')
            # pg_dump used to be started with Popen and its exit status never read, and a failed
            # write was only print()ed: a broken or truncated dump went on as a backup. `stream`
            # is a path (the uploads) or an open file (the folder backup).
            if not stream:
                output = tempfile.TemporaryFile()
                self._run_pg_dump(cmd, env, output)
                output.seek(0)
                return output
            if isinstance(stream, (str, bytes, os.PathLike)):
                with open(stream, 'wb') as output:
                    self._run_pg_dump(cmd, env, output)
            else:
                self._run_pg_dump(cmd, env, stream)

    def _run_pg_dump(self, cmd, env, output):
        """Run pg_dump writing straight into the open file `output`; raise if it fails."""
        output.flush()
        try:
            subprocess.run(cmd, env=env, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.PIPE,
                           check=True)
        except subprocess.CalledProcessError as error:
            detail = (error.stderr or b'').decode('utf-8', 'replace').strip()[-1000:]
            raise UserError(_("pg_dump failed (exit %s): %s") % (error.returncode, detail or _("no output"))) from error

    def _dump_db_manifest(self, cr):
        pg_version = "%d.%d" % divmod(cr._obj.connection.server_version / 100, 100)
        cr.execute("SELECT name, latest_version FROM ir_module_module WHERE state = 'installed'")
        modules = dict(cr.fetchall())
        manifest = {
            'odoo_dump': '1',
            'db_name': cr.dbname,
            'version': odoo.release.version,
            'version_info': odoo.release.version_info,
            'major_version': odoo.release.major_version,
            'pg_version': pg_version,
            'modules': modules,
        }
        return manifest
