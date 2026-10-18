# -*- coding: utf-8 -*-
# Copyright (C) 2025-2026 Ebshel Technologies (https://ebshel.com)
# License OPL-1. See LICENSE file for full copyright and licensing details.

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestSplitAward(TransactionCase):
    """Exercise the RFQ → split-award → multiple-PO generation path used by the
    vendor-comparison 'Allocate & Order' / 'Generate POs' button."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        # These tests exercise the PO-creation mechanics directly, so disable the
        # award-approval gate (covered separately in test_award_approval).
        cls.company.pr_require_po_approval = False
        cls.uom_unit = cls.env.ref("uom.product_uom_unit")

        cls.product_a = cls.env["product.product"].create({
            "name": "Test Product A",
            "type": "consu",
            "purchase_ok": True,
            "uom_id": cls.uom_unit.id,
        })
        cls.product_b = cls.env["product.product"].create({
            "name": "Test Product B",
            "type": "consu",
            "purchase_ok": True,
            "uom_id": cls.uom_unit.id,
        })
        # A storable product confirms a PO that generates stock moves/pickings —
        # closer to real procurement data than a pure consumable.
        storable_vals = {
            "name": "Test Storable",
            "type": "consu",
            "purchase_ok": True,
            "uom_id": cls.uom_unit.id,
        }
        if "is_storable" in cls.env["product.product"]._fields:
            storable_vals["is_storable"] = True
        cls.product_storable = cls.env["product.product"].create(storable_vals)

        cls.vendor1 = cls.env["res.partner"].create({
            "name": "Vendor One", "is_company": True, "supplier_rank": 1,
        })
        cls.vendor2 = cls.env["res.partner"].create({
            "name": "Vendor Two", "is_company": True, "supplier_rank": 1,
        })

    def _build_request(self):
        pr = self.env["ebshel.purchase.request"].create({
            "description": "Test PR",
            "requester_id": self.env.user.id,
            "company_id": self.company.id,
            "line_ids": [
                (0, 0, {"product_id": self.product_a.id, "name": "Product A",
                        "qty": 10.0, "uom_id": self.uom_unit.id, "price_unit": 100.0}),
                (0, 0, {"product_id": self.product_b.id, "name": "Product B",
                        "qty": 6.0, "uom_id": self.uom_unit.id, "price_unit": 200.0}),
            ],
        })
        return pr

    def _build_rfq(self, pr):
        rfq = self.env["ebshel.purchase.rfq"].create({
            "company_id": self.company.id,
            "rfq_line_ids": [
                (0, 0, {"request_line_id": line.id, "product_id": line.product_id.id,
                        "name": line.name, "qty": line.qty, "uom_id": line.uom_id.id})
                for line in pr.line_ids
            ],
        })
        return rfq

    def _quote(self, rfq, vendor, price):
        """Create a received quotation from `vendor` pricing every RFQ line."""
        q = self.env["ebshel.purchase.vendor.quotation"].create({
            "rfq_id": rfq.id,
            "vendor_id": vendor.id,
            "line_ids": [
                (0, 0, {"rfq_line_id": rl.id, "product_id": rl.product_id.id,
                        "name": rl.name, "qty": rl.qty, "uom_id": rl.uom_id.id,
                        "price_unit": price})
                for rl in rfq.rfq_line_ids
            ],
        })
        q.state = "received"
        return q

    def _allocations(self, mapping):
        """mapping: list of (rfq_line, quotation, qty)."""
        return [{"line_id": rl.id, "vendor_id": q.id, "qty": qty}
                for rl, q, qty in mapping]

    def _receive_all(self, po):
        """Validate the incoming receipt of a confirmed PO in full."""
        for picking in po.picking_ids:
            for move in picking.move_ids:
                move.quantity = move.product_uom_qty
            picking.button_validate()

    # ── Tests ──────────────────────────────────────────────────────────────────
    def test_split_one_vendor_per_line(self):
        """Each line awarded to a different vendor → one PO per vendor."""
        pr = self._build_request()
        rfq = self._build_rfq(pr)
        q1 = self._quote(rfq, self.vendor1, 100.0)
        q2 = self._quote(rfq, self.vendor2, 110.0)
        rfq.state = "comparing"
        lines = rfq.rfq_line_ids
        res = rfq.action_generate_split_pos(self._allocations([
            (lines[0], q1, lines[0].qty),
            (lines[1], q2, lines[1].qty),
        ]))
        self.assertEqual(len(res["po_ids"]), 2)
        self.assertEqual(rfq.state, "po_created")

    def test_split_single_line_across_two_vendors(self):
        """One line's quantity split across two vendors → two POs."""
        pr = self._build_request()
        rfq = self._build_rfq(pr)
        q1 = self._quote(rfq, self.vendor1, 100.0)
        q2 = self._quote(rfq, self.vendor2, 105.0)
        rfq.state = "comparing"
        line = rfq.rfq_line_ids[0]
        res = rfq.action_generate_split_pos(self._allocations([
            (line, q1, 4.0),
            (line, q2, line.qty - 4.0),
        ]))
        self.assertEqual(len(res["po_ids"]), 2)
        # Total ordered for the line must equal the requested qty.
        pos = self.env["purchase.order"].browse(res["po_ids"])
        ordered = sum(pos.order_line.filtered(
            lambda l: l.product_id == self.product_a).mapped("product_qty"))
        self.assertAlmostEqual(ordered, line.qty)

    def test_split_with_new_unregistered_vendor(self):
        """A prospective (unregistered) vendor is auto-registered on PO generation."""
        pr = self._build_request()
        rfq = self._build_rfq(pr)
        q1 = self._quote(rfq, self.vendor1, 100.0)
        # New vendor quotation, no partner yet.
        q2 = self.env["ebshel.purchase.vendor.quotation"].create({
            "rfq_id": rfq.id,
            "is_new_vendor": True,
            "new_vendor_name": "Prospective Vendor",
            "line_ids": [
                (0, 0, {"rfq_line_id": rl.id, "product_id": rl.product_id.id,
                        "name": rl.name, "qty": rl.qty, "uom_id": rl.uom_id.id,
                        "price_unit": 90.0})
                for rl in rfq.rfq_line_ids
            ],
        })
        q2.state = "received"
        rfq.state = "comparing"
        lines = rfq.rfq_line_ids
        res = rfq.action_generate_split_pos(self._allocations([
            (lines[0], q1, lines[0].qty),
            (lines[1], q2, lines[1].qty),
        ]))
        self.assertEqual(len(res["po_ids"]), 2)
        self.assertTrue(q2.vendor_id, "New vendor should have been auto-registered.")

    def test_split_with_storable_products(self):
        """Split award for storable products (PO confirm creates stock moves)."""
        pr = self.env["ebshel.purchase.request"].create({
            "description": "Storable PR",
            "requester_id": self.env.user.id,
            "company_id": self.company.id,
            "line_ids": [
                (0, 0, {"product_id": self.product_storable.id, "name": "Storable",
                        "qty": 12.0, "uom_id": self.uom_unit.id, "price_unit": 50.0}),
                (0, 0, {"product_id": self.product_a.id, "name": "Product A",
                        "qty": 5.0, "uom_id": self.uom_unit.id, "price_unit": 100.0}),
            ],
        })
        rfq = self._build_rfq(pr)
        q1 = self._quote(rfq, self.vendor1, 50.0)
        q2 = self._quote(rfq, self.vendor2, 55.0)
        rfq.state = "comparing"
        lines = rfq.rfq_line_ids
        res = rfq.action_generate_split_pos(self._allocations([
            (lines[0], q1, lines[0].qty),
            (lines[1], q2, lines[1].qty),
        ]))
        self.assertEqual(len(res["po_ids"]), 2)
        pos = self.env["purchase.order"].browse(res["po_ids"])
        self.assertTrue(all(po.state == "purchase" for po in pos),
                        "All split POs should be confirmed.")

    def test_reception_status_updates_on_receipt(self):
        """Receiving the goods must update qty_done and the reception status of
        the request line (allocations are linked via the PO line, not the move)."""
        pr = self.env["ebshel.purchase.request"].create({
            "description": "Reception PR",
            "requester_id": self.env.user.id,
            "company_id": self.company.id,
            "line_ids": [
                (0, 0, {"product_id": self.product_storable.id, "name": "Storable",
                        "qty": 6.0, "uom_id": self.uom_unit.id, "price_unit": 50.0}),
            ],
        })
        rfq = self._build_rfq(pr)
        q1 = self._quote(rfq, self.vendor1, 50.0)
        rfq.state = "comparing"
        line = rfq.rfq_line_ids[0]
        res = rfq.action_generate_split_pos(self._allocations([(line, q1, line.qty)]))
        po = self.env["purchase.order"].browse(res["po_ids"])

        pr_line = pr.line_ids[0]
        self.assertEqual(pr_line.reception_status, "nothing")
        self.assertEqual(pr_line.qty_done, 0.0)

        if not po.picking_ids:
            self.skipTest("No receipt generated (stock route not configured).")
        self._receive_all(po)

        self.assertAlmostEqual(pr_line.qty_done, 6.0)
        self.assertEqual(pr_line.reception_status, "full")

    def test_over_allocation_raises(self):
        """Awarding more than the requested qty must be rejected cleanly."""
        from odoo.exceptions import UserError
        pr = self._build_request()
        rfq = self._build_rfq(pr)
        q1 = self._quote(rfq, self.vendor1, 100.0)
        rfq.state = "comparing"
        line = rfq.rfq_line_ids[0]
        with self.assertRaises(UserError):
            rfq.action_generate_split_pos(self._allocations([
                (line, q1, line.qty + 5.0),
            ]))
