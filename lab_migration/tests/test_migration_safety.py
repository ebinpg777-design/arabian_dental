# -*- coding: utf-8 -*-
"""Re-running the migration must never be more destructive than the first run.

No Odoo 17 database is needed: the source cursor is replaced by a stand-in that
answers each query by a marker in its SQL, so these run on any test database.
Odoo 17 ids here are deliberately huge so they cannot collide with the real
migration.map rows of a database copied from production.
"""
from unittest.mock import patch

from odoo import Command, fields
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged
from odoo.tests.common import new_test_user

SRC = 9_900_000     # offset for every fake Odoo 17 id


class FakeSource:
    """The Odoo 17 cursor: the rows of the first marker found in the query."""

    def __init__(self, answers):
        self.answers = answers
        self.rows = []

    def execute(self, query, params=None):
        self.rows = next((list(rows) for marker, rows in self.answers if marker in query), [])

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def close(self):
        pass


class FakeConnection:
    autocommit = False

    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self, cursor_factory=None):
        return self._cursor

    def close(self):
        pass


@tagged('post_install', '-at_install')
class TestMigrationAccess(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.backend = cls.env['migration.backend'].create({
            'name': 'Access Test Source', 'db_password': 'source-secret'})
        cls.viewer = new_test_user(
            cls.env, login='migration_viewer',
            groups='base.group_user,lab_migration.group_lab_migration_user')
        cls.runner = new_test_user(
            cls.env, login='migration_runner',
            groups='base.group_user,lab_migration.group_lab_migration_manager')

    def test_read_only_user_cannot_start_any_sync(self):
        """Every action switches to superuser, so the read-only ACL alone never
        stopped a button pressed through RPC. The gate must fire before the
        source database is even contacted."""
        Backend = type(self.backend)
        actions = [
            'action_test_connection', 'action_sync_master', 'action_sync_configuration',
            'action_sync_all', 'action_continue_invoice_numbering',
            'action_sync_timestamps', 'action_sync_transactions',
            'action_sync_transactions_gl', 'action_sync_transactions_ops',
            'action_refresh_inventory', 'action_refresh_opening_balances',
        ]
        with patch.object(Backend, '_connect',
                          side_effect=AssertionError("source contacted")):
            for name in actions:
                with self.subTest(action=name), self.assertRaises(AccessError):
                    getattr(self.backend.with_user(self.viewer), name)()

    def test_read_only_user_cannot_read_the_source_password(self):
        with self.assertRaises(AccessError):
            self.backend.with_user(self.viewer).read(['db_password'])
        self.assertNotIn('db_password', self.backend.with_user(self.viewer).fields_get())
        self.assertEqual(self.backend.with_user(self.runner).db_password, 'source-secret')

    def test_sync_runner_passes_the_gate(self):
        elevated = self.backend.with_user(self.runner)._migration_env()
        self.assertTrue(elevated.env.su)


@tagged('post_install', '-at_install')
class TestMigrationRerun(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # The re-runs are pressed by a Run Sync member; the accounting fixture's
        # own user is not one, and the sync gate refuses it.
        # It also writes the orders the source holds, so it sells as well.
        cls.env.user.group_ids = [
            (4, cls.env.ref('lab_migration.group_lab_migration_manager').id),
            (4, cls.env.ref('sales_team.group_sale_manager').id)]
        cls.backend = cls.env['migration.backend'].create({
            'name': 'Re-run Test Source', 'txn_from_date': '2026-04-01',
            'opening_date': '2026-03-31', 'opening_open_item_years': 0})
        cls.receivable = cls.company_data['default_account_receivable']
        cls.revenue = cls.company_data['default_account_revenue']
        cls.misc = cls.company_data['default_journal_misc']

    def _cache(self):
        return {
            'res.company': {SRC + 1: self.env.company.id},
            'res.partner': {SRC + 5: self.partner_a.id, SRC + 6: self.partner_b.id},
            'product.product': {SRC + 11: self.product_a.id, SRC + 12: self.product_b.id},
            'account.journal': {SRC + 3: self.misc.id},
            'account.account': {SRC + 20: self.receivable.id, SRC + 21: self.revenue.id},
        }

    def _no_commit(self):
        # the phases commit per batch; a test transaction must not
        return patch.object(self.env.cr, 'commit', lambda: None)

    # ------------------------------------------------------------ sale orders
    def _sale_line(self, key, product, name, qty=1.0, sequence=1):
        return {'id': SRC + key, 'order_id': SRC + 900, 'product_id': SRC + product,
                'name': name, 'product_uom_qty': qty, 'price_unit': 100.0,
                'sequence': sequence}

    def _run_sale_orders(self, lines):
        order = {'id': SRC + 900, 'name': 'MIG-SO-900', 'partner_id': SRC + 5,
                 'company_id': SRC + 1, 'date_order': '2026-04-02 10:00:00',
                 'state': 'sale'}
        cur = FakeSource([
            ('account_tax_sale_order_line_rel', []),
            ('FROM sale_order_line', lines),
            ('FROM sale_order WHERE', [order]),
        ])
        stats = []
        with self._no_commit():
            self.backend._txn_sale_orders(cur, self._cache(), stats)
        return stats

    def test_rerun_updates_sale_lines_in_place_and_keeps_invoice_links(self):
        self._run_sale_orders([
            self._sale_line(9001, 11, 'Crown', sequence=1),
            self._sale_line(9002, 12, 'Bridge', sequence=2),
            self._sale_line(9003, 11, 'Retainer', sequence=3),
        ])
        order = self.env['sale.order'].search([('x_src_id', '=', SRC + 900)])
        self.assertEqual(len(order.order_line), 3)
        by_key = {l.x_src_id: l for l in order.order_line}
        crown, bridge, retainer = (by_key[SRC + k] for k in (9001, 9002, 9003))

        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': self.partner_a.id,
            'invoice_date': '2026-04-03',
            'invoice_line_ids': [
                Command.create({'product_id': self.product_a.id, 'quantity': 1,
                                'price_unit': 100.0, 'sale_line_ids': [Command.set(crown.ids)]}),
                Command.create({'product_id': self.product_b.id, 'quantity': 1,
                                'price_unit': 100.0, 'sale_line_ids': [Command.set(bridge.ids)]}),
            ],
        })
        # a line added in Odoo 19 after the cut-over carries no Odoo 17 key
        order.write({'state': 'draft'})
        manual = self.env['sale.order.line'].create({
            'order_id': order.id, 'product_id': self.product_b.id, 'name': 'Added later'})
        order.write({'state': 'sale'})

        # the source now: crown re-quantified, bridge and retainer gone, a new line
        stats = self._run_sale_orders([
            self._sale_line(9001, 11, 'Crown', qty=2.0, sequence=1),
            self._sale_line(9004, 12, 'Night guard', sequence=4),
        ])
        self.assertIn('updated=1', stats[-1])
        self.assertTrue(crown.exists(), "a matched line must keep its id")
        self.assertEqual(crown.product_uom_qty, 2.0)
        self.assertTrue(bridge.exists(), "an invoiced line is never deleted")
        self.assertFalse(retainer.exists(), "a stale line nothing points at is dropped")
        self.assertTrue(manual.exists(), "a line without an Odoo 17 key is not ours")
        self.assertIn(SRC + 9004, order.order_line.mapped('x_src_id'))
        self.assertEqual(invoice.invoice_line_ids.sale_line_ids, crown | bridge)
        self.assertEqual(order.state, 'sale')

    def test_unkeyed_lines_are_adopted_only_when_the_pairing_is_certain(self):
        order = self.env['sale.order'].create({
            'partner_id': self.partner_a.id,
            'order_line': [
                Command.create({'product_id': self.product_a.id, 'sequence': 1}),
                Command.create({'product_id': self.product_b.id, 'sequence': 2}),
            ],
        })
        mismatched = [(0, 0, {'product_id': self.product_b.id, 'x_src_id': SRC + 1}),
                      (0, 0, {'product_id': self.product_a.id, 'x_src_id': SRC + 2})]
        before = order.order_line.ids
        self.assertFalse(self.backend._txn_rewrite_lines(order, {}, mismatched))
        self.assertEqual(order.order_line.ids, before, "unpairable lines are left alone")
        self.assertFalse(any(order.order_line.mapped('x_src_id')))

        paired = [(0, 0, {'product_id': self.product_a.id, 'x_src_id': SRC + 1}),
                  (0, 0, {'product_id': self.product_b.id, 'x_src_id': SRC + 2})]
        self.assertTrue(self.backend._txn_rewrite_lines(order, {}, paired))
        self.assertEqual(order.order_line.ids, before, "adopted lines keep their ids")
        self.assertEqual(sorted(order.order_line.mapped('x_src_id')), [SRC + 1, SRC + 2])

    # ------------------------------------------------------------ journal entries
    def _entry(self, amount, ref=False):
        move = self.env['account.move'].create({
            'move_type': 'entry', 'journal_id': self.misc.id, 'date': '2026-04-02',
            'ref': ref,
            'line_ids': [
                Command.create({'account_id': self.receivable.id, 'partner_id': self.partner_a.id,
                                'debit': max(amount, 0.0), 'credit': max(-amount, 0.0)}),
                Command.create({'account_id': self.revenue.id,
                                'debit': max(-amount, 0.0), 'credit': max(amount, 0.0)}),
            ],
        })
        move.action_post()
        return move

    def test_rerun_keeps_existing_journal_entries(self):
        row = {'id': SRC + 700, 'journal_id': SRC + 3, 'company_id': SRC + 1,
               'date': '2026-04-02', 'ref': 'MIG-JE', 'name': '/', 'partner_id': SRC + 5,
               'narration': None}
        lines = [
            {'id': SRC + 7001, 'move_id': SRC + 700, 'account_id': SRC + 20,
             'partner_id': SRC + 5, 'name': 'Receipt', 'debit': 0.0, 'credit': 50.0,
             'date_maturity': None, 'product_id': None},
            {'id': SRC + 7002, 'move_id': SRC + 700, 'account_id': SRC + 21,
             'partner_id': None, 'name': 'Receipt', 'debit': 50.0, 'credit': 0.0,
             'date_maturity': None, 'product_id': None},
        ]
        cur = FakeSource([('FROM account_move_line WHERE', lines),
                          ('FROM account_move m', [row])])
        cache = self._cache()
        with self._no_commit():
            self.backend._txn_journal_entries(cur, cache, [])
        move = self.env['account.move'].search([('x_src_id', '=', SRC + 700)])
        self.assertEqual(move.state, 'posted')
        line_ids = move.line_ids.ids

        stats = []
        with self._no_commit():
            self.backend._txn_journal_entries(cur, cache, stats)
        self.assertIn('kept=1', stats[-1])
        self.assertEqual(move.line_ids.ids, line_ids)

    def test_forced_rewrite_refuses_reconciled_entries(self):
        invoice_like, payment_like = self._entry(100.0), self._entry(-100.0)
        counts = {'kept': 0, 'protected': 0}
        self.assertTrue(self.backend._txn_keep_move(invoice_like.id, counts, 1))
        self.assertEqual(counts['kept'], 1)

        forced = self.backend.with_context(migration_rewrite_documents=True)
        self.assertFalse(forced._txn_keep_move(invoice_like.id, counts, 1))

        (invoice_like.line_ids | payment_like.line_ids).filtered(
            lambda l: l.account_id == self.receivable).reconcile()
        self.assertTrue(forced._txn_keep_move(invoice_like.id, counts, 1))
        self.assertEqual(counts['protected'], 1)

    # ------------------------------------------------------------ opening balances
    _TEST_REF = 'Opening Balance (migration safety test)'

    def _opening_source(self):
        Map = self.env['migration.map']
        Map.create([
            {'dst_model': 'res.company', 'src_id': SRC + 1, 'dst_id': self.env.company.id},
            {'dst_model': 'account.account', 'src_id': SRC + 20, 'dst_id': self.receivable.id},
            {'dst_model': 'res.partner', 'src_id': SRC + 5, 'dst_id': self.partner_a.id},
            {'dst_model': 'res.partner', 'src_id': SRC + 6, 'dst_id': self.partner_b.id},
        ])
        cur = FakeSource([
            ('WITH win', []),
            ('include_initial_balance', []),
            ('GROUP BY aml.account_id, aml.partner_id', [
                {'account_id': SRC + 20, 'partner_id': SRC + 5, 'd': 100.0, 'c': 0.0},
                {'account_id': SRC + 20, 'partner_id': SRC + 6, 'd': 50.0, 'c': 0.0},
            ]),
            ('res_company', [{'id': SRC + 1}]),
        ])
        Backend = type(self.backend)
        return (patch.object(Backend, '_connect', lambda rec: FakeConnection(cur)),
                patch.object(Backend, '_OPENING_REF', self._TEST_REF))

    def _old_opening(self):
        return self._entry(10.0, ref=self._TEST_REF)

    def test_opening_posts_every_partner_without_committing(self):
        """The test cursor refuses commit(): the old per-chunk commit would fail here."""
        connect, ref = self._opening_source()
        old = self._old_opening()
        with connect, ref:
            out = self.backend._opening_accounting()
        self.assertIn('2 entries', out[0])
        self.assertFalse(old.exists())
        new = self.env['account.move'].search([('ref', '=', self._TEST_REF)])
        self.assertEqual(new.mapped('state'), ['posted', 'posted'])
        self.assertEqual(new.line_ids.partner_id, self.partner_a | self.partner_b)

    # ------------------------------------------------- opening refresh (v17 edits)
    def _opening_with_items(self, items, totals):
        """A source whose open items are `items` and whose account totals are
        `totals`, both as the SQL returns them."""
        Map = self.env['migration.map']
        if not Map.search_count([('dst_model', '=', 'res.company'),
                                 ('src_id', '=', SRC + 1)]):
            Map.create([
                {'dst_model': 'res.company', 'src_id': SRC + 1, 'dst_id': self.env.company.id},
                {'dst_model': 'account.account', 'src_id': SRC + 20, 'dst_id': self.receivable.id},
                {'dst_model': 'res.partner', 'src_id': SRC + 5, 'dst_id': self.partner_a.id},
                {'dst_model': 'res.partner', 'src_id': SRC + 6, 'dst_id': self.partner_b.id},
            ])
        cur = FakeSource([
            ('WITH win', items),
            ('include_initial_balance', []),
            ('GROUP BY aml.account_id, aml.partner_id', totals),
            ('res_company', [{'id': SRC + 1}]),
        ])
        Backend = type(self.backend)
        return (patch.object(Backend, '_connect', lambda rec: FakeConnection(cur)),
                patch.object(Backend, '_OPENING_REF', self._TEST_REF))

    def _item(self, src, partner, amount, doc, maturity=None):
        return {'src_id': src, 'account_id': SRC + 20, 'partner_id': partner,
                'date': self.backend.opening_date or fields.Date.today(),
                'date_maturity': maturity or (self.backend.opening_date or fields.Date.today()),
                'doc': doc, 'residual': amount}

    def _total(self, partner, amount):
        return {'account_id': SRC + 20, 'partner_id': partner,
                'd': amount if amount > 0 else 0.0, 'c': -amount if amount < 0 else 0.0}

    def _opening_lines(self, partner):
        """The partner's opening ITEMS. The entry also holds the line that closes it
        against opening equity, and Odoo puts the partner on that one too."""
        moves = self.env['account.move'].search([('ref', '=', self._TEST_REF)])
        return moves.line_ids.filtered(
            lambda l: l.partner_id == partner and l.account_id == self.receivable)

    def _build_opening(self, items, totals):
        connect, ref = self._opening_with_items(items, totals)
        with connect, ref:
            self.backend._opening_accounting()

    def test_the_refresh_follows_an_edit_made_in_the_old_system(self):
        """An invoice corrected in Odoo 17 after the cut: the opening item follows
        it, and nobody else's entry is touched. (client, 2026-09-18)"""
        self._build_opening(
            [self._item(SRC + 100, SRC + 5, 330.0, 'OC132437'),
             self._item(SRC + 101, SRC + 6, 50.0, 'OC132500')],
            [self._total(SRC + 5, 330.0), self._total(SRC + 6, 50.0)])
        before = self._opening_lines(self.partner_b).mapped('write_date')
        connect, ref = self._opening_with_items(
            [self._item(SRC + 100, SRC + 5, 450.0, 'OC132437'),
             self._item(SRC + 101, SRC + 6, 50.0, 'OC132500')],
            [self._total(SRC + 5, 450.0), self._total(SRC + 6, 50.0)])
        with connect, ref:
            [note] = self.backend._refresh_opening()
        self.assertIn('1 changed', note)
        item = self._opening_lines(self.partner_a).filtered(lambda l: l.name == 'OC132437')
        self.assertEqual(item.debit, 450.0, "the corrected amount")
        self.assertEqual(item.x_src_id, SRC + 100, "matched by the old line id")
        self.assertEqual(item.move_id.state, 'posted', "and posted again")
        self.assertEqual(
            round(sum(self._opening_lines(self.partner_a).mapped(
                lambda l: l.debit - l.credit)), 2), 450.0,
            "the partner's opening equals the old ledger")
        self.assertEqual(self._opening_lines(self.partner_b).mapped('write_date'), before,
                         "an unchanged partner's entry is left alone")

    def test_the_refresh_adds_and_drops_documents(self):
        self._build_opening([self._item(SRC + 100, SRC + 5, 330.0, 'OC132437')],
                            [self._total(SRC + 5, 330.0)])
        connect, ref = self._opening_with_items(
            [self._item(SRC + 102, SRC + 5, 120.0, 'OC140000')],
            [self._total(SRC + 5, 120.0)])
        with connect, ref:
            [note] = self.backend._refresh_opening()
        names = self._opening_lines(self.partner_a).mapped('name')
        self.assertIn('OC140000', names, "a document raised in the old system since")
        self.assertNotIn('OC132437', names, "and one that is no longer open")
        self.assertIn('1 added', note)
        self.assertIn('1 removed', note)
        self.assertEqual(
            round(sum(self._opening_lines(self.partner_a).mapped(
                lambda l: l.debit - l.credit)), 2), 120.0)

    def test_the_refresh_leaves_a_reconciled_item_alone(self):
        """A doctor has already paid it here: the statement and the payment stand,
        and the difference is named instead of forced."""
        self._build_opening([self._item(SRC + 100, SRC + 5, 330.0, 'OC132437')],
                            [self._total(SRC + 5, 330.0)])
        item = self._opening_lines(self.partner_a).filtered(lambda l: l.name == 'OC132437')
        payment = self._entry(-330.0)
        (item | payment.line_ids.filtered(
            lambda l: l.account_id == self.receivable)).reconcile()
        self.assertTrue(item.reconciled)
        connect, ref = self._opening_with_items(
            [self._item(SRC + 100, SRC + 5, 999.0, 'OC132437')],
            [self._total(SRC + 5, 999.0)])
        with connect, ref:
            [note] = self.backend._refresh_opening()
        self.assertEqual(item.debit, 330.0, "what was reconciled is not rewritten")
        self.assertIn('already reconciled here', note)

    def test_a_balance_that_went_to_nothing_leaves_no_empty_entry(self):
        self._build_opening([self._item(SRC + 100, SRC + 5, 330.0, 'OC132437')],
                            [self._total(SRC + 5, 330.0)])
        move = self._opening_lines(self.partner_a).move_id
        connect, ref = self._opening_with_items([], [])
        with connect, ref:
            [note] = self.backend._refresh_opening()
        self.assertIn('1 removed', note)
        self.assertFalse(move.exists(), "nothing left to say, so no entry to read")

    def test_a_doctor_with_no_opening_entry_gets_one(self):
        """Open items in the old ledger and nothing here: the entry is made, not
        merely reported. (client, 2026-09-18)"""
        self._build_opening([self._item(SRC + 100, SRC + 5, 330.0, 'OC132437')],
                            [self._total(SRC + 5, 330.0)])
        self.assertFalse(self._opening_lines(self.partner_b))
        connect, ref = self._opening_with_items(
            [self._item(SRC + 100, SRC + 5, 330.0, 'OC132437'),
             self._item(SRC + 103, SRC + 6, 275.0, 'OC150000')],
            [self._total(SRC + 5, 330.0), self._total(SRC + 6, 275.0)])
        with connect, ref:
            [note] = self.backend._refresh_opening()
        lines = self._opening_lines(self.partner_b)
        self.assertEqual(lines.mapped('name'), ['OC150000'])
        self.assertEqual(lines.debit, 275.0)
        self.assertEqual(lines.x_src_id, SRC + 103, "kept by its old line id")
        self.assertEqual(lines.move_id.state, 'posted')
        self.assertIn('1 of them new', note)
        self.assertIn(self.partner_b.name, note)

    def test_nothing_to_change_says_so(self):
        self._build_opening([self._item(SRC + 100, SRC + 5, 330.0, 'OC132437')],
                            [self._total(SRC + 5, 330.0)])
        connect, ref = self._opening_with_items(
            [self._item(SRC + 100, SRC + 5, 330.0, 'OC132437')],
            [self._total(SRC + 5, 330.0)])
        with connect, ref:
            [note] = self.backend._refresh_opening()
        self.assertIn('nothing to change', note)

    def test_lines_from_a_first_migration_are_matched_by_their_document(self):
        """Opening lines posted before this feature carry no old id: they are
        matched once, by partner and document number, then kept by id."""
        self._build_opening([self._item(SRC + 100, SRC + 5, 330.0, 'OC132437')],
                            [self._total(SRC + 5, 330.0)])
        item = self._opening_lines(self.partner_a).filtered(lambda l: l.name == 'OC132437')
        item.with_context(check_move_validity=False).x_src_id = False
        connect, ref = self._opening_with_items(
            [self._item(SRC + 100, SRC + 5, 400.0, 'OC132437')],
            [self._total(SRC + 5, 400.0)])
        with connect, ref:
            [note] = self.backend._refresh_opening()
        self.assertIn('matched to their old line', note)
        self.assertEqual(item.x_src_id, SRC + 100)
        self.assertEqual(item.debit, 400.0)

    def test_a_failing_opening_entry_rolls_the_whole_opening_back(self):
        connect, ref = self._opening_source()
        old = self._old_opening()
        AccountMove = type(self.env['account.move'])
        original = AccountMove.action_post
        partner_b = self.partner_b

        def action_post(moves):
            if partner_b in moves.line_ids.partner_id:
                raise UserError("cannot post for this partner")
            return original(moves)

        with connect, ref, patch.object(AccountMove, 'action_post', action_post):
            with self.assertRaises(UserError) as caught:
                self.backend._opening_accounting()
        self.assertIn(partner_b.name, str(caught.exception))
        self.assertEqual(self.env['account.move'].search([('ref', '=', self._TEST_REF)]), old,
                         "the previous opening must survive a failed re-run")
        self.assertEqual(old.state, 'posted')
