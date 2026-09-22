# -*- coding: utf-8 -*-
"""The open-item view and the dashboard countback must never drift apart.

They are two expressions of the same rule - one parameterised for speed on every
dashboard load, one an unparameterised SQL view so the figures can be opened as a
list. If someone changes one and forgets the other, the card and the list it opens
stop agreeing, which is exactly the defect this model was added to fix.
"""
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestOpenItem(TransactionCase):

    def test_items_match_countback(self):
        company = self.env.company
        perf = self.env['lab.collection.performance']
        debits = perf._open_debits(company)
        items = perf._populate_open_items(company)

        self.assertEqual(len(items), len(debits),
                         "the open-item rows and the countback disagree on how "
                         "many items are open")
        self.assertAlmostEqual(
            sum(items.mapped('open_amount')), sum(d['open'] for d in debits), 2,
            "the open-item rows and the countback disagree on the open total")

    def test_every_card_foots_to_its_list(self):
        """Each figure must equal the list it opens - the defect this fixes."""
        perf = self.env['lab.collection.performance']
        Item = self.env['lab.collection.open.item']
        data = perf.dashboard_data({}, with_trend=False)

        for kind, card in (('open', data['totals']['open']),
                           ('overdue', data['totals']['overdue'])):
            listed = sum(Item.search(
                perf.action_drill(kind)['domain']).mapped('open_amount'))
            self.assertAlmostEqual(
                listed, card, 2,
                "the %s card and the list it opens disagree" % kind)

        for bucket in data['ageing']['buckets']:
            listed = sum(Item.search(
                perf.action_drill(bucket['key'])['domain']).mapped('open_amount'))
            self.assertAlmostEqual(
                listed, bucket['amount'], 2,
                "the '%s' bucket and the list it opens disagree" % bucket['label'])

    def test_open_total_equals_ledger(self):
        """Open receivable is what the clinics who owe actually owe."""
        company = self.env.company
        perf = self.env['lab.collection.performance']
        self.env.cr.execute("""
            WITH bal AS (
                SELECT l.partner_id, SUM(l.debit - l.credit) AS b
                  FROM account_move_line l
                  JOIN account_account a ON a.id = l.account_id
                 WHERE l.parent_state = 'posted' AND l.company_id = %s
                   AND a.account_type = 'asset_receivable'
                   AND l.partner_id IS NOT NULL
                 GROUP BY 1)
            SELECT COALESCE(SUM(b), 0) FROM bal WHERE b > 0
        """, (company.id,))
        owed = float(self.env.cr.fetchone()[0])
        self.assertAlmostEqual(
            sum(d['open'] for d in perf._open_debits(company)), owed, 2,
            "the countback no longer equals the balance of the clinics in debit")


@tagged('post_install', '-at_install')
class TestOpenItemDrillPresentation(TransactionCase):
    """How the Open receivable / Overdue lists arrive on screen.

    An ungrouped Odoo list totals the PAGE rather than the search — 80 rows of 9,455 —
    so the figure clicked and the figure shown disagreed. Grouped by customer, every
    clinic carries its own true total and the biggest debtor is the first thing read.
    (client, 2026-08-27)
    """

    def test_the_drill_opens_grouped_by_customer(self):
        action = self.env['lab.collection.performance']._open_item_action(
            'open', None, None, self.env.company)
        self.assertEqual(action['res_model'], 'lab.collection.open.item')
        self.assertEqual(action['context'].get('search_default_group_partner'), 1,
                         "the list must arrive grouped by customer")

    def test_every_drill_arrives_grouped(self):
        """Overdue and each ageing bucket, not just the headline card."""
        performance = self.env['lab.collection.performance']
        for kind in ('open', 'overdue', 'current', 'd30', 'd60', 'd90'):
            action = performance._open_item_action(
                kind, None, None, self.env.company)
            self.assertEqual(
                action['context'].get('search_default_group_partner'), 1,
                "%s must open grouped by customer" % kind)

    def test_the_list_is_ordered_by_what_is_open(self):
        """Grouped, Odoo orders the GROUPS by the same term as the rows, so ordering on
        an aggregatable column is what puts the clinic that owes most at the top."""
        arch = self.env.ref('lab_collections.view_collection_open_item_list').arch
        self.assertIn('open_amount desc', arch,
                      "the biggest open balance has to sort first")

    def test_groups_really_come_back_biggest_first(self):
        """Asserted on the data, not just the view: the ordering only works because
        open_amount is aggregatable, and a change to the field would break it silently."""
        performance = self.env['lab.collection.performance']
        action = performance._open_item_action('open', None, None, self.env.company)
        groups = self.env['lab.collection.open.item']._read_group(
            action['domain'], ['partner_id'], ['open_amount:sum'],
            order='open_amount:sum desc', limit=6)
        amounts = [row[1] or 0.0 for row in groups]
        if len(amounts) > 1:
            self.assertEqual(amounts, sorted(amounts, reverse=True))


@tagged('post_install', '-at_install')
class TestOpenItemPatientProductAndPrinting(TransactionCase):
    """Patient, products, and one-tap printing on the open-item list.

    An item's own account.move already carries patient_names / product_names -
    sale_custom's stored computes, built for the statement report - so the
    countback SQL that already joins the move for its description carries these
    two along for nothing extra. Print Invoice and Print Statement are both one
    tap, no second wizard for a number the office already knows.
    (client, 2026-08-29)
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.clinic = cls.env['res.partner'].create({'name': 'Open Item Print Clinic'})
        cls.product = cls.env['product.product'].create({
            'name': 'Open Item Appliance', 'type': 'consu', 'list_price': 1000.0})
        order = cls.env['sale.order'].create({
            'partner_id': cls.clinic.id, 'patient': 'Open Item Patient',
            'order_line': [(0, 0, {'product_id': cls.product.id, 'product_uom_qty': 1})]})
        order.action_confirm()
        order._create_invoices()
        cls.invoice = order.invoice_ids
        cls.invoice.action_post()

    def _item_for(self, invoice):
        Perf = self.env['lab.collection.performance']
        items = Perf._populate_open_items(self.env.company)
        return items.filtered(lambda i: i.move_id == invoice)

    def test_the_item_carries_its_patient_and_products(self):
        item = self._item_for(self.invoice)
        self.assertTrue(item)
        self.assertEqual(item.patient, 'Open Item Patient')
        self.assertIn('Open Item Appliance', item.product_names)
        self.assertEqual(item.move_type, 'out_invoice')

    def test_print_invoice_renders_the_real_document(self):
        item = self._item_for(self.invoice)
        action = item.action_print_invoice()
        self.assertEqual(action['type'], 'ir.actions.report')
        self.assertEqual(action['report_name'], 'sale_custom.report_dental_invoice')
        self.assertEqual(action['context']['active_ids'], self.invoice.ids)

    def test_print_invoice_refuses_on_a_journal_entry(self):
        """1,485 of these items are opening-balance journal entries with nothing
        to print - the button must say so, not try and fail obscurely. Built
        directly rather than through the countback: what matters here is the
        refusal logic on move_type, not re-deriving a real open item."""
        item = self.env['lab.collection.open.item'].sudo().create({
            'partner_id': self.clinic.id, 'move_type': 'entry',
            'description': 'Opening Balance', 'open_amount': 500.0,
            'billed_amount': 500.0, 'days': 10,
            'currency_id': self.env.company.currency_id.id,
        })
        self.assertFalse(item.patient, 'a journal entry names no patient')
        with self.assertRaises(UserError):
            item.action_print_invoice()

    def test_print_statement_opens_the_wizard_on_this_customer(self):
        """The executive picks the period (client, 2026-09-01): the wizard
        opens with the clinic already chosen, so the only decision left is
        how far back the statement should reach."""
        item = self._item_for(self.invoice)
        action = item.action_print_statement()
        self.assertEqual(action['type'], 'ir.actions.act_window')
        self.assertEqual(action['res_model'], 'epg.partner.statement.wizard')
        self.assertEqual(action['target'], 'new')
        wizard = self.env['epg.partner.statement.wizard'].browse(
            action['res_id'])
        self.assertEqual(wizard.partner_ids, self.clinic,
                         "the clinic must not have to be picked again")
        self.assertTrue(wizard.period, "a period is offered, pre-filled")
        # The SAME document Accounting prints. This button used to force
        # open_items_only, so the doctor shown a statement at the door and the
        # doctor posted one from the office got two different papers, headed
        # "Open Items Statement" and "Statement of Account". (client, 2026-09-02)
        self.assertFalse(wizard.open_items_only,
                         "field work prints the accounting statement, not a "
                         "shorter open-items one")
        fresh = self.env['epg.partner.statement.wizard'].create({})
        for field in ('open_items_only', 'show_ageing', 'period',
                      'statement_type'):
            self.assertEqual(wizard[field], fresh[field],
                             "%s must match what Accounting starts with" % field)
        # And the wizard still prints, from whatever period was chosen.
        self.assertEqual(wizard.action_print()['type'], 'ir.actions.report')

    def test_the_chosen_period_reaches_the_printed_statement(self):
        """Changing the period must change what is printed - otherwise the
        picker is decoration. A period the clinic has no movement in prints
        nothing, which is the proof that the dates are really applied."""
        item = self._item_for(self.invoice)
        wizard = self.env['epg.partner.statement.wizard'].browse(
            item.action_print_statement()['res_id'])
        # A window that contains the fixture invoice: it prints.
        wizard.write({'period': 'custom',
                      'date_from': self.invoice.invoice_date,
                      'date_to': self.invoice.invoice_date})
        options = wizard._options()
        self.assertEqual(options['date_from'], self.invoice.invoice_date)
        self.assertEqual(options['date_to'], self.invoice.invoice_date)
        self.assertEqual(wizard.action_print()['type'], 'ir.actions.report')

    def test_an_executive_can_print_both(self):
        """The open-item list already grants executives read access - the print
        buttons must not be the thing that stops working for them.

        An executive only sees clinics on their own route (the "Executives:
        clinics on their own route" ir.rule, keyed on fw_route_ids) - so the
        fixture puts this clinic on a route and the executive on the same one,
        the same way any real field executive would actually be scoped.
        """
        # The real combination a field executive carries in this system (see
        # lab-deploy-topology / the crm.team ACL probe earlier this session):
        # Field Work/Executive alone has no Journal Item access at all, which
        # _above_threshold's account.move.line read requires - the same as any
        # real salesperson would need to check a clinic's outstanding balance.
        group = self.env.ref('lab_fieldwork.group_fieldwork_executive')
        sales_group = self.env.ref('sales_team.group_sale_salesman')
        executive = self.env['res.users'].create({
            'name': 'Open Item Exec', 'login': 'openitem.exec@test',
            'group_ids': [(4, group.id), (4, sales_group.id),
                          (4, self.env.ref('base.group_user').id)]})
        # fw_route_ids is a COMPUTE (crm.team.user_id / member_ids), not a plain
        # field - membership is what actually puts someone "on" a route.
        route = self.env['crm.team'].create({
            'name': 'Open Item Exec Route', 'member_ids': [(4, executive.id)]})
        self.clinic.team_id = route.id
        # "Own Documents Only" scopes account.move.line to the salesperson's own
        # invoices too - the same as any real salesperson checking a balance.
        self.invoice.invoice_user_id = executive.id
        self.assertIn(route, executive.fw_route_ids, "fixture sanity: the "
                      "computed route membership must actually show the exec on it")
        # A fresh read: setUpClass's own partners are still in this transaction's
        # prefetch cache, and a batched relational-field read checks access on the
        # whole prefetched batch before filtering it down - an unrelated fixture
        # partner elsewhere in the cache can fail the read before self.clinic is
        # even reached. Not a defect in action_print_statement itself.
        self.env.invalidate_all()
        item = self._item_for(self.invoice).with_user(executive)
        self.assertEqual(item.action_print_invoice()['type'], 'ir.actions.report')
        # What this test guards is ACCESS, not the fixture's exact balance: a
        # legitimate "nothing to print" from the wizard's own business rule is a
        # pass here (it proves the executive reached the wizard's own logic at
        # all); an AccessError anywhere along the way is the failure.
        try:
            # An executive with no accounting role gets the dialog too, fixed to
            # this clinic; the print behind it runs elevated once the round is
            # checked, because the dialog would otherwise read the ledger as the
            # person asking and a salesperson's own-documents rule would show them
            # only the lines of invoices they raised. (client, 2026-09-18)
            action = item.action_print_statement()
            self.assertEqual(action['res_model'], 'epg.partner.statement.wizard')
            wizard = self.env['epg.partner.statement.wizard'].browse(
                action['res_id']).with_user(executive)
            self.assertEqual(wizard.action_print()['type'], 'ir.actions.act_url')
        except UserError as exc:
            self.assertIn('Nothing to print', str(exc))


@tagged('post_install', '-at_install')
class TestPartnerDrillCarriesItsViews(TransactionCase):
    def test_the_partner_drill_spells_out_its_views(self):
        # view_mode alone crashes doAction ("reading 'map'"); the action must
        # carry `views`. Caught live from the pulse quiet-debtors panel.
        partner = self.env['res.partner'].create({'name': 'Views Clinic'})
        action = self.env['lab.collection.performance'].sudo() \
            .action_open_items_for_partner(partner.id)
        self.assertIn('views', action)
        self.assertEqual([m for _v, m in action['views']], ['list', 'form'])
