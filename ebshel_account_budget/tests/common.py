# Part of ebshel_account_budget. See LICENSE file for full copyright and licensing details.
from odoo import fields

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


class BudgetCommon(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.plan = cls.env['account.analytic.plan'].create({'name': 'Departments'})
        cls.dept_it = cls.env['account.analytic.account'].create({'name': 'IT', 'plan_id': cls.plan.id})
        cls.dept_hr = cls.env['account.analytic.account'].create({'name': 'HR', 'plan_id': cls.plan.id})
        today = fields.Date.context_today(cls.env['res.users'])
        cls.date_from = today.replace(month=1, day=1)
        cls.date_to = today.replace(month=12, day=31)
        cls.today = today

    @classmethod
    def _budget(cls, amounts, **vals):
        return cls.env['ebshel.account.budget'].create({
            'name': 'Departments',
            'date_from': cls.date_from,
            'date_to': cls.date_to,
            'line_ids': [(0, 0, {'analytic_account_id': account.id, 'budget_amount': amount})
                         for account, amount in amounts],
            **vals,
        })

    @classmethod
    def _bill(cls, account, amount, date=None, post=True):
        bill = cls.env['account.move'].create({
            'move_type': 'in_invoice',
            'partner_id': cls.partner_a.id,
            'invoice_date': date or cls.today,
            'invoice_line_ids': [(0, 0, {
                'name': 'Spend',
                'quantity': 1,
                'price_unit': amount,
                'tax_ids': [(6, 0, [])],
                'analytic_distribution': {str(account.id): 100},
            })],
        })
        if post:
            bill.action_post()
        return bill
