# -*- coding: utf-8 -*-
# Copyright (C) 2025-2026 Ebshel Technologies (https://ebshel.com)
# License OPL-1. See LICENSE file for full copyright and licensing details.

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestBargain(TransactionCase):
    """Bulk vendor price-bargaining: global message + per-item target/notes."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.uom = cls.env.ref("uom.product_uom_unit")
        cls.product = cls.env["product.product"].create({
            "name": "Bargain Product", "type": "consu",
            "purchase_ok": True, "uom_id": cls.uom.id})
        cls.v1 = cls.env["res.partner"].create({
            "name": "BV1", "supplier_rank": 1, "email": "bv1@example.com"})
        cls.v2 = cls.env["res.partner"].create({
            "name": "BV2", "supplier_rank": 1, "email": "bv2@example.com"})

    def _rfq_with_quotes(self):
        pr = self.env["ebshel.purchase.request"].create({
            "description": "Bargain PR", "requester_id": self.env.uid,
            "line_ids": [(0, 0, {"product_id": self.product.id, "name": "L1",
                                 "qty": 4.0, "uom_id": self.uom.id, "price_unit": 100.0})],
        })
        rfq = self.env["ebshel.purchase.rfq"].create({
            "rfq_line_ids": [(0, 0, {
                "request_line_id": pr.line_ids.id, "product_id": self.product.id,
                "name": "L1", "qty": 4.0, "uom_id": self.uom.id})],
        })
        for v, price in ((self.v1, 120.0), (self.v2, 130.0)):
            q = self.env["ebshel.purchase.vendor.quotation"].create({
                "rfq_id": rfq.id, "vendor_id": v.id,
                "line_ids": [(0, 0, {
                    "rfq_line_id": rfq.rfq_line_ids.id, "product_id": self.product.id,
                    "name": "L1", "qty": 4.0, "uom_id": self.uom.id, "price_unit": price})],
            })
            q.state = "received"
        return rfq

    def test_bargain_sends_and_tracks_rounds(self):
        rfq = self._rfq_with_quotes()
        action = rfq.action_open_bargain_wizard()
        wiz = self.env["ebshel.pr.bargain.wizard"].with_context(action["context"]).create({})
        self.assertEqual(len(wiz.line_ids), 1)
        self.assertEqual(len(wiz.quotation_ids), 2)

        # Set a target price + item message and send.
        wiz.line_ids.write({"target_price": 90.0, "item_message": "Please match budget"})
        wiz.global_message = "<p>Kindly revise.</p>"
        wiz.action_send_bargain()

        # Each vendor's bargain round incremented; target persisted on the RFQ line.
        for q in rfq.vendor_quotation_ids:
            self.assertEqual(q.bargain_round, 1)
            self.assertTrue(q.bargain_last_date)
        self.assertEqual(rfq.rfq_line_ids.target_price, 90.0)
        self.assertEqual(rfq.rfq_line_ids.bargain_note, "Please match budget")

        # A second round bumps the counter again.
        wiz2 = self.env["ebshel.pr.bargain.wizard"].with_context(
            rfq.action_open_bargain_wizard()["context"]).create({})
        wiz2.action_send_bargain()
        self.assertEqual(rfq.vendor_quotation_ids[0].bargain_round, 2)
