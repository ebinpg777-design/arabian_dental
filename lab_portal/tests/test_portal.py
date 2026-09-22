# -*- coding: utf-8 -*-
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestLabPortal(TransactionCase):
    """What a doctor sees, and what they must not."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.clinic = cls.env['res.partner'].create({
            'name': 'Portal Clinic', 'is_clinic': True, 'is_company': True})
        cls.other_clinic = cls.env['res.partner'].create({
            'name': 'Someone Else', 'is_clinic': True, 'is_company': True})
        cls.doctor = cls.env['res.users'].create({
            'name': 'Dr Portal', 'login': 'portal_doc',
            'partner_id': cls.clinic.id,
            'group_ids': [(6, 0, [cls.env.ref('base.group_portal').id])]})
        product = cls.env['product.product'].create({
            'name': 'Retainer', 'type': 'consu', 'is_storable': True})
        cls.case = cls.env['sale.order'].create({
            'partner_id': cls.clinic.id, 'patient': 'Small Patient',
            'order_line': [(0, 0, {'product_id': product.id, 'product_uom_qty': 1})]})

    def test_a_new_case_reads_as_registered(self):
        self.assertEqual(self.case.portal_stage, 'registered')
        self.assertEqual(self.case.portal_progress, 20)

    def test_the_stage_is_derived_never_typed(self):
        """A stage somebody has to remember to update is wrong by Thursday."""
        self.case.action_confirm()
        self.case.invalidate_recordset()
        self.assertEqual(self.case.portal_stage, 'in_lab')
        self.assertTrue(self.case.portal_operation)

    def test_a_cancelled_case_says_so(self):
        self.case.action_cancel()
        self.case.invalidate_recordset()
        self.assertEqual(self.case.portal_stage, 'cancel')
        self.assertEqual(self.case.portal_progress, 0)

    def test_a_doctor_can_read_the_stage_without_seeing_the_factory(self):
        """A portal user has no access to mrp.production or stock.picking — rightly. The
        stage is derived from them, so computing it as the user raised AccessError and
        the whole page 403'd."""
        self.case.action_confirm()
        as_doctor = self.case.with_user(self.doctor)
        as_doctor.invalidate_recordset()
        self.assertTrue(as_doctor.portal_stage, "the page must render")
        with self.assertRaises(Exception):
            self.env['mrp.production'].with_user(self.doctor).search([])

    def test_the_case_lines_speak_the_labs_language(self):
        rows = self.case.portal_case_lines()
        self.assertTrue(rows)
        self.assertIn('name', rows[0])
        self.assertIn('ul', rows[0])
        self.assertIn('colour', rows[0])

    def test_a_doctor_sees_their_whole_practice_not_just_one_contact(self):
        """A clinic with three branch contacts is one practice, and a doctor who cannot
        see the case they sent from the other branch will ring the lab about it."""
        from odoo.addons.lab_portal.controllers.portal import LabCustomerPortal
        branch = self.env['res.partner'].create({
            'name': 'Portal Clinic — Annexe', 'parent_id': self.clinic.id,
            'type': 'delivery'})
        self.case.action_confirm()
        branch_case = self.env['sale.order'].create({
            'partner_id': branch.id, 'patient': 'Other Child',
            'order_line': [(0, 0, {
                'product_id': self.case.order_line[0].product_id.id,
                'product_uom_qty': 1})]})
        branch_case.action_confirm()

        domain = LabCustomerPortal()._case_domain(self.doctor.partner_id)
        found = self.env['sale.order'].search(domain)
        self.assertIn(self.case, found)
        self.assertIn(branch_case, found)

    def test_another_practices_case_is_not_in_the_list(self):
        from odoo.addons.lab_portal.controllers.portal import LabCustomerPortal
        theirs = self.env['sale.order'].create({
            'partner_id': self.other_clinic.id, 'patient': 'Not Yours',
            'order_line': [(0, 0, {
                'product_id': self.case.order_line[0].product_id.id,
                'product_uom_qty': 1})]})
        theirs.action_confirm()
        domain = LabCustomerPortal()._case_domain(self.doctor.partner_id)
        self.assertNotIn(theirs, self.env['sale.order'].search(domain))

    def test_draft_quotations_are_not_shown_as_cases(self):
        """A quotation is not a case the lab is making; showing it would have a doctor
        tracking work nobody has started."""
        from odoo.addons.lab_portal.controllers.portal import LabCustomerPortal
        domain = LabCustomerPortal()._case_domain(self.doctor.partner_id)
        self.assertNotIn(self.case, self.env['sale.order'].search(domain))
        self.case.action_confirm()
        self.assertIn(self.case, self.env['sale.order'].search(domain))


@tagged('post_install', '-at_install')
class TestDigitalRx(TransactionCase):
    """A doctor's prescription: what they may see, and what the form accepts.
    (2026-09-15)"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.clinic = cls.env['res.partner'].create({
            'name': 'Rx Clinic', 'is_clinic': True, 'is_company': True})
        cls.other_clinic = cls.env['res.partner'].create({
            'name': 'Rx Elsewhere', 'is_clinic': True, 'is_company': True})
        cls.doctor = cls.env['res.users'].create({
            'name': 'Dr Rx', 'login': 'rx_portal_doc', 'partner_id': cls.clinic.id,
            'group_ids': [(6, 0, [cls.env.ref('base.group_portal').id])]})
        cls.product = cls.env['product.product'].create({
            'name': 'Rx Retainer', 'type': 'consu', 'sale_ok': True})
        cls.not_for_sale = cls.env['product.product'].create({
            'name': 'Rx Internal Part', 'type': 'consu', 'sale_ok': False})
        Request = cls.env['lab.case.request']
        line = [(0, 0, {'product_id': cls.product.id, 'ul': 'upper'})]
        cls.mine = Request.create({
            'partner_id': cls.clinic.id, 'patient': 'Mine', 'line_ids': line})
        cls.theirs = Request.create({
            'partner_id': cls.other_clinic.id, 'patient': 'Theirs', 'line_ids': line})

    def _portal(self):
        from odoo.addons.lab_portal.controllers.portal import LabCustomerPortal
        return LabCustomerPortal()

    # ------------------------------------------------------------------ record rules
    def test_a_doctor_reads_only_their_own_request_lines(self):
        """The request had a rule; its lines had none, so every practice's lines
        were readable to any portal user."""
        lines = self.env['lab.case.request.line'].with_user(self.doctor).search([])
        self.assertEqual(lines, self.mine.line_ids)
        self.assertTrue(self.theirs.line_ids)

    def test_the_line_rule_mirrors_the_request_rule(self):
        requests = self.env['lab.case.request'].with_user(self.doctor).search([])
        lines = self.env['lab.case.request.line'].with_user(self.doctor).search([])
        self.assertEqual(lines.request_id, requests)

    # ------------------------------------------------------------------ form input
    def test_valid_rows_become_lines(self):
        lines, error = self._portal()._rx_lines(
            self.env, [str(self.product.id), ''], ['lower', 'upper'], ['2', '1'])
        self.assertIsNone(error)
        self.assertEqual(lines, [(0, 0, {'product_id': self.product.id, 'ul': 'lower',
                                         'quantity': 2.0})])

    def test_a_product_id_that_is_not_a_number_is_a_form_error(self):
        lines, error = self._portal()._rx_lines(self.env, ['abc'], ['upper'], ['1'])
        self.assertEqual((lines, error), ([], 'product'))

    def test_a_product_that_does_not_exist_or_is_not_sold_is_a_form_error(self):
        missing = self.env['product.product'].search([], order='id desc', limit=1).id + 1000
        for pid in (missing, self.not_for_sale.id):
            lines, error = self._portal()._rx_lines(self.env, [str(pid)], ['upper'], ['1'])
            self.assertEqual((lines, error), ([], 'product'))

    def test_an_arch_that_is_not_one_of_the_three_is_a_form_error(self):
        lines, error = self._portal()._rx_lines(
            self.env, [str(self.product.id)], ['both'], ['1'])
        self.assertEqual((lines, error), ([], 'arch'))

    def test_a_silly_quantity_falls_back_to_one(self):
        for qty in ('nan', 'inf', '-3', 'x'):
            lines, error = self._portal()._rx_lines(
                self.env, [str(self.product.id)], ['ul'], [qty])
            self.assertIsNone(error)
            self.assertEqual(lines[0][2]['quantity'], 1.0, qty)

    def test_the_form_template_names_each_error(self):
        arch = self.env.ref('lab_portal.portal_rx_form').arch_db
        self.assertIn("error == 'product'", arch)
        self.assertIn("error == 'arch'", arch)

    # ------------------------------------------------------------------ scans
    def test_an_uploaded_scan_is_listed_on_the_request_and_reaches_the_order(self):
        """The scan was stored against the request but never put in its Scans / Files,
        so nobody saw it and conversion had nothing to carry."""
        import io
        from werkzeug.datastructures import FileStorage
        request = self.mine.sudo()
        upload = FileStorage(stream=io.BytesIO(b'solid scan\nendsolid'),
                             filename='upper.stl')
        empty = FileStorage(stream=io.BytesIO(b''), filename='')
        ids = self._portal()._rx_attach_scans(request, [upload, empty])
        self.assertEqual(len(ids), 1)
        self.assertEqual(request.attachment_ids.ids, ids)
        self.assertEqual(request.attachment_ids.res_id, request.id)

        request.action_convert_to_case()
        carried = self.env['ir.attachment'].search([
            ('res_model', '=', 'sale.order'), ('res_id', '=', request.sale_order_id.id)])
        self.assertEqual(carried.mapped('name'), ['upper.stl'])
        self.assertEqual(carried.raw, b'solid scan\nendsolid')
        self.assertTrue(request.attachment_ids, "the request keeps its own copy")
