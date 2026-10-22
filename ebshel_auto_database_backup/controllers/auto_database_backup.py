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
from werkzeug.exceptions import BadRequest, Forbidden

from odoo import http
from odoo.http import request


class OnedriveAuth(http.Controller):
    """Controller for handling OneDrive and google authentication
    Methods:
        oauth2callback: Callback route for OneDrive authentication
        gdrive_oauth2callback: Callback route for Google Drive authentication
        """

    def _oauth_callback(self, token_method, token_flag, kw):
        """Store the tokens of an authorization code on the configuration the
        signed `state` names. Runs as the logged in user, so only users who
        may edit backup configurations (Settings) can set tokens, and only
        for a flow they started themselves."""
        if not kw.get('state'):
            raise BadRequest()
        backup_config = request.env['db.backup.configure'].with_context(
            active_test=False)._get_record_from_oauth_state(kw['state'])
        if not backup_config:
            raise Forbidden()
        backup_config.check_access('write')
        getattr(backup_config, token_method)(kw.get('code'))
        if backup_config[token_flag]:
            backup_config.hide_active = True
            backup_config.active = True
        return request.redirect(backup_config._get_form_url())

    @http.route('/onedrive/authentication', type='http', auth="user")
    def oauth2callback(self, **kw):
        """Callback route for OneDrive authentication"""
        return self._oauth_callback('get_onedrive_tokens',
                                    'is_onedrive_token_generated', kw)

    @http.route('/google_drive/authentication', type='http', auth="user")
    def gdrive_oauth2callback(self, **kw):
        """Callback route for Google Drive authentication"""
        return self._oauth_callback('get_gdrive_tokens',
                                    'is_google_drive_token_generated', kw)
