# -*- coding: utf-8 -*-
from odoo import _, fields, models

from .statement_engine import STATEMENT_TYPES


class ResPartner(models.Model):
    _inherit = 'res.partner'

    statement_auto_send = fields.Boolean(
        string='Monthly Statement by E-mail',
        help="Every month the statement of the previous month is e-mailed to this partner "
             "automatically (needs an e-mail address).")
    statement_send_type = fields.Selection(
        STATEMENT_TYPES, string='Statement Type', default='receivable')
    statement_last_sent = fields.Date(string='Statement Last Sent', readonly=True, copy=False)

    def action_open_statement_wizard(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': _('Statement of Account'),
            'res_model': 'epg.partner.statement.wizard', 'view_mode': 'form', 'target': 'new',
            'context': {'active_model': 'res.partner', 'active_ids': self.ids,
                        'default_partner_ids': [(6, 0, self.ids)]},
        }
