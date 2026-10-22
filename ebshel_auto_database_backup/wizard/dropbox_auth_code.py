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
from odoo import api, fields, models


class DropboxAuthCode(models.TransientModel):
    """Model for managing the Dropbox authentication code and URL.
    Methods:
        _compute_dropbox_auth_url:
                    Compute method to retrieve the Dropbox authentication URL
        action_setup_dropbox_token:
            Method to set up the Dropbox token using the provided authorization
            code.
    """
    _name = 'dropbox.auth.code'
    _description = 'Dropbox Authentication Code Wizard'

    backup_config_id = fields.Many2one(
        'db.backup.configure', string='Backup Configuration', required=True,
        ondelete='cascade',
        default=lambda self: self.env.context.get('active_id')
        if self.env.context.get('active_model') == 'db.backup.configure'
        else False,
        help='The backup configuration the Dropbox token is set up for')
    dropbox_authorization_code = fields.Char(string='Authorization Code',
                                             help='Authorization code received'
                                                  'from Dropbox')
    dropbox_auth_url = fields.Char(string='Dropbox Authentication URL',
                                   compute='_compute_dropbox_auth_url',
                                   help='URL for Dropbox authentication')

    @api.depends('backup_config_id')
    def _compute_dropbox_auth_url(self):
        """Compute method to retrieve the Dropbox authentication URL"""
        for rec in self:
            rec.dropbox_auth_url = rec.backup_config_id.get_dropbox_auth_url() \
                if rec.backup_config_id else False

    def action_setup_dropbox_token(self):
        """Method to set up the Dropbox token using the provided authorization
            code."""
        self.ensure_one()
        backup_config = self.backup_config_id
        backup_config.set_dropbox_refresh_token(self.dropbox_authorization_code)
        backup_config.hide_active = True
        backup_config.active = True
