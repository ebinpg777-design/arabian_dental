# -*- coding: utf-8 -*-
"""Stored computes that went stale because they read a field they did not depend on.

Both of these failed before the depends were corrected. The failure is invisible in the
UI — the figure is right when it is first written and silently wrong from then on — so
it is worth a test that says so out loud.
"""
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestStaleComputes(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.env.user.group_ids = [
            (4, cls.env.ref('petty_cash.group_petty_cash_manager').id)]
        journal = cls.env['account.journal'].search(
            [('type', '=', 'cash'), ('company_id', '=', cls.company.id)], limit=1)
        if not journal:
            journal = cls.env['account.journal'].create({
                'name': 'Stale PC', 'type': 'cash', 'code': 'STPC',
                'company_id': cls.company.id})
        journal.is_petty_cash = True
        cls.journal = journal
        cls.holder = cls.env['res.partner'].create({'name': 'Stale Holder'})
        cls.company.write({'petty_cash_warning_threshold': 40.0,
                           'petty_cash_critical_threshold': 15.0})

    def _allocation(self, amount=1000.0):
        return self.env['petty.cash.allocation'].create({
            'partner_id': self.holder.id,
            'journal_id': self.journal.id,
            'amount_limit': amount,
            'company_id': self.company.id,
        })

    def test_raising_a_budget_updates_what_is_left_of_it(self):
        """`amount_remaining` is budget_amount - amount_spent, so it depends on both.

        Without `budget_amount` in the depends, the remaining figure keeps answering
        against the old budget — the one number a category budget exists to report.
        """
        alloc = self._allocation()
        budget = self.env['petty.cash.category.budget'].create({
            'allocation_id': alloc.id,
            'account_id': self.env['account.account'].search(
                [('company_ids', 'in', [self.company.id])], limit=1).id,
            'budget_amount': 100.0,
        })
        self.assertEqual(budget.amount_remaining, 100.0)

        budget.budget_amount = 250.0
        self.assertEqual(
            budget.amount_remaining, 250.0,
            "raising the budget must raise what is left of it")

    def test_changing_the_company_threshold_recolours_existing_floats(self):
        """`health_status` is stored and read off the company, so it depends on it.

        A manager who tightens the policy in Settings expects every float to be judged
        by the new rule. Without the dependency the existing ones keep the health they
        were last saved with, and the setting silently applies only to new records.
        """
        alloc = self._allocation()
        alloc.action_submit_request()
        alloc.action_allocate()
        tx = alloc.transaction_ids.filtered(lambda t: t.type == 'allocation')[:1]
        tx.action_approve()
        tx.action_post()
        self.assertEqual(alloc.health_status, 'healthy')

        # Nothing about the float changed — only the rule it is judged by.
        self.company.petty_cash_warning_threshold = 150.0
        self.assertEqual(
            alloc.health_status, 'warning',
            "a float untouched but now below the new warning threshold must be "
            "re-judged; a stored health status that ignores the policy is a policy "
            "that only applies to records created after it")
