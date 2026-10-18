# -*- coding: utf-8 -*-
# Copyright (C) 2025-2026 Ebshel Technologies (https://ebshel.com)
# License OPL-1. See LICENSE file for full copyright and licensing details.

from odoo.tests import TransactionCase, tagged, Form


@tagged("post_install", "-at_install")
class TestCreateRfqWizard(TransactionCase):
    """The Create-RFQ wizard builds its lines from an onchange. Driving it
    through `Form` mirrors the web client (only view fields are persisted), so
    this catches the regression where the required `request_line_id` was missing
    from the wizard-line list view."""

    def test_wizard_form_creates_rfq(self):
        uom = self.env.ref("uom.product_uom_unit")
        product = self.env["product.product"].create({
            "name": "Wizard Product", "type": "consu",
            "purchase_ok": True, "uom_id": uom.id,
        })
        pr = self.env["ebshel.purchase.request"].create({
            "description": "Wizard PR",
            "requester_id": self.env.uid,
            "company_id": self.env.company.id,
            "line_ids": [(0, 0, {
                "product_id": product.id, "name": "Line 1",
                "qty": 5.0, "uom_id": uom.id, "price_unit": 10.0,
            })],
        })
        pr.state = "approved"

        # Drive the wizard through the web Form (respects the view's fields and
        # runs the request_ids onchange that builds the lines).
        form = Form(self.env["ebshel.pr.create.rfq.wizard"])
        form.request_ids.add(pr)
        wiz = form.save()   # <-- previously raised "Missing required ... request_line_id"

        self.assertTrue(wiz.wizard_line_ids, "Wizard should have built lines.")
        self.assertTrue(
            all(wl.request_line_id for wl in wiz.wizard_line_ids),
            "Every wizard line must carry its source request line.")

        action = wiz.action_create_rfq()
        rfq = self.env["ebshel.purchase.rfq"].browse(action["res_id"])
        self.assertTrue(rfq.exists())
        self.assertEqual(len(rfq.rfq_line_ids), 1)
        self.assertEqual(rfq.rfq_line_ids.request_line_id, pr.line_ids)

    def test_wizard_creates_vendor_quotations(self):
        uom = self.env.ref("uom.product_uom_unit")
        product = self.env["product.product"].create({
            "name": "Vendor Wizard Product", "type": "consu",
            "purchase_ok": True, "uom_id": uom.id,
        })
        pr = self.env["ebshel.purchase.request"].create({
            "description": "Vendor Wizard PR", "requester_id": self.env.uid,
            "company_id": self.env.company.id,
            "line_ids": [(0, 0, {
                "product_id": product.id, "name": "Line 1",
                "qty": 5.0, "uom_id": uom.id, "price_unit": 10.0})],
        })
        pr.state = "approved"
        v1 = self.env["res.partner"].create({"name": "WV1", "supplier_rank": 1})
        v2 = self.env["res.partner"].create({"name": "WV2", "supplier_rank": 1})

        form = Form(self.env["ebshel.pr.create.rfq.wizard"])
        form.request_ids.add(pr)
        form.vendor_ids.add(v1)
        form.vendor_ids.add(v2)
        wiz = form.save()
        action = wiz.action_create_rfq()
        rfq = self.env["ebshel.purchase.rfq"].browse(action["res_id"])

        # One quotation per invited vendor, each pre-loaded with the RFQ lines.
        self.assertEqual(len(rfq.vendor_quotation_ids), 2)
        self.assertEqual(rfq.vendor_quotation_ids.mapped("vendor_id"), v1 | v2)
        for q in rfq.vendor_quotation_ids:
            self.assertEqual(len(q.line_ids), len(rfq.rfq_line_ids))
