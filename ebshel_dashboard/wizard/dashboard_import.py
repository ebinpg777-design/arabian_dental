# -*- coding: utf-8 -*-
"""Import a dashboard from a file somebody exported elsewhere.

The work is done by :meth:`DashboardBoard.import_boards`; this wizard is the
door to it. It exists because an import can only ever be partly successful - a
board built where Sales is installed will not bring its sales cards into a
database without it - and a user needs to be told exactly what did not come
across, rather than being handed either a silent gap or a failure.
"""
import base64
import binascii
import json
import logging

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class DashboardImport(models.TransientModel):
    _name = 'dashboard.import'
    _description = 'Import a Dashboard'

    file_data = fields.Binary(string='File', required=True, attachment=False)
    file_name = fields.Char(string='File Name')
    replace = fields.Boolean(
        string='Replace Existing',
        help="Delete any dashboard already carrying the same name before "
             "importing. Off, the import adds a second one.")
    state = fields.Selection(
        [('choose', 'Choose'), ('done', 'Done')], default='choose')
    result = fields.Text(string='Result', readonly=True)
    board_ids = fields.Many2many('dashboard.board', string='Imported')

    @api.onchange('file_data')
    def _onchange_file_data(self):
        self.state = 'choose'
        self.result = False

    def action_import(self):
        """Read the file, create what it describes, and report what it could not."""
        self.ensure_one()
        try:
            payload = json.loads(base64.b64decode(self.file_data or b'').decode('utf-8'))
        except (ValueError, UnicodeDecodeError, binascii.Error) as err:
            raise UserError(self.env._(
                'This file is not a dashboard export: %(error)s', error=err)) from err

        outcome = self.env['dashboard.board'].import_boards(payload, replace=self.replace)
        boards = self.env['dashboard.board'].browse(outcome['boards'])
        lines = [self.env._('%(count)s dashboard(s) imported: %(names)s',
                            count=len(boards), names=', '.join(boards.mapped('name')) or '-')]
        if outcome['skipped']:
            lines.append('')
            lines.append(self.env._(
                'These cards were skipped because their model is not installed here:'))
            lines.extend(f'  - {name}' for name in outcome['skipped'])
        self.write({
            'state': 'done',
            'result': '\n'.join(lines),
            'board_ids': [(6, 0, boards.ids)],
        })
        # Same wizard, second page: the user has to see what was skipped before
        # the window closes on them.
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('Import a Dashboard'),
            'res_model': 'dashboard.import',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def action_open_imported(self):
        self.ensure_one()
        if len(self.board_ids) == 1:
            return self.board_ids.action_open_board()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('Imported Dashboards'),
            'res_model': 'dashboard.board',
            'domain': [('id', 'in', self.board_ids.ids)],
            'view_mode': 'list,form',
        }
