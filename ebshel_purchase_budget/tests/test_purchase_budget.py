# Part of ebshel_purchase_budget. See LICENSE file for full copyright and licensing details.
from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.ebshel_account_budget.tests.common import BudgetCommon


@tagged('post_install', '-at_install')
class TestPurchaseBudget(BudgetCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # The accounting test user also runs the requests: requester, approver
        # and budget approver at once.
        cls.env.user.group_ids += cls.env.ref('ebshel_purchase_request.group_pr_admin')
        cls.uom = cls.env.ref('uom.product_uom_unit')
        cls.product = cls.env['product.product'].create({
            'name': 'Laptop', 'type': 'consu', 'purchase_ok': True, 'uom_id': cls.uom.id})
        (cls.dept_it | cls.dept_hr).is_cost_center = True
        cls.budget = cls._budget([(cls.dept_it, 1000.0)])
        cls.budget.action_confirm()

    def _request(self, amount, department=None):
        return self.env['ebshel.purchase.request'].create({
            'description': 'Budget check',
            'requester_id': self.env.uid,
            'analytic_account_id': (department or self.dept_it).id,
            'line_ids': [(0, 0, {
                'product_id': self.product.id, 'name': 'Laptop',
                'qty': 1.0, 'uom_id': self.uom.id, 'price_unit': amount})],
        })

    def test_request_reads_the_open_budget(self):
        self._bill(self.dept_it, 200.0)
        request = self._request(300.0)
        self.assertTrue(request.dept_has_budget)
        self.assertAlmostEqual(request.dept_budget_amount, 1000.0)
        self.assertAlmostEqual(request.dept_budget_achieved, 200.0)
        self.assertAlmostEqual(request.dept_budget_available, 800.0)
        self.assertFalse(request.dept_over_budget)
        self.assertFalse(self._request(300.0, self.dept_hr).dept_has_budget)

    def test_approved_request_is_committed_on_the_budget_line(self):
        self.env.company.write({'pr_auto_approve_enabled': True, 'pr_auto_approve_amount': 10000.0})
        self._bill(self.dept_it, 200.0)
        request = self._request(300.0)
        request.action_submit()
        self.assertEqual(request.state, 'approved')
        line = self.budget.line_ids
        self.assertAlmostEqual(line.purchased_amount, 300.0)
        self.assertAlmostEqual(line.invoiced_amount, 0.0)
        self.assertAlmostEqual(line.committed_amount, 300.0)
        self.assertAlmostEqual(line.available_amount, 500.0)
        # The next request sees what the first one reserved.
        self.assertAlmostEqual(self._request(100.0).dept_budget_available, 500.0)

    def test_over_budget_request_is_blocked(self):
        self.env.company.pr_for_amount_exceed_budget = False
        request = self._request(1200.0)
        self.assertTrue(request.dept_over_budget)
        self.assertTrue(request.budget_approval_blocked)
        with self.assertRaises(UserError):
            request.action_submit()

    def test_over_budget_request_needs_budget_approval(self):
        self.env.company.pr_for_amount_exceed_budget = True
        request = self._request(1200.0)
        self.assertTrue(request.budget_approval_required)
        with self.assertRaises(UserError):
            request.action_submit()
        request.action_request_budget_approval()
        self.assertEqual(request.budget_approval_state, 'pending')
        request.action_budget_approve()
        self.assertFalse(request.budget_approval_required)
        request.action_submit()
        self.assertEqual(request.state, 'submitted')

    def test_closed_budget_no_longer_applies(self):
        self.budget.action_done()
        self.assertFalse(self._request(300.0).dept_has_budget)

    def test_purchase_order_moves_commitment_to_invoiced(self):
        self.env.company.write({
            'pr_auto_approve_enabled': True, 'pr_auto_approve_amount': 10000.0,
            'pr_require_po_approval': False,
        })
        request = self._request(100.0)
        request.line_ids.qty = 4.0
        request.action_submit()
        vendor = self.env['res.partner'].create({'name': 'Vendor', 'is_company': True, 'supplier_rank': 1})
        rfq = self.env['ebshel.purchase.rfq'].create({
            'rfq_line_ids': [(0, 0, {
                'request_line_id': request.line_ids.id, 'product_id': self.product.id,
                'name': 'Laptop', 'qty': 4.0, 'uom_id': self.uom.id})],
        })
        quotation = self.env['ebshel.purchase.vendor.quotation'].create({
            'rfq_id': rfq.id, 'vendor_id': vendor.id,
            'line_ids': [(0, 0, {
                'rfq_line_id': rfq.rfq_line_ids.id, 'product_id': self.product.id,
                'name': 'Laptop', 'qty': 4.0, 'uom_id': self.uom.id, 'price_unit': 90.0})],
        })
        quotation.state = 'received'
        rfq.state = 'comparing'
        rfq.action_generate_split_pos([
            {'line_id': rfq.rfq_line_ids.id, 'vendor_id': quotation.id, 'qty': 4.0}])
        self.assertEqual(rfq.po_count, 1)
        line = self.budget.line_ids
        self.assertAlmostEqual(line.purchased_amount, 0.0)
        self.assertAlmostEqual(line.invoiced_amount, 360.0)
        self.assertAlmostEqual(line.available_amount, 640.0)
