# -*- coding: utf-8 -*-
# Copyright (C) 2025-2026 Ebshel Technologies (https://ebshel.com)
# License OPL-1. See LICENSE file for full copyright and licensing details.

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestAwardApproval(TransactionCase):
    """A lower-level procurement officer proposes a PO award; a higher authority
    (manager) must approve before the purchase order(s) are created."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.uom = cls.env.ref("uom.product_uom_unit")
        cls.product = cls.env["product.product"].create({
            "name": "Approval Product", "type": "consu",
            "purchase_ok": True, "uom_id": cls.uom.id,
        })
        cls.vendor1 = cls.env["res.partner"].create({
            "name": "Vendor A", "is_company": True, "supplier_rank": 1})
        cls.vendor2 = cls.env["res.partner"].create({
            "name": "Vendor B", "is_company": True, "supplier_rank": 1})
        # A procurement officer who is NOT a manager (the "lower-level" user).
        cls.officer = cls.env["res.users"].create({
            "name": "Officer", "login": "test_officer",
            "group_ids": [(6, 0, [
                cls.env.ref("ebshel_purchase_request.group_pr_user").id,
                cls.env.ref("ebshel_purchase_request.group_pr_procurement_officer").id,
            ])],
        })

    def _ready_rfq(self):
        pr = self.env["ebshel.purchase.request"].create({
            "description": "Approval PR", "requester_id": self.env.uid,
            "company_id": self.company.id,
            "line_ids": [
                (0, 0, {"product_id": self.product.id, "name": "L1",
                        "qty": 8.0, "uom_id": self.uom.id, "price_unit": 10.0}),
            ],
        })
        rfq = self.env["ebshel.purchase.rfq"].create({
            "company_id": self.company.id,
            "rfq_line_ids": [(0, 0, {
                "request_line_id": pr.line_ids.id, "product_id": self.product.id,
                "name": "L1", "qty": 8.0, "uom_id": self.uom.id})],
        })
        for vendor, price in ((self.vendor1, 10.0), (self.vendor2, 12.0)):
            q = self.env["ebshel.purchase.vendor.quotation"].create({
                "rfq_id": rfq.id, "vendor_id": vendor.id,
                "line_ids": [(0, 0, {
                    "rfq_line_id": rfq.rfq_line_ids.id, "product_id": self.product.id,
                    "name": "L1", "qty": 8.0, "uom_id": self.uom.id,
                    "price_unit": price})],
            })
            q.state = "received"
        rfq.state = "comparing"
        return rfq

    def _alloc(self, rfq, qmap):
        return [{"line_id": rfq.rfq_line_ids.id, "vendor_id": q.id, "qty": qty}
                for q, qty in qmap]

    def test_officer_award_requires_approval(self):
        rfq = self._ready_rfq()
        q1 = rfq.vendor_quotation_ids.filtered(lambda q: q.vendor_id == self.vendor1)
        # Officer proposes → pending approval, NO PO yet.
        res = rfq.with_user(self.officer).action_generate_split_pos(
            self._alloc(rfq, [(q1, 8.0)]))
        self.assertEqual(res.get("status"), "pending_approval")
        self.assertEqual(rfq.award_state, "pending")
        self.assertEqual(rfq.po_count, 0)
        self.assertEqual(rfq.award_proposed_by_id, self.officer)
        self.assertTrue(rfq.award_line_ids)

        # An officer cannot approve their own award.
        with self.assertRaises(Exception):
            rfq.with_user(self.officer).action_approve_award()

        # Manager approves → PO created, award approved.
        res2 = rfq.action_approve_award()   # admin = manager
        self.assertEqual(rfq.award_state, "approved")
        self.assertTrue(res2["po_ids"])
        self.assertEqual(rfq.po_count, 1)

    def test_admin_award_proposes_then_approves(self):
        # With the policy ON (default), even an admin's award is proposed first,
        # then the admin (a higher authority) approves to create the PO.
        rfq = self._ready_rfq()
        q1 = rfq.vendor_quotation_ids.filtered(lambda q: q.vendor_id == self.vendor1)
        res = rfq.action_generate_split_pos(self._alloc(rfq, [(q1, 8.0)]))
        self.assertEqual(res.get("status"), "pending_approval")
        self.assertEqual(rfq.po_count, 0)
        res2 = rfq.action_approve_award()
        self.assertEqual(rfq.award_state, "approved")
        self.assertTrue(res2["po_ids"])
        self.assertEqual(rfq.po_count, 1)

    def test_award_direct_when_policy_off(self):
        # Companies can disable the gate → PO created immediately on award.
        self.company.pr_require_po_approval = False
        rfq = self._ready_rfq()
        q1 = rfq.vendor_quotation_ids.filtered(lambda q: q.vendor_id == self.vendor1)
        res = rfq.action_generate_split_pos(self._alloc(rfq, [(q1, 8.0)]))
        self.assertEqual(res.get("status"), "po_created")
        self.assertTrue(res["po_ids"])

    def test_reject_sends_back(self):
        rfq = self._ready_rfq()
        q1 = rfq.vendor_quotation_ids.filtered(lambda q: q.vendor_id == self.vendor1)
        rfq.with_user(self.officer).action_generate_split_pos(self._alloc(rfq, [(q1, 8.0)]))
        self.assertEqual(rfq.award_state, "pending")
        rfq.action_reject_award(reason="Price too high")
        self.assertEqual(rfq.award_state, "rejected")
        self.assertEqual(rfq.award_rejection_reason, "Price too high")
        self.assertEqual(rfq.po_count, 0)
