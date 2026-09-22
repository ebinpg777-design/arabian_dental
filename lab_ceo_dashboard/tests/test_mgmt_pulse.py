# -*- coding: utf-8 -*-
"""The Business Pulse behind Sales & Cases / Field Force / Money.

The lab asked for widgets, not pivot views, on the Management menu; these pin
down the payload those widgets read. (client, 2026-08-27)
"""
from datetime import date, datetime, timedelta

from unittest.mock import patch

from odoo import fields

from ..models.mgmt_pulse import TOP_ROWS
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestMgmtPulse(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Pulse = cls.env['lab.mgmt.pulse']
        cls.today = fields.Date.context_today(cls.env['res.users'])

    def test_the_whole_page_comes_back_in_one_call(self):
        data = self.Pulse.get_pulse()
        for section in ('sales', 'field', 'money'):
            self.assertIn(section, data)
        self.assertTrue(data['currency_id'])

    def test_sales_trend_is_twelve_months_ending_now(self):
        trend = self.Pulse.get_pulse()['sales']['trend']
        self.assertEqual(len(trend), 12)

    def test_a_confirmed_order_lands_in_this_month(self):
        clinic = self.env['res.partner'].create({'name': 'Pulse Clinic'})
        product = self.env['product.product'].create(
            # Big enough to out-rank every real clinic this month: the panel is a
            # top-six, and this database is not empty.
            {'name': 'Pulse Appliance', 'list_price': 10_00_00_000.0})
        order = self.env['sale.order'].create({
            'partner_id': clinic.id,
            'order_line': [(0, 0, {'product_id': product.id,
                                   'product_uom_qty': 1})]})
        order.action_confirm()
        before = self.Pulse._sales(12)
        self.assertGreaterEqual(before['this_month']['orders'], 1)
        self.assertIn(clinic.id, [c['id'] for c in before['clinics']])
        self.assertIn(product.id, [a['id'] for a in before['appliances']])

    def test_a_draft_order_does_not_count(self):
        clinic = self.env['res.partner'].create({'name': 'Pulse Draft Clinic'})
        product = self.env['product.product'].create(
            {'name': 'Pulse Draft App', 'list_price': 10.0})
        base = self.Pulse._sales(12)['this_month']['orders']
        self.env['sale.order'].create({
            'partner_id': clinic.id,
            'order_line': [(0, 0, {'product_id': product.id,
                                   'product_uom_qty': 1})]})
        self.assertEqual(self.Pulse._sales(12)['this_month']['orders'], base)

    def test_field_weeks_are_eight_and_bucketed_on_monday(self):
        weeks = self.Pulse._field()['weeks']
        self.assertEqual(len(weeks), 8)

    def test_a_done_visit_is_folded_into_its_week(self):
        team = self.env['crm.team'].create({'name': 'Pulse Route'})
        ex = self.env['res.users'].create({
            'name': 'Pulse Exec', 'login': 'pulse_exec',
            'group_ids': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref('lab_fieldwork.group_fieldwork_executive').id])]})
        team.user_id = ex.id
        clinic = self.env['res.partner'].create(
            {'name': 'Pulse Visit Clinic', 'team_id': team.id})
        # Asserted as the DIFFERENCE this visit makes. Summing the whole section
        # and expecting 1 only holds on an empty database; against the real one
        # it counts the lab's genuine week and fails at 8 != 1. (2026-08-31)
        before = self.Pulse._field()
        was_visits = sum(w['visits'] for w in before['weeks'])
        was_collected = sum(w['collected'] for w in before['weeks'])

        self.env['lab.visit'].create({
            'user_id': ex.id, 'partner_id': clinic.id,
            'date': self.today, 'state': 'done', 'collected': 150.0})
        field = self.Pulse._field()
        self.assertTrue(field['has_data'])
        self.assertEqual(sum(w['visits'] for w in field['weeks']),
                         was_visits + 1)
        self.assertAlmostEqual(sum(w['collected'] for w in field['weeks']),
                               was_collected + 150.0, 2)
        self.assertIn(ex.id, [p['id'] for p in field['people']])

    def test_an_empty_field_section_says_so_instead_of_charting_zeroes(self):
        if self.env['lab.visit'].search_count([('state', '=', 'done')]):
            self.skipTest('this database has real visit data')
        self.assertFalse(self.Pulse._field()['has_data'])

    def test_money_trend_starts_where_the_ledger_does(self):
        # Twelve months used to be drawn unconditionally; on this ledger the
        # first six were pre-migration nothing. The chart now opens with the
        # first posted invoice month. (client, 2026-09-02)
        money = self.Pulse._money(12)
        self.assertLessEqual(len(money['trend']), 12)
        self.assertGreaterEqual(len(money['trend']), 1)
        first = self.env['account.move'].sudo()._read_group(
            [('move_type', 'in', ('out_invoice', 'out_refund')),
             ('state', '=', 'posted')], [], ['invoice_date:min'])[0][0]
        if first and fields.Date.to_date(first).replace(day=1) >= \
                self.Pulse._month_starts(12)[0]:
            self.assertEqual(money['trend'][0]['month'],
                             fields.Date.to_string(
                                 fields.Date.to_date(first).replace(day=1)))
        # `overdue`/`overdue_pct` were dropped on purpose: this ledger sets
        # date_maturity to the invoice date, so "past due" read 100.0% every day.
        # Age bands say something instead. (client, 2026-08-28)
        for key in ('total', 'bands', 'stale', 'stale_pct', 'count'):
            self.assertIn(key, money['ar'])

    def test_debtors_rank_biggest_first_and_never_show_credits(self):
        debtors = self.Pulse._money(12)['debtors']
        opens = [d['open'] for d in debtors]
        self.assertEqual(opens, sorted(opens, reverse=True))
        self.assertTrue(all(v > 0 for v in opens))

    def test_the_hub_entries_land_on_the_widget_not_a_pivot(self):
        rows = {r['key']: r for r in self.env['lab.ceo.dashboard'].with_user(
            self.env.ref('base.user_admin')).get_launchers()}
        for key, section in (('sales', 'sales'), ('money', 'money')):
            action = self.env['ir.actions.client'].browse(rows[key]['action_id'])
            self.assertEqual(action.tag, 'lab_mgmt_pulse', key)
            self.assertEqual(action.params.get('section'), section, key)
        # Field Force lands on the Field Command switcher (2026-08-31), whose
        # LAST tab is this same pulse widget on its field section - covered by
        # TestFieldCommand. Still a widget, still never a pivot.
        field = self.env['ir.actions.client'].browse(rows['field']['action_id'])
        self.assertEqual(field.tag, 'lab_field_command')

    def test_the_pivots_survive_as_the_full_analysis_drill(self):
        # The widget's "Full analysis" button opens these by xml id; deleting
        # them would leave a button that throws.
        for xmlid in ('action_mgmt_sales', 'action_mgmt_field_force',
                      'action_mgmt_money'):
            self.assertTrue(self.env.ref('lab_ceo_dashboard.%s' % xmlid))

    def test_redo_works_sits_on_the_hub(self):
        rows = {r['key']: r for r in self.env['lab.ceo.dashboard'].with_user(
            self.env.ref('base.user_admin')).get_launchers()}
        self.assertEqual(rows['redo']['action_id'],
                         self.env.ref('lab_workcenter_scan.action_mrp_redo').id)

    def test_an_outsider_cannot_pull_the_numbers_over_rpc(self):
        from odoo.exceptions import AccessError
        nobody = self.env['res.users'].create({
            'name': 'Pulse Nobody', 'login': 'pulse_nobody',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        with self.assertRaises(AccessError):
            self.Pulse.with_user(nobody).get_pulse()
        with self.assertRaises(AccessError):
            self.env['lab.ceo.dashboard'].with_user(nobody).get_launchers()

    def test_management_can(self):
        boss = self.env['res.users'].create({
            'name': 'Pulse Boss', 'login': 'pulse_boss',
            'group_ids': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref('lab_ceo_dashboard.group_lab_executive').id])]})
        self.assertTrue(self.Pulse.with_user(boss).get_pulse())

    def test_a_month_starts_at_the_lab_midnight_not_utc(self):
        start = self.Pulse.with_context(tz='Asia/Kolkata')._utc_start(date(2026, 9, 1))
        self.assertEqual(start, datetime(2026, 8, 31, 18, 30))


@tagged('post_install', '-at_install')
class TestPulseHonesty(TransactionCase):
    """Every figure drills to exactly the slice it claims, and none of them lie.

    Three things were wrong: the ranked panels said "this month" and opened all
    time; the month card asked for a core search filter that does not exist, so it
    opened unfiltered too; and "overdue" read 100.0% every day because this ledger
    sets date_maturity to the invoice date. (client, 2026-08-28)
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Pulse = cls.env['lab.mgmt.pulse']

    def _pulse(self):
        return self.Pulse.sudo().get_pulse()

    # ------------------------------------------------------------ the slice
    def test_each_month_bar_carries_the_month_it_counts(self):
        trend = self._pulse()['sales']['trend']
        self.assertEqual(len(trend), 12)
        for row in trend:
            self.assertRegex(row['month'], r'^\d{4}-\d{2}-01$')
        self.assertEqual(trend[-1]['month'],
                         fields.Date.to_string(
                             fields.Date.context_today(self.env.user).replace(day=1)))

    def test_the_ranked_panels_say_which_month_they_are_about(self):
        sales = self._pulse()['sales']
        self.assertEqual(sales['month_from'],
                         fields.Date.to_string(
                             fields.Date.context_today(self.env.user).replace(day=1)))

    def test_the_billed_trend_carries_its_months_too(self):
        for row in self._pulse()['money']['trend']:
            self.assertRegex(row['month'], r'^\d{4}-\d{2}-01$')

    # ------------------------------------------------------------ the ageing
    def test_the_open_receivable_is_split_by_age_not_by_a_dead_due_date(self):
        ar = self._pulse()['money']['ar']
        self.assertEqual([b['key'] for b in ar['bands']],
                         ['current', 'd30', 'd60', 'd90'])
        self.assertNotIn('overdue_pct', ar,
                         "a percentage that reads 100 every day is not a figure")

    def test_the_bands_add_up_to_the_total(self):
        ar = self._pulse()['money']['ar']
        self.assertAlmostEqual(sum(b['amount'] for b in ar['bands']),
                               ar['total'], places=0)
        self.assertAlmostEqual(sum(b['pct'] for b in ar['bands']), 100.0, places=0)

    def test_stale_is_everything_older_than_a_month(self):
        ar = self._pulse()['money']['ar']
        expected = sum(b['amount'] for b in ar['bands'] if b['key'] != 'current')
        self.assertAlmostEqual(ar['stale'], expected, places=0)
        self.assertLessEqual(ar['stale'], ar['total'])

    def test_every_band_carries_a_count_so_a_reader_can_judge_it(self):
        ar = self._pulse()['money']['ar']
        self.assertEqual(sum(b['count'] for b in ar['bands']), ar['count'])

    def test_an_empty_ledger_does_not_divide_by_zero(self):
        # Patched at the countback, which is where the bands come from since
        # 2026-08-31; patching the old residual view left the real ledger
        # showing through and the test asserting against live data.
        with patch.object(
                type(self.env['lab.collection.performance']), '_open_debits',
                return_value=[]):
            ar = self.Pulse.sudo()._ageing()
        self.assertEqual(ar['total'], 0.0)
        self.assertEqual(ar['stale_pct'], 0.0)
        self.assertEqual([b['pct'] for b in ar['bands']], [0.0, 0.0, 0.0, 0.0])

    # ------------------------------------------------------------ the payload
    def test_the_page_is_still_one_call(self):
        data = self._pulse()
        self.assertEqual(set(data),
                         {'currency_id', 'user_id', 'users',
                          'sales', 'field', 'money'})


@tagged('post_install', '-at_install')
class TestPulseBySalesperson(TransactionCase):
    """The same page, read for one salesperson. (client, 2026-08-28)"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Pulse = cls.env['lab.mgmt.pulse']
        cls.clinic = cls.env['res.partner'].create({
            'name': 'Filter Clinic', 'is_clinic': True})
        cls.product = cls.env['product.product'].create({
            'name': 'Filter Appliance', 'type': 'consu', 'list_price': 500.0})
        cls.mine = cls.env['res.users'].create({
            'name': 'Filter Mine', 'login': 'filter_mine',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])]})
        cls.theirs = cls.env['res.users'].create({
            'name': 'Filter Theirs', 'login': 'filter_theirs',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])]})

    def _order(self, user, qty=1):
        order = self.env['sale.order'].create({
            'partner_id': self.clinic.id, 'user_id': user.id,
            'order_line': [(0, 0, {'product_id': self.product.id,
                                   'product_uom_qty': qty})]})
        order.action_confirm()
        return order

    def _pulse(self, user_id=False):
        return self.Pulse.sudo().get_pulse(12, user_id)

    def test_the_page_says_whose_figures_it_is_showing(self):
        data = self._pulse()
        self.assertFalse(data['user_id'])
        self.assertEqual(self._pulse(self.mine.id)['user_id'], self.mine.id)

    def test_the_dropdown_lists_only_people_who_have_work(self):
        self._order(self.mine)
        names = {u['name'] for u in self._pulse()['users']}
        self.assertIn('Filter Mine', names)
        self.assertNotIn('Filter Theirs', names,
                         "a name with no orders is a name nobody wants to scroll past")

    def test_the_dropdown_is_ranked_by_who_is_busiest(self):
        rows = self._pulse()['users']
        counts = [u['orders'] for u in rows]
        self.assertEqual(counts, sorted(counts, reverse=True))

    def test_narrowing_to_one_person_narrows_the_cases(self):
        self._order(self.mine)
        self._order(self.theirs)
        everyone = self._pulse()['sales']['this_month']['orders']
        mine = self._pulse(self.mine.id)['sales']['this_month']['orders']
        theirs = self._pulse(self.theirs.id)['sales']['this_month']['orders']
        self.assertGreaterEqual(mine, 1)
        self.assertGreaterEqual(theirs, 1)
        self.assertLess(mine, everyone)
        self.assertLess(theirs, everyone)

    def test_the_ranked_panels_narrow_too(self):
        self._order(self.mine, qty=3)
        appliances = self._pulse(self.mine.id)['sales']['appliances']
        self.assertTrue(any(a['name'].startswith('Filter Appliance')
                            for a in appliances))
        theirs = self._pulse(self.theirs.id)['sales']['appliances']
        self.assertFalse(any(a['name'].startswith('Filter Appliance')
                             for a in theirs))

    def test_the_like_for_like_comparison_narrows_with_it(self):
        """A person's month must be compared with their OWN last month."""
        everyone = self._pulse()['sales']['prev_month']['orders']
        mine = self._pulse(self.mine.id)['sales']['prev_month']['orders']
        self.assertLessEqual(mine, everyone)

    def test_the_money_tab_narrows_by_the_invoice_salesperson(self):
        everyone = self._pulse()['money']
        mine = self._pulse(self.mine.id)['money']
        self.assertLessEqual(mine['ar']['total'], everyone['ar']['total'])
        self.assertLessEqual(len(mine['debtors']), TOP_ROWS)

    def test_the_ageing_bands_narrow_and_still_add_up(self):
        ar = self._pulse(self.mine.id)['money']['ar']
        self.assertAlmostEqual(sum(b['amount'] for b in ar['bands']),
                               ar['total'], places=0)

    def test_an_unknown_person_yields_an_empty_page_not_an_error(self):
        data = self._pulse(999999)
        self.assertEqual(data['sales']['this_month']['orders'], 0)
        self.assertEqual(data['money']['ar']['total'], 0.0)
        self.assertEqual([b['pct'] for b in data['money']['ar']['bands']],
                         [0.0, 0.0, 0.0, 0.0])

    def test_open_money_follows_the_clinic_salesperson_not_the_invoice_stamp(self):
        other = self.env['res.partner'].create({'name': 'Filter Other Clinic'})
        rows = [{'partner_id': pid, 'team_key': 0, 'move_id': 0,
                 'date': fields.Date.today(), 'open': 100.0, 'days': 40,
                 'billed': 100.0} for pid in (self.clinic.id, other.id)]
        Perf = self.env['lab.collection.performance']
        with patch.object(type(Perf), '_open_debits', return_value=rows), \
                patch.object(type(Perf), '_partner_user_map', return_value={
                    self.clinic.id: self.mine.id, other.id: self.theirs.id}):
            _all, mine = self.Pulse.sudo()._scoped_debits(
                Perf, self.env.company, self.mine.id)
        self.assertEqual([d['partner_id'] for d in mine], [self.clinic.id])

    def test_billed_follows_the_order_salesperson_not_the_invoice_stamp(self):
        product = self.env['product.product'].create({
            'name': 'Filter Billed Service', 'type': 'service',
            'list_price': 900.0, 'invoice_policy': 'order'})
        order = self.env['sale.order'].create({
            'partner_id': self.clinic.id, 'user_id': self.mine.id,
            'order_line': [(0, 0, {'product_id': product.id, 'product_uom_qty': 1})]})
        order.action_confirm()
        today = fields.Date.context_today(self.Pulse)
        start = today.replace(day=1)
        Pulse = self.Pulse.sudo()
        was_mine = Pulse._billed_window(start, today, self.mine.id)['amount']
        was_theirs = Pulse._billed_window(start, today, self.theirs.id)['amount']
        invoice = order._create_invoices()
        invoice.invoice_user_id = self.theirs      # the bulk-recompute stamp
        invoice.action_post()
        self.assertAlmostEqual(
            Pulse._billed_window(start, today, self.mine.id)['amount'],
            was_mine + invoice.amount_total_signed, 2)
        self.assertAlmostEqual(
            Pulse._billed_window(start, today, self.theirs.id)['amount'], was_theirs, 2)


@tagged('post_install', '-at_install')
class TestBoardsAgreeOnWhatIsOwed(TransactionCase):
    """One ledger, one answer, on every board that shows it.

    The client put the CEO hub and the Money pulse side by side with the
    Collections screen and got different money: 10.31 M of "overdue" against
    Collections' 6.26 M. Both read
    ``lab.outstanding.report.amount_residual``, which on this ledger is not
    what is owed — the view sees only invoices, receipts here are plain journal
    entries, and nothing is ever reconciled, so a paid invoice still claims its
    face value and anything past its date reads as overdue.

    These pin the fix where it can actually regress: not that the number is
    right today, but that the three boards keep answering with the SAME
    definition. (client, 2026-08-31)
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Perf = cls.env['lab.collection.performance']
        cls.clinic = cls.env['res.partner'].create({'name': 'Agree Clinic'})
        receivable = cls.env['account.account'].search(
            [('account_type', '=', 'asset_receivable'),
             ('company_ids', 'in', [cls.env.company.id])], limit=1)
        other = cls.env['account.account'].search(
            [('account_type', '=', 'income'),
             ('company_ids', 'in', [cls.env.company.id])], limit=1)
        journal = cls.env['account.journal'].search(
            [('type', '=', 'general'),
             ('company_id', '=', cls.env.company.id)], limit=1)
        if not (receivable and other and journal):
            cls.skip_ledger = True
            return
        cls.skip_ledger = False
        today = fields.Date.context_today(cls.env.user)

        def entry(days_ago, amount):
            """A receivable movement the way this lab really books them: a plain
            journal entry, never reconciled against anything."""
            debit, credit = max(amount, 0.0), max(-amount, 0.0)
            move = cls.env['account.move'].create({
                'move_type': 'entry', 'date': today - timedelta(days=days_ago),
                'journal_id': journal.id,
                'line_ids': [
                    (0, 0, {'account_id': receivable.id,
                            'partner_id': cls.clinic.id,
                            'debit': debit, 'credit': credit}),
                    (0, 0, {'account_id': other.id,
                            'partner_id': cls.clinic.id,
                            'debit': credit, 'credit': debit})]})
            move.action_post()
            return move

        entry(120, 5000.0)      # old, and still open  -> overdue
        entry(10, 3000.0)       # recent               -> current
        entry(5, -1000.0)       # a receipt, unreconciled

    def _figures(self):
        coll = self.Perf.dashboard_data({}, with_trend=False)['totals']
        ceo = self.env['lab.ceo.dashboard']._receivables()
        pulse = self.env['lab.mgmt.pulse'].sudo()._ageing()
        return coll, ceo, pulse

    def test_the_three_boards_report_the_same_open_and_overdue(self):
        if self.skip_ledger:
            self.skipTest('no chart of accounts on this database')
        coll, ceo, pulse = self._figures()
        self.assertAlmostEqual(ceo['total'], coll['open'], 2,
                               "the CEO hub and Collections disagree on open")
        self.assertAlmostEqual(ceo['overdue'], coll['overdue'], 2,
                               "the CEO hub and Collections disagree on overdue")
        self.assertAlmostEqual(pulse['total'], coll['open'], 2,
                               "the Money pulse and Collections disagree on open")
        self.assertAlmostEqual(pulse['stale'], coll['overdue'], 2,
                               "the Money pulse and Collections disagree on overdue")

    def test_what_is_owed_is_the_ledger_balance_not_the_residual(self):
        """The countback equals the clinics' own debit balance. `amount_residual`
        does not, which is the whole reason this bug existed."""
        if self.skip_ledger:
            self.skipTest('no chart of accounts on this database')
        self.env.cr.execute("""
            WITH bal AS (SELECT l.partner_id, SUM(l.debit - l.credit) b
                         FROM account_move_line l
                         JOIN account_account a ON a.id = l.account_id
                         WHERE l.parent_state = 'posted'
                           AND a.account_type = 'asset_receivable'
                           AND l.company_id = %s AND l.partner_id IS NOT NULL
                         GROUP BY 1)
            SELECT COALESCE(SUM(b), 0) FROM bal WHERE b > 0
        """, (self.env.company.id,))
        ledger = float(self.env.cr.fetchone()[0])
        self.assertAlmostEqual(
            self.env['lab.ceo.dashboard']._receivables()['total'], ledger, 2,
            "the board must report the money the ledger says is owed")

    def test_the_receipt_reduces_what_the_boards_report(self):
        """The behaviour residuals got wrong: an unreconciled receipt is still
        money that came in, and every board must count it."""
        if self.skip_ledger:
            self.skipTest('no chart of accounts on this database')
        mine = [d for d in self.Perf._open_debits(self.env.company)
                if d['partner_id'] == self.clinic.id]
        self.assertAlmostEqual(sum(d['open'] for d in mine), 7000.0, 2,
                               "5000 + 3000 billed, 1000 received")
        overdue = sum(d['open'] for d in mine if d['days'] > 30)
        self.assertAlmostEqual(overdue, 4000.0, 2,
                               "the receipt pays the oldest bill down first")


@tagged('post_install', '-at_install')
class TestMoneyTabDepth(TransactionCase):
    """The 2026-09-02 Money tab: periods, received, routes, and the honesty
    around a ledger that opens in April with a migration lump in it."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Pulse = cls.env['lab.mgmt.pulse']
        cls.Perf = cls.env['lab.collection.performance']

    def _money(self, period='month'):
        return self.Pulse.sudo()._money(12, False, period)

    def test_every_period_key_answers_and_echoes_itself(self):
        for key, _label in self.Pulse.MONEY_PERIODS:
            money = self._money(key)
            self.assertEqual(money['period']['key'], key)
            self.assertLessEqual(money['period']['from'], money['period']['to'])

    def test_fy_starts_in_april_and_offers_no_fake_comparison(self):
        money = self._money('fy')
        self.assertTrue(money['period']['from'].endswith('-04-01'))
        self.assertIsNone(money['prev']['billed'],
                          "nothing to compare an FY against on this ledger")
        self.assertIsNone(money['period']['delta_label'])

    def test_last_month_is_a_whole_calendar_month(self):
        money = self._money('last_month')
        self.assertTrue(money['period']['from'].endswith('-01'))
        today = fields.Date.context_today(self.env.user)
        self.assertEqual(
            fields.Date.to_date(money['period']['to']),
            today.replace(day=1) - timedelta(days=1))

    def test_the_ratio_is_received_over_billed_or_honestly_absent(self):
        money = self._money('fy')
        if money['billed']['amount'] > 0:
            self.assertAlmostEqual(
                money['ratio'],
                round(money['received']['amount']
                      / money['billed']['amount'] * 100, 1), places=1)
        else:
            self.assertIsNone(money['ratio'])

    def test_received_matches_the_collections_definition(self):
        money = self._money('fy')
        amount, count = self.Perf.sudo()._received_total(
            self.env.company,
            fields.Date.to_date(money['period']['from']),
            fields.Date.to_date(money['period']['to']))
        self.assertAlmostEqual(money['received']['amount'], round(amount, 2),
                               places=2)
        self.assertEqual(money['received']['count'], count)

    def test_the_monthly_received_series_conserves_the_window_total(self):
        money = self._money()
        trend = money['trend']
        first = fields.Date.to_date(trend[0]['month'])
        total, _count = self.Perf.sudo()._received_total(
            self.env.company, first, fields.Date.context_today(self.env.user))
        self.assertAlmostEqual(sum(m['received'] for m in trend),
                               round(total, 2), places=0,
                               msg="every rupee lands in exactly one month bar")

    def test_routes_carry_the_whole_open_book_between_them(self):
        money = self._money()
        self.assertAlmostEqual(sum(r['open'] for r in money['routes']),
                               money['ar']['total'], places=0)
        for row in money['routes']:
            self.assertLessEqual(row['overdue'], row['open'] + 0.01)

    def test_the_mix_shares_add_to_one_hundred_when_money_moved(self):
        mix = self._money('fy')['mix']
        if mix:
            self.assertAlmostEqual(sum(j['share'] for j in mix), 100.0,
                                   places=0)
            amounts = [j['amount'] for j in mix]
            self.assertEqual(amounts, sorted(amounts, reverse=True))
            # April's opening lump made Bank read 104.7% and Other -37.8%:
            # arrival channels are receipts, never negative. (2026-09-02)
            self.assertTrue(all(j['amount'] > 0 for j in mix))
            self.assertTrue(all(0 <= j['share'] <= 100 for j in mix))
            self.assertEqual(len({j['type'] for j in mix}), len(mix))

    def test_quiet_debtors_owe_and_have_been_silent(self):
        for row in self._money()['quiet']:
            self.assertGreater(row['open'], 0)
            if row['last_pay']:
                self.assertGreaterEqual(row['days_silent'], 60)
            else:
                self.assertIsNone(row['days_silent'])

    def test_the_ar_judgment_numbers_are_sane(self):
        ar = self._money()['ar']
        self.assertGreaterEqual(ar['oldest_days'], 0)
        self.assertLessEqual(ar['avg_age'], max(ar['oldest_days'], 1))
        self.assertLessEqual(ar['top5_pct'], 100.0)
        self.assertGreaterEqual(ar['clinics'], bool(ar['count']))

    def test_weekly_cash_is_eight_mondays(self):
        self.assertEqual(len(self._money()['weekly_cash']), 8)

    def test_the_opening_month_is_flagged_never_charted_as_cash(self):
        trend = self._money()['trend']
        flags = [m['opening'] for m in trend]
        if self.env['account.move'].sudo().search_count(
                [('move_type', 'in', ('out_invoice', 'out_refund')),
                 ('state', '=', 'posted')], limit=1):
            self.assertTrue(flags[0], "the first ledger month carries the flag")
        self.assertNotIn(True, flags[1:],
                         "only the opening month is an opening month")

    def test_a_pure_management_reader_can_walk_into_every_money_drill(self):
        # fw_adm hit "not allowed to access Open Receivable Item" on the open
        # receivable card: the ACL covered field executives and accountants,
        # not the management group the pulse itself serves. (2026-09-02)
        boss = self.env['res.users'].create({
            'name': 'Money Boss', 'login': 'money_boss',
            'group_ids': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref('lab_ceo_dashboard.group_lab_executive').id])]})
        Perf = self.Perf.with_user(boss)
        action = Perf.action_drill('open')
        self.assertEqual(action['res_model'], 'lab.collection.open.item')
        Item = self.env['lab.collection.open.item'].with_user(boss)
        Item.search(action['domain'], limit=1)      # raises without the ACL
        debtors = self.Pulse.sudo()._money(12)['debtors']
        if debtors:
            partner_action = Perf.action_open_items_for_partner(debtors[0]['id'])
            Item.search(partner_action['domain'], limit=1)
