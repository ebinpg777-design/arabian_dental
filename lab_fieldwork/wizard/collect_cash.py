# -*- coding: utf-8 -*-
"""Cash taken at a door the executive did not raise a visit for.

Money arrives ahead of the paperwork. A doctor settles an old bill while the
executive is there for something else, a receptionist hands over an envelope on
the way past, a payment is made for a case somebody else delivered. Until now
the only way into the float was the button on a visit, so recording that money
meant inventing a visit that never happened - which puts a false call in the
day sheet to get a true number into the cash box.

This records the money on its own terms. It reaches the float by the SAME path
the visit button uses, so the accountant sees one kind of collection however it
was entered, and a visit can still be attached afterwards if one turns up.
(client, 2026-09-12)
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class CollectCash(models.TransientModel):
    _name = 'lab.collect.cash'
    _description = 'Record Cash Collected'

    user_id = fields.Many2one(
        'res.users', string='Collected by', required=True,
        default=lambda self: self.env.user)
    partner_id = fields.Many2one(
        'res.partner', string='Doctor / Clinic', required=True,
        help="Who the money came from.")
    amount = fields.Monetary(
        string='Amount', required=True, currency_field='currency_id')
    currency_id = fields.Many2one(
        'res.currency', compute='_compute_float', string='Currency')
    date = fields.Date(
        string='Collected on', required=True,
        default=lambda self: fields.Date.context_today(self))
    pay_mode = fields.Selection(
        [('cash', 'Cash'), ('cheque', 'Cheque'), ('online', 'UPI / Online')],
        string='Received As', required=True, default='cash')
    note = fields.Char(
        string='Note',
        help="What this payment was for, if it is worth saying.")

    allocation_id = fields.Many2one(
        'petty.cash.allocation', compute='_compute_float', string='Float')
    balance = fields.Monetary(
        compute='_compute_float', string='Cash in Hand',
        currency_field='currency_id')

    @api.depends('user_id')
    def _compute_float(self):
        for rec in self:
            allocation = rec.user_id.sudo().petty_cash_allocation_id
            rec.allocation_id = allocation
            rec.balance = rec.user_id.sudo().petty_cash_balance
            rec.currency_id = (allocation.currency_id
                               or rec.env.company.currency_id)

    def action_record(self):
        """Put the money in the float, and show the movement it became."""
        self.ensure_one()
        # Only your own cash, unless you are the desk that manages floats. An
        # executive recording a collection "collected by" somebody else would
        # move money into another person's accountability.
        if self.user_id != self.env.user and not self.env.user.has_group(
                'lab_fieldwork.group_fieldwork_manager'):
            raise UserError(_(
                "You can only record cash you collected yourself."))
        if not self.amount or self.amount <= 0:
            raise UserError(_("Enter how much was collected."))
        collection = self.env['lab.cash.collection'].create({
            'user_id': self.user_id.id,
            'partner_id': self.partner_id.id,
            'amount': self.amount,
            'date': self.date,
            'pay_mode': self.pay_mode,
            'note': self.note,
        })
        # Into the float by the same path a visit's cash takes. The float opens
        # itself on first use now (unless the setting leaves that to accounts),
        # so this no longer waits for one to exist; and a failure is written on
        # the collection rather than raised - the money is recorded either way,
        # which is the point of this screen. (client, 2026-09-12 / 2026-09-18)
        if self.pay_mode == 'cash':
            try:
                with self.env.cr.savepoint():
                    collection.action_cash_to_float()
            except UserError as exc:
                collection.message_post(body=_(
                    "The cash did not go into the float by itself: %s Use "
                    "\"Into my float\" once that is sorted.",
                    exc.args[0] if exc.args else exc))
        return {'type': 'ir.actions.act_window_close'}
