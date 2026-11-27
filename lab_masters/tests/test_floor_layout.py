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
        for name in ('Cutting', 'Scooping', 'Blocking', 'Metal/Sintering', 'Metal/Casting',
                     'Metal/Printing', 'Sintering', 'Orthodontic/Model Preparation',
                     'Acrylic/Model Preparation', 'Metal/Trimming', 'Ceramic/Trimming',
                     'Orthodontic/Trimming', 'Acrylic/Trimming', 'Metal/Polishing',
                     'Acrylic/Polishing', 'Acrylic/Packing', 'Packing', 'Scanning', 'Finishing'):
            self.assertIn(name, names)
        self.assertEqual(len(names), len(set(names)), "no two benches share a name")
        self.assertEqual(len(names), 38)

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

    # ------------------------------------------ Opaque and Buildup, two benches
    def _ceramic_bom(self, minutes=20.0):
        opaque = self.env.ref('lab_masters.wc_opaque_buildup')
        return self.env['mrp.bom'].create({
            'product_tmpl_id': self.env['product.template'].create({'name': 'ZZ Crown'}).id,
            'operation_ids': [
                (0, 0, {'name': 'Opaque & Buildup', 'workcenter_id': opaque.id, 'sequence': 10,
                        'time_mode': 'manual', 'time_cycle_manual': minutes}),
                (0, 0, {'name': 'Ceramic/Trimming', 'sequence': 20, 'time_mode': 'manual',
                        'workcenter_id': self.env.ref('lab_masters.wc_trimming_carving').id})]})

    def test_opaque_and_buildup_are_two_benches(self):
        opaque = self.env.ref('lab_masters.wc_opaque_buildup')
        buildup = self.env.ref('lab_masters.wc_ceramic_buildup')
        self.assertEqual((opaque.name, buildup.name), ('Opaque', 'Buildup'))
        self.assertEqual(buildup.costs_hour, opaque.costs_hour, "the same rate, so no cost moves")
        self.assertEqual(buildup.department_id, opaque.department_id)

    def test_the_one_step_becomes_two_and_only_once(self):
        """(client, 2026-11-26: "Split opaque and buildup to two work centres and
        split operations also")"""
        bom = self._ceramic_bom()
        before = self.Loader._floor_labour(bom)
        self.Loader._floor_split_opaque_buildup()
        self.assertFalse(self.Loader._floor_split_opaque_buildup(), "a second run finds nothing")
        steps = bom.operation_ids.sorted(lambda o: (o.sequence, o.id))
        self.assertEqual(steps.mapped('name'), ['Opaque', 'Buildup', 'Ceramic/Trimming'])
        self.assertEqual(steps.mapped('workcenter_id.name'), ['Opaque', 'Buildup', 'Ceramic/Trimming'])
        self.assertEqual(sum(steps[:2].mapped('time_cycle_manual')), 20.0, "the minutes are divided, not added to")
        self.assertAlmostEqual(self.Loader._floor_labour(bom), before, places=2)

    def test_the_costed_crown_divides_as_its_old_stages_say(self):
        """The model check to Opaque, the two build-ups to Buildup."""
        steps = self.env.ref('lab_masters.bom_metal_ceramic').operation_ids
        minutes = {s.workcenter_id.name: s.time_cycle_manual for s in steps}
        self.assertEqual((minutes['Opaque'], minutes['Buildup']), (10.0, 13.0))

    def test_a_job_on_the_floor_gets_its_buildup_step(self):
        bom = self._ceramic_bom()
        job = self.env['mrp.production'].create({
            'product_id': bom.product_tmpl_id.product_variant_id.id,
            'bom_id': bom.id, 'product_qty': 1})
        job.action_confirm()
        self.Loader._floor_split_opaque_buildup()
        left = self.Loader.split_open_opaque_steps(productions=job)
        self.assertFalse(left)
        self.assertFalse(self.Loader.split_open_opaque_steps(productions=job), "and only once")
        steps = job.workorder_ids.sorted(lambda w: (w.sequence, w.id))
        self.assertEqual(steps.mapped('name'), ['Opaque', 'Buildup', 'Ceramic/Trimming'])
        self.assertEqual(steps.mapped('workcenter_id.name'), ['Opaque', 'Buildup', 'Ceramic/Trimming'])
        self.assertEqual(sum(steps[:2].mapped('duration_expected')), 20.0)
        # the chain: Buildup waits for Opaque, Trimming waits for Buildup
        self.assertEqual(steps[1].blocked_by_workorder_ids, steps[0])
        self.assertEqual(steps[2].blocked_by_workorder_ids, steps[1])
        self.assertEqual(steps.mapped('state'), ['ready', 'blocked', 'blocked'])
        self.assertFalse(job.is_outdated_bom, "it has every step of its bill of materials again")

    def test_a_job_not_yet_confirmed_gets_it_too(self):
        """Most of the jobs waiting were still drafts: the step is added the same
        way, and confirming the job afterwards chains it like any other."""
        bom = self._ceramic_bom()
        job = self.env['mrp.production'].create({
            'product_id': bom.product_tmpl_id.product_variant_id.id,
            'bom_id': bom.id, 'product_qty': 1})
        self.assertEqual(job.state, 'draft')
        self.assertEqual(job.workorder_ids.mapped('name'), ['Opaque & Buildup', 'Ceramic/Trimming'])
        self.Loader._floor_split_opaque_buildup()
        self.assertFalse(self.Loader.split_open_opaque_steps(productions=job))
        job.action_confirm()
        steps = job.workorder_ids.sorted(lambda w: (w.sequence, w.id))
        self.assertEqual(steps.mapped('workcenter_id.name'), ['Opaque', 'Buildup', 'Ceramic/Trimming'])
        self.assertEqual(steps[1].blocked_by_workorder_ids, steps[0])
        self.assertEqual(steps[2].blocked_by_workorder_ids, steps[1])

    # ------------------------------------- a printed model is prepared like any
    def _model_bom(self, category=None):
        ref = lambda x: self.env.ref('lab_masters.%s' % x).id       # noqa: E731
        product = self.env['product.template'].create(dict(
            {'name': 'ZZ PRINTED MODEL'}, **({'categ_id': category.id} if category else {})))
        return self.env['mrp.bom'].create({
            'product_tmpl_id': product.id,
            'operation_ids': [(0, 0, {'name': name, 'workcenter_id': ref(xmlid), 'sequence': seq,
                                      'time_mode': 'manual'})
                              for seq, (name, xmlid) in enumerate([
                                  ('Scanning', 'wc_scanning'), ('Designing', 'wc_designing'),
                                  ('Printing', 'wc_resin_printing'), ('Light Curing', 'wc_light_curing'),
                                  ('Final Check', 'wc_quality_control'), ('Packing', 'wc_packing')], start=1)]})

    def test_a_printed_model_goes_to_model_preparation(self):
        """(client, 2026-11-26: "Model preparation workorder should be added to the
        MOs with SO is 3d printing")"""
        bom = self._model_bom()
        job = self.env['mrp.production'].create({
            'product_id': bom.product_tmpl_id.product_variant_id.id,
            'bom_id': bom.id, 'product_qty': 1})
        job.action_confirm()
        self.Loader._floor_add_model_preparation()
        self.assertNotIn(bom, self.Loader._floor_printed_model_boms(self.Loader._floor_benches()),
                         "it has the step now: a second run leaves it alone")
        steps = bom.operation_ids.sorted(lambda o: (o.sequence, o.id))
        self.assertEqual(steps.mapped('workcenter_id.name'), [
            'Scanning', 'Designing', 'Printing', 'Light Curing', 'Cutting',
            'Final Check', 'Packing'])
        self.assertFalse(steps[4].time_cycle_manual, "no minutes, no cost")
        # the job already on the floor
        self.assertEqual(self.Loader.add_open_model_preparation(productions=job), 1)
        self.assertFalse(self.Loader.add_open_model_preparation(productions=job))
        made = job.workorder_ids.sorted(lambda w: (w.sequence, w.id))
        self.assertEqual(made.mapped('workcenter_id.name'), steps.mapped('workcenter_id.name'))
        self.assertEqual(made[5].blocked_by_workorder_ids, made[4], "Final Check waits for it")

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

    # ----------------------- Wax Up's model preparation as Cutting, Scooping, Blocking
    def _wax_bom(self, minutes=8.0):
        ref = lambda x: self.env.ref('lab_masters.%s' % x)      # noqa: E731
        return self.env['mrp.bom'].create({
            'product_tmpl_id': self.env['product.template'].create({'name': 'ZZ Wax Crown'}).id,
            'operation_ids': [
                (0, 0, {'name': 'Wax Up/Model Preparation', 'workcenter_id': ref('wc_model_preparation').id,
                        'sequence': 10, 'time_mode': 'manual', 'time_cycle_manual': minutes}),
                (0, 0, {'name': 'Spruing', 'workcenter_id': ref('wc_spruing').id, 'sequence': 20,
                        'time_mode': 'manual', 'time_cycle_manual': 1.0}),
                (0, 0, {'name': 'Investing', 'workcenter_id': ref('wc_investing').id, 'sequence': 30}),
                (0, 0, {'name': 'Metal/Casting', 'workcenter_id': ref('wc_casting').id, 'sequence': 40}),
                (0, 0, {'name': 'Metal/Trimming', 'workcenter_id': ref('wc_metal_trimming').id, 'sequence': 50}),
            ]})

    def test_model_preparation_is_three_benches(self):
        """(client, 2026-11-27: "In Waxup split Wax Up/Model Preparation to Cutting,
        Scooping, Blocking")"""
        cutting = self.env.ref('lab_masters.wc_model_preparation')
        scooping = self.env.ref('lab_masters.wc_wax_scooping')
        blocking = self.env.ref('lab_masters.wc_wax_blocking')
        self.assertEqual((cutting.name, scooping.name, blocking.name), ('Cutting', 'Scooping', 'Blocking'))
        self.assertEqual(set((scooping | blocking).mapped('costs_hour')), {cutting.costs_hour})
        self.assertEqual((scooping | blocking).department_id, cutting.department_id)

    def test_the_metal_benches_around_the_casting(self):
        """(client, 2026-11-27: "Add two workcentres before and after Metal/Casting")"""
        casting = self.env.ref('lab_masters.wc_casting')
        sintering = self.env.ref('lab_masters.wc_metal_sintering')
        printing = self.env.ref('lab_masters.wc_metal_printing')
        self.assertEqual((sintering.name, casting.name, printing.name),
                         ('Metal/Sintering', 'Metal/Casting', 'Metal/Printing'))
        self.assertEqual((sintering | printing).department_id, casting.department_id)
        self.assertNotEqual(sintering, self.env.ref('lab_masters.wc_sintering'), "CAD/CAM keeps its own")

    def test_the_routes_split_and_grow_only_once(self):
        bom = self._wax_bom(8.0)
        before = self.Loader._floor_labour(bom)
        self.Loader._floor_split_model_preparation()
        self.Loader._floor_add_metal_steps()
        self.assertFalse(self.Loader._floor_split_model_preparation(), "a second run finds nothing")
        self.assertFalse(self.Loader._floor_add_metal_steps(), "a second run finds nothing")
        steps = bom.operation_ids.sorted(lambda o: (o.sequence, o.id))
        self.assertEqual(steps.mapped('workcenter_id.name'), [
            'Cutting', 'Scooping', 'Blocking', 'Spruing', 'Investing',
            'Metal/Sintering', 'Metal/Casting', 'Metal/Printing', 'Metal/Trimming'])
        self.assertEqual(steps[:3].mapped('time_cycle_manual'), [6.0, 1.0, 1.0],
                         "divided as the costed crown's old stages say (8 = 6 + 1 + 1)")
        self.assertFalse(sum(steps.filtered(lambda o: o.workcenter_id.name in (
            'Metal/Sintering', 'Metal/Printing')).mapped('time_cycle_manual')), "no minutes, no cost")
        self.assertAlmostEqual(self.Loader._floor_labour(bom), before, places=2)

    def test_the_costed_crown_has_the_new_steps(self):
        steps = self.env.ref('lab_masters.bom_metal_ceramic').operation_ids
        minutes = {s.workcenter_id.name: s.time_cycle_manual for s in steps}
        self.assertEqual((minutes['Cutting'], minutes['Scooping'], minutes['Blocking']), (6.0, 1.0, 1.0))
        self.assertIn('Metal/Sintering', minutes)
        self.assertIn('Metal/Printing', minutes)

    def test_a_job_on_the_floor_gets_the_new_steps(self):
        bom = self._wax_bom(8.0)
        job = self.env['mrp.production'].create({
            'product_id': bom.product_tmpl_id.product_variant_id.id,
            'bom_id': bom.id, 'product_qty': 1})
        job.action_confirm()
        self.Loader._floor_split_model_preparation()
        self.Loader._floor_add_metal_steps()
        self.assertFalse(self.Loader.split_open_model_preparation(productions=job))
        self.assertFalse(self.Loader.add_open_metal_steps(productions=job))
        self.assertFalse(self.Loader.split_open_model_preparation(productions=job), "and only once")
        self.assertFalse(self.Loader.add_open_metal_steps(productions=job), "and only once")
        steps = job.workorder_ids.sorted(lambda w: (w.sequence, w.id))
        self.assertEqual(steps.mapped('workcenter_id.name'), [
            'Cutting', 'Scooping', 'Blocking', 'Spruing', 'Investing',
            'Metal/Sintering', 'Metal/Casting', 'Metal/Printing', 'Metal/Trimming'])
        self.assertEqual(steps.mapped('name')[:3], ['Cutting', 'Scooping', 'Blocking'])
        self.assertAlmostEqual(sum(steps[:3].mapped('duration_expected')), 8.0, places=2)
        for later, earlier in zip(steps[1:], steps[:-1]):
            self.assertEqual(later.blocked_by_workorder_ids, earlier,
                             "%s waits for %s" % (later.name, earlier.name))
        self.assertEqual(steps[0].state, 'ready')
        self.assertEqual(set(steps[1:].mapped('state')), {'blocked'})
        self.assertFalse(job.is_outdated_bom, "it has every step of its bill of materials again")

    def test_a_job_waiting_at_the_casting_goes_to_sintering_first(self):
        bom = self._wax_bom(8.0)
        job = self.env['mrp.production'].create({
            'product_id': bom.product_tmpl_id.product_variant_id.id,
            'bom_id': bom.id, 'product_qty': 1})
        job.action_confirm()
        for step in job.workorder_ids.sorted(lambda w: (w.sequence, w.id)):
            if step.workcenter_id == self.env.ref('lab_masters.wc_casting'):
                break
            step.write({'state': 'done'})
        cast = job.workorder_ids.filtered(lambda w: w.workcenter_id == self.env.ref('lab_masters.wc_casting'))
        job.workorder_ids.invalidate_recordset(['state'])
        self.Loader._floor_add_metal_steps()
        self.assertFalse(self.Loader.add_open_metal_steps(productions=job))
        sintering = job.workorder_ids.filtered(lambda w: w.workcenter_id.name == 'Metal/Sintering')
        self.assertEqual(sintering.state, 'ready', "the previous steps are done")
        self.assertEqual(cast.blocked_by_workorder_ids, sintering)
        self.assertEqual(cast.state, 'blocked', "the casting now waits for the sintering")

    def test_a_started_step_is_left_as_it_is(self):
        bom = self._wax_bom(8.0)
        job = self.env['mrp.production'].create({
            'product_id': bom.product_tmpl_id.product_variant_id.id,
            'bom_id': bom.id, 'product_qty': 1})
        job.action_confirm()
        first = job.workorder_ids.sorted(lambda w: (w.sequence, w.id))[0]
        first.write({'state': 'progress'})
        self.Loader._floor_split_model_preparation()
        self.Loader.split_open_model_preparation(productions=job)
        self.assertNotIn('Scooping', job.workorder_ids.mapped('workcenter_id.name'),
                         "a model preparation begun as one is finished as one")

