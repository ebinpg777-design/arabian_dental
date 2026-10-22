# -*- coding: utf-8 -*-
###############################################################################
#
#    Ebshel Technologies
#
#    Copyright (C) 2026 Ebshel Technologies (https://ebshel.com)
#    Ported to Odoo 20 and modified by Ebshel Technologies.
#
#    Based on "auto_database_backup" by Cybrosys Technologies:
#    Copyright (C) 2023-TODAY Cybrosys Technologies(<https://www.cybrosys.com>)
#    Author: Cybrosys Techno Solutions(<https://www.cybrosys.com>)
#
#    You can modify it under the terms of the GNU LESSER
#    GENERAL PUBLIC LICENSE (LGPL v3), Version 3.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU LESSER GENERAL PUBLIC LICENSE (LGPL v3) for more details.
#
#    You should have received a copy of the GNU LESSER GENERAL PUBLIC LICENSE
#    (LGPL v3) along with this program.
#    If not, see <http://www.gnu.org/licenses/>.
#
###############################################################################
import contextlib
import errno
import ftplib
import importlib
import json
import logging
import os
import posixpath
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta
from urllib.parse import quote, urlencode

import requests

import odoo.release
import odoo.sql_db
from odoo import api, fields, models, _
from odoo.exceptions import AccessDenied, AccessError, UserError, \
    ValidationError
from odoo.modules import db as odoo_db
from odoo.tools import config, format_datetime, osutil
from odoo.tools.misc import exec_pg_environ, find_pg_tool, hash_sign, \
    verify_hash_signed

_logger = logging.getLogger(__name__)
ONEDRIVE_SCOPE = 'offline_access openid Files.ReadWrite.All'
ONEDRIVE_AUTH_ENDPOINT = \
    'https://login.microsoftonline.com/common/oauth2/v2.0/authorize'
ONEDRIVE_TOKEN_ENDPOINT = \
    'https://login.microsoftonline.com/common/oauth2/v2.0/token'
MICROSOFT_GRAPH_END_POINT = "https://graph.microsoft.com"
GOOGLE_AUTH_ENDPOINT = 'https://accounts.google.com/o/oauth2/auth'
GOOGLE_TOKEN_ENDPOINT = 'https://accounts.google.com/o/oauth2/token'
GOOGLE_API_BASE_URL = 'https://www.googleapis.com'
headers = {"content-type": "application/x-www-form-urlencoded"}

# Seconds before an idle network call (connect, or a read/write that makes no
# progress) is given up. Uploads of any size are fine: it is not a total time.
NETWORK_TIMEOUT = 300
# OneDrive upload fragments must be a multiple of 320 KiB (max 60 MiB).
ONEDRIVE_CHUNK_SIZE = 320 * 1024 * 32
# Dropbox refuses a single upload request larger than 150 MiB.
DROPBOX_CHUNK_SIZE = 64 * 1024 * 1024
BACKUP_TIME_FORMAT = '%Y-%m-%d_%H-%M-%S'
OAUTH_STATE_SCOPE = 'ebshel_auto_database_backup.oauth_state'
# Strip query strings from URLs quoted in error messages: pre-authenticated
# upload URLs carry their credential there.
URL_QUERY_RE = re.compile(r'(https?://[^\s?#]+)[?#]\S*')
# Optional libraries: (module, pip package, destination label)
DESTINATION_LIBRARIES = {
    'sftp': ('paramiko', 'paramiko', 'SFTP'),
    'dropbox': ('dropbox', 'dropbox', 'Dropbox'),
    'amazon_s3': ('boto3', 'boto3', 'Amazon S3'),
    'next_cloud': ('nextcloud_client', 'pyncclient', 'Nextcloud'),
}


class DbBackupConfigure(models.Model):
    """Model for configuring automatic database backup settings.
    Methods:
        action_nextcloud: Perform a Nextcloud connection test and display a
        notification based on the result.
        action_s3cloud:Perform an Amazon connection test and display a
        notification based on the result.
        _compute_redirect_uri: Compute the redirect URIs based on the backup
        destination.
        _compute_is_google_drive_token_generated:  Set True if the Google Drive
        refresh token is generated.
        generate_gdrive_refresh_token: Generate Google Drive access token from
        refresh token if expired.
        get_gdrive_tokens: Generate onedrive tokens from authorization code.
        _compute_is_onedrive_token_generated: Set true if onedrive tokens are
        generated.
        _compute_is_dropbox_token_generated: Set True if the dropbox refresh
        token is generated.
        action_get_dropbox_auth_code : Open a wizard to set up dropbox
        Authorization code.
        action_get_onedrive_auth_code: Generate onedrive authorization code
        generate_onedrive_refresh_token: Generate onedrive access token from
        refresh token if expired.
        get_onedrive_tokens: Generate onedrive tokens from authorization code
        get_dropbox_auth_url: Return dropbox authorization url.
        set_dropbox_refresh_token: Generate and set the dropbox refresh token
        from authorization code.
        _check_db_credentials: Validate entered database name and master
        password.
        action_sftp_connection: Test the sftp and ftp connection using entered
        credentials.
        _schedule_auto_backup: Function for generating and storing backup
        Database backup for all the active records in backup configuration
        model will be created
    """
    _name = 'db.backup.configure'
    _description = 'Automatic Database Backup'

    name = fields.Char(string='Name', required=True, help='Name of the Record')
    db_name = fields.Char(string='Database Name', required=True,
                          help='Name of the current database')
    master_pwd = fields.Char(string='Master Password', required=True,
                             help='Master password of the database')
    backup_format = fields.Selection([
        ('zip', 'Zip'),
        ('dump', 'Dump')
    ], string='Backup Format', default='zip', required=True,
        help='The format in which the backup should be created')
    backup_destination = fields.Selection([
        ('local', 'Local Storage'),
        ('google_drive', 'Google Drive'),
        ('ftp', 'FTP'),
        ('sftp', 'SFTP'),
        ('dropbox', 'Dropbox'),
        ('onedrive', 'Onedrive'),
        ('next_cloud', 'Next Cloud'),
        ('amazon_s3', 'Amazon S3')
    ], string='Backup Destination',
        help='The destination where the backup should be stored')
    backup_path = fields.Char(string='Backup Path',
                              help='Local storage directory path')
    sftp_host = fields.Char(string='SFTP Host',
                            help='The host of the SFTP server')
    sftp_port = fields.Char(string='SFTP Port', default='22',
                            help='The port number for the SFTP connection')
    sftp_user = fields.Char(string='SFTP User', copy=False,
                            help='The username for the SFTP connection')
    sftp_password = fields.Char(string='SFTP Password', copy=False,
                                help='The password for the SFTP connection')
    sftp_path = fields.Char(string='SFTP Path',
                            help='The path to the destination directory on '
                                 'the SFTP server')
    ftp_host = fields.Char(string='FTP Host',
                           help='The hostname or IP address of the FTP server')
    ftp_port = fields.Char(string='FTP Port', default='21',
                           help='The port number used for FTP connections')
    ftp_user = fields.Char(string='FTP User', copy=False,
                           help='The username used for FTP authentication')
    ftp_password = fields.Char(string='FTP Password', copy=False,
                               help='The password used for FTP authentication')
    ftp_path = fields.Char(string='FTP Path',
                           help='The path to the directory on the FTP server '
                                'where the backup should be stored')
    dropbox_client_key = fields.Char(string='Dropbox Client ID', copy=False,
                                     help='The client ID for accessing Dropbox '
                                          'API')
    dropbox_client_secret = fields.Char(string='Dropbox Client Secret',
                                        copy=False,
                                        help='The client secret for accessing '
                                             'Dropbox API')
    dropbox_refresh_token = fields.Char(string='Dropbox Refresh Token',
                                        copy=False,
                                        help='The refresh token for accessing '
                                             'Dropbox API')
    is_dropbox_token_generated = fields.Boolean(
        string='Dropbox Token Generated',
        compute='_compute_is_dropbox_token_generated', copy=False,
        help='A boolean indicating whether the Dropbox token has been '
             'generated or not')
    dropbox_folder = fields.Char(string='Dropbox Folder',
                                 help='The name of the Dropbox folder where '
                                      'backups should be stored')
    active = fields.Boolean(string='Active',
                            help='Set this field to True to activate the backup'
                                 'configuration.')
    hide_active = fields.Boolean(string="Hide Active",
                                 help="Make active field to readonly")
    auto_remove = fields.Boolean(string='Remove Old Backups',
                                 help='Set this field to True to automatically '
                                      'remove old backups.')
    days_to_remove = fields.Integer(string='Remove After',
                                    help='Automatically delete stored backups '
                                         'after this specified number of days')
    google_drive_folder_key = fields.Char(string='Drive Folder ID',
                                          help='Folder ID inside Google Drive')
    notify_user = fields.Boolean(string='Notify User',
                                 help='Send an email notification to user '
                                      'when the backup operation is '
                                      'successful or failed')
    user_id = fields.Many2one('res.users', string='User',
                              help='Select the user to send email')
    backup_filename = fields.Char(string='Backup Filename',
                                  help='For Storing generated backup filename')
    generated_exception = fields.Char(string='Exception',
                                      help='Exception Encountered while '
                                           'Backup generation')
    onedrive_client_key = fields.Char(string='Onedrive Client ID', copy=False,
                                      help='The client ID of your Onedrive '
                                           'application.')
    onedrive_client_secret = fields.Char(string='Onedrive Client Secret',
                                         copy=False,
                                         help='The client secret of your '
                                              'Onedrive application.')
    onedrive_access_token = fields.Char(string='Onedrive Access Token',
                                        copy=False,
                                        help='The access token required to '
                                             'authenticate with Onedrive API.'
                                        )
    onedrive_refresh_token = fields.Char(string='Onedrive Refresh Token',
                                         copy=False,
                                         help='The refresh token required to '
                                              'obtain a new access token from '
                                              'Onedrive API.')
    onedrive_token_validity = fields.Datetime(string='Onedrive Token Validity',
                                              copy=False,
                                              help='The date and time when '
                                                   'the Onedrive access token '
                                                   'expires.')
    onedrive_folder_key = fields.Char(string='Folder ID',
                                      help='The unique identifier of the '
                                           'Onedrive folder where the backup '
                                           'file will be stored.')
    is_onedrive_token_generated = fields.Boolean(
        string='onedrive Tokens Generated',
        compute='_compute_is_onedrive_token_generated', copy=False,
        help='Indicates whether the Onedrive tokens have been generated.')
    gdrive_refresh_token = fields.Char(string='Google drive Refresh Token',
                                       copy=False,
                                       help='The refresh token for the Google '
                                            'Drive account')
    gdrive_access_token = fields.Char(string='Google Drive Access Token',
                                      copy=False,
                                      help='The access token for the Google '
                                           'Drive account')
    is_google_drive_token_generated = fields.Boolean(
        string='Google drive Token Generated',
        compute='_compute_is_google_drive_token_generated', copy=False,
        help='A flag to indicate whether a token has been generated for the '
             'Google Drive account')
    gdrive_client_key = fields.Char(string='Google Drive Client ID', copy=False,
                                    help='The client ID for the Google Drive API'
                                    )
    gdrive_client_secret = fields.Char(string='Google Drive Client Secret',
                                       copy=False,
                                       help='The client secret for the Google '
                                            'Drive API')
    gdrive_token_validity = fields.Datetime(
        string='Google Drive Token Validity', copy=False,
        help='Specify the validity period of the Google Drive access token.')
    gdrive_redirect_uri = fields.Char(string='Google Drive Redirect URI',
                                      compute='_compute_redirect_uri',
                                      help='The redirect URI used for the '
                                           'Google Drive API authentication '
                                           'flow')
    onedrive_redirect_uri = fields.Char(string='Onedrive Redirect URI',
                                        compute='_compute_redirect_uri',
                                        help='The redirect URI used for the '
                                             'Onedrive API authentication flow')
    aws_access_key = fields.Char(string="Amazon S3 Access Key",
                                 help="Field used to store the Access Key "
                                      "for an Amazon S3 bucket.")
    aws_secret_access_key = fields.Char(string='Amazon S3 Secret Key',
                                        help="Field used to store the Secret "
                                             "Key for an Amazon S3 bucket.")
    bucket_file_name = fields.Char(string='Bucket Name',
                                   help="Field used to store the name of an "
                                        "Amazon S3 bucket.")
    aws_folder_name = fields.Char(string='File Name',
                                  help="field used to store the name of a "
                                       "folder in an Amazon S3 bucket.")
    domain = fields.Char(string='Domain Name', help="Field used to store the "
                                                    "name of a domain")
    next_cloud_user_name = fields.Char(string='User Name',
                                       help="Field used to store the user "
                                            "name for a Nextcloud account.")
    next_cloud_password = fields.Char(string='Password',
                                      help="Field used to store the password "
                                           "for a Nextcloud account.")
    nextcloud_folder_key = fields.Char(string='Next Cloud Folder Id',
                                       help="Field used to store the unique "
                                            "identifier for a Nextcloud folder.")
    gdrive_backup_error_test = fields.Boolean(string="Google Drive Error Test")
    onedrive_backup_error_test = fields.Boolean(string="OneDrive Error Test")

    @api.onchange('backup_destination')
    def _onchange_backup_destination(self):
        """Reset the token error flags, and let the active toggle be edited
        for local backups (there is no connection to test)."""
        self.gdrive_backup_error_test = False
        self.onedrive_backup_error_test = False
        if self.backup_destination == 'local':
            self.hide_active = True

    @api.onchange('gdrive_client_key', 'gdrive_client_secret',
                  'google_drive_folder_key', 'onedrive_client_key',
                  'onedrive_client_secret', 'onedrive_folder_key')
    def _onchange_gdrive_backup_error_test(self):
        if self.backup_destination == 'google_drive':
            self.gdrive_backup_error_test = False
        if self.backup_destination == 'onedrive':
            self.onedrive_backup_error_test = False

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def _import_optional(self, module_name, package, destination):
        """Import a third-party library needed by one destination only, so
        that sites using other destinations do not have to install it."""
        try:
            return importlib.import_module(module_name)
        except ImportError as error:
            raise UserError(_(
                "%(destination)s backups need the Python package "
                "\"%(package)s\", which is not installed on the Odoo server. "
                "Install it (pip install %(package)s) and restart Odoo.",
                destination=destination, package=package)) from error

    @api.model
    def _format_error(self, error):
        """Human readable error for the log, the record and the e-mail,
        without the query strings of URLs (they can hold credentials)."""
        message = str(error) or error.__class__.__name__
        if isinstance(error, requests.HTTPError) and \
                error.response is not None:
            message = "%s: %s" % (message, error.response.text[:300])
        return URL_QUERY_RE.sub(r'\1', message)[:2000]

    def _notification(self, success, message=None):
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'success' if success else 'danger',
                'title': _("Connection Test Succeeded!") if success else _(
                    "Connection Test Failed!"),
                'message': message or (
                    _("Everything seems properly set up!") if success else _(
                        "An error occurred while testing the connection.")),
                'sticky': False,
            }
        }

    def _get_form_url(self):
        """Relative URL of this configuration in the web client."""
        return '/odoo/action-ebshel_auto_database_backup.db_backup_configure_action' \
               '/%d' % self.id

    def _get_oauth_state(self):
        """Signed OAuth `state`: the callback only accepts it back for this
        record, from the same user, within the hour."""
        self.ensure_one()
        return hash_sign(self.sudo().env, OAUTH_STATE_SCOPE, {
            'backup_config_id': self.id,
            'uid': self.env.uid,
        }, expiration_hours=1)

    @api.model
    def _get_record_from_oauth_state(self, state):
        """Return the configuration an OAuth callback belongs to, or an empty
        recordset when the state was not issued by us to the current user."""
        try:
            values = verify_hash_signed(self.sudo().env, OAUTH_STATE_SCOPE,
                                        state or '')
        except Exception:
            values = None
        if not isinstance(values, dict) or values.get('uid') != self.env.uid:
            return self.browse()
        return self.browse(values.get('backup_config_id')).exists()

    def _is_expired_backup(self, filename, current_filename):
        """Whether `filename` is a backup of this configuration's database
        older than `days_to_remove` days. Its age is read from the timestamp
        in the name, so files that are not our backups are never touched and
        every destination gives the same answer."""
        if filename == current_filename:
            return False
        match = re.fullmatch(
            r'%s_(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})\.(?:zip|dump)'
            % re.escape(self.db_name), filename)
        if not match:
            return False
        try:
            backup_time = datetime.strptime(match.group(1),
                                            BACKUP_TIME_FORMAT)
        except ValueError:
            return False
        return (fields.Datetime.now() - backup_time).days >= \
            self.days_to_remove

    # -------------------------------------------------------------------------
    # Connection tests
    # -------------------------------------------------------------------------

    def _nextcloud_client(self):
        """Return a logged in Nextcloud client for this configuration."""
        nextcloud_client = self._import_optional(
            *DESTINATION_LIBRARIES['next_cloud'])
        if not (self.domain and self.next_cloud_user_name and
                self.next_cloud_password):
            raise UserError(_("Please set the Nextcloud domain, user name "
                              "and password."))
        client = nextcloud_client.Client(self.domain)
        client.login(self.next_cloud_user_name, self.next_cloud_password)
        return client

    def action_nextcloud(self):
        """If it has next_cloud_password, domain, and next_cloud_user_name
         which will perform an action for nextcloud connection test"""
        self.ensure_one()
        self._import_optional(*DESTINATION_LIBRARIES['next_cloud'])
        try:
            client = self._nextcloud_client()
            try:
                client.list('/')
            finally:
                client.logout()
        except Exception as error:
            self.active = False
            self.hide_active = False
            return self._notification(False, _(
                "An error occurred while testing the connection: %s",
                self._format_error(error)))
        self.active = True
        self.hide_active = True
        return self._notification(True)

    def action_s3cloud(self):
        """If it has aws_secret_access_key, which will perform s3 cloud
         operations for connection test"""
        self.ensure_one()
        boto3 = self._import_optional(*DESTINATION_LIBRARIES['amazon_s3'])
        try:
            if not (self.aws_access_key and self.aws_secret_access_key and
                    self.bucket_file_name):
                raise UserError(_("Please set the access key, the secret key "
                                  "and the bucket name."))
            client = boto3.client(
                's3',
                aws_access_key_id=self.aws_access_key,
                aws_secret_access_key=self.aws_secret_access_key)
            # Only needs access to this bucket, unlike list_buckets().
            client.head_bucket(Bucket=self.bucket_file_name)
        except Exception as error:
            self.active = False
            self.hide_active = False
            return self._notification(False, _(
                "An error occurred while testing the connection: %s",
                self._format_error(error)))
        self.active = True
        self.hide_active = True
        return self._notification(True)

    def _compute_redirect_uri(self):
        """Compute the redirect URIs based on the backup destination"""
        for rec in self:
            base_url = rec.get_base_url()
            rec.onedrive_redirect_uri = base_url + '/onedrive/authentication'
            rec.gdrive_redirect_uri = base_url + '/google_drive/authentication'

    @api.depends('gdrive_access_token', 'gdrive_refresh_token')
    def _compute_is_google_drive_token_generated(self):
        """Set True if the Google Drive refresh token is generated"""
        for rec in self:
            rec.is_google_drive_token_generated = bool(
                rec.gdrive_access_token) and bool(rec.gdrive_refresh_token)

    # -------------------------------------------------------------------------
    # OAuth tokens
    # -------------------------------------------------------------------------

    def action_get_gdrive_auth_code(self):
        """Generate Google drive authorization code"""
        self.ensure_one()
        encoded_params = urlencode({
            'response_type': 'code',
            'client_id': self.gdrive_client_key,
            'scope': 'https://www.googleapis.com/auth/drive https://www.googleapis.com/auth/drive.file',
            'redirect_uri': self.get_base_url() + '/google_drive/authentication',
            'access_type': 'offline',
            'state': self._get_oauth_state(),
            'approval_prompt': 'force',
        })
        auth_url = "%s?%s" % (GOOGLE_AUTH_ENDPOINT, encoded_params)
        return {
            'type': 'ir.actions.act_url',
            'target': 'self',
            'url': auth_url,
        }

    def generate_gdrive_refresh_token(self):
        """Generate Google Drive access token from refresh token if expired"""
        self.ensure_one()
        data = {
            'refresh_token': self.gdrive_refresh_token,
            'client_id': self.gdrive_client_key,
            'client_secret': self.gdrive_client_secret,
            'grant_type': 'refresh_token',
        }
        try:
            res = requests.post(GOOGLE_TOKEN_ENDPOINT, data=data,
                                headers=headers, timeout=NETWORK_TIMEOUT)
            res.raise_for_status()
            response = res.content and res.json() or {}
            if response:
                expires_in = response.get('expires_in')
                self.write({
                    'gdrive_access_token': response.get('access_token'),
                    'gdrive_token_validity': fields.Datetime.now() + timedelta(
                        seconds=expires_in) if expires_in else False,
                })
        except requests.HTTPError as error:
            try:
                error_key = error.response.json().get("error", "nc")
            except ValueError:
                error_key = error.response.status_code
            error_msg = _(
                "An error occurred while generating the token. Your "
                "authorization code may be invalid or has already expired [%s]."
                "You should check your Client ID and secret on the Google "
                "APIs platform or set up the token again.",
                error_key)
            raise UserError(error_msg) from error

    def get_gdrive_tokens(self, authorize_code):
        """Generate Google Drive tokens from authorization code"""
        self.ensure_one()
        data = {
            'code': authorize_code,
            'client_id': self.gdrive_client_key,
            'client_secret': self.gdrive_client_secret,
            'grant_type': 'authorization_code',
            'redirect_uri': self.get_base_url() + '/google_drive/authentication'
        }
        try:
            # The secret goes in the form body, never in the URL.
            res = requests.post(GOOGLE_TOKEN_ENDPOINT, data=data,
                                headers=headers, timeout=NETWORK_TIMEOUT)
            res.raise_for_status()
            response = res.content and res.json() or {}
            if response:
                expires_in = response.get('expires_in')
                self.write({
                    'gdrive_access_token': response.get('access_token'),
                    'gdrive_refresh_token': response.get('refresh_token'),
                    'gdrive_token_validity': fields.Datetime.now() + timedelta(
                        seconds=expires_in) if expires_in else False,
                    'gdrive_backup_error_test': False,
                })
        except Exception as error:
            _logger.warning("Google Drive token generation failed for backup "
                            "configuration %s: %s", self.id,
                            self._format_error(error))
            self.gdrive_backup_error_test = True

    @api.depends('onedrive_access_token', 'onedrive_refresh_token')
    def _compute_is_onedrive_token_generated(self):
        """Set true if onedrive tokens are generated"""
        for rec in self:
            rec.is_onedrive_token_generated = bool(
                rec.onedrive_access_token) and bool(rec.onedrive_refresh_token)

    @api.depends('dropbox_refresh_token')
    def _compute_is_dropbox_token_generated(self):
        """Set True if the dropbox refresh token is generated"""
        for rec in self:
            rec.is_dropbox_token_generated = bool(rec.dropbox_refresh_token)

    def action_get_dropbox_auth_code(self):
        """Open a wizard to set up dropbox Authorization code"""
        self.ensure_one()
        # Fail here, not inside the wizard's compute, if dropbox is missing.
        self._import_optional(*DESTINATION_LIBRARIES['dropbox'])
        return {
            'type': 'ir.actions.act_window',
            'name': _('Dropbox Authorization Wizard'),
            'res_model': 'dropbox.auth.code',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_backup_config_id': self.id},
        }

    def action_get_onedrive_auth_code(self):
        """Generate onedrive authorization code"""
        self.ensure_one()
        encoded_params = urlencode({
            'response_type': 'code',
            'client_id': self.onedrive_client_key,
            'state': self._get_oauth_state(),
            'scope': ONEDRIVE_SCOPE,
            'redirect_uri': self.get_base_url() + '/onedrive/authentication',
            'prompt': 'consent',
            'access_type': 'offline'
        })
        auth_url = "%s?%s" % (ONEDRIVE_AUTH_ENDPOINT, encoded_params)
        return {
            'type': 'ir.actions.act_url',
            'target': 'self',
            'url': auth_url,
        }

    def generate_onedrive_refresh_token(self):
        """Generate onedrive access token from refresh token if expired"""
        self.ensure_one()
        data = {
            'client_id': self.onedrive_client_key,
            'client_secret': self.onedrive_client_secret,
            'scope': ONEDRIVE_SCOPE,
            'grant_type': "refresh_token",
            'redirect_uri': self.get_base_url() + '/onedrive/authentication',
            'refresh_token': self.onedrive_refresh_token
        }
        try:
            res = requests.post(ONEDRIVE_TOKEN_ENDPOINT, data=data,
                                headers=headers, timeout=NETWORK_TIMEOUT)
            res.raise_for_status()
            response = res.content and res.json() or {}
            if response:
                expires_in = response.get('expires_in')
                self.write({
                    'onedrive_access_token': response.get('access_token'),
                    'onedrive_refresh_token': response.get('refresh_token'),
                    'onedrive_token_validity': fields.Datetime.now() + timedelta(
                        seconds=expires_in) if expires_in else False,
                })
        except requests.HTTPError as error:
            raise UserError(_(
                "Bad Microsoft OneDrive request, the token could not be "
                "refreshed: %s", self._format_error(error))) from error

    def get_onedrive_tokens(self, authorize_code):
        """Generate onedrive tokens from authorization code"""
        self.ensure_one()
        data = {
            'code': authorize_code,
            'client_id': self.onedrive_client_key,
            'client_secret': self.onedrive_client_secret,
            'grant_type': 'authorization_code',
            'scope': ONEDRIVE_SCOPE,
            'redirect_uri': self.get_base_url() + '/onedrive/authentication'
        }
        try:
            res = requests.post(ONEDRIVE_TOKEN_ENDPOINT, data=data,
                                headers=headers, timeout=NETWORK_TIMEOUT)
            res.raise_for_status()
            response = res.content and res.json() or {}
            if response:
                expires_in = response.get('expires_in')
                self.write({
                    'onedrive_access_token': response.get('access_token'),
                    'onedrive_refresh_token': response.get('refresh_token'),
                    'onedrive_token_validity': fields.Datetime.now() + timedelta
                    (seconds=expires_in) if expires_in else False,
                    'onedrive_backup_error_test': False,
                })
        except Exception as error:
            _logger.warning("OneDrive token generation failed for backup "
                            "configuration %s: %s", self.id,
                            self._format_error(error))
            self.onedrive_backup_error_test = True

    def _dropbox_auth_flow(self):
        dropbox = self._import_optional(*DESTINATION_LIBRARIES['dropbox'])
        return dropbox.DropboxOAuth2FlowNoRedirect(
            self.dropbox_client_key, self.dropbox_client_secret,
            token_access_type='offline')

    def get_dropbox_auth_url(self):
        """Return dropbox authorization url"""
        self.ensure_one()
        return self._dropbox_auth_flow().start()

    def set_dropbox_refresh_token(self, auth_code):
        """Generate and set the dropbox refresh token from authorization code"""
        self.ensure_one()
        dbx_auth = self._dropbox_auth_flow()
        try:
            outh_result = dbx_auth.finish(auth_code)
        except Exception as error:
            raise ValidationError(
                _('Please Enter Valid Authentication Code')) from error
        self.dropbox_refresh_token = outh_result.refresh_token

    # -------------------------------------------------------------------------
    # Constraints
    # -------------------------------------------------------------------------

    @api.constrains('db_name', 'master_pwd')
    def _check_db_credentials(self):
        """Validate entered database name and master password"""
        database_list = odoo_db.list_dbs(force=True)
        for rec in self:
            if rec.db_name not in database_list:
                raise ValidationError(_("Invalid Database Name!"))
            try:
                odoo_db.verify_admin_password(rec.master_pwd)
            except AccessDenied:
                raise ValidationError(_("Invalid Master Password!")) from None

    @api.constrains('auto_remove', 'days_to_remove')
    def _check_days_to_remove(self):
        """With 0 days every stored backup is removed after each run."""
        for rec in self:
            if rec.auto_remove and rec.days_to_remove < 1:
                raise ValidationError(_(
                    "Old backups can only be removed after at least 1 day."))

    def action_sftp_connection(self):
        """Test the sftp and ftp connection using entered credentials"""
        self.ensure_one()
        if self.backup_destination == 'sftp':
            self._import_optional(*DESTINATION_LIBRARIES['sftp'])
            try:
                client = self._sftp_connect()
                try:
                    client.open_sftp().close()
                finally:
                    client.close()
            except Exception as e:
                raise UserError(_("SFTP Exception: %s", e)) from e
        elif self.backup_destination == 'ftp':
            try:
                with self._ftp_connect():
                    pass
            except Exception as e:
                raise UserError(_("FTP Exception: %s", e)) from e
        self.hide_active = True
        self.active = True
        return self._notification(True)

    # -------------------------------------------------------------------------
    # Backup
    # -------------------------------------------------------------------------

    def _schedule_auto_backup(self):
        """Function for generating and storing backup Database backup for all
        the active records in backup configuration model will be created"""
        for rec in self.search([]):
            rec._run_backup()

    def _run_backup(self):
        """Back up this configuration's database to its destination and
        notify the user. A failure is recorded and mailed, never raised, so
        that one broken configuration does not stop the others."""
        self.ensure_one()
        backup_filename = "%s_%s.%s" % (
            self.db_name, fields.Datetime.now().strftime(BACKUP_TIME_FORMAT),
            self.backup_format)
        self.backup_filename = backup_filename
        try:
            if not self.backup_destination:
                raise UserError(_("No backup destination is set."))
            if self.backup_destination in DESTINATION_LIBRARIES:
                # before spending time on the dump
                self._import_optional(
                    *DESTINATION_LIBRARIES[self.backup_destination])
            if self.backup_destination == 'local':
                self._backup_to_local(backup_filename)
            else:
                with self._dump_to_tempfile(backup_filename) as backup_file:
                    getattr(self, '_backup_to_%s' % self.backup_destination)(
                        backup_file, backup_filename)
        except Exception as error:
            self.generated_exception = self._format_error(error)
            _logger.error('Database backup "%s" of %s to %s failed: %s',
                          self.name, self.db_name, self.backup_destination,
                          self.generated_exception,
                          exc_info=not isinstance(error, UserError))
            self._notify_user(
                'ebshel_auto_database_backup.mail_template_data_db_backup_failed')
        else:
            self.generated_exception = False
            _logger.info('Database backup "%s" of %s stored to %s as %s',
                         self.name, self.db_name, self.backup_destination,
                         backup_filename)
            self._notify_user(
                'ebshel_auto_database_backup.mail_template_data_db_backup_successful')

    def _notify_user(self, template_xmlid):
        if not (self.notify_user and self.user_id):
            return
        template = self.env.ref(template_xmlid, raise_if_not_found=False)
        if not template:
            return
        try:
            template.send_mail(self.id, force_send=True)
        except Exception:
            _logger.exception("Could not send the backup notification of %s",
                              self.name)

    @contextlib.contextmanager
    def _dump_to_tempfile(self, backup_filename):
        """Dump the database into a temporary file, removed afterwards."""
        with tempfile.TemporaryDirectory(prefix='odoo_backup_') as tmp_dir:
            backup_file = os.path.join(tmp_dir, backup_filename)
            with open(backup_file, 'wb') as stream:
                self._dump_data(self.db_name, stream, self.backup_format)
            yield backup_file

    def _backup_to_local(self, backup_filename):
        if not self.backup_path:
            raise UserError(_("No backup path is set."))
        os.makedirs(self.backup_path, exist_ok=True)
        backup_file = os.path.join(self.backup_path, backup_filename)
        try:
            with open(backup_file, "wb") as stream:
                self._dump_data(self.db_name, stream, self.backup_format)
        except BaseException:
            # never leave a truncated backup behind
            with contextlib.suppress(OSError):
                os.remove(backup_file)
            raise
        if self.auto_remove:
            for filename in os.listdir(self.backup_path):
                if self._is_expired_backup(filename, backup_filename):
                    os.remove(os.path.join(self.backup_path, filename))

    def _ftp_connect(self):
        ftp_server = ftplib.FTP(timeout=NETWORK_TIMEOUT, encoding='utf-8')
        try:
            ftp_server.connect(self.ftp_host, int(self.ftp_port or 21))
            ftp_server.login(self.ftp_user, self.ftp_password)
        except BaseException:
            ftp_server.close()
            raise
        return ftp_server

    def _backup_to_ftp(self, backup_file, backup_filename):
        with self._ftp_connect() as ftp_server:
            if self.ftp_path:
                try:
                    ftp_server.cwd(self.ftp_path)
                except ftplib.error_perm:
                    ftp_server.mkd(self.ftp_path)
                    ftp_server.cwd(self.ftp_path)
            with open(backup_file, 'rb') as stream:
                ftp_server.storbinary('STOR %s' % backup_filename, stream)
            if self.auto_remove:
                for name in ftp_server.nlst():
                    # some servers answer NLST with paths
                    filename = posixpath.basename(name)
                    if self._is_expired_backup(filename, backup_filename):
                        ftp_server.delete(filename)

    def _sftp_connect(self):
        paramiko = self._import_optional(*DESTINATION_LIBRARIES['sftp'])
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(hostname=self.sftp_host,
                           username=self.sftp_user,
                           password=self.sftp_password,
                           port=int(self.sftp_port or 22),
                           timeout=NETWORK_TIMEOUT)
        except BaseException:
            client.close()
            raise
        return client

    def _backup_to_sftp(self, backup_file, backup_filename):
        client = self._sftp_connect()
        try:
            with client.open_sftp() as sftp:
                if self.sftp_path:
                    try:
                        sftp.chdir(self.sftp_path)
                    except IOError as e:
                        if e.errno != errno.ENOENT:
                            raise
                        sftp.mkdir(self.sftp_path)
                        sftp.chdir(self.sftp_path)
                sftp.put(backup_file, backup_filename)
                if self.auto_remove:
                    for filename in sftp.listdir():
                        if self._is_expired_backup(filename, backup_filename):
                            sftp.unlink(filename)
        finally:
            client.close()

    def _backup_to_google_drive(self, backup_file, backup_filename):
        if self.gdrive_token_validity and \
                self.gdrive_token_validity <= fields.Datetime.now():
            self.generate_gdrive_refresh_token()
        auth = {"Authorization": "Bearer %s" % self.gdrive_access_token}
        metadata = {
            "name": backup_filename,
            "parents": [self.google_drive_folder_key],
        }
        size = os.path.getsize(backup_file)
        # Resumable upload: the file is streamed, not loaded in memory, and
        # is not subject to the 5 MB limit of multipart uploads.
        session = requests.post(
            GOOGLE_API_BASE_URL + "/upload/drive/v3/files",
            params={'uploadType': 'resumable'},
            headers=dict(auth, **{
                'Content-Type': 'application/json; charset=UTF-8',
                'X-Upload-Content-Length': str(size),
            }),
            data=json.dumps(metadata), timeout=NETWORK_TIMEOUT)
        session.raise_for_status()
        with open(backup_file, 'rb') as stream:
            upload = requests.put(
                session.headers['Location'], data=stream,
                headers=dict(auth, **{'Content-Length': str(size)}),
                timeout=NETWORK_TIMEOUT)
        upload.raise_for_status()
        if self.auto_remove:
            files_url = GOOGLE_API_BASE_URL + "/drive/v3/files"
            params = {
                'q': "'%s' in parents and trashed = false" % (
                    self.google_drive_folder_key or '').replace("'", "\\'"),
                'fields': 'nextPageToken, files(id, name)',
                'pageSize': 1000,
            }
            while True:
                res = requests.get(files_url, params=params, headers=auth,
                                   timeout=NETWORK_TIMEOUT)
                res.raise_for_status()
                result = res.json()
                for file in result.get('files', []):
                    if self._is_expired_backup(file['name'], backup_filename):
                        requests.delete(
                            "%s/%s" % (files_url, file['id']), headers=auth,
                            timeout=NETWORK_TIMEOUT).raise_for_status()
                if not result.get('nextPageToken'):
                    break
                params['pageToken'] = result['nextPageToken']

    def _backup_to_dropbox(self, backup_file, backup_filename):
        dropbox = self._import_optional(*DESTINATION_LIBRARIES['dropbox'])
        from dropbox import files as dropbox_files
        drop_connection = dropbox.Dropbox(
            app_key=self.dropbox_client_key,
            app_secret=self.dropbox_client_secret,
            oauth2_refresh_token=self.dropbox_refresh_token,
            timeout=NETWORK_TIMEOUT)
        # Dropbox paths must start with '/'; the root folder is ''.
        folder = (self.dropbox_folder or '').strip('/')
        folder = '/' + folder if folder else ''
        dropbox_destination = folder + '/' + backup_filename
        size = os.path.getsize(backup_file)
        with open(backup_file, 'rb') as stream:
            if size <= DROPBOX_CHUNK_SIZE:
                drop_connection.files_upload(stream.read(),
                                             dropbox_destination)
            else:
                session = drop_connection.files_upload_session_start(
                    stream.read(DROPBOX_CHUNK_SIZE))
                cursor = dropbox_files.UploadSessionCursor(
                    session_id=session.session_id, offset=stream.tell())
                while size - stream.tell() > DROPBOX_CHUNK_SIZE:
                    drop_connection.files_upload_session_append_v2(
                        stream.read(DROPBOX_CHUNK_SIZE), cursor)
                    cursor.offset = stream.tell()
                drop_connection.files_upload_session_finish(
                    stream.read(), cursor,
                    dropbox_files.CommitInfo(path=dropbox_destination))
        if self.auto_remove:
            result = drop_connection.files_list_folder(folder)
            while True:
                for entry in result.entries:
                    if isinstance(entry, dropbox_files.FileMetadata) and \
                            self._is_expired_backup(entry.name,
                                                    backup_filename):
                        drop_connection.files_delete_v2(entry.path_lower)
                if not result.has_more:
                    break
                result = drop_connection.files_list_folder_continue(
                    result.cursor)

    def _backup_to_onedrive(self, backup_file, backup_filename):
        if self.onedrive_token_validity and \
                self.onedrive_token_validity <= fields.Datetime.now():
            self.generate_onedrive_refresh_token()
        auth = {'Authorization': 'Bearer %s' % self.onedrive_access_token}
        upload_session_url = MICROSOFT_GRAPH_END_POINT + \
            "/v1.0/me/drive/items/%s:/%s:/createUploadSession" % (
                self.onedrive_folder_key, quote(backup_filename))
        upload_session = requests.post(
            upload_session_url,
            headers=dict(auth, **{'Content-Type': 'application/json'}),
            timeout=NETWORK_TIMEOUT)
        upload_session.raise_for_status()
        upload_url = upload_session.json()['uploadUrl']
        # An upload session takes the file in fragments of at most 60 MiB,
        # each with its Content-Range (the URL is pre-authenticated).
        size = os.path.getsize(backup_file)
        with open(backup_file, 'rb') as stream:
            offset = 0
            while offset < size:
                chunk = stream.read(ONEDRIVE_CHUNK_SIZE)
                end = offset + len(chunk) - 1
                requests.put(upload_url, data=chunk, headers={
                    'Content-Length': str(len(chunk)),
                    'Content-Range': 'bytes %d-%d/%d' % (offset, end, size),
                }, timeout=NETWORK_TIMEOUT).raise_for_status()
                offset = end + 1
        if self.auto_remove:
            list_url = MICROSOFT_GRAPH_END_POINT + \
                "/v1.0/me/drive/items/%s/children?$select=id,name" % \
                self.onedrive_folder_key
            while list_url:
                response = requests.get(list_url, headers=auth,
                                        timeout=NETWORK_TIMEOUT)
                response.raise_for_status()
                result = response.json()
                for file in result.get('value', []):
                    if self._is_expired_backup(file['name'], backup_filename):
                        requests.delete(
                            MICROSOFT_GRAPH_END_POINT +
                            "/v1.0/me/drive/items/%s" % file['id'],
                            headers=auth,
                            timeout=NETWORK_TIMEOUT).raise_for_status()
                list_url = result.get('@odata.nextLink')

    def _backup_to_amazon_s3(self, backup_file, backup_filename):
        boto3 = self._import_optional(*DESTINATION_LIBRARIES['amazon_s3'])
        if not (self.aws_access_key and self.aws_secret_access_key and
                self.bucket_file_name):
            raise UserError(_("Please set the access key, the secret key and "
                              "the bucket name."))
        client = boto3.client(
            's3',
            aws_access_key_id=self.aws_access_key,
            aws_secret_access_key=self.aws_secret_access_key)
        prefix = (self.aws_folder_name or '').strip('/')
        prefix = prefix + '/' if prefix else ''
        # upload_file switches to a multipart upload for large files
        client.upload_file(backup_file, self.bucket_file_name,
                           prefix + backup_filename)
        if self.auto_remove:
            paginator = client.get_paginator('list_objects_v2')
            for page in paginator.paginate(Bucket=self.bucket_file_name,
                                           Prefix=prefix):
                for obj in page.get('Contents', []):
                    filename = obj['Key'][len(prefix):]
                    if '/' not in filename and self._is_expired_backup(
                            filename, backup_filename):
                        client.delete_object(Bucket=self.bucket_file_name,
                                             Key=obj['Key'])

    def _backup_to_next_cloud(self, backup_file, backup_filename):
        nextcloud_client = self._import_optional(
            *DESTINATION_LIBRARIES['next_cloud'])
        client = self._nextcloud_client()
        folder = '/' + (self.nextcloud_folder_key or '').strip('/')
        try:
            if folder != '/':
                try:
                    client.file_info(folder)
                except nextcloud_client.HTTPResponseError as error:
                    if error.status_code != 404:
                        raise
                    client.mkdir(folder)
            if not client.put_file(posixpath.join(folder, backup_filename),
                                   backup_file):
                raise UserError(_("Nextcloud refused the backup file."))
            if self.auto_remove:
                for item in client.list(folder):
                    if not item.is_dir() and self._is_expired_backup(
                            item.get_name(), backup_filename):
                        client.delete(item.path)
        finally:
            client.logout()

    def _dump_data(self, db_name, stream, backup_format):
        """Dump database `db_name` into the binary file object `stream`,
        as pg_dump does (works with list_db = False)."""
        cron = self.env.ref('ebshel_auto_database_backup.ir_cron_auto_db_backup',
                            raise_if_not_found=False)
        if not cron or cron.user_id != self.env.user:
            _logger.error(
                'Unauthorized database operation. Backups should only be '
                'available from the cron job.')
            raise AccessError(_(
                "Unauthorized database operation. Backups should only be "
                "available from the cron job."))

        _logger.info('DUMP DB: %s format %s', db_name, backup_format)
        cmd = [find_pg_tool('pg_dump'), '--no-owner', db_name]
        env = exec_pg_environ()
        if backup_format == 'zip':
            with tempfile.TemporaryDirectory() as dump_dir:
                cmd.insert(-1, '--file=' + os.path.join(dump_dir, 'dump.sql'))
                self._run_pg_dump(cmd, env, subprocess.DEVNULL)
                # after the SQL dump, so that every attachment it references
                # is in the copy
                filestore = config.filestore(db_name)
                if os.path.exists(filestore):
                    shutil.copytree(filestore,
                                    os.path.join(dump_dir, 'filestore'))
                with open(os.path.join(dump_dir, 'manifest.json'), 'w',
                          encoding='utf-8') as fh:
                    with odoo.sql_db.db_connect(db_name).cursor() as cr:
                        json.dump(self._dump_manifest(cr), fh, indent=4)
                osutil.zip_dir(dump_dir, stream, include_dir=False,
                               fnct_sort=lambda file_name:
                               file_name != 'dump.sql')
        else:
            cmd.insert(-1, '--format=c')
            # streamed straight into the file, not buffered in memory
            stream.flush()
            self._run_pg_dump(cmd, env, stream)

    @api.model
    def _dump_manifest(self, cr):
        """The manifest.json of a zip backup, as Odoo's own database manager
        writes it. Odoo's helper (odoo.modules.db._dump_db_manifest) is
        private and memoised per cursor, so the module writes its own."""
        pg_version = "%d.%d" % divmod(
            cr._obj.connection.server_version / 100, 100)
        cr.execute("SELECT name, latest_version FROM ir_module_module "
                   "WHERE state = 'installed'")
        return {
            'odoo_dump': '1',
            'db_name': cr.dbname,
            'version': odoo.release.version,
            'version_info': odoo.release.version_info,
            'major_version': odoo.release.major_version,
            'pg_version': pg_version,
            'modules': dict(cr.fetchall()),
        }

    @api.model
    def _run_pg_dump(self, cmd, env, stdout):
        result = subprocess.run(cmd, env=env, stdin=subprocess.DEVNULL,
                                stdout=stdout, stderr=subprocess.PIPE,
                                check=False)
        if result.returncode:
            raise UserError(_(
                "pg_dump failed (exit code %(code)s): %(error)s",
                code=result.returncode,
                error=result.stderr.decode(errors='replace').strip()[-1000:]))

    # -------------------------------------------------------------------------
    # Home menu warnings
    # -------------------------------------------------------------------------

    # how late the daily job may be before the home menu says backups stopped
    SCHEDULER_GRACE = timedelta(days=1)

    def _attempt_time(self):
        """When the last backup of this configuration was tried (UTC), read from
        the timestamp the run puts in ``backup_filename``."""
        match = re.search(r'_(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})\.(?:zip|dump)$',
                          self.backup_filename or '')
        if match:
            try:
                return datetime.strptime(match.group(1), BACKUP_TIME_FORMAT)
            except ValueError:
                pass
        return self.write_date

    def _format_when(self, value):
        tz = self.env.user.tz or self.env.company.partner_id.tz or 'UTC'
        return format_datetime(self.env, value, tz=tz, dt_format='d MMM y, h:mm a') if value else _("an unknown date")

    @api.model
    def _home_alerts(self):
        """What the home menu warns administrators about (see backend_theme): a
        backup whose last run failed, a backup job that keeps failing or that Odoo
        switched off, and a backup job that stopped running. Nothing for other
        users, and nothing while no backup is configured."""
        if not self.env.user.has_group('base.group_system'):
            return []
        alerts = []
        configurations = self.search([])
        for configuration in configurations.filtered('generated_exception'):
            alerts.append({
                'level': 'danger',
                'title': _("Database backup failed"),
                'message': _(
                    '"%(name)s" (%(destination)s) failed on %(when)s: %(error)s',
                    name=configuration.name,
                    destination=dict(configuration._fields['backup_destination']._description_selection(self.env)).get(
                        configuration.backup_destination, configuration.backup_destination or _("no destination")),
                    when=self._format_when(configuration._attempt_time()),
                    error=(configuration.generated_exception or '')[:300]),
                'action': {
                    'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': configuration.id,
                    'views': [[False, 'form']], 'target': 'current',
                },
            })
        cron = self.env.ref('ebshel_auto_database_backup.ir_cron_auto_db_backup', raise_if_not_found=False)
        if configurations and cron:
            cron = cron.sudo()
            open_cron = {'type': 'ir.actions.act_window', 'res_model': 'ir.cron', 'res_id': cron.id,
                         'views': [[False, 'form']], 'target': 'current'}
            if not cron.active:
                alerts.append({
                    'level': 'danger',
                    'title': _("Database backups are switched off"),
                    'message': _('The scheduled job "%(job)s" is not active, so no backup is taken. '
                                 '(Odoo switches a job off after it failed 5 times over a week.)',
                                 job=cron.cron_name),
                    'action': open_cron,
                })
            elif cron.failure_count:
                alerts.append({
                    'level': 'danger',
                    'title': _("The database backup job is failing"),
                    'message': _('"%(job)s" failed %(count)s time(s) in a row since %(since)s.',
                                 job=cron.cron_name, count=cron.failure_count,
                                 since=self._format_when(cron.first_failure_date)),
                    'action': open_cron,
                })
            elif cron.nextcall and cron.nextcall < fields.Datetime.now() - self.SCHEDULER_GRACE:
                alerts.append({
                    'level': 'warning',
                    'title': _("No database backup is being taken"),
                    'message': _('"%(job)s" was due on %(due)s and has not run: the scheduled jobs of '
                                 'this server are not running.',
                                 job=cron.cron_name, due=self._format_when(cron.nextcall)),
                    'action': open_cron,
                })
        return alerts
