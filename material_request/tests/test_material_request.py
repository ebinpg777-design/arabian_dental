# -*- coding: utf-8 -*-
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tests.common import new_test_user


@tagged('post_install', '-at_install')
class TestMaterialRequest(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.warehouse = cls.env['stock.warehouse'].search([('company_id', '=', cls.env.company.id)], limit=1)
        cls.store = cls.warehouse.lot_stock_id
        cls.dept = cls.env['stock.location'].create({
            'name': 'Z- Test Department', 'usage': 'internal', 'location_id': cls.store.id})
        cls.env.company.material_request_type_id = cls.warehouse.int_type_id
        cls.requester = new_test_user(cls.env, login='mr_requester',
                                      groups='base.group_user,material_request.material_request_user')
        cls.store_keeper = new_test_user(cls.env, login='mr_store',
                                         groups='base.group_user,material_request.material_request_manager,stock.group_stock_user')
        cls.env.company.material_request_approver_id = cls.store_keeper
        cls.mask = cls.env['product.product'].create({
            'name': 'Face Mask 3ply', 'type': 'consu', 'is_storable': True, 'standard_price': 2.0})
        cls.wax = cls.env['product.product'].create({
            'name': 'Modelling Wax', 'type': 'consu', 'is_storable': True, 'standard_price': 100.0})
        cls.env['stock.quant']._update_available_quantity(cls.mask, cls.store, 50)
        cls.env['stock.quant']._update_available_quantity(cls.wax, cls.store, 2)

    def _request(self, user=None, lines=None):
        Request = self.env['material.request'].with_user(user or self.requester)
        lines = lines or [(self.mask, 20), (self.wax, 5)]
        return Request.create({
            'location_id': self.dept.id,
            'line_ids': [(0, 0, {'product_id': p.id, 'quantity': q}) for p, q in lines],
        })

    def test_availability_is_read_from_the_store_before_approval(self):
        request = self._request()
        mask, wax = request.line_ids.sorted('id')
        self.assertEqual(mask.availability, 'ok')
        self.assertEqual(mask.qty_available, 50)
        self.assertEqual(wax.availability, 'short', "2 on hand against 5 asked")
        self.assertTrue(request.has_shortage)
        self.assertEqual(request.shortage_count, 1)
        self.assertEqual(request.estimated_cost, 20 * 2.0 + 5 * 100.0)

    def test_submit_notifies_the_approver_and_needs_lines(self):
        empty = self.env['material.request'].with_user(self.requester).create({'location_id': self.dept.id})
        with self.assertRaises(ValidationError):
            empty.action_confirm()
        request = self._request()
        request.action_confirm()
        self.assertEqual(request.state, 'confirm')
        activity = request.activity_ids.filtered(lambda a: a.user_id == self.store_keeper)
        self.assertTrue(activity, "the store keeper is asked to approve")

    def test_approve_raises_the_transfer_for_the_approved_quantities(self):
        request = self._request()
        request.action_confirm()
        mask, wax = request.line_ids.sorted('id')
        wax.with_user(self.store_keeper).write({'qty_approved': 2, 'qty_approved_set': True})
        request.with_user(self.store_keeper).action_approve()
        self.assertEqual(request.state, 'approved')
        self.assertEqual(request.approver_id, self.store_keeper)
        self.assertEqual(mask.qty_approved, 20, "left untouched, approved for what was asked")
        picking = request.picking_ids
        self.assertEqual(len(picking), 1)
        self.assertEqual(picking.location_id, self.store)
        self.assertEqual(picking.location_dest_id, self.dept)
        moves = {m.product_id: m.product_uom_qty for m in picking.move_ids}
        self.assertEqual(moves, {self.mask: 20.0, self.wax: 2.0})
        self.assertFalse(request.activity_ids, "the approval activity is done")

    def test_a_line_refused_outright_is_left_out_of_the_transfer(self):
        request = self._request()
        request.action_confirm()
        mask, wax = request.line_ids.sorted('id')
        wax.write({'qty_approved': 0, 'qty_approved_set': True})
        request.with_user(self.store_keeper).action_approve()
        self.assertEqual(request.picking_ids.move_ids.product_id, self.mask)

    def test_validating_the_transfer_delivers_the_request(self):
        request = self._request(lines=[(self.mask, 10)])
        request.action_confirm()
        request.with_user(self.store_keeper).action_approve()
        # the store validates its own transfer; the requester never touches moves
        picking = request.sudo().picking_ids.with_user(self.store_keeper)
        picking.move_ids.quantity = 10
        picking.move_ids.picked = True
        picking.button_validate()
        self.assertEqual(picking.state, 'done')
        self.assertEqual(request.state, 'done')
        self.assertEqual(request.line_ids.qty_delivered, 10)
        self.assertEqual(request.progress, 100)

    def test_partial_delivery_shows_as_progress(self):
        request = self._request(lines=[(self.mask, 10)])
        request.action_confirm()
        request.with_user(self.store_keeper).action_approve()
        picking = request.sudo().picking_ids.with_user(self.store_keeper)
        picking.move_ids.quantity = 4
        picking.move_ids.picked = True
        picking.with_context(skip_backorder=True).button_validate()
        self.assertEqual(request.line_ids.qty_delivered, 4)
        self.assertEqual(request.progress, 40)

    def test_reject_needs_a_reason_and_tells_the_requester(self):
        request = self._request()
        request.action_confirm()
        wizard = self.env['material.request.reject'].with_user(self.store_keeper).create({
            'request_id': request.id, 'reason': 'Out of stock until next purchase.'})
        wizard.action_reject()
        self.assertEqual(request.state, 'rejected')
        self.assertIn('next purchase', request.rejection_reason)
        self.assertTrue(any('Rejected' in (m.body or '') for m in request.message_ids))

    def test_request_again_copies_the_lines_into_a_fresh_draft(self):
        request = self._request()
        request.action_confirm()
        request.with_user(self.store_keeper).action_approve()
        request.action_mark_done()
        action = request.action_reorder()
        new = self.env['material.request'].browse(action['res_id'])
        self.assertEqual(new.state, 'draft')
        self.assertEqual(sorted(new.line_ids.mapped('product_id').ids),
                         sorted(request.line_ids.mapped('product_id').ids))
        self.assertFalse(any(new.line_ids.mapped('qty_approved')))

    def test_a_requester_sees_only_their_own_and_the_store_sees_all(self):
        mine = self._request()
        other = new_test_user(self.env, login='mr_other',
                              groups='base.group_user,material_request.material_request_user')
        theirs = self._request(user=other)
        Request = self.env['material.request']
        self.assertEqual(Request.with_user(self.requester).search([]).ids, [mine.id])
        self.assertIn(theirs.id, Request.with_user(self.store_keeper).search([]).ids)
        with self.assertRaises(AccessError):
            theirs.with_user(self.requester).read(['name'])

    def test_a_delivered_request_cannot_be_cancelled_or_deleted(self):
        request = self._request(lines=[(self.mask, 1)])
        request.action_confirm()
        request.with_user(self.store_keeper).action_approve()
        request.action_mark_done()
        with self.assertRaises(UserError):
            request.action_cancel()
        with self.assertRaises(UserError):
            request.unlink()

    def test_late_means_needed_by_has_passed_on_an_open_request(self):
        request = self._request()
        request.date_needed = '2020-01-01'
        self.assertTrue(request.is_late)
        self.assertIn(request.id, self.env['material.request'].search([('is_late', '=', True)]).ids)
        request.action_cancel()
        self.assertFalse(request.is_late)
