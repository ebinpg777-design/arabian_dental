# -*- coding: utf-8 -*-
"""What the department must be right about.

Two things decide whether this module is worth having: that the department is
derived correctly without anybody typing it, and that it never overwrites
somebody who typed it anyway.
"""
from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestCostCentre(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.plan = cls.env['account.analytic.plan']._lab_cost_centre_plan()
        cls.ceramic = cls.env['hr.department'].create({
            'name': 'Ceramic (test)', 'code': 'T-CR', 'dept_type': 'production'})
        cls.acrylic = cls.env['hr.department'].create({
            'name': 'Acrylic (test)', 'code': 'T-AC', 'dept_type': 'production'})
        cls.ceramic.action_create_cost_centre()
        cls.acrylic.action_create_cost_centre()

        cls.category = cls.env['product.category'].create({
            'name': 'Ceramic work (test)', 'department_id': cls.ceramic.id})
        cls.product = cls.env['product.product'].create({
            'name': 'Test Crown', 'type': 'consu', 'is_storable': True,
            'categ_id': cls.category.id, 'list_price': 2000.0})
        cls.clinic = cls.env['res.partner'].create({'name': 'Test Clinic'})

    def _order(self):
        return self.env['sale.order'].create({
            'partner_id': self.clinic.id,
            'order_line': [(0, 0, {'product_id': self.product.id,
                                   'product_uom_qty': 1})],
        })

    # --------------------------------------------------------- the cost centre
    def test_a_department_gets_a_cost_centre_named_after_it(self):
        self.assertTrue(self.ceramic.cost_centre_id)
        self.assertEqual(self.ceramic.cost_centre_id.code, 'T-CR')
        self.assertEqual(self.ceramic.cost_centre_id.plan_id, self.plan)

    def test_running_it_twice_does_not_make_a_second_one(self):
        before = self.ceramic.cost_centre_id
        self.ceramic.action_create_cost_centre()
        self.assertEqual(self.ceramic.cost_centre_id, before)

    def test_the_plan_is_found_not_guessed(self):
        """Every path that writes a cost centre needs the plan, so it is found by
        its flag and created when missing rather than fetched by an xml id that
        a lab could have deleted."""
        self.assertTrue(self.plan.is_cost_centre_plan)
        self.assertEqual(
            self.env['account.analytic.plan']._lab_cost_centre_plan(), self.plan)

    # ------------------------------------------------------------ the sale side
    def test_a_sale_line_takes_the_department_of_what_it_sells(self):
        order = self._order()
        self.assertEqual(order.order_line.department_id, self.ceramic)
        self.assertEqual(order.department_id, self.ceramic,
                         "the header follows its lines")

    def test_a_sale_line_carries_the_cost_centre_into_its_analytic(self):
        order = self._order()
        centre = str(self.ceramic.cost_centre_id.id)
        self.assertEqual(order.order_line.analytic_distribution, {centre: 100.0})

    def test_a_department_chosen_by_hand_is_not_overwritten(self):
        """The direction that matters. A stored computed field with
        readonly=False recomputes when its dependencies change, which would undo
        a correction the next time anybody touched the line."""
        order = self._order()
        order.order_line.department_id = self.acrylic
        order.order_line.product_uom_qty = 3          # touches the line again
        self.assertEqual(order.order_line.department_id, self.acrylic)

    def test_an_analytic_distribution_set_by_hand_is_not_overwritten(self):
        order = self._order()
        mine = {str(self.acrylic.cost_centre_id.id): 100.0}
        order.order_line.analytic_distribution = mine
        order.order_line.write({'product_uom_qty': 2})
        self.assertEqual(order.order_line.analytic_distribution, mine,
                         "an analytic entry is an accounting statement, not a guess")

    # -------------------------------------------------------- the stock side
    def test_a_store_gives_its_department_to_what_moves_into_it(self):
        store = self.env['stock.location'].create({
            'name': 'Ceramic sub-store (test)',
            'usage': 'internal',
            'location_id': self.env.ref('stock.stock_location_stock').id,
            'department_id': self.ceramic.id,
        })
        picking = self.env['stock.picking'].create({
            'picking_type_id': self.env.ref('stock.picking_type_internal').id,
            'location_id': self.env.ref('stock.stock_location_stock').id,
            'location_dest_id': store.id,
        })
        self.assertEqual(picking.department_id, self.ceramic)

    # ---------------------------------------------------------- the floor side
    def test_a_job_belongs_to_the_bench_it_starts_at(self):
        bench = self.env['mrp.workcenter'].create({
            'name': 'Ceramic bench (test)', 'department_id': self.ceramic.id})
        production = self.env['mrp.production'].create({
            'product_id': self.product.id, 'product_qty': 1})
        workorder = self.env['mrp.workorder'].create({
            'name': 'Glazing', 'production_id': production.id,
            'workcenter_id': bench.id,
            'product_uom_id': self.product.uom_id.id,
        })
        self.assertEqual(workorder.department_id, self.ceramic,
                         "a work order's department is a fact, not a derivation")
        production.invalidate_recordset(['department_id'])
        self.assertEqual(production._derive_department(), self.ceramic)

    def test_a_bench_offers_its_department_s_cost_centre(self):
        bench = self.env['mrp.workcenter'].new({
            'name': 'Bench (test)', 'department_id': self.ceramic.id})
        bench._onchange_department_analytic()
        self.assertEqual(bench.analytic_distribution,
                         {str(self.ceramic.cost_centre_id.id): 100.0})

    # ------------------------------------------------------------- the cockpit
    def test_the_cockpit_answers_in_one_call(self):
        self._order().action_confirm()
        board = self.env['lab.cost.centre.board'].get_board('month')
        codes = {row['code']: row for row in board['rows']}
        self.assertIn('T-CR', codes)
        self.assertEqual(board['period'], 'month')
        self.assertIn('spend', codes['T-CR'])
        self.assertGreaterEqual(codes['T-CR']['orders'], 1,
                                "a confirmed order counts for its department")

    def test_the_cockpit_reports_what_a_centre_actually_spent(self):
        """Not just that the call returns - that the figure is the ledger's.

        The grouped read behind this cannot go through `auto_account_id`: Odoo 19
        computes that field from whichever plan the context names and refuses to
        turn it into SQL, so the read has to name the cost centre plan's own
        column. Asking only "does it answer" let that pass for a whole release.
        """
        column = self.plan._column_name()
        self.env['account.analytic.line'].create({
            'name': 'Ceramic powder (test)',
            'date': fields.Date.context_today(self.env.user),
            'amount': -1234.0,
            column: self.ceramic.cost_centre_id.id,
        })
        board = self.env['lab.cost.centre.board'].get_board('month')
        row = next(r for r in board['rows'] if r['code'] == 'T-CR')
        self.assertAlmostEqual(row['spend'], 1234.0, places=2)
        other = next(r for r in board['rows'] if r['code'] == 'T-AC')
        self.assertAlmostEqual(other['spend'], 0.0, places=2,
                               msg="one centre's spend landed on another")

    def test_the_cockpit_says_how_many_departments_have_no_cost_centre(self):
        """A department with no cost centre is invisible in the ledger, which is
        the one fault this screen has to report rather than hide."""
        self.env['hr.department'].create({'name': 'Unbanked (test)', 'code': 'T-XX'})
        board = self.env['lab.cost.centre.board'].get_board('month')
        self.assertGreaterEqual(board['totals']['uncosted'], 1)

    def test_every_action_the_board_hands_back_can_be_opened(self):
        """An action fetched by a board needs `views`, not just `view_mode`.

        Only `call_button` runs Odoo's `clean_action`, which expands the
        view_mode string. A board calls through `call_kw`, so the dict arrives
        as written, and the web client's `_preprocessAction` does
        `action.views.map(...)` on it - undefined, TypeError, and the click
        dies without opening anything or saying why.
        """
        actions = [
            self.env['lab.cost.centre.board'].open_items(self.ceramic.id, 'month'),
            self.ceramic.action_view_analytic_items(),
            self.ceramic.action_view_workcenters(),
        ]
        for action in actions:
            if action.get('type') != 'ir.actions.act_window':
                continue
            views = action.get('views')
            self.assertTrue(views, "%s has no views" % action.get('name'))
            for view in views:
                self.assertEqual(len(view), 2,
                                 "a view is (id, type): %r" % (view,))

    # ----------------------------------------------------------------- the menus
    def test_no_menu_opens_an_action_that_needs_a_record(self):
        """A menu click has no `active_id`, and an action that reads one dies.

        `analytic.account_analytic_line_action` is a drill-down FROM an analytic
        account: both its context and its domain say `active_id`. Hung off a
        menu it fails in the browser with "Name 'active_id' is not defined" and
        the screen never opens - server-side nothing is wrong, so only a test
        that reads the action the menu points at can catch it.
        """
        root = self.env.ref('lab_cost_centre.menu_cost_centre_root')
        menus = self.env['ir.ui.menu'].with_context(active_test=False).search(
            [('id', 'child_of', root.id), ('action', '!=', False)])
        self.assertGreaterEqual(len(menus), 4, "the menu tree did not load")
        for menu in menus:
            action = menu.action
            # A window action carries context and domain; a server action
            # carries neither, and its own code builds them at run time.
            parts = [action[fname] for fname in ('context', 'domain', 'code')
                     if fname in action._fields]
            for part in parts:
                self.assertNotIn('active_id', str(part or ''),
                                 "%s opens %s, which needs a record"
                                 % (menu.complete_name, action.display_name))

    def test_every_menu_action_names_its_views(self):
        """`view_mode` alone is not enough for an action the browser preprocesses."""
        root = self.env.ref('lab_cost_centre.menu_cost_centre_root')
        menus = self.env['ir.ui.menu'].with_context(active_test=False).search(
            [('id', 'child_of', root.id), ('action', '!=', False)])
        for menu in menus:
            action = menu.action
            if action._name != 'ir.actions.act_window':
                continue
            self.assertTrue(action.views,
                            "%s opens an action with no views" % menu.complete_name)

    # ------------------------------------------------------- tagging the ledger
    def test_a_cost_centre_never_lands_on_the_balance_sheet(self):
        """Profit and loss only.

        Nine thousand six hundred lines on the lab's ledger have a department
        and sit on "Inventories" or "GRN - Not billed". Tagging those posts the
        same material to a cost centre twice - once arriving in stock, once on
        the Cost of Goods Sold line when it is consumed - and a cockpit that
        double-counts its own material is worse than one that shows nothing.
        """
        Line = self.env['account.move.line']
        types = [leaf[2] for leaf in Line._lab_taggable_domain()
                 if leaf[0] == 'account_id.account_type']
        self.assertEqual(len(types), 1, "the domain no longer restricts account types")
        for banned in ('asset_current', 'asset_fixed', 'asset_receivable',
                       'liability_payable', 'liability_current', 'asset_cash',
                       'equity'):
            self.assertNotIn(banned, types[0],
                             "%s is a balance sheet account" % banned)
        for wanted in ('income', 'expense', 'expense_direct_cost'):
            self.assertIn(wanted, types[0])

    def test_the_back_fill_only_looks_at_untagged_posted_lines(self):
        """What makes it idempotent, and restartable if it dies half way.

        A line that has just been written drops out of the domain, so the next
        search returns the next untagged ones rather than the same batch.
        """
        domain = self.env['account.move.line']._lab_taggable_domain()
        self.assertIn(('analytic_distribution', '=', False), domain)
        self.assertIn(('parent_state', '=', 'posted'), domain)
        self.assertIn(('department_id', '!=', False), domain)

    def test_tagging_a_line_creates_the_analytic_item(self):
        """The whole point: a distribution on a posted line makes an item.

        Writing `analytic_distribution` fires `_inverse_analytic_distribution`,
        which is what creates them. It is allowed on a posted line because it
        moves no debit and no credit.
        """
        order = self._order()
        order.action_confirm()
        invoice = order._create_invoices()
        invoice.action_post()
        line = invoice.line_ids.filtered(
            lambda l: l.account_id.account_type == 'income')[:1]
        self.assertTrue(line, "the invoice posted no income line")
        self.assertEqual(line.parent_state, 'posted')

        centre = self.ceramic.cost_centre_id
        line.analytic_distribution = {str(centre.id): 100.0}
        self.env.flush_all()
        items = self.env['account.analytic.line'].search(
            [('move_line_id', '=', line.id)])
        self.assertEqual(len(items), 1, "no analytic item was created")

        # Once, not twice. `_inverse_analytic_distribution` unlinks what the
        # line had before recreating it, which is what lets the back-fill be
        # re-run without doubling the ledger. Counting analytic items globally
        # would not show this: posting the invoice already made one.
        line.analytic_distribution = {str(centre.id): 100.0}
        self.env.flush_all()
        self.assertEqual(
            self.env['account.analytic.line'].search_count(
                [('move_line_id', '=', line.id)]), 1,
            "writing the same distribution twice doubled the analytic items")

        items = self.env['account.analytic.line'].search(
            [('move_line_id', '=', line.id)])
        column = self.plan._column_name()
        self.assertEqual(items[column], centre,
                         "the item landed in another plan's column")
        # Revenue is positive and a cost negative, which is the split the
        # cockpit reads. An income line is a credit, so its balance is negative.
        self.assertAlmostEqual(items.amount, -line.balance, places=2)

    def test_a_period_nobody_shipped_falls_back_to_the_month(self):
        board = self.env['lab.cost.centre.board'].get_board('decade')
        self.assertEqual(board['period'], 'month')
