# -*- coding: utf-8 -*-
"""How dental_sale's fields land on the suite's — the rules a technician or an
accountant would check first when the migrated data looks wrong."""
from odoo import fields
from odoo.tests import TransactionCase, tagged

from ..models.migration_spec import (DISTRICT_ALIASES, JAW_MAP, PRIORITY_MAP,
                                     teeth_from_quadrants)


@tagged('post_install', '-at_install')
class TestDentalFieldMapping(TransactionCase):

    def test_quadrant_digits_become_fdi_numbers(self):
        # "4 3" in the 1st quadrant column means teeth 14 and 13
        self.assertEqual(teeth_from_quadrants({'quad1': '4 3', 'quad2': '2 4 5'}),
                         '14, 13, 22, 24, 25')

    def test_tooth_numbers_already_in_fdi_pass_through(self):
        self.assertEqual(teeth_from_quadrants({'t_no': '46,47'}), '46, 47')
        self.assertEqual(teeth_from_quadrants({'quad1': '14 13'}), '14, 13')

    def test_quadrants_and_free_text_combine_without_repeats(self):
        self.assertEqual(teeth_from_quadrants({'quad3': '6', 't_no': '36, 45'}), '36, 45')

    def test_a_quarter_alone_is_kept_as_a_quarter(self):
        self.assertEqual(teeth_from_quadrants({'quarter': 'q2'}), 'Q2')
        self.assertEqual(teeth_from_quadrants({'quarter': 'q2', 'quad2': '1'}), '21')

    def test_nothing_gives_nothing(self):
        self.assertEqual(teeth_from_quadrants({}), '')
        self.assertEqual(teeth_from_quadrants({'quad1': '  ', 't_no': None}), '')

    def test_jaw_and_priority_land_on_the_suites_selections(self):
        self.assertEqual(JAW_MAP['upper_lower'], 'ul')
        self.assertEqual(PRIORITY_MAP['high'], 'urgent')
        self.assertEqual(PRIORITY_MAP['medium'], 'normal')
        Order = self.env['sale.order']
        for value in PRIORITY_MAP.values():
            self.assertIn(value, dict(Order._fields['priority'].selection),
                          "%s is not a priority the suite knows" % value)
        Line = self.env['sale.order.line']
        for value in JAW_MAP.values():
            self.assertIn(value, dict(Line._fields['ul'].selection))

    def test_district_spellings_collapse_onto_one_tag(self):
        backend = self.env['migration.backend'].create({'name': 'map test'})
        for raw, canonical in (('WAYAND', 'WAYANAD'), ('trissur', 'THRISSUR'),
                               ('MALAPPURA.', 'MALAPPURAM'), ('Kannur', 'KANNUR')):
            vals = {'name': raw}
            backend._hook_district({'id': 1, 'name': raw}, vals, 'normalize', None, {})
            self.assertEqual(vals['name'], canonical)
        self.assertEqual(DISTRICT_ALIASES['CALICUT'], 'KOZHIKODE')

    def test_a_shade_with_a_stray_comma_is_the_same_shade(self):
        backend = self.env['migration.backend'].create({'name': 'map test'})
        for raw, clean in (('A3,', 'A3'), ('2L2.5 2m3', '2L2.5 2M3'), (' b3 ', 'B3')):
            vals = {'name': raw}
            backend._hook_colour({'id': 1, 'name': raw}, vals, 'normalize', None, {})
            self.assertEqual(vals['name'], clean)

    def test_translated_json_is_unwrapped_to_english(self):
        Backend = self.env['migration.backend']
        self.assertEqual(Backend._coerce({'en_US': 'Ceramic', 'en_IN': 'Ceramic'}), 'Ceramic')
        self.assertEqual(Backend._coerce({'en_IN': 'Only Indian'}), 'Only Indian')
        self.assertEqual(Backend._coerce('plain'), 'plain')
        self.assertIsNone(Backend._coerce(None))

    def test_the_patient_comes_from_the_lines_when_the_order_has_none(self):
        Backend = self.env['migration.backend']
        self.assertEqual(Backend._dental_patient({'patient_name': None},
                                                 [{'patient_name': ' RAMLA  BEEVI '},
                                                  {'patient_name': 'ramla beevi'}]),
                         'RAMLA BEEVI')
        self.assertEqual(Backend._dental_patient({'patient_name': 'ANAS'},
                                                 [{'patient_name': 'SUHAIL'}]),
                         'ANAS / SUHAIL')

    def test_a_customer_is_a_clinic_and_a_doctor_named_one_is_both(self):
        backend = self.env['migration.backend'].create({'name': 'map test'})
        vals = {'name': 'DR. MEHROOF, KONDOTTY', 'customer_rank': 1}
        backend._hook_partner({'id': 1, 'mobile': None}, vals, 'scalars', True, {})
        self.assertTrue(vals['is_clinic'])
        self.assertTrue(vals['is_doctor'])
        vals = {'name': 'AL RAHA DC, PARAPPANANGADI', 'customer_rank': 0}
        backend._hook_partner({'id': 2, 'is_customer': True, 'mobile': '9847'}, vals,
                              'scalars', True, {})
        self.assertTrue(vals['is_clinic'])
        self.assertNotIn('is_doctor', vals)
        self.assertEqual(vals['phone'], '9847', "v19 has no mobile field; the number survives")
        self.assertEqual(vals['customer_rank'], 1)


@tagged('post_install', '-at_install')
class TestNormaliseBeforeMatch(TransactionCase):
    """A hook that cleans a name must clean it BEFORE the engine looks for an
    existing record, or the second spelling becomes a second record."""

    def _run_districts(self, rows):
        from .test_migration_safety import FakeSource
        backend = self.env['migration.backend'].create({'name': 'normalise test'})
        spec = next(s for s in __import__(
            'odoo.addons.lab_migration.models.migration_spec',
            fromlist=['ENTITY_SPECS']).ENTITY_SPECS if s['key'] == 'district')
        cur = FakeSource([('FROM "res_district"', rows)])
        stats = []
        backend._migration_env()._sync_pass1(cur, spec, {}, stats)
        return stats

    def test_two_spellings_of_a_district_make_one_tag(self):
        Tag = self.env['res.partner.category']
        before = Tag.search_count([('name', 'in', ('THRISSUR', 'TRISSUR'))])
        self._run_districts([{'id': 9_900_101, 'name': 'TRISSUR'},
                             {'id': 9_900_102, 'name': 'THRISSUR'}])
        self.assertEqual(Tag.search_count([('name', '=', 'THRISSUR')]) - before, 1)
        self.assertFalse(Tag.search([('name', '=', 'TRISSUR')]))
        # both source ids point at the one tag
        Map = self.env['migration.map']
        targets = {m.dst_id for m in Map.search([('dst_model', '=', 'res.partner.category'),
                                                 ('src_id', 'in', (9_900_101, 9_900_102))])}
        self.assertEqual(len(targets), 1)


@tagged('post_install', '-at_install')
class TestUnitMapping(TransactionCase):
    """'Nos' is Units; 'SET' is the lab's own label and becomes a unit of Units."""

    def _run_units(self, rows):
        from .test_migration_safety import FakeSource
        backend = self.env['migration.backend'].create({'name': 'unit test'})
        spec = next(s for s in __import__(
            'odoo.addons.lab_migration.models.migration_spec',
            fromlist=['ENTITY_SPECS']).ENTITY_SPECS if s['key'] == 'uom')
        cache = {'_uom_categ_': {1: 'Nos', 2: 'Weight'}}
        backend._migration_env()._sync_pass1(FakeSource([('FROM "uom_uom"', rows)]),
                                             spec, cache, [])
        return cache

    def test_nos_is_adopted_as_units_and_set_is_created_under_it(self):
        Uom = self.env['uom.uom']
        units = Uom.search([('name', '=', 'Units')], limit=1)
        cache = self._run_units([
            {'id': 9_900_201, 'name': 'Nos', 'category_id': 1, 'factor': 1.0, 'rounding': 0.01, 'active': True},
            {'id': 9_900_202, 'name': 'SET', 'category_id': 1, 'factor': 1.0, 'rounding': 0.01, 'active': True},
            {'id': 9_900_203, 'name': 'ML', 'category_id': 2, 'factor': 1.0, 'rounding': 0.01, 'active': True},
        ])
        self.assertEqual(cache['uom.uom'][9_900_201], units.id, "Nos is Units, not a new unit")
        made = Uom.browse(cache['uom.uom'][9_900_202])
        self.assertEqual(made.name, 'SET')
        self.assertEqual(made.relative_uom_id, units)
        self.assertEqual(made.relative_factor, 1.0)
        self.assertEqual(Uom.browse(cache['uom.uom'][9_900_203]).name, 'ml',
                         "a case variant adopts the built-in spelling")


@tagged('post_install', '-at_install')
class TestMaterialRequestPhase(TransactionCase):
    """The lab's requisitions land on the rewritten module, with their transfer."""

    def test_an_approved_request_with_a_done_transfer_arrives_delivered(self):
        if 'material.request' not in self.env:
            self.fail("material_request must be installed on the target for this phase")
        from .test_migration_safety import FakeSource, SRC
        from unittest.mock import patch
        backend = self.env['migration.backend'].create({
            'name': 'mr test', 'txn_from_date': '2025-01-01'})
        wh = self.env['stock.warehouse'].search([], limit=1)
        dept = self.env['stock.location'].create({
            'name': 'Z- Test Dept', 'usage': 'internal', 'location_id': wh.lot_stock_id.id})
        product = self.env['product.product'].create({'name': 'Wax', 'type': 'consu', 'is_storable': True})
        picking = self.env['stock.picking'].create({
            'picking_type_id': wh.int_type_id.id, 'location_id': wh.lot_stock_id.id,
            'location_dest_id': dept.id,
            'move_ids': [(0, 0, {'product_id': product.id, 'product_uom_qty': 3,
                                 'product_uom': product.uom_id.id,
                                 'location_id': wh.lot_stock_id.id, 'location_dest_id': dept.id})],
        })
        picking.action_confirm()
        picking.move_ids.quantity = 3
        picking.move_ids.picked = True
        picking.button_validate()
        cache = {
            'res.company': {SRC + 1: self.env.company.id},
            'stock.location': {SRC + 7: dept.id},
            'product.product': {SRC + 11: product.id},
            'res.users': {SRC + 2: self.env.user.id},
            'stock.picking': {SRC + 50: picking.id},
        }
        cur = FakeSource([
            ('FROM material_request_lines', [
                {'id': SRC + 301, 'material_request_id': SRC + 300, 'product_id': SRC + 11,
                 'quantity': 3.0}]),
            ('FROM stock_move m', [
                {'id': SRC + 400, 'picking_id': SRC + 50, 'product_id': SRC + 11}]),
            ('FROM stock_picking WHERE material_request_id', [
                {'id': SRC + 50, 'material_request_id': SRC + 300}]),
            ('FROM material_request WHERE', [
                {'id': SRC + 300, 'name': 'MR000900', 'location_id': SRC + 7, 'state': 'approved',
                 'company_id': SRC + 1, 'create_uid': SRC + 2,
                 'create_date': fields.Datetime.now(), 'write_date': fields.Datetime.now()}]),
        ])
        stats = []
        with patch.object(self.env.cr, 'commit', lambda: None):
            backend._migration_env()._txn_material_requests(cur, cache, stats)
        request = self.env['material.request'].search([('name', '=', 'MR000900')])
        self.assertEqual(len(request), 1, stats)
        self.assertEqual(request.state, 'done', "approved + transfer done = delivered")
        self.assertEqual(request.location_id, dept)
        self.assertEqual(request.picking_ids, picking)
        self.assertEqual(request.line_ids.qty_approved, 3.0)
        self.assertEqual(request.line_ids.qty_delivered, 3.0)
        self.assertEqual(request.progress, 100)

    def test_a_request_migrated_before_its_transfer_is_linked_on_the_next_pass(self):
        """v17 approved = goods moved. If the request came across first and the
        transfer later, a resume pass links them and settles Delivered."""
        if 'material.request' not in self.env:
            self.fail("material_request must be installed on the target for this phase")
        from .test_migration_safety import FakeSource, SRC
        from unittest.mock import patch
        backend = self.env['migration.backend'].create({
            'name': 'mr resume test', 'txn_from_date': '2025-01-01', 'txn_only_new': True})
        wh = self.env['stock.warehouse'].search([], limit=1)
        dept = self.env['stock.location'].create({
            'name': 'Z- Resume Dept', 'usage': 'internal', 'location_id': wh.lot_stock_id.id})
        product = self.env['product.product'].create({'name': 'Stone', 'type': 'consu', 'is_storable': True})
        request = self.env['material.request'].create({
            'name': 'MR000901', 'location_id': dept.id,
            'line_ids': [(0, 0, {'product_id': product.id, 'quantity': 2, 'qty_approved': 2,
                                 'qty_approved_set': True})]})
        request.write({'state': 'approved'})
        self.env['migration.map'].create({'dst_model': 'material.request', 'src_id': SRC + 310,
                                          'dst_id': request.id})
        picking = self.env['stock.picking'].create({
            'picking_type_id': wh.int_type_id.id, 'location_id': wh.lot_stock_id.id,
            'location_dest_id': dept.id,
            'move_ids': [(0, 0, {'product_id': product.id, 'product_uom_qty': 2,
                                 'product_uom': product.uom_id.id,
                                 'location_id': wh.lot_stock_id.id, 'location_dest_id': dept.id})]})
        picking.action_confirm(); picking.move_ids.quantity = 2; picking.move_ids.picked = True
        picking.button_validate()
        cache = {'res.company': {SRC + 1: self.env.company.id},
                 'stock.location': {SRC + 7: dept.id}, 'product.product': {SRC + 11: product.id},
                 'res.users': {SRC + 2: self.env.user.id}, 'stock.picking': {SRC + 51: picking.id}}
        cur = FakeSource([
            ('FROM material_request_lines', [{'id': SRC + 311, 'material_request_id': SRC + 310,
                                              'product_id': SRC + 11, 'quantity': 2.0}]),
            ('FROM stock_move m', [{'id': SRC + 401, 'picking_id': SRC + 51, 'product_id': SRC + 11}]),
            ('FROM stock_picking WHERE material_request_id', [{'id': SRC + 51, 'material_request_id': SRC + 310}]),
            ('FROM material_request WHERE', [{'id': SRC + 310, 'name': 'MR000901', 'location_id': SRC + 7,
                                              'state': 'approved', 'company_id': SRC + 1, 'create_uid': SRC + 2,
                                              'create_date': fields.Datetime.now(), 'write_date': fields.Datetime.now()}]),
        ])
        stats = []
        with patch.object(self.env.cr, 'commit', lambda: None):
            backend._migration_env()._txn_material_requests(cur, cache, stats)
        self.assertIn('linked-transfers=1', stats[0], stats)
        self.assertEqual(request.picking_ids, picking)
        self.assertEqual(request.state, 'done')
        self.assertEqual(request.line_ids.qty_delivered, 2.0)
        self.assertEqual(len(request.line_ids), 1, "resume mode never rewrites the lines")


@tagged('post_install', '-at_install')
class TestStockReplay(TransactionCase):
    """Move lines replayed on done moves rebuild the stock, and nothing else does."""

    def test_a_done_move_line_puts_the_quantity_on_the_shelf(self):
        from .test_migration_safety import FakeSource, SRC
        from unittest.mock import patch
        backend = self.env['migration.backend'].create({'name': 'stock test', 'txn_from_date': '2025-01-01'})
        wh = self.env['stock.warehouse'].search([], limit=1)
        product = self.env['product.product'].create({'name': 'Plaster', 'type': 'consu', 'is_storable': True})
        adjust = self.env['stock.location'].search([('usage', '=', 'inventory')], limit=1)
        # the move the pickings phase leaves behind: done by SQL, no lines yet
        move = self.env['stock.move'].create({
            'product_id': product.id, 'product_uom': product.uom_id.id, 'product_uom_qty': 5,
            'location_id': adjust.id, 'location_dest_id': wh.lot_stock_id.id,
            'description_picking': 'opening count', 'x_src_id': SRC + 500, 'is_inventory': True,
        })
        self.env.flush_all()
        self.env.cr.execute("UPDATE stock_move SET state='done' WHERE id=%s", (move.id,))
        self.env.invalidate_all()
        before = product.with_context(location=wh.lot_stock_id.id).qty_available
        cache = {'stock.location': {SRC + 8: wh.lot_stock_id.id, SRC + 9: adjust.id},
                 'product.product': {SRC + 11: product.id}, 'uom.uom': {}}
        cur = FakeSource([('FROM stock_move_line l', [
            {'id': SRC + 600, 'move_id': SRC + 500, 'product_id': SRC + 11,
             'location_id': SRC + 9, 'location_dest_id': SRC + 8, 'quantity': 5.0,
             'date': fields.Datetime.now(), 'product_uom_id': None}])])
        stats = []
        with patch.object(self.env.cr, 'commit', lambda: None):
            backend._migration_env()._txn_move_lines(cur, cache, stats)
        self.assertIn('created=1', stats[0], stats)
        product.invalidate_recordset()
        after = product.with_context(location=wh.lot_stock_id.id).qty_available
        self.assertEqual(after - before, 5.0, "the replayed line is the stock")
        self.assertEqual(move.move_line_ids.quantity, 5.0)
        # a second run leaves it alone
        with patch.object(self.env.cr, 'commit', lambda: None):
            backend._migration_env()._txn_move_lines(cur, cache, stats)
        self.assertIn('skipped=1', stats[-1])
        product.invalidate_recordset()
        self.assertEqual(product.with_context(location=wh.lot_stock_id.id).qty_available - before, 5.0)

    def test_an_adjustment_move_and_its_line_count_once(self):
        """The move must be created WITHOUT a quantity: written with one, Odoo 19
        makes a move line of its own and the replayed source line then doubles
        the stock. (live run, 2026-09-24: on-hand came out at twice the source)"""
        from .test_migration_safety import FakeSource, SRC
        from unittest.mock import patch
        backend = self.env['migration.backend'].create({'name': 'stock once', 'txn_from_date': '2025-01-01'})
        wh = self.env['stock.warehouse'].search([], limit=1)
        product = self.env['product.product'].create({'name': 'Alginate', 'type': 'consu', 'is_storable': True})
        adjust = self.env['stock.location'].search([('usage', '=', 'inventory')], limit=1)
        before = product.with_context(location=wh.lot_stock_id.id).qty_available
        cache = {'stock.location': {SRC + 8: wh.lot_stock_id.id, SRC + 9: adjust.id},
                 'product.product': {SRC + 11: product.id}, 'uom.uom': {},
                 'res.company': {SRC + 1: self.env.company.id}}
        now = fields.Datetime.now()
        cur = FakeSource([
            ('FROM stock_move_line l', [
                {'id': SRC + 610, 'move_id': SRC + 510, 'product_id': SRC + 11,
                 'location_id': SRC + 9, 'location_dest_id': SRC + 8, 'quantity': 7.0,
                 'date': now, 'product_uom_id': None}]),
            ('FROM stock_move', [
                {'id': SRC + 510, 'product_id': SRC + 11, 'location_id': SRC + 9,
                 'location_dest_id': SRC + 8, 'product_uom_qty': 7.0, 'quantity': 7.0,
                 'name': 'count', 'origin': None, 'reference': 'count', 'date': now,
                 'company_id': SRC + 1, 'is_inventory': True, 'state': 'done',
                 'description_picking': None, 'product_uom': None}]),
        ])
        stats = []
        with patch.object(self.env.cr, 'commit', lambda: None):
            backend._migration_env()._txn_stock_moves(cur, cache, stats)
            backend._migration_env()._txn_move_lines(cur, cache, stats)
        move = self.env['stock.move'].search([('x_src_id', '=', SRC + 510)])
        self.assertEqual(move.state, 'done')
        self.assertEqual(len(move.move_line_ids), 1, "one line: the source's, not one Odoo made as well")
        self.assertEqual(move.quantity, 7.0)
        product.invalidate_recordset()
        self.assertEqual(product.with_context(location=wh.lot_stock_id.id).qty_available - before, 7.0)
