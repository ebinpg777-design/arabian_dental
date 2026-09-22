from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class PettyCashCategoryBudget(models.Model):
    """Per-category spend budget lines on an allocation.

    Warns (not blocks) when an expense exceeds the configured amount for a
    product category or expense account. The warning is surfaced as a
    chatter message when the expense is approved, and flagged on the
    expense form so approvers can see it.
    """
    _name = 'petty.cash.category.budget'
    _description = 'Petty Cash Category Budget'

    allocation_id = fields.Many2one('petty.cash.allocation', required=True, ondelete='cascade')
    categ_id = fields.Many2one('product.category', string='Product Category')
    account_id = fields.Many2one(
        'account.account', string='Expense Account',
        domain="[('account_type', 'like', 'expense')]")
    budget_amount = fields.Monetary(string='Budget', currency_field='currency_id', required=True)
    company_id = fields.Many2one(related='allocation_id.company_id', store=True)
    currency_id = fields.Many2one(related='allocation_id.currency_id', store=True)
    # Stored so it always shows the last-saved value; never shows an incorrect
    # in-progress number while a new line is being typed in the editable list.
    amount_spent = fields.Monetary(
        string='Spent', compute='_compute_amount_spent', store=True,
        currency_field='currency_id')
    amount_remaining = fields.Monetary(
        string='Remaining', compute='_compute_amount_spent', store=True,
        currency_field='currency_id')

    @api.constrains('categ_id', 'account_id')
    def _check_at_least_one(self):
        for rec in self:
            if not rec.categ_id and not rec.account_id:
                raise ValidationError(
                    _('Set a product category or an expense account for each budget line.'))

    @api.depends(
        'allocation_id',
        'categ_id', 'account_id',
        'allocation_id.expense_ids.state',
        'allocation_id.expense_ids.line_ids.amount',
        'allocation_id.expense_ids.line_ids.categ_id',
        'allocation_id.expense_ids.line_ids.account_id',
    )
    def _compute_amount_spent(self):
        for rec in self:
            if not rec.allocation_id:
                rec.amount_spent = 0.0
                continue
            # Only count lines from approved/paid expenses on this allocation.
            paid_lines = rec.allocation_id.expense_ids.filtered(
                lambda e: e.state in ('approved', 'bill_created', 'paid')
            ).mapped('line_ids')
            if rec.categ_id:
                paid_lines = paid_lines.filtered(
                    lambda l: l.categ_id and l.categ_id == rec.categ_id)
            elif rec.account_id:
                paid_lines = paid_lines.filtered(
                    lambda l: l.account_id and l.account_id == rec.account_id)
            else:
                paid_lines = rec.env['petty.cash.expense.line']
            rec.amount_spent = sum(paid_lines.mapped('amount'))
            rec.amount_remaining = max(rec.budget_amount - rec.amount_spent, 0.0)

    def _label(self):
        self.ensure_one()
        return (self.categ_id.display_name or
                self.account_id.display_name or
                _('Budget'))
