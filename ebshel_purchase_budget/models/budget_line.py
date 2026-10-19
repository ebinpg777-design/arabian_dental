# Part of ebshel_purchase_budget. See LICENSE file for full copyright and licensing details.
from odoo import api, fields, models


class EbshelAccountBudgetLine(models.Model):
    _inherit = 'ebshel.account.budget.line'

    # Encumbrance from purchase.analytic.line for the line's analytic account and
    # period: "Purchased" = open purchase requests, "Invoiced" = open purchase
    # orders not billed yet. Their sum is the line's Committed amount; once billed
    # the amount moves to Achieved.
    purchased_amount = fields.Monetary(
        'Purchased', compute='_compute_committed_amount',
        help='Open purchase requests booked to this analytic account in the period.')
    invoiced_amount = fields.Monetary(
        'Invoiced', compute='_compute_committed_amount',
        help='Open purchase orders, not billed yet, booked to this analytic account in the period.')

    @api.depends('analytic_account_id', 'company_id', 'date_from', 'date_to')
    def _compute_committed_amount(self):
        Ledger = self.env['purchase.analytic.line'].sudo()
        for line in self:
            amounts = Ledger._amounts_by_type(
                line.analytic_account_id.id, line.company_id.id, line.date_from, line.date_to)
            line.purchased_amount = amounts['request']
            line.invoiced_amount = amounts['order']
            line.committed_amount = amounts['request'] + amounts['order']
