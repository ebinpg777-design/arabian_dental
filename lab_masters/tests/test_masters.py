# -*- coding: utf-8 -*-
"""What the setup has to keep being true.

These are not tests of the data files' syntax - an install proves that. They
test the four things that could silently rot: that the tree hangs together, that
every bench can post to a cost centre, that a job raised from one of these bills
of materials reaches the right department, and that the costs the explorer
reports are the costs the bill of materials actually holds.
"""
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestMastersTree(TransactionCase):
    """The departments, their centres and their stores."""

    def test_every_department_has_a_cost_centre(self):
        """A department without one cannot answer for what it spends.

        This is the whole point of the setup: a purchase line, a job or a
        transfer that lands on a department with no centre posts its cost
        nowhere, and the cockpit's "uncosted" counter goes up without anyone
        knowing why.
        """
        departments = self.env['hr.department'].search([('code', '!=', False)])
        self.assertTrue(departments, "the department tree did not load")
        without = departments.filtered(lambda d: not d.cost_centre_id)
        self.assertFalse(
            without, "departments with no cost centre: %s" % without.mapped('code'))

    def test_the_tree_is_a_tree(self):
        """Five roots, and every other department under one of them."""
        roots = self.env['hr.department'].search(
            [('code', '!=', False), ('parent_id', '=', False)])
        self.assertEqual(
            sorted(roots.mapped('code')), ['ADM', 'COM', 'PRD', 'QLT', 'SCM'],
            "the five functions the lab is organised into")
        children = self.env['hr.department'].search(
            [('code', '!=', False), ('parent_id', '!=', False)])
        self.assertTrue(len(children) >= 15)
        for child in children:
            self.assertIn(child.parent_id, roots | children,
                          "%s hangs off nothing" % child.code)

    def test_existing_departments_were_adopted_not_duplicated(self):
        """The three the lab already had carry codes, and there is one of each.

        Nine employees are filed under them. A second "Administration" beside
        the first would split the staff across two cost centres, and nobody
        would notice until a payroll cost landed on the wrong one.
        """
        for name in ('Administration',):
            found = self.env['hr.department'].search([('name', '=', name)])
            self.assertEqual(len(found), 1, "%s exists twice" % name)
        admin = self.env.ref('lab_masters.dept_admin')
        self.assertEqual(admin.code, 'ADM')

    def test_production_departments_have_a_store(self):
        """Material is issued to a place, and the place says whose it is."""
        for xmlid in ('dept_wax_up', 'dept_cadcam', 'dept_metal',
                      'dept_ceramic', 'dept_acrylic', 'dept_ortho'):
            department = self.env.ref('lab_masters.%s' % xmlid)
            self.assertTrue(department.store_location_id,
                            "%s has no store" % department.code)
            self.assertEqual(department.store_location_id.department_id, department,
                             "%s's store points back somewhere else" % department.code)

    def test_cost_centres_are_in_the_cost_centre_plan(self):
        plan = self.env['account.analytic.plan']._lab_cost_centre_plan()
        self.assertTrue(plan.is_cost_centre_plan)
        departments = self.env['hr.department'].search([('code', '!=', False)])
        for department in departments:
            self.assertEqual(
                department.cost_centre_id.plan_id, plan,
                "%s's centre is in the wrong plan" % department.code)


@tagged('post_install', '-at_install')
class TestBenches(TransactionCase):
    """The work centres."""

    def test_every_bench_belongs_to_a_department(self):
        benches = self.env['mrp.workcenter'].search([])
        self.assertTrue(benches, "no benches loaded")
        orphans = benches.filtered(lambda b: not b.department_id)
        self.assertFalse(orphans, "benches with no department: %s" % orphans.mapped('code'))

    def test_every_bench_can_post_its_time(self):
        """A distribution, or the cost of the hour worked goes nowhere.

        `lab_cost_centre` fills this from an onchange, and an onchange never
        fires for a record a data file creates - so this is the test that would
        have caught fifty-seven benches arriving blank.
        """
        benches = self.env['mrp.workcenter'].search([])
        blank = benches.filtered(lambda b: not b.analytic_distribution)
        self.assertFalse(blank, "benches that cannot post: %s" % blank.mapped('code'))
        for bench in benches:
            self.assertIn(str(bench.cost_centre_id.id), bench.analytic_distribution,
                          "%s posts somewhere other than its own centre" % bench.code)

    def test_a_machine_bench_costs_no_wages(self):
        """Nobody stands at a furnace, and the lab books machines to overheads.

        Not decoration: an invented machine rate would double-count the
        depreciation their own costing sheet already carries.
        """
        sintering = self.env.ref('lab_masters.wc_sintering')
        self.assertEqual(sintering.costs_hour, 0.0)
        bench = self.env.ref('lab_masters.wc_first_build_up')
        self.assertAlmostEqual(bench.costs_hour, 105.77, places=2)

    def test_no_bench_carries_a_setup_time(self):
        """The cycle belongs to the operation, not the bench.

        Odoo adds `time_start` to every work order on top of the operation's own
        duration, so a per-unit minute count here would be counted twice.
        """
        benches = self.env['mrp.workcenter'].search([])
        with_setup = benches.filtered(lambda b: b.time_start)
        self.assertFalse(with_setup, "benches with a setup time: %s"
                         % with_setup.mapped('code'))


@tagged('post_install', '-at_install')
class TestBillsOfMaterials(TransactionCase):
    """The fifteen routes."""

    def test_the_costed_products_are_manufacturable(self):
        """A bill of materials cannot be written against a service.

        This is the change that lets a sale order raise a job at all.
        """
        boms = self.env['mrp.bom'].search([])
        self.assertEqual(len(boms), 15)
        for bom in boms:
            self.assertEqual(bom.product_tmpl_id.type, 'consu',
                             "%s is on a service" % bom.code)
            self.assertFalse(bom.product_tmpl_id.is_storable,
                             "%s holds finished crowns in stock" % bom.code)

    def test_every_bill_consumes_and_is_worked(self):
        for bom in self.env['mrp.bom'].search([]):
            self.assertTrue(bom.bom_line_ids, "%s consumes nothing" % bom.code)
            self.assertTrue(bom.operation_ids, "%s is worked nowhere" % bom.code)

    def test_every_component_has_a_rate(self):
        """A component at zero makes a case look free.

        The whole setup is here so somebody can ask what a crown costs; one
        blank rate and the answer is wrong by however much that material costs.
        """
        for bom in self.env['mrp.bom'].search([]):
            for line in bom.bom_line_ids:
                self.assertGreater(
                    line.product_id.standard_price, 0.0,
                    "%s consumes %s at no cost" % (bom.code, line.product_id.name))

    def test_the_zirconia_grades_differ_only_by_their_blank(self):
        """Six grades, one road. The price difference is the disc, and nothing else."""
        grades = {
            'ZR-EMR': 705.00, 'ZR-RUB': 528.57, 'ZR-DIA': 290.36,
            'ZR-PRM': 107.15, 'ZR-CLS': 125.00, 'ZR-BSC': 137.14,
        }
        for code, blank_cost in grades.items():
            bom = self.env['mrp.bom'].search([('code', '=', code)])
            self.assertEqual(len(bom), 1, "no bill of materials coded %s" % code)
            blanks = bom.bom_line_ids.filtered(
                lambda line: 'Zirconia Blank' in line.product_id.name)
            self.assertEqual(len(blanks), 1, "%s has %s blanks" % (code, len(blanks)))
            self.assertAlmostEqual(blanks.product_id.standard_price, blank_cost, places=2)

    def test_a_job_raised_from_a_bill_lands_on_a_department(self):
        """The point of all of it: a job knows whose cost it is.

        The manufacturing order takes the department of the bench its first
        operation runs at, so a case in Wax Up is Wax Up's cost until it moves.
        """
        bom = self.env['mrp.bom'].search([('code', '=', 'MC-BSC')])
        production = self.env['mrp.production'].create({
            'product_id': bom.product_tmpl_id.product_variant_id.id,
            'bom_id': bom.id,
            'product_qty': 1,
        })
        self.assertTrue(production.department_id,
                        "a job with a bill of materials has no department")
        self.assertTrue(production.cost_centre_id,
                        "a job with a department has no cost centre")
        expected = bom.operation_ids[0].workcenter_id.department_id
        self.assertEqual(production.department_id, expected)

    def test_a_work_order_belongs_to_its_own_bench(self):
        """A case routed through four departments is each of theirs in turn."""
        bom = self.env['mrp.bom'].search([('code', '=', 'MC-BSC')])
        production = self.env['mrp.production'].create({
            'product_id': bom.product_tmpl_id.product_variant_id.id,
            'bom_id': bom.id,
            'product_qty': 1,
        })
        production.action_confirm()
        self.assertTrue(production.workorder_ids, "confirming raised no work orders")
        departments = set()
        for workorder in production.workorder_ids:
            self.assertEqual(workorder.department_id,
                             workorder.workcenter_id.department_id,
                             "%s is filed under the wrong department" % workorder.name)
            departments.add(workorder.department_id.code)
        self.assertGreater(len(departments), 2,
                           "a metal ceramic crown passes through more than two "
                           "departments; this one did not")


@tagged('post_install', '-at_install')
class TestCaseCostExplorer(TransactionCase):
    """The screen."""

    def test_one_call_fills_the_screen(self):
        data = self.env['lab.case.cost'].get_routes()
        self.assertEqual(len(data['rows']), 15)
        self.assertEqual(data['totals']['routes'], 15)
        self.assertTrue(data['currency']['id'])

    def test_the_reported_cost_is_the_bill_of_materials_cost(self):
        """The screen may not do its own arithmetic.

        A board that recomputes a cost its own way is a board that disagrees
        with the bill of materials, and then nobody knows which to believe.
        """
        data = self.env['lab.case.cost'].get_routes()
        for row in data['rows']:
            bom = self.env['mrp.bom'].browse(row['bom_id'])
            material = sum(line.product_qty * line.product_id.standard_price
                           for line in bom.bom_line_ids)
            self.assertAlmostEqual(row['material'], material, places=2,
                                   msg="%s's material cost disagrees" % row['code'])
            self.assertAlmostEqual(row['works'], row['material'] + row['labour'],
                                   places=2)

    def test_the_bands_never_run_past_the_bar(self):
        """A route that costs more than it sells for still has to draw.

        Scaled to the price alone, a loss-making route would overflow its own
        container and the bar would read as a full margin.
        """
        data = self.env['lab.case.cost'].get_routes()
        for row in data['rows']:
            overhead = max(row['overhead'] or 0.0, 0.0)
            cost = row['works'] + overhead
            self.assertAlmostEqual(cost, row['full_cost'], places=2,
                                   msg="%s's bands and its full cost disagree"
                                       % row['code'])
            scale = max(row['price'], cost) or 1.0
            self.assertLessEqual(cost / scale, 1.0 + 1e-9,
                                 "%s would overflow its bar" % row['code'])

    def test_a_route_reports_the_departments_it_passes_through(self):
        data = self.env['lab.case.cost'].get_routes()
        metal_ceramic = next(r for r in data['rows'] if r['code'] == 'MC-BSC')
        names = {slot['name'] for slot in metal_ceramic['departments']}
        self.assertIn('Ceramic', names)
        self.assertIn('Metal', names)
        self.assertNotIn('Unassigned', names,
                         "a material or a bench has no department")

    def test_a_margin_is_taken_against_the_fuller_cost(self):
        """Overheads included wherever the lab has a figure for them.

        Against works cost alone a metal ceramic crown reads 64%; the lab's own
        sheet says 45%. A screen that flatters every product is worse than none.
        """
        data = self.env['lab.case.cost'].get_routes()
        metal_ceramic = next(r for r in data['rows'] if r['code'] == 'MC-BSC')
        self.assertTrue(metal_ceramic['costed_fully'])
        self.assertGreater(metal_ceramic['full_cost'], metal_ceramic['works'],
                           "the sheet's overhead was dropped")
        self.assertAlmostEqual(
            metal_ceramic['margin'],
            metal_ceramic['price'] - metal_ceramic['full_cost'], places=2)
        # And where there is no sheet figure, the row says so rather than
        # sitting in the same column pretending to be comparable.
        veneer = next(r for r in data['rows'] if r['code'] == 'VN-01')
        self.assertFalse(veneer['costed_fully'])
        self.assertAlmostEqual(veneer['full_cost'], veneer['works'], places=2)

    def test_no_action_asks_the_browser_to_resolve_an_xmlid(self):
        """A domain is evaluated in the browser, and py.js has no `ref`.

        Written as a plain field, `[('categ_id', 'child_of', ref('...'))]` is
        stored as that text and handed to the client, which cannot evaluate it:
        the screen opens on "Oops!" and says nothing about why. `eval` on the
        field resolves the id here instead, and what reaches the browser is a
        number.
        """
        actions = self.env['ir.actions.act_window'].search([])
        ours = actions.filtered(
            lambda a: self.env['ir.model.data'].search_count([
                ('module', 'in', ('lab_masters', 'lab_cost_centre')),
                ('model', '=', 'ir.actions.act_window'),
                ('res_id', '=', a.id)]))
        self.assertTrue(ours, "no window actions of ours to check")
        for action in ours:
            for part in (action.domain or '', action.context or ''):
                self.assertNotIn('ref(', str(part),
                                 "%s asks the browser to resolve an xmlid"
                                 % action.name)

    def test_the_screen_walks_each_category_once(self):
        """Not once per component line.

        Fifteen routes share seven categories between a hundred and fourteen
        lines, and every line was climbing the category tree again. It is
        invisible at fifteen routes and it is the whole cost of the screen once
        the other four hundred products are costed.
        """
        walks = []
        Category = type(self.env['product.category'])
        original = Category._lab_department

        def counted(category):
            walks.append(category.id)
            return original(category)

        Category._lab_department = counted
        try:
            data = self.env['lab.case.cost'].get_routes()
        finally:
            Category._lab_department = original

        lines = sum(len(row['materials']) for row in data['rows'])
        self.assertGreater(lines, 50, "too few lines for this to prove anything")
        self.assertLessEqual(
            len(walks), len(set(walks)),
            "a category was walked more than once")
        self.assertLess(len(walks), lines,
                        "still walking the tree once per component line")

    def test_the_uncosted_count_is_honest(self):
        """Fifteen of sixteen hundred products are costed, and the screen says so."""
        data = self.env['lab.case.cost'].get_routes()
        self.assertGreater(data['totals']['uncosted'], 0,
                           "the lab's catalogue is not fully costed and the "
                           "screen should not pretend it is")
