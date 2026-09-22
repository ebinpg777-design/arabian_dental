# -*- coding: utf-8 -*-
from odoo import api, fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    lab_collection_target = fields.Float(
        string='Collection Target (%)', default=75.0,
        help="The share of a month's invoicing the lab expects to collect. Used for "
             "every Sales Route that does not set a target of its own.")


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    lab_collection_target = fields.Float(
        related='company_id.lab_collection_target', readonly=False,
        string='Collection Target (%)')


class ResUsers(models.Model):
    _inherit = 'res.users'

    # A salesperson's own monthly book. Kept on the user rather than on the route
    # because people work more than one route, and the person is who the target is
    # actually agreed with. (client, 2026-08-21)
    lab_sales_target = fields.Monetary(
        string='Monthly Sales Target', currency_field='lab_target_currency_id',
        help="What this salesperson is expected to invoice in a month. Zero means "
             "no target has been agreed.")
    lab_target_currency_id = fields.Many2one(
        'res.currency', compute='_compute_lab_target_currency')

    def _compute_lab_target_currency(self):
        currency = self.env.company.currency_id
        for user in self:
            user.lab_target_currency_id = currency

    # A login that is not a competing salesperson: the accounts desk (user 117
    # "Accounts Manager", leader of the GNRL catch-all route and #1 on BOTH money
    # boards on work it never sold), OdooBot, a duplicate login. Their money is
    # REAL - every receipt cleared a real invoice - so this flag takes the NAME off
    # the three leaderboards and changes no figure anywhere else.
    # (client, 2026-08-21: "exclude some sales person's from the top sales People,
    # top Collector, Best collection Rate. Because some unknown sales and payments
    # are assigned to accounts person")
    lab_exclude_from_ranking = fields.Boolean(
        string='Not Ranked on Leaderboards', default=False,
        help="Keep this login off Top Salespeople, Top Collectors and Best "
             "Collection Rate. Their invoices, receipts and open money are "
             "unchanged and still count in every total.")


class CrmTeam(models.Model):
    _inherit = 'crm.team'

    # A route's own target. Zero means "no target of its own" and the company
    # figure applies - which is why this is not simply defaulted to 80: a route
    # left alone should follow the company when the company's number changes,
    # not keep a copy of whatever it was on the day the route was created.
    lab_collection_target = fields.Float(
        string='Collection Target (%)',
        help="The share of this route's invoicing that should be collected. "
             "Leave at zero to follow the company-wide target.")
    lab_effective_target = fields.Float(
        string='Effective Target (%)', compute='_compute_lab_effective_target',
        help="The target actually applied: this route's own, or the company's.")

    lab_sales_target = fields.Monetary(
        string='Monthly Sales Target', currency_field='currency_id',
        help="What this route is expected to invoice in a month. Zero means no "
             "target has been agreed.")

    @api.depends('lab_collection_target', 'company_id.lab_collection_target')
    def _compute_lab_effective_target(self):
        default = self.env.company.lab_collection_target
        for team in self:
            company_target = team.company_id.lab_collection_target or default
            team.lab_effective_target = team.lab_collection_target or company_target
