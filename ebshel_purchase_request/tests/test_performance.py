# -*- coding: utf-8 -*-
# Copyright (C) 2025-2026 Ebshel Technologies (https://ebshel.com)
# License OPL-1. See LICENSE file for full copyright and licensing details.

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestApprovalPerformance(TransactionCase):
    """Guard the approval-routing hot path against per-line query/CPU blow-up
    on purchase requests with many lines."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.uom_unit = cls.env.ref("uom.product_uom_unit")
        cls.categ = cls.env["product.category"].create({"name": "Perf Categ"})
        cls.product = cls.env["product.product"].create({
            "name": "Perf Product", "type": "consu", "purchase_ok": True,
            "uom_id": cls.uom_unit.id, "categ_id": cls.categ.id,
        })
        # Several approval levels that USE category limits — the worst case for
        # the old O(levels x lines) category-amount recomputation.
        cls.group = cls.env.ref("ebshel_purchase_request.group_pr_approver")
        for i in range(5):
            cls.env["ebshel.purchase.request.approval.config"].create({
                "name": "Level %s" % i,
                "sequence": i + 1,
                "company_id": cls.company.id,
                "approver_group_ids": [(4, cls.group.id)],
                "category_limit_ids": [(0, 0, {
                    "product_category_id": cls.categ.id,
                    "amount_min": 0.0,
                })],
            })

    def _make_request(self, n_lines):
        return self.env["ebshel.purchase.request"].create({
            "description": "Perf PR %s lines" % n_lines,
            "requester_id": self.env.user.id,
            "company_id": self.company.id,
            "line_ids": [
                (0, 0, {"product_id": self.product.id, "name": "L%s" % i,
                        "qty": 1.0, "uom_id": self.uom_unit.id, "price_unit": 10.0})
                for i in range(n_lines)
            ],
        })

    def test_approval_eval_query_count_independent_of_lines(self):
        """Evaluating applicable approval levels must not issue more queries for
        a 200-line request than for a 20-line one (category spend is computed
        once, from already-loaded lines)."""
        small = self._make_request(20)
        large = self._make_request(200)
        self.env.invalidate_all()

        # Warm + measure small.
        small.amount_total  # ensure stored amount computed
        with self.assertQueryCount(default=40):
            levels_small = small._get_applicable_approval_levels()

        large.amount_total
        # The large request must not cost materially more queries than the small.
        with self.assertQueryCount(default=40):
            levels_large = large._get_applicable_approval_levels()

        # Correctness preserved: both route through the category-limited levels.
        self.assertTrue(levels_small)
        self.assertEqual(set(levels_small.ids), set(levels_large.ids))

    def test_category_amounts_correct(self):
        pr = self._make_request(10)
        amounts = pr._category_amounts()
        # 10 lines x qty 1 x price 10 = 100 for the single category.
        self.assertAlmostEqual(amounts.get(self.categ.id), 100.0)
