# -*- coding: utf-8 -*-
"""Saved replies: the sentences the desk types every day, one pick away.

"We are open 9 to 6", "Your case will be ready on Thursday", "Please share the
impression photo" - typed a hundred times a month. A saved reply drops in with the
record's own values filled in. (client, 2026-09-17)
"""
from odoo import api, fields, models


class EpgWhatsappSnippet(models.Model):
    _name = 'epg.whatsapp.snippet'
    _description = 'WhatsApp Saved Reply'
    _order = 'sequence, name'

    name = fields.Char(required=True, help="How it is listed, e.g. Opening hours.")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    body = fields.Text(
        'Text', required=True,
        help="Use {{name}}, {{partner_id.name}} or any field of the record in double "
             "braces; they are filled in when the reply is picked.")
    model_id = fields.Many2one(
        'ir.model', string='Only On', ondelete='cascade',
        help="Offered only when writing from this kind of record. Empty: everywhere.")
    model = fields.Char(related='model_id.model', store=True)
    company_id = fields.Many2one('res.company', default=lambda s: s.env.company)

    @api.model
    def for_model(self, model_name):
        """The replies offered on a record of this model, as the pickers show them."""
        domain = ['|', ('model', '=', False), ('model', '=', model_name or False)]
        return [{'id': s.id, 'name': s.name, 'body': s.body}
                for s in self.search(domain)]

    def render(self, res_model=None, res_id=None):
        """This reply, with the record's values filled in."""
        self.ensure_one()
        record = None
        if res_model and res_id and res_model in self.env:
            record = self.env[res_model].browse(int(res_id)).exists() or None
        Template = self.env['epg.whatsapp.template']
        return Template._render_text(record, self.body) if record is not None else self.body
