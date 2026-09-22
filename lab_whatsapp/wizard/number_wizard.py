# -*- coding: utf-8 -*-
import re

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class LabWhatsappNumber(models.TransientModel):
    """The doctor's WhatsApp number, asked for on the record that needs it.

    "Add WhatsApp number" on an invoice used to mean opening the contact, finding
    the field, saving, coming back. One box, one Save. (client, 2026-09-17)
    """
    _name = 'lab.whatsapp.number'
    _description = 'WhatsApp number'

    partner_id = fields.Many2one('res.partner', required=True, readonly=True)
    number = fields.Char('WhatsApp Number', required=True)
    number_ok = fields.Boolean(compute='_compute_number_ok')

    @api.depends('number')
    def _compute_number_ok(self):
        Template = self.env['epg.whatsapp.template']
        for wizard in self:
            # Country code and a full national number: ten digits at the least.
            digits = Template._normalise_number(wizard.number, wizard.partner_id) \
                if wizard.number else ''
            wizard.number_ok = len(re.sub(r'\D', '', digits)) >= 10

    def action_save(self):
        self.ensure_one()
        if not self.number_ok:
            raise UserError(_("That is not a number WhatsApp will accept - it needs a "
                              "country code and a full national number."))
        # Through sudo, and only these two fields: the person at the counter is
        # allowed to say where a WhatsApp goes - that is what the button is - but
        # in this database they may not edit contacts at large. (production,
        # 2026-09-18)
        self.partner_id.sudo().write({'whatsapp_number': self.number,
                                      'whatsapp_optin': True})
        return {'type': 'ir.actions.act_window_close'}
