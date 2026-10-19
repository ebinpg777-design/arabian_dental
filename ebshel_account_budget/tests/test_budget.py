# Part of ebshel_account_budget. See LICENSE file for full copyright and licensing details.
from datetime import timedelta

from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .common import BudgetCommon


@tagged('post_install', '-at_install')
class TestBudget(BudgetCommon):

    def test_achieved_reads_posted_entries_in_period(self):
        budget = self._budget([(self.dept_it, 1000.0), (self.dept_hr, 500.0)])
        self._bill(self.dept_it, 300.0)
        self._bill(self.dept_it, 50.0, post=False)                       # draft: not booked
        self._bill(self.dept_it, 70.0, date=self.date_from - timedelta(days=1))  # before the period
        self._bill(self.dept_hr, 600.0)
        it, hr = budget.line_ids
        self.assertAlmostEqual(it.achieved_amount, 300.0)
        self.assertAlmostEqual(it.available_amount, 700.0)
        self.assertFalse(it.is_over_budget)
        self.assertAlmostEqual(hr.achieved_amount, 600.0)
        self.assertTrue(hr.is_over_budget)
        self.assertAlmostEqual(budget.budget_amount, 1500.0)
        self.assertAlmostEqual(budget.achieved_amount, 900.0)
        self.assertAlmostEqual(budget.achieved_percentage, 60.0)

    def test_revenue_budget_counts_income(self):
        budget = self._budget([(self.dept_it, 1000.0)], budget_type='revenue')
        invoice = self.init_invoice('out_invoice', amounts=[400.0], invoice_date=self.today, taxes=[])
        invoice.invoice_line_ids.analytic_distribution = {str(self.dept_it.id): 100}
        invoice.action_post()
        self.assertAlmostEqual(budget.line_ids.achieved_amount, 400.0)

    def test_theoretical_is_pro_rata(self):
        budget = self._budget([(self.dept_it, 365.0)])
        line = budget.line_ids
        days = (self.date_to - self.date_from).days + 1
        elapsed = (self.today - self.date_from).days + 1
        self.assertAlmostEqual(line.theoretical_amount, 365.0 * elapsed / days)

    def test_workflow_and_revision(self):
        empty = self.env['ebshel.account.budget'].create({
            'name': 'Empty', 'date_from': self.date_from, 'date_to': self.date_to})
        with self.assertRaises(UserError):
            empty.action_confirm()
        budget = self._budget([(self.dept_it, 1000.0)])
        budget.action_confirm()
        self.assertEqual(budget.state, 'confirmed')
        self.assertEqual(
            self.env['ebshel.account.budget.line']._get_open_lines(self.dept_it.id, self.env.company.id, self.today),
            budget.line_ids)
        action = budget.action_revise()
        revision = self.env['ebshel.account.budget'].browse(action['res_id'])
        self.assertEqual(budget.state, 'revised')
        self.assertEqual(revision.state, 'draft')
        self.assertEqual(revision.parent_id, budget)
        self.assertEqual(revision.line_ids.budget_amount, 1000.0)
        self.assertFalse(
            self.env['ebshel.account.budget.line']._get_open_lines(self.dept_it.id, self.env.company.id, self.today))

    def test_analytic_account_shows_its_budgets(self):
        budget = self._budget([(self.dept_it, 1000.0)])
        self.assertEqual(self.dept_it.budget_count, 1)
        self.assertEqual(self.dept_hr.budget_count, 0)
        action = self.dept_it.action_view_budgets()
        self.assertEqual(self.env['ebshel.account.budget'].search(action['domain']), budget)

    def test_access_and_companies(self):
        budget = self._budget([(self.dept_it, 1000.0)])
        reader = self.env['res.users'].create({
            'name': 'Reader', 'login': 'budget_reader',
            'group_ids': [(6, 0, [self.env.ref('account.group_account_readonly').id])],
        })
        self.assertEqual(budget.with_user(reader).read(['name'])[0]['name'], 'Departments')
        with self.assertRaises(AccessError):
            budget.with_user(reader).write({'name': 'Changed'})
        other = self.setup_other_company()['company']
        foreign = self.env['ebshel.account.budget'].create({
            'name': 'Elsewhere', 'company_id': other.id,
            'date_from': self.date_from, 'date_to': self.date_to})
        budgets = self.env['ebshel.account.budget'].with_user(reader).search([('id', 'in', (budget | foreign).ids)])
        self.assertEqual(budgets, budget)
