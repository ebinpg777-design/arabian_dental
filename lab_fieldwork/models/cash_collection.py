# -*- coding: utf-8 -*-
"""Money taken at a door the executive did not raise a visit for.

Payment arrives ahead of the paperwork. A doctor settles an old bill while the
executive is there for something else; a receptionist hands over an envelope on
the way past; somebody pays for a case another executive delivered last week.
Until now the only way to record that was the payment box on a visit, so the
money could only be entered by inventing a visit that never happened - a false
call in the day sheet to get a true number into the day's total.

This is that payment on its own. It is deliberately the SAME three facts a
visit records - who it came from, how much, and how it was paid - because every
figure the lab keeps about collections reads those three and must not care
which screen they were typed on: the day's total, the day sheet, the
executive's collection target, and the manager's performance report. Cash also
reaches the petty cash float by the same path a visit's cash does.
(client, 2026-09-12)
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class CashCollection(models.Model):
    _name = 'lab.cash.collection'
    _description = 'Cash Collected Without a Visit'
    _order = 'date desc, id desc'
    _inherit = ['mail.thread']

    user_id = fields.Many2one(
        'res.users', string='Collected by', required=True, index=True,
        default=lambda self: self.env.user, tracking=True)
    partner_id = fields.Many2one(
        'res.partner', string='Doctor / Clinic', required=True, index=True,
        tracking=True, help="Who the money came from.")
    date = fields.Date(
        string='Collected on', required=True, index=True,
        default=lambda self: fields.Date.context_today(self), tracking=True)
    amount = fields.Monetary(
        string='Amount', required=True, currency_field='currency_id',
        tracking=True)
    # Named and valued exactly as on a visit: the two are added together
    # everywhere, so a difference here would be a difference in the totals.
    pay_mode = fields.Selection(
        [('cash', 'Cash'), ('cheque', 'Cheque'), ('online', 'UPI / Online')],
        string='Received As', required=True, default='cash', tracking=True)
    note = fields.Char(string='Note')
    company_id = fields.Many2one(
        'res.company', required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(
        related='company_id.currency_id', string='Currency', readonly=True)

    cash_txn_id = fields.Many2one(
        'petty.cash.transaction', string='Cash Movement', readonly=True,
        copy=False)
    cash_banked = fields.Boolean(compute='_compute_cash_banked', store=True)

    _amount_positive = models.Constraint(
        'CHECK(amount > 0)', "A collection has to be for some money.")

    @api.depends('cash_txn_id', 'cash_txn_id.state')
    def _compute_cash_banked(self):
        for rec in self:
            rec.cash_banked = (bool(rec.cash_txn_id)
                               and rec.cash_txn_id.state != 'cancelled')

    @api.depends('partner_id', 'amount', 'date')
    def _compute_display_name(self):
        for rec in self:
            # sudo: an executive may take money from a door that is not on
            # their round, and would then have no right to read its name -
            # which is not a reason to refuse to label their own record.
            rec.display_name = '%s · %s' % (
                rec.partner_id.sudo().display_name or _('Collection'),
                rec.amount)

    # What the float movement was raised from. Once the cash is in the float,
    # changing any of these makes the collection and the movement disagree.
    BANKED_FIELDS = ('amount', 'pay_mode', 'partner_id', 'user_id', 'date')

    def write(self, vals):
        if not (self.env.su or self.env.user.has_group(
                'lab_fieldwork.group_fieldwork_manager')):
            touched = [f for f in self.BANKED_FIELDS if f in vals]
            banked = self.filtered('cash_banked')
            if touched and banked:
                raise UserError(_(
                    "%(what)s is already in the float, so its %(fields)s can no "
                    "longer be changed here. Ask a field work manager to correct "
                    "it.", what=banked[0].display_name,
                    fields=', '.join(self._fields[f].string for f in touched)))
        return super().write(vals)

    def action_cash_to_float(self):
        """Take this money into the executive's float, as a visit's cash does.

        Only cash: a cheque or an online payment reaches the bank directly and
        is reconciled there, so putting it in the float would count the same
        money twice - the rule the visit button already states.
        """
        self.ensure_one()
        if self.cash_banked:
            return self.action_view_cash_txn()
        if self.pay_mode != 'cash':
            raise UserError(_(
                "Only cash goes into the float. A cheque or online payment "
                "reaches the bank directly and is reconciled there — putting "
                "it here would count the same money twice."))
        txn = self.env['petty.cash.transaction']._lab_collect(
            self.user_id, self.partner_id, self.amount, date=self.date,
            note=self.note or _("Collected from %(clinic)s",
                                clinic=self.partner_id.sudo().display_name))
        self.sudo().cash_txn_id = txn
        return self.action_view_cash_txn()

    def action_view_cash_txn(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'petty.cash.transaction',
            'res_id': self.cash_txn_id.id,
            'view_mode': 'form',
        }

    # ------------------------------------------------------------ the readings
    @api.model
    def _collected_for(self, user_ids, date_from, date_to=None):
        """{user_id: money} collected outside a visit, over a span of days.

        One query for every screen that adds these to the visits' own figure,
        so none of them can drift into counting it differently.
        """
        if not user_ids:
            return {}
        domain = [('user_id', 'in', list(user_ids)),
                  ('date', '>=', date_from),
                  ('date', '<=', date_to or date_from)]
        return {
            user.id: total
            for user, total in self.sudo()._read_group(
                domain, ['user_id'], ['amount:sum'])
        }

    @api.model
    def _unbanked_for(self, user, day):
        """Cash collected on `day` that has not reached the float yet."""
        rows = self.sudo().search([('user_id', '=', user.id),
                                   ('date', '=', day),
                                   ('pay_mode', '=', 'cash'),
                                   ('cash_banked', '=', False)])
        return sum(rows.mapped('amount'))
