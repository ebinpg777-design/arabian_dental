# -*- coding: utf-8 -*-
"""The Bank Reconciliation Statement arithmetic, the rules around ticking lines, and
the assistants built on them."""
from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestBankReconciliation(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.journal = cls.env['account.journal'].create({
            'name': 'Rec Test Bank', 'type': 'bank', 'code': 'RTB9'})
        Account = cls.env['account.account']
        cls.bank = Account.create({'name': 'Rec Test Bank GL', 'code': 'RTBGL9',
                                   'account_type': 'asset_cash'})
        cls.income = Account.create({'name': 'Rec Test Income', 'code': 'RTINC9',
                                     'account_type': 'income'})
        cls.expense = Account.create({'name': 'Rec Test Charges', 'code': 'RTEXP9',
                                      'account_type': 'expense'})
        cls.general = cls.env['account.journal'].search(
            [('type', '=', 'general'), ('company_id', '=', cls.company.id)], limit=1)
        cls.day = fields.Date.context_today(cls.env['bank.reconciliation'])
        cls.partner = cls.env['res.partner'].create({'name': 'Rec Test Clinic'})
        cls.other_partner = cls.env['res.partner'].create({'name': 'Rec Test Other Clinic'})

    def _entry(self, amount, day=None, journal=None, label='bank side', partner=None):
        """A posted entry through the bank GL: >0 into the bank, <0 out of it."""
        other = self.income if amount > 0 else self.expense
        move = self.env['account.move'].create({
            'move_type': 'entry',
            'journal_id': (journal or self.journal).id,
            'date': day or self.day,
            'line_ids': [
                Command.create({'account_id': self.bank.id, 'name': label,
                                'partner_id': partner and partner.id,
                                'debit': max(amount, 0.0), 'credit': max(-amount, 0.0)}),
                Command.create({'account_id': other.id, 'name': 'other side',
                                'debit': max(-amount, 0.0), 'credit': max(amount, 0.0)}),
            ],
        })
        move.action_post()
        return move.line_ids.filtered(lambda l: l.account_id == self.bank)

    def _rec(self, **vals):
        return self.env['bank.reconciliation'].create(dict({
            'journal_id': self.journal.id, 'date': self.day,
            'charge_account_id': self.expense.id, 'interest_account_id': self.income.id,
        }, **vals))

    # ------------------------------------------------------------------ the account
    def test_the_account_is_the_one_the_entries_actually_use(self):
        """The journal's own default account is not where this lab's entries post."""
        self._entry(1000.0)
        rec = self._rec()
        self.assertEqual(rec.account_id, self.bank)
        self.assertNotEqual(rec.account_id, self.journal.default_account_id)

    # ------------------------------------------------------------------ the formula
    def test_the_statement_follows_the_brs_formula(self):
        in1, in2, out1 = self._entry(1000.0), self._entry(500.0), self._entry(-300.0)
        rec = self._rec()
        self.assertEqual(rec.book_balance, 1200.0)
        self.assertEqual(rec.deposits_not_credited, 1500.0, "nothing cleared yet")
        self.assertEqual(rec.payments_not_presented, 300.0)
        self.assertEqual(rec.adjusted_balance, 0.0, "books less uncleared = what the bank has")
        rec.set_cleared([in1.id, out1.id])
        self.assertEqual(rec.deposits_not_credited, 500.0)
        self.assertEqual(rec.payments_not_presented, 0.0)
        self.assertEqual(rec.adjusted_balance, 700.0)
        rec.bank_balance = 700.0
        self.assertEqual(rec.difference, 0.0)

    def test_a_line_cleared_after_the_statement_date_is_still_outstanding(self):
        """So an old statement reprints exactly as it stood."""
        line = self._entry(1000.0, day=self.day - timedelta(days=3))
        rec = self._rec(date=self.day - timedelta(days=1))
        self.env['bank.clearance'].create({'move_line_id': line.id,
                                           'cleared_date': self.day})
        rec.invalidate_recordset()
        self.assertEqual(rec.deposits_not_credited, 1000.0)

    def test_the_opening_balance_from_the_general_journal_is_in_the_books(self):
        self._entry(5000.0, journal=self.general)
        self._entry(1000.0)
        rec = self._rec()
        self.assertEqual(rec.book_balance, 6000.0)

    # ------------------------------------------------------------------ acting
    def test_ticking_all_outstanding_clears_each_on_its_own_date(self):
        first = self._entry(100.0, day=self.day - timedelta(days=2))
        self._entry(200.0)
        self._entry(-50.0)
        rec = self._rec()
        rec.clear_matching('deposits')
        self.assertEqual(rec.deposits_not_credited, 0.0)
        self.assertEqual(rec.payments_not_presented, 50.0, "the other side is untouched")
        clearance = self.env['bank.clearance'].search([('move_line_id', '=', first.id)])
        self.assertEqual(clearance.cleared_date, first.date)

    def test_ticking_up_to_a_date_leaves_later_entries_outstanding(self):
        self._entry(100.0, day=self.day - timedelta(days=10))
        self._entry(200.0, day=self.day - timedelta(days=1))
        rec = self._rec()
        rec.clear_matching('deposits', upto=fields.Date.to_string(self.day - timedelta(days=5)))
        self.assertEqual(rec.deposits_not_credited, 200.0)

    def test_a_bank_charge_is_posted_and_already_cleared(self):
        self._entry(1000.0)
        rec = self._rec()
        rec.post_bank_item('charge', 25.0, label='SMS charges')
        self.assertEqual(rec.book_balance, 975.0, "the charge reached the books")
        self.assertEqual(rec.payments_not_presented, 0.0, "and needs no ticking")

    def test_any_other_bank_item_goes_against_the_account_picked(self):
        self._entry(1000.0)
        rec = self._rec()
        with self.assertRaises(UserError, msg="no account picked"):
            rec.post_bank_item('other_out', 40.0)
        with self.assertRaises(UserError, msg="the bank cannot be its own other side"):
            rec.post_bank_item('other_out', 40.0, account_id=self.bank.id)
        rec.post_bank_item('other_out', 40.0, account_id=self.expense.id)
        self.assertEqual(rec.book_balance, 960.0)
        self.assertEqual(rec.payments_not_presented, 0.0)
        move = self.env['account.move.line'].search(
            [('account_id', '=', self.expense.id), ('name', '=', 'Paid from the bank')])
        self.assertEqual(move.debit, 40.0)

    def test_validating_needs_a_zero_difference_and_then_locks_the_lines(self):
        line = self._entry(1000.0)
        rec = self._rec()
        rec.set_cleared([line.id])
        with self.assertRaises(UserError):
            rec.action_validate()            # bank balance still 0 -> difference 1000
        rec.bank_balance = 1000.0
        rec.action_validate()
        self.assertEqual(rec.state, 'done')
        with self.assertRaises(UserError):
            self.env['bank.clearance'].search([('move_line_id', '=', line.id)]).unlink()

    def test_an_untouched_statement_is_not_ready_to_reconcile(self):
        """Nothing ticked and no bank balance 'balances' at zero, and means nothing."""
        self._entry(1000.0)
        rec = self._rec()
        figures = rec.get_screen()['figures']
        self.assertEqual((figures['balanced'], figures['ready']), (True, False))
        with self.assertRaises(UserError):
            rec.action_validate()

    def test_lines_from_another_account_cannot_be_cleared_here(self):
        bank_line = self._entry(1000.0)
        income_line = bank_line.move_id.line_ids - bank_line
        rec = self._rec()
        with self.assertRaises(UserError):
            rec.set_cleared(income_line.ids)
        later = self._entry(300.0, day=self.day + timedelta(days=5))
        with self.assertRaises(UserError, msg="booked after the statement date"):
            rec.set_cleared(later.ids)

    # ------------------------------------------------------------------ the screen
    def test_the_screen_carries_the_figures_and_both_sides(self):
        self._entry(1000.0)
        self._entry(-400.0)
        screen = self._rec().get_screen()
        self.assertEqual(screen['figures']['book'], 600.0)
        self.assertEqual([l['amount'] for l in screen['deposits']], [1000.0])
        self.assertEqual([l['amount'] for l in screen['withdrawals']], [400.0])
        self.assertEqual(screen['stats']['deposits']['count'], 1)
        self.assertEqual(screen['stats']['withdrawals']['count'], 1)

    def test_the_report_lists_every_outstanding_line(self):
        self._entry(1000.0)
        cleared = self._entry(250.0)
        rec = self._rec()
        rec.set_cleared([cleared.id])
        data = rec._brs_data()
        self.assertEqual([l['amount'] for l in data['deposits']], [1000.0])
        self.assertEqual(data['deposits_not_credited'], 1000.0)
        self.assertEqual(sum(b['deposits']['count'] for b in data['ageing']), 1)

    # ------------------------------------------------------------------ ageing
    def test_outstanding_entries_are_aged_and_old_ones_flagged_stale(self):
        self._entry(50.0, day=self.day - timedelta(days=40))
        self._entry(20.0, day=self.day - timedelta(days=2))
        rec = self._rec(stale_days=31)
        screen = rec.get_screen()
        ageing = screen['stats']['deposits']['ageing']
        self.assertEqual((ageing['a']['count'], ageing['d']['count']), (1, 1))
        self.assertEqual(ageing['d']['amount'], 50.0)
        stale = {l['amount']: l['stale'] for l in screen['deposits']}
        self.assertEqual(stale, {50.0: True, 20.0: False})
        only_old = rec.get_screen(age='d')
        self.assertEqual([l['amount'] for l in only_old['deposits']], [50.0])
        with self.assertRaises(ValidationError):
            rec.stale_days = 10

    # ------------------------------------------------------------------ first reconciliation
    def test_the_first_statement_offers_to_tick_the_opening_balance(self):
        self._entry(5000.0, journal=self.general)
        self._entry(100.0)
        rec = self._rec()
        opening = rec.get_screen()['opening']
        self.assertEqual((opening['count'], opening['amount']), (1, 5000.0))
        rec.clear_opening()
        self.assertEqual(rec.deposits_not_credited, 100.0)
        self.assertIsNone(rec.get_screen()['opening'])

    # ------------------------------------------------------------------ passbook finder
    def test_the_finder_finds_the_entry_and_the_group_the_bank_added_up(self):
        one = self._entry(1000.0, day=self.day - timedelta(days=1))
        a = self._entry(300.0, day=self.day - timedelta(days=2))
        b = self._entry(700.0, day=self.day - timedelta(days=2))
        self._entry(450.0, day=self.day - timedelta(days=2))
        rec = self._rec()
        found = rec.find_matches(1000.0, fields.Date.to_string(self.day), 'deposits', 7)
        self.assertEqual(found['results'][0]['kind'], 'single')
        self.assertEqual(found['results'][0]['line_ids'], [one.id])
        groups = [r for r in found['results'] if r['kind'] == 'group']
        self.assertEqual(set(groups[0]['line_ids']), {a.id, b.id})
        self.assertTrue(groups[0]['same_day'])
        rec.set_cleared(groups[0]['line_ids'], True, found['day'])
        self.assertEqual(
            self.env['bank.clearance'].search([('move_line_id', '=', a.id)]).cleared_date,
            self.day, "cleared on the passbook's date, not the entry's")

    def test_a_round_figure_still_leaves_room_for_the_groups(self):
        """5,000 matches many single receipts; the groups must not be crowded out."""
        for _i in range(5):
            self._entry(1000.0)
        a, b = self._entry(600.0), self._entry(400.0)
        found = self._rec().find_matches(1000.0, False, 'deposits', 7)
        kinds = [r['kind'] for r in found['results']]
        self.assertEqual(kinds[:3], ['single'] * 3)
        self.assertIn('group', kinds)
        self.assertIn({a.id, b.id}, [set(r['line_ids']) for r in found['results']])
        self.assertLessEqual(len(kinds), 6)

    def test_groups_of_the_same_amounts_are_one_answer(self):
        """600 + 400 can be made from either 400; that is one answer, not two."""
        six = self._entry(600.0)
        near = self._entry(400.0, day=self.day - timedelta(days=1))
        self._entry(400.0, day=self.day - timedelta(days=3))
        found = self._rec().find_matches(1000.0, False, 'deposits', 7)
        groups = [r for r in found['results'] if r['kind'] == 'group']
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]['alternatives'], 1)
        self.assertEqual(set(groups[0]['line_ids']), {six.id, near.id},
                         "the 400 nearer the date is the better placed one")

    def test_the_finder_reaches_three_and_four_entry_groups(self):
        lines = [self._entry(amount) for amount in (110.0, 220.0, 330.0, 440.0)]
        found = self._rec().find_matches(1100.0, False, 'deposits', 7)
        self.assertEqual([set(r['line_ids']) for r in found['results']],
                         [{l.id for l in lines}])
        found = self._rec().find_matches(660.0, False, 'deposits', 7)
        self.assertIn({lines[0].id, lines[1].id, lines[2].id},
                      [set(r['line_ids']) for r in found['results']])

    def test_the_finder_says_so_when_nothing_adds_up(self):
        self._entry(300.0)
        found = self._rec().find_matches(999.0, False, 'deposits', 7)
        self.assertEqual(found['results'], [])
        with self.assertRaises(UserError):
            self._rec().find_matches(0, False, 'deposits', 7)

    # ------------------------------------------------------------------ difference hints
    def test_hints_point_at_the_entry_the_bank_has_already_credited(self):
        self._entry(1000.0)
        small = self._entry(250.0)
        rec = self._rec(bank_balance=250.0)
        hints = rec.get_screen()['hints']
        self.assertEqual((hints[0]['kind'], hints[0]['line_ids']), ('tick', [small.id]))
        self.assertEqual(hints[-1]['kind'], 'post')

    def test_hints_find_two_entries_the_bank_shows_as_one(self):
        big, small = self._entry(1000.0), self._entry(250.0)
        rec = self._rec(bank_balance=1250.0)
        pair = [h for h in rec.get_screen()['hints'] if h['kind'] == 'pair']
        self.assertEqual(set(pair[0]['line_ids']), {big.id, small.id})

    def test_hints_point_at_a_ticked_entry_the_bank_does_not_have(self):
        big, small = self._entry(1000.0), self._entry(250.0)
        rec = self._rec()
        rec.set_cleared([big.id, small.id])
        rec.bank_balance = 250.0
        hints = rec.get_screen()['hints']
        self.assertEqual((hints[0]['kind'], hints[0]['line_ids']), ('untick', [big.id]))

    def test_a_difference_divisible_by_nine_suggests_swapped_digits(self):
        line = self._entry(5410.0)
        rec = self._rec()
        rec.set_cleared([line.id])
        rec.bank_balance = 4510.0
        hints = rec.get_screen()['hints']
        self.assertTrue(any(h['kind'] == 'info' and '9' in h['text'] for h in hints))
        self.assertEqual((hints[-1]['item_kind'], hints[-1]['amount']), ('charge', 900.0))

    def test_no_hints_once_it_balances(self):
        line = self._entry(1000.0)
        rec = self._rec()
        rec.set_cleared([line.id])
        rec.bank_balance = 1000.0
        self.assertEqual(rec.get_screen()['hints'], [])

    # ------------------------------------------------------------------ carrying forward
    def test_a_new_statement_takes_the_previous_ones_settings(self):
        self._entry(1000.0)
        self._rec(stale_days=60)
        later = self.env['bank.reconciliation'].create({
            'journal_id': self.journal.id, 'date': self.day})
        self.assertEqual(later.charge_account_id, self.expense)
        self.assertEqual(later.interest_account_id, self.income)
        self.assertEqual(later.stale_days, 60)

    def test_start_next_opens_one_draft_after_the_reconciled_statement(self):
        line = self._entry(1000.0)
        rec = self._rec()
        rec.set_cleared([line.id])
        rec.bank_balance = 1000.0
        rec.action_validate()
        action = rec.action_start_next()
        nxt = self.env['bank.reconciliation'].browse(action['params']['reconciliation_id'])
        self.assertEqual((nxt.state, nxt.charge_account_id), ('draft', self.expense))
        self.assertGreater(nxt.date, rec.date)
        self.assertEqual(rec.action_start_next()['params']['reconciliation_id'], nxt.id)

    # ------------------------------------------------------------------ the overview
    def test_the_overview_shows_where_each_bank_stands(self):
        self._entry(1000.0)
        self._entry(-200.0, day=self.day - timedelta(days=120))
        Rec = self.env['bank.reconciliation']
        card = next(c for c in Rec.get_overview()['cards'] if c['journal_id'] == self.journal.id)
        self.assertEqual(card['status'], 'never')
        self.assertEqual((card['deposits']['count'], card['payments']['count']), (1, 1))
        self.assertEqual(card['book'], 800.0)
        self.assertEqual(card['stale'], 1)

        first = Rec.open_bank(self.journal.id)['params']['reconciliation_id']
        self.assertEqual(Rec.open_bank(self.journal.id)['params']['reconciliation_id'], first,
                         "the open statement is reused, not duplicated")
        card = next(c for c in Rec.get_overview()['cards'] if c['journal_id'] == self.journal.id)
        self.assertEqual((card['status'], card['draft']['id']), ('progress', first))

    # ------------------------------------------------------------------ sources and day totals
    def test_entries_are_sorted_by_where_they_came_from(self):
        self._entry(500.0, label='Transfer from cash ( KYLM )')
        self._entry(300.0, label='Transfer from BANK(EKM1)')
        self._entry(200.0, label='CUST.IN/2026/1', partner=self.partner)
        self._entry(100.0, label='Adjustment')
        rec = self._rec()
        sources = rec.get_screen()['stats']['deposits']['sources']
        self.assertEqual({k: v['count'] for k, v in sources.items()},
                         {'cash': 1, 'bank': 1, 'party': 1, 'other': 1})
        cash = rec.get_screen(source='cash')['deposits']
        self.assertEqual([(l['amount'], l['branch']) for l in cash], [(500.0, 'KYLM')])

    def test_a_days_batch_is_totalled_and_ticked_in_one_go(self):
        d1 = self.day - timedelta(days=2)
        self._entry(500.0, day=d1, label='Transfer from cash ( KYLM )')
        self._entry(250.0, day=d1, label='Transfer from cash (kylm)')
        self._entry(200.0, day=d1, label='CUST.IN/2026/2', partner=self.partner)
        self._entry(100.0, label='CUST.IN/2026/3', partner=self.partner)
        rec = self._rec()
        days = rec.get_day_totals('deposits')['days']
        first = days[0]
        self.assertEqual((first['day'], first['count'], first['amount']),
                         (fields.Date.to_string(d1), 3, 950.0))
        self.assertEqual((first['groups'][0]['key'], first['groups'][0]['amount'],
                          first['groups'][0]['count']), ('cash|KYLM', 750.0, 2))
        rec.clear_day('deposits', fields.Date.to_string(d1), 'cash|KYLM')
        self.assertEqual(rec.deposits_not_credited, 300.0)
        rec.clear_day('deposits', fields.Date.to_string(d1))
        self.assertEqual(rec.deposits_not_credited, 100.0)

    def test_the_finder_matches_a_branchs_whole_day_of_cash(self):
        """Five cash lines the bank shows as one credit: beyond any 2-4 line group."""
        lines = [self._entry(amount, label='Transfer from cash ( KYLM )')
                 for amount in (500.0, 250.0, 125.0, 75.0, 50.0)]
        self._entry(40.0, label='CUST.IN/2026/4', partner=self.partner)
        found = self._rec().find_matches(1000.0, False, 'deposits', 7)
        first = found['results'][0]
        self.assertEqual(first['kind'], 'batch')
        self.assertEqual(set(first['line_ids']), {l.id for l in lines})
        self.assertIn('KYLM', first['label'])

    # ------------------------------------------------------------------ undo
    def test_undo_puts_back_a_tick_an_untick_and_a_date(self):
        line = self._entry(100.0, day=self.day - timedelta(days=4))
        rec = self._rec()
        Clearance = self.env['bank.clearance']
        with self.assertRaises(UserError):
            rec.undo_last()
        rec.set_cleared([line.id])
        rec.undo_last()
        self.assertFalse(Clearance.search([('move_line_id', '=', line.id)]))
        rec.set_cleared([line.id])
        rec.set_cleared([line.id], True, fields.Date.to_string(self.day))
        rec.undo_last()
        self.assertEqual(Clearance.search([('move_line_id', '=', line.id)]).cleared_date,
                         line.date, "the date goes back")
        rec.set_cleared([line.id], False)
        self.assertEqual(rec.get_screen()['undo']['label'], 'Unticked 1 entry')
        rec.undo_last()
        self.assertEqual(Clearance.search([('move_line_id', '=', line.id)]).cleared_date,
                         line.date, "the untick is taken back")

    def test_undo_takes_back_a_bulk_tick_and_ends_at_reconcile(self):
        a, b = self._entry(100.0), self._entry(200.0)
        rec = self._rec()
        rec.clear_matching('deposits')
        self.assertIn('all shown', rec.get_screen()['undo']['label'])
        rec.undo_last()
        self.assertEqual(rec.deposits_not_credited, 300.0)
        rec.set_cleared([a.id, b.id])
        rec.bank_balance = 300.0
        rec.action_validate()
        self.assertFalse(rec.step_ids, "a reconciled statement has nothing to undo")

    # ------------------------------------------------------------------ duplicates
    def test_same_party_same_amount_close_together_looks_doubled(self):
        a = self._entry(700.0, label='CUST.IN/2026/5', partner=self.partner)
        b = self._entry(700.0, day=self.day - timedelta(days=2), label='CUST.IN/2026/6',
                        partner=self.partner)
        self._entry(700.0, label='CUST.IN/2026/7', partner=self.other_partner)
        self._entry(700.0, day=self.day - timedelta(days=20), label='CUST.IN/2026/8',
                    partner=self.partner)
        found = self._rec().get_duplicates()
        self.assertEqual(found['count'], 1)
        self.assertEqual({l['id'] for l in found['groups'][0]['lines']}, {a.id, b.id})

    # ------------------------------------------------------------------ reading the labels
    def test_transfers_to_a_bank_and_cut_short_labels_keep_their_branch(self):
        self._entry(-100.0, label='Transfer to BANK(KYLM)')
        self._entry(200.0, label='Transfer from BANK(TCR')
        self._entry(50.0, label='Transfer to cash ( PKD )')
        rec = self._rec()
        screen = rec.get_screen(source='bank')
        self.assertEqual([(l['side'], l['branch']) for l in screen['withdrawals']],
                         [('withdrawals', 'KYLM')])
        self.assertEqual([l['branch'] for l in screen['deposits']], ['TCR'])
        self.assertEqual(rec.get_screen(source='cash')['deposits'][0]['branch'], 'PKD')

    # ------------------------------------------------------------------ details and entries
    def test_details_show_the_entry_its_lines_the_party_and_look_alikes(self):
        self._entry(10.0)
        rec = self._rec()
        posted = rec.create_bank_entry('receipt', 500.0, partner_id=self.partner.id,
                                       reference='UTR 4471')['posted']
        twin = self._entry(500.0, label='CUST.IN/2026/9', partner=self.other_partner)
        details = rec.get_line_details(posted['line_id'])
        self.assertEqual((details['move']['name'], details['move']['ref']),
                         (posted['name'], 'UTR 4471'))
        self.assertEqual(len(details['lines']), 2)
        self.assertEqual([l['bank'] for l in details['lines']].count(True), 1)
        self.assertTrue(details['cleared'])
        self.assertEqual(details['party']['balance'], -500.0, "the receipt credits the party")
        self.assertIn(twin.id, [s['id'] for s in details['similar']])
        with self.assertRaises(UserError):
            rec.get_line_details((twin.move_id.line_ids - twin).id)

    def test_bank_entries_of_every_kind(self):
        self._entry(10.0)
        rec = self._rec()
        rec.create_bank_entry('payment', 300.0, partner_id=self.partner.id)
        payable = self.partner.with_company(self.company).property_account_payable_id
        paid = self.env['account.move.line'].search(
            [('account_id', '=', payable.id), ('partner_id', '=', self.partner.id)])
        self.assertEqual(paid.debit, 300.0)
        with self.assertRaises(UserError, msg="a receipt needs its party"):
            rec.create_bank_entry('receipt', 50.0)
        with self.assertRaises(UserError, msg="a transfer needs its account"):
            rec.create_bank_entry('transfer_out', 50.0)
        with self.assertRaises(UserError, msg="after the statement date"):
            rec.create_bank_entry('receipt', 50.0, partner_id=self.partner.id,
                                  entry_date=fields.Date.to_string(self.day + timedelta(days=1)))
        rec.create_bank_entry('transfer_out', 80.0, account_id=self.expense.id, cleared=False)
        self.assertEqual(rec.book_balance, 10.0 - 300.0 - 80.0)
        self.assertEqual(rec.payments_not_presented, 80.0, "posted, left for the passbook to show")

    # ------------------------------------------------------------------ the lighter refresh
    def test_a_single_tick_sends_back_only_what_changed(self):
        a = self._entry(100.0)
        self._entry(200.0)
        rec = self._rec()
        payload = rec.set_cleared([a.id], True, False, delta=True)
        self.assertTrue(payload['delta'])
        self.assertNotIn('deposits', payload)
        self.assertEqual([(l['id'], l['cleared']) for l in payload['lines']], [(a.id, True)])
        self.assertEqual(payload['figures']['deposits_not_credited'], 200.0)
        self.assertEqual((payload['progress']['done'], payload['progress']['total']), (1, 2))

    # ------------------------------------------------------------------ short credits
    def test_a_deposit_credited_short_is_offered_and_its_shortfall_booked(self):
        line = self._entry(10000.0)
        rec = self._rec()
        near = [r for r in rec.find_matches(9982.0, False, 'deposits', 7)['results']
                if r['kind'] == 'near']
        self.assertEqual((near[0]['line_ids'], near[0]['difference']), ([line.id], 18.0))
        too_short = rec.find_matches(9000.0, False, 'deposits', 7)['results']
        self.assertFalse([r for r in too_short if r['kind'] == 'near'],
                         "short by more than the tolerance is not near")
        rec.tick_with_difference(near[0]['line_ids'], fields.Date.to_string(self.day), 18.0)
        self.assertEqual(rec.book_balance, 9982.0, "the charges reached the books")
        self.assertEqual(rec.deposits_not_credited, 0.0, "and the entry is ticked")
        self.assertIn('short as charges', rec.get_screen()['undo']['label'])
        with self.assertRaises(UserError):
            rec.tick_with_difference([line.id], False, 900.0)

    def test_a_short_credit_needs_the_charges_account(self):
        line = self._entry(10000.0)
        rec = self._rec(charge_account_id=False)
        with self.assertRaises(UserError):
            rec.tick_with_difference([line.id], False, 18.0)

    # ------------------------------------------------------------------ carried forward
    def test_entries_carried_from_the_last_statement_can_be_picked_out(self):
        old = self._entry(300.0, day=self.day - timedelta(days=15))
        paid = self._entry(200.0, day=self.day - timedelta(days=12))
        first = self._rec(date=self.day - timedelta(days=10))
        first.set_cleared([paid.id])
        first.bank_balance = 200.0
        first.action_validate()
        self._entry(50.0)
        second = self._rec()
        screen = second.get_screen(source='carried')
        self.assertEqual([l['id'] for l in screen['deposits']], [old.id])
        self.assertEqual(screen['stats']['deposits']['carried'], 1)
        self.assertEqual(second.charge_tolerance, first.charge_tolerance)

    # ------------------------------------------------------------------ the trend
    def test_the_overview_draws_the_outstanding_trend(self):
        self._entry(100.0, day=self.day - timedelta(days=20))
        self._entry(100.0)
        card = next(c for c in self.env['bank.reconciliation'].get_overview()['cards']
                    if c['journal_id'] == self.journal.id)
        self.assertEqual(len(card['trend']), 13)
        self.assertEqual((card['trend'][0]['count'], card['trend'][-1]['count']), (0, 2))

    # ------------------------------------------------------------------ queries
    def test_the_query_letter_lists_what_is_asked(self):
        line = self._entry(400.0)
        rec = self._rec()
        with self.assertRaises(UserError):
            rec.action_print_query_letter()
        rec.set_query(line.id, 'Credit not seen in your statement')
        html = self.env['ir.actions.report']._render_qweb_html(
            'lab_bank_reconciliation.report_query_letter', rec.ids)[0].decode()
        self.assertIn('Credit not seen in your statement', html)
        self.assertIn('Branch Manager', html)

    def test_a_line_can_be_put_under_query_and_printed(self):
        line = self._entry(400.0)
        self._entry(90.0)
        rec = self._rec()
        rec.set_query(line.id, 'Not in the passbook: ask the branch')
        screen = rec.get_screen()
        flagged = {l['amount']: l['query'] for l in screen['deposits']}
        self.assertEqual(flagged, {400.0: 'Not in the passbook: ask the branch', 90.0: ''})
        self.assertEqual(screen['stats']['deposits']['queries'], 1)
        self.assertEqual([l['amount'] for l in rec.get_screen(source='query')['deposits']], [400.0])
        self.assertEqual([l['amount'] for l in rec._brs_data()['queries']], [400.0])
        rec.set_query(line.id, '')
        self.assertFalse(self.env['bank.line.query'].search([('move_line_id', '=', line.id)]))
