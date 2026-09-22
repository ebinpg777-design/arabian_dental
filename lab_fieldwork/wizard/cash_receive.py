# -*- coding: utf-8 -*-
"""The office's side of a handover: type the code, count, confirm.

One box. The executive reads six letters across the desk (or the office
scans the QR on their phone), the slip appears with what was declared, the
office types what it counted and confirms. Built for the person at the desk
with an envelope in one hand, not for somebody browsing a list.
(client, 2026-09-18)
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class CashReceive(models.TransientModel):
    _name = 'lab.cash.receive'
    _description = 'Receive Field Cash'

    code = fields.Char(
        string='Handover Code',
        help="The six letters on the executive's phone, or under the QR on the slip.")
    handover_id = fields.Many2one('lab.cash.handover', string='Handover', readonly=True)
    user_id = fields.Many2one(related='handover_id.user_id', string='Handed Over By')
    date = fields.Date(related='handover_id.date', string='Handed Over On')
    amount = fields.Monetary(related='handover_id.amount', string='Declared',
                             currency_field='currency_id')
    counted_amount = fields.Monetary(string='Counted', currency_field='currency_id')
    difference_reason = fields.Char(
        string='Why It Differs',
        help="Needed when the count is not the declared amount. The executive reads it.")
    currency_id = fields.Many2one(
        'res.currency', default=lambda self: self.env.company.currency_id)
    not_found = fields.Boolean(compute='_compute_not_found')

    @api.depends('code', 'handover_id')
    def _compute_not_found(self):
        for wiz in self:
            wiz.not_found = bool(wiz.code) and not wiz.handover_id

    @api.onchange('code')
    def _onchange_code(self):
        for wiz in self:
            found = self.env['lab.cash.handover'].find_by_code(wiz.code)
            wiz.handover_id = found if found and found.state == 'declared' else False
            wiz.counted_amount = wiz.handover_id.amount if wiz.handover_id else 0.0

    @api.onchange('handover_id')
    def _onchange_handover(self):
        for wiz in self:
            if wiz.handover_id and not wiz.counted_amount:
                wiz.counted_amount = wiz.handover_id.amount

    def action_confirm(self):
        self.ensure_one()
        handover = self.handover_id
        if not handover:
            found = self.env['lab.cash.handover'].find_by_code(self.code)
            if not found:
                raise UserError(_("No handover carries the code %s.", self.code or ''))
            if found.state != 'declared':
                raise UserError(_(
                    "%(name)s (%(code)s) is %(state)s, not waiting to be counted.",
                    name=found.name, code=found.code,
                    state=dict(found._fields['state']._description_selection(
                        self.env)).get(found.state, found.state)))
            handover = found
        handover.write({'counted_amount': self.counted_amount,
                        'difference_reason': self.difference_reason})
        handover.action_confirm_receipt()
        return handover._open()
