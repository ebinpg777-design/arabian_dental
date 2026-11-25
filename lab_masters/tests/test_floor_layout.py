# -*- coding: utf-8 -*-
"""The floor as the lab's production sheets lay it out (models/floor_layout.py)."""
from odoo.tests import TransactionCase, tagged

from ..models.floor_layout import BENCHES, BOM_ROUTE, ROUTES, bench_name


@tagged('post_install', '-at_install')
class TestFloorLayout(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Loader = cls.env['lab.masters.loader']
        cls.names = {
            key: bench_name(row, cls.env.ref('lab_masters.%s' % dept), shared)
            for key, _xmlid, row, _code, dept, _rate, shared in BENCHES}

    def test_a_row_several_departments_have_is_a_bench_in_each(self):
        """Named Department/Row, as the lab asked; a row only one department has
        keeps its plain name."""
        names = self.env['mrp.workcenter'].search([]).mapped('name')
        for name in ('Wax Up/Model Preparation', 'Orthodontic/Model Preparation',
                     'Acrylic/Model Preparation', 'Metal/Trimming', 'Ceramic/Trimming',
                     'Orthodontic/Trimming', 'Acrylic/Trimming', 'Metal/Polishing',
                     'Acrylic/Polishing', 'Acrylic/Packing', 'Packing', 'Scanning', 'Finishing'):
            self.assertIn(name, names)
        self.assertEqual(len(names), len(set(names)), "no two benches share a name")
        self.assertEqual(len(names), 33)

    def test_the_benches_are_the_rows_of_the_sheets(self):
        for key, xmlid, _row, code, _dept, _rate, _shared in BENCHES:
            name = self.names[key]
            bench = self.env.ref('lab_masters.%s' % xmlid)
            self.assertTrue(bench.active, "%s is not on the floor" % name)
            self.assertEqual((bench.name, bench.code), (name, code))
            self.assertTrue(bench.department_id, "%s belongs to no department" % name)
            self.assertTrue(bench.analytic_distribution, "%s cannot post its time" % name)
        old = self.env.ref('lab_masters.wc_wash_firing')
        self.assertFalse(old.active, "a bench that is on no sheet is put away")

    def test_trail_carving_and_wax_up_follow_teeth_setting(self):
        """In every bill of materials with Teeth Setting, and only once however
        often it runs. (client, 2026-11-25)"""
        Loader = self.env['lab.masters.loader']
        teeth = self.env.ref('lab_masters.wc_teeth_setting')
        bom = self.env['mrp.bom'].create({
            'product_tmpl_id': self.env['product.template'].create({'name': 'ZZ Denture'}).id,
            'operation_ids': [(0, 0, {'name': 'Teeth Setting', 'workcenter_id': teeth.id, 'sequence': 10}),
                              (0, 0, {'name': 'Flasking', 'sequence': 20,
                                      'workcenter_id': self.env.ref('lab_masters.wc_flasking').id})]})
        Loader._floor_add_after_teeth_setting()
        Loader._floor_add_after_teeth_setting()
        steps = bom.operation_ids.sorted(lambda o: (o.sequence, o.id))
        self.assertEqual(steps.mapped('workcenter_id.name'),
                         ['Teeth Setting', 'Trail Carving', 'Wax Up', 'Flasking'])
        self.assertFalse(sum(steps[1:3].mapped('time_cycle_manual')), "no minutes, no cost")

    def test_every_route_is_its_sheets_rows_in_order(self):
        for xmlid, route in BOM_ROUTE.items():
            bom = self.env.ref('lab_masters.%s' % xmlid)
            steps = bom.operation_ids.sorted(lambda o: (o.sequence, o.id))
            self.assertEqual(steps.mapped('workcenter_id.name'),
                             [self.names[key] for key in ROUTES[route]], bom.code)
            self.assertEqual(steps.mapped('name'), steps.mapped('workcenter_id.name'),
                             "a step is named for its bench")
            self.assertFalse(steps.filtered(lambda o: not o.workcenter_id.active), bom.code)

    def test_no_products_labour_cost_moved(self):
        """Merged benches carry hands-on minutes, so the Case Cost screen says
        what it said before."""
        cost = lambda ops: sum(  # noqa: E731
            op.time_cycle_manual / 60.0 * op.workcenter_id.costs_hour for op in ops)
        checked = 0
        for bom in self.env['mrp.bom'].search([]):
            every = bom.with_context(active_test=False).operation_ids
            old = every.filtered(lambda op: not op.active)
            if not old:
                continue
            checked += 1
            self.assertAlmostEqual(cost(old), cost(every - old), places=2, msg=bom.code)
        self.assertTrue(checked, "no route kept its old operations to compare with")

    def test_a_second_run_changes_nothing(self):
        """And leaves alone what the lab has re-timed since."""
        step = self.env.ref('lab_masters.bom_metal_ceramic').operation_ids[0]
        step.time_cycle_manual = 99
        result = self.Loader.apply_floor_layout()
        self.assertEqual((result['routes'], result['archived']), (0, 0))
        self.assertEqual(step.time_cycle_manual, 99)

    def test_a_bench_somebody_still_uses_is_not_put_away(self):
        Bench = self.env['mrp.workcenter']
        department = self.env.ref('lab_masters.dept_metal')
        used = Bench.create({'name': 'ZZ Own Bench', 'department_id': department.id})
        loose = Bench.create({'name': 'ZZ Loose Bench', 'department_id': department.id})
        bom = self.env.ref('lab_masters.bom_hawleys')
        self.env['mrp.routing.workcenter'].create({
            'bom_id': bom.id, 'name': 'ZZ Own Bench', 'workcenter_id': used.id, 'sequence': 35})
        self.Loader.apply_floor_layout()
        self.assertTrue(used.active, "a route still walks it")
        self.assertFalse(loose.active)

    def test_a_job_is_filed_under_the_department_it_starts_in(self):
        """Each sheet has its own model preparation, so a retainer starts in
        Orthodontic and a denture in Acrylic."""
        expected = {'bom_hawleys': 'dept_ortho', 'bom_rpd': 'dept_acrylic',
                    'bom_zirconia_basic': 'dept_wax_up'}
        for bom_xmlid, department in expected.items():
            bom = self.env.ref('lab_masters.%s' % bom_xmlid)
            order = self.env['mrp.production'].create({
                'product_id': bom.product_tmpl_id.product_variant_id.id,
                'bom_id': bom.id, 'product_qty': 1})
            order.action_confirm()
            self.assertEqual(order.department_id, self.env.ref('lab_masters.%s' % department),
                             bom.code)

    def test_an_open_order_follows_its_route_onto_the_new_steps(self):
        bom = self.env.ref('lab_masters.bom_hawleys')
        order = self.env['mrp.production'].create({
            'product_id': bom.product_tmpl_id.product_variant_id.id,
            'bom_id': bom.id, 'product_qty': 1})
        order.action_confirm()
        self.assertNotIn(order.id, self.Loader._floor_stale_order_ids())

        # the route changes under it, as the migration changes every route
        retired = bom.operation_ids.filtered(lambda o: o.name == 'Wire Bending')
        retired.action_archive()
        self.env['mrp.routing.workcenter'].create({
            'bom_id': bom.id, 'name': 'Casting', 'sequence': 25, 'time_mode': 'manual',
            'workcenter_id': self.env.ref('lab_masters.wc_casting').id})
        self.assertIn(order.id, self.Loader._floor_stale_order_ids())

        # Newest first, one order: this one. Without the limit the test would move
        # every open order of whatever database it runs on.
        self.assertEqual(self.Loader._floor_stale_order_ids(limit=1), [order.id])
        self.Loader.reroute_open_orders(limit=1)
        self.assertNotIn(order.id, self.Loader._floor_stale_order_ids())
        steps = order.workorder_ids.filtered(lambda w: w.state != 'cancel').sorted(
            lambda w: (w.sequence, w.id))
        self.assertEqual(steps.mapped('name'),
                         ['Orthodontic/Model Preparation', 'Casting', 'Acrylization',
                          'Orthodontic/Trimming', 'Final Check', 'Packing'])
        self.assertEqual(steps[0].state, 'ready', "the case is at its first step")
        self.assertEqual(set(steps[1:].mapped('state')), {'blocked'})
        self.assertFalse(order.is_outdated_bom)
