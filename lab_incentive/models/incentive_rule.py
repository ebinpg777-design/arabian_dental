# -*- coding: utf-8 -*-
"""The slab table: what percentage an executive earns at what level of monthly sales
(R19). Kept as data, not code, so a lab that changes its scheme does not need a
module upgrade.
"""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class LabIncentiveRule(models.Model):
    _name = 'lab.incentive.rule'
    _description = 'Incentive Rule'
    _order = 'date_from desc, id desc'

    name = fields.Char(required=True)
    company_id = fields.Many2one('res.company', default=lambda s: s.env.company,
                                 required=True)
    currency_id = fields.Many2one(related='company_id.currency_id')
    active = fields.Boolean(default=True)
    date_from = fields.Date(required=True, default=fields.Date.context_today)
    date_to = fields.Date()
    slab_mode = fields.Selection(
        [('flat', 'Flat — the slab reached applies to the whole base'),
         ('telescopic', 'Telescopic — each slab applies to its own band')],
        default='flat', required=True,
        help="Flat: ₹80,000 of sales at a 5% slab (₹50k+) earns 5% on the full "
             "₹80,000. Telescopic: it earns the lower slab's rate on the first band "
             "and 5% only on the amount past ₹50,000.")
    slab_ids = fields.One2many('lab.incentive.rule.slab', 'rule_id', string='Slabs')
    # Opening a door is the other half of the job.
    #
    # The slabs reward what an executive SELLS, which rewards the round they inherited
    # as much as the work they did on it. A clinic that was not on the lab's books last
    # month is unambiguously theirs, and paying for it is how the lab asks for more of
    # them. Zero by default, so nothing changes for a lab that does not want it.
    # (client, 2026-08-27)
    new_clinic_bonus = fields.Monetary(
        string='Per New Clinic', currency_field='currency_id',
        help="Paid for each clinic the executive opened on their own route during the "
             "month, on top of whatever the sales slabs give. A clinic counts once, in "
             "the month it was registered.")

    @api.model
    def _active_rule(self, company, on_date):
        return self.search([
            ('company_id', '=', company.id), ('active', '=', True),
            ('date_from', '<=', on_date),
            '|', ('date_to', '=', False), ('date_to', '>=', on_date),
        ], order='date_from desc', limit=1)

    def compute_incentive(self, base):
        """The rupee amount a given base earns under this rule's slabs."""
        self.ensure_one()
        slabs = self.slab_ids.sorted('amount_from')
        if not slabs or base <= 0:
            return 0.0
        if self.slab_mode == 'flat':
            hit = slabs.filtered(lambda s: s.amount_from <= base and
                                 (not s.amount_to or base <= s.amount_to))
            slab = hit[-1:] or slabs.filtered(lambda s: s.amount_from <= base)[-1:]
            if not slab:
                return 0.0
            return slab._apply(base)
        # telescopic: walk every band the base actually reaches
        total = 0.0
        for slab in slabs:
            top = slab.amount_to or base
            if base <= slab.amount_from:
                break
            band = min(base, top) - slab.amount_from
            if band > 0:
                total += slab._apply(band)
        return total


class LabIncentiveRuleSlab(models.Model):
    _name = 'lab.incentive.rule.slab'
    _description = 'Incentive Slab'
    _order = 'amount_from'

    rule_id = fields.Many2one('lab.incentive.rule', required=True,
                              ondelete='cascade')
    amount_from = fields.Monetary(required=True, currency_field='currency_id')
    amount_to = fields.Monetary(
        currency_field='currency_id',
        help="Leave blank for no upper limit.")
    incentive_type = fields.Selection(
        [('percent', 'Percentage'), ('fixed', 'Fixed Amount')],
        default='percent', required=True)
    value = fields.Float(required=True)
    currency_id = fields.Many2one(related='rule_id.currency_id')

    @api.constrains('amount_from', 'amount_to')
    def _check_range(self):
        for slab in self:
            if slab.amount_to and slab.amount_to <= slab.amount_from:
                raise ValidationError(_(
                    "A slab's upper bound must be above its lower bound (%s).",
                    slab.rule_id.name))

    def _apply(self, amount):
        self.ensure_one()
        if self.incentive_type == 'fixed':
            return self.value
        return amount * self.value / 100.0
