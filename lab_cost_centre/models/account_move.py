# -*- coding: utf-8 -*-
"""Invoices, bills and every journal item, told which department they belong to.

This is where the module pays for itself: once a journal item carries an
analytic distribution, the profit and loss statement reads department by
department with nothing custom at all.

One direction, as everywhere else here: the line derives from what it bills, the
move from its lines.
"""
from odoo import api, models

# Analytic accounting is about profit and loss. Everything else is the balance
# sheet, and a cost centre has no business there - see `_lab_taggable_domain`.
PROFIT_AND_LOSS = (
    'income', 'income_other',
    'expense', 'expense_direct_cost', 'expense_depreciation',
)


class AccountMove(models.Model):
    _name = 'account.move'
    _inherit = ['account.move', 'lab.cost.centre.mixin']

    def _derive_department(self):
        self.ensure_one()
        for line in self.invoice_line_ids:
            if line.department_id:
                return line.department_id
        return self.env['hr.department']

    @api.depends('invoice_line_ids.department_id')
    def _compute_department_id(self):
        return super()._compute_department_id()

    @api.onchange('department_id')
    def _onchange_department_fill_lines(self):
        for move in self:
            if not move.department_id:
                continue
            for line in move.invoice_line_ids:
                if not line.department_id:
                    line.department_id = move.department_id


class AccountMoveLine(models.Model):
    _name = 'account.move.line'
    _inherit = ['account.move.line', 'lab.cost.centre.mixin']

    def _lab_taggable_domain(self, department=None):
        """The posted journal items a cost centre may be written onto.

        Profit and loss only. Nine thousand six hundred lines on this ledger
        have a department and sit on "Inventories" or "GRN - Not billed" -
        9.6 million rupees of stock movement. Tagging those would post the same
        material to a cost centre twice: once when it arrives in stock, and
        again on the Cost of Goods Sold line when it is consumed. A cockpit that
        double-counts its own material is worse than one that shows nothing.

        Nothing else needs excluding: receivable, payable, tax, cash and equity
        lines never get a department, because a department is derived from the
        product and those lines have none.

        Untagged only, which is what makes the back-fill idempotent and
        restartable - a written line drops straight out of this domain.
        """
        domain = [
            ('analytic_distribution', '=', False),
            ('parent_state', '=', 'posted'),
            ('account_id.account_type', 'in', PROFIT_AND_LOSS),
        ]
        if department is not None:
            domain.append(('department_id', '=', department.id))
        else:
            domain.append(('department_id', '!=', False))
        return domain

    def _derive_department(self):
        """The line it bills, the purchase it settles, or what it is made of.

        Three sources, in the order they are trustworthy: the sale line behind
        an invoice line, the purchase line behind a bill line, and failing both
        the product's own category - which is what a line typed by hand has.
        """
        self.ensure_one()
        if 'sale_line_ids' in self._fields and self.sale_line_ids[:1].department_id:
            return self.sale_line_ids[:1].department_id
        if 'purchase_line_id' in self._fields and self.purchase_line_id.department_id:
            return self.purchase_line_id.department_id
        if self.product_id:
            return self.product_id.categ_id._lab_department()
        return self.env['hr.department']

    @api.depends('product_id')
    def _compute_department_id(self):
        return super()._compute_department_id()

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        # Only the lines that MAY carry analytic: Odoo refuses a distribution on
        # a receivable or payable line, and a refusal here would stop an invoice
        # being posted at all.
        lines._lab_analytic_candidates()._lab_apply_analytic()
        return lines

    def _lab_analytic_candidates(self):
        return self.filtered(
            lambda line: line.display_type == 'product'
            and line.account_id.account_type not in (
                'asset_receivable', 'liability_payable'))
