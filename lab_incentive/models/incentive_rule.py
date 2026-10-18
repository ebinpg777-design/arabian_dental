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

    # Most bought-in items at a given lab keep roughly the same share, and typing a
    # number on every one of them is not a plan. The product's own margin wins; this
    # answers "and everything else?". It sits on the rule, not on the company,
    # because a margin that changes next year should change with the scheme.
    # (Ortho, 2026-10-08)
    default_margin_pct = fields.Float(
        'Default Margin %',
        help="Used for a product set to 'Margin %' that carries no margin of its "
             "own. Set the ones that differ on the product itself.")

    @api.constrains('default_margin_pct')
    def _check_default_margin_pct(self):
        for rule in self:
            if not 0 <= rule.default_margin_pct <= 100:
                raise ValidationError(_(
                    "The default margin is a percentage of the sale value, so it "
                    "must be between 0 and 100 (%s).", rule.name))

    @api.model
    def _active_rule(self, company, on_date):
        return self.search([
            ('company_id', '=', company.id), ('active', '=', True),
            ('date_from', '<=', on_date),
            '|', ('date_to', '=', False), ('date_to', '>=', on_date),
        ], order='date_from desc', limit=1)

    def compute_incentive(self, base):
        """The rupee amount a given base earns under this rule's slabs."""
        return sum(step['result'] for step in self.explain(base) if step['applied'])

    def explain(self, base):
        """Every step the slabs take on `base`, in slab order - the amount and the
        sheet's "How this was calculated" are both read from it, so they cannot
        disagree.

        Each step: the slab (`slab`), the part of the base it is applied to
        (`amount`), what it gives (`result`), and whether it counts (`applied`).
        Flat: the one slab the base reached, on the whole base. Telescopic: each band
        on its own share - the first band at its rate, the next at the next rate on
        what lies past the first, and so on; a band the base never reached is listed
        with nothing in it, so the reader sees how far away it is.
        """
        self.ensure_one()
        slabs = self.slab_ids.sorted('amount_from')
        if not slabs:
            return []
        base = max(base or 0.0, 0.0)
        if self.slab_mode == 'flat':
            hit = slabs.filtered(lambda s: s.amount_from <= base and
                                 (not s.amount_to or base <= s.amount_to))
            chosen = hit[-1:] or slabs.filtered(lambda s: s.amount_from <= base)[-1:]
            if not base:
                chosen = slabs.browse()
            return [{'slab': slab, 'amount': base if slab == chosen else 0.0,
                     'result': slab._apply(base) if slab == chosen else 0.0,
                     'applied': slab == chosen}
                    for slab in slabs]
        steps = []
        for slab in slabs:
            top = slab.amount_to or base
            band = max(min(base, top) - slab.amount_from, 0.0)
            steps.append({'slab': slab, 'amount': band,
                          'result': slab._apply(band) if band > 0 else 0.0,
                          'applied': band > 0})
        return steps


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
