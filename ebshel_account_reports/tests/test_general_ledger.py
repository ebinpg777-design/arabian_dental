# -*- coding: utf-8 -*-
"""The general ledger read the way an accountant checks one: every account line adds
up, every figure opens the entries that make it, the entries run in the order they
were numbered, a grouped ledger loses no account, and nothing is left out of an
export without the export saying so. (client, 2026-10-01: "the general ledger is
not working properly")"""
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests import TransactionCase, tagged

HANDLERS = 'odoo.addons.ebshel_account_reports.models.handlers'


@tagged('post_install', '-at_install')
class TestGeneralLedger(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.company.write({'ebshel_fin_totals_last': False, 'ebshel_fin_negative': 'minus'})
        cls.Report = cls.env['ebshel.fin.report']
        cls.ledger = cls.Report.by_key('general_ledger')
        cls.engine = cls.env['ebshel.fin.engine']
        cls.today = fields.Date.context_today(cls.env.user)
        cls.fy_from = cls.env.company.compute_fiscalyear_dates(cls.today)['date_from']
        cls.year = {'date': {'preset': 'custom', 'from': fields.Date.to_string(cls.fy_from),
                             'to': fields.Date.to_string(cls.today)}}
        Account = cls.env['account.account']
        cls.till = Account.create({'name': 'EFG till', 'code': 'EFG001', 'account_type': 'asset_current'})
        cls.loan = Account.create({'name': 'EFG loan', 'code': 'EFG002', 'account_type': 'liability_current'})
        cls.journal = cls.env['account.journal'].search([('type', '=', 'general'),
                                                         ('company_id', '=', cls.env.company.id)], limit=1)

        def entry(day, amount):
            return cls.env['account.move'].create({
                'move_type': 'entry', 'date': day, 'journal_id': cls.journal.id,
                'line_ids': [(0, 0, {'name': 'EFG in', 'account_id': cls.till.id, 'debit': amount}),
                             (0, 0, {'name': 'EFG out', 'account_id': cls.loan.id, 'credit': amount})]})

        cls.opening = entry(cls.fy_from - timedelta(days=10), 1000.0)
        cls.opening.action_post()
        # made first, numbered second: the ledger must follow the numbers, not the ids
        cls.later = entry(cls.today, 70.0)
        cls.first = entry(cls.today, 30.0)
        cls.first.action_post()
        cls.later.action_post()
        cls.only = dict(cls.year, accounts_query='EFG')

    def _account(self, data, account):
        return next(l for l in data['lines'] if l['id'] == 'ac:%d' % account.id)

    def _sum(self, domain, field='balance'):
        return self.env['account.move.line']._read_group(domain, [], ['%s:sum' % field])[0][0] or 0.0

    # ------------------------------------------------------------ an account line
    def test_every_account_line_adds_up(self):
        data = self.ledger.get_report_data(self.year)
        self.assertEqual([c['key'] for c in data['columns']], ['opening', 'debit', 'credit', 'balance'])
        for line in (l for l in data['lines'] if l['kind'] == 'account'):
            opening, debit, credit, balance = [c['value'] or 0.0 for c in line['columns']]
            self.assertAlmostEqual(opening + debit - credit, balance, 2, line['name'])
        till = self._account(data, self.till)
        self.assertEqual([c['value'] for c in till['columns']], [1000.0, 100.0, 0.0, 1100.0])
        total = next(l for l in data['lines'] if l['kind'] == 'total')
        opening, debit, credit, balance = [c['value'] or 0.0 for c in total['columns']]
        self.assertAlmostEqual(opening + debit - credit, balance, 2, "and so does the whole ledger")

    def test_each_figure_opens_the_entries_that_make_it(self):
        data = self.ledger.get_report_data(self.year)
        till = self._account(data, self.till)
        for index, key in enumerate(('opening', 'debit', 'credit', 'balance')):
            action = self.ledger.get_drill_action(self.year, till['id'], key)
            field = key if key in ('debit', 'credit') else 'balance'
            self.assertAlmostEqual(self._sum(action['domain'], field), till['columns'][index]['value'], 2, key)
        total = next(l for l in data['lines'] if l['kind'] == 'total')
        for index, key in ((0, 'opening'), (3, 'balance')):
            action = self.ledger.get_drill_action(self.year, 'total', key)
            self.assertAlmostEqual(self._sum(action['domain']), total['columns'][index]['value'], 2, 'total ' + key)

    def test_items_stand_for_the_period(self):
        """The Items button of an account lists what moved in the period, not its opening."""
        data = self.ledger.get_report_data(self.only)
        self.assertTrue(next(c for c in data['columns'] if c['key'] == 'debit').get('items'))
        found = self.ledger.get_items(self.only, 'ac:%d' % self.till.id, 'debit')
        self.assertEqual({r['move'] for r in found['rows']}, {self.first.name, self.later.name})

    # ------------------------------------------------------------ its entries
    def test_entries_run_in_their_numbered_order_from_the_opening(self):
        self.assertLess(self.later.id, self.first.id)
        self.assertLess(self.first.name, self.later.name)
        res = self.ledger.expand_line(self.only, 'ac:%d' % self.till.id)
        self.assertEqual(res['lines'][0]['kind'], 'initial')
        self.assertEqual(res['lines'][0]['columns'][0]['value'], 1000.0, "the opening stands in its own column")
        entries = [l for l in res['lines'] if l['kind'] == 'move_line']
        self.assertEqual([l['parts']['move'] for l in entries], [self.first.name, self.later.name])
        self.assertEqual([l['columns'][3]['value'] for l in entries], [1030.0, 1100.0])

    def test_a_debit_leaves_the_credit_empty(self):
        res = self.ledger.expand_line(self.only, 'ac:%d' % self.till.id)
        entry = next(l for l in res['lines'] if l['kind'] == 'move_line')
        self.assertIsNone(entry['columns'][0]['value'], "an entry has no opening")
        self.assertEqual(entry['columns'][1]['value'], 30.0)
        self.assertIsNone(entry['columns'][2]['value'])
        self.assertEqual(entry['columns'][2]['text'], '')

    def test_more_entries_come_a_page_at_a_time_and_say_how_many_are_left(self):
        lid = 'ac:%d' % self.till.id
        with patch(HANDLERS + '.GL_FIRST', 1), patch(HANDLERS + '.GL_MORE', 1):
            first = self.ledger.expand_line(self.only, lid)
            self.assertTrue(first['has_more'])
            self.assertIn('1 not shown yet', first['more_label'])
            rest = self.ledger.expand_line(self.only, lid, 1)
            self.assertFalse(rest['has_more'])
            data = self.ledger.get_report_data(dict(self.only, expanded=[lid]))
        shown = [l for l in first['lines'] + rest['lines'] if l['kind'] == 'move_line']
        self.assertEqual([l['parts']['move'] for l in shown], [self.first.name, self.later.name])
        self.assertEqual(shown[-1]['columns'][3]['value'], 1100.0, "the running balance carries over the pages")
        more = next(l for l in data['lines'] if l['kind'] == 'more')
        self.assertEqual((more['offset'], more['left']), (1, 1))

    def test_an_export_says_what_it_leaves_out(self):
        with patch(HANDLERS + '.GL_EXPORT', 1):
            rows = self.ledger.export_rows(self.only, {'scope': 'full'})['rows']
        notes = [r[0] for r in rows if 'not in this export' in str(r[0])]
        self.assertEqual(len(notes), 2, "one for each account cut short")
        self.assertTrue(all(n.startswith('1 more') for n in notes))
        whole = self.ledger.export_rows(self.only, {'scope': 'full'})['rows']
        self.assertFalse([r for r in whole if 'not in this export' in str(r[0])])

    # ------------------------------------------------------------ grouped
    def _grouped(self, options):
        flat = self.ledger.get_report_data(options)
        grouped = self.ledger.get_report_data(dict(options, hierarchy=True))
        accounts = {l['id'] for l in flat['lines'] if l['kind'] == 'account'}
        self.assertEqual({l['id'] for l in grouped['lines'] if l['kind'] == 'account'}, accounts,
                         "grouping moves accounts, it never drops one")
        by_id = {l['id']: l for l in grouped['lines']}
        heads = [l for l in grouped['lines'] if l['kind'] == 'group']
        self.assertTrue(heads)
        for head in heads:
            under = [l for l in grouped['lines'] if l['kind'] == 'account' and self._is_under(l, head['id'], by_id)]
            self.assertTrue(under, head['name'])
            for i in range(4):
                self.assertAlmostEqual(head['columns'][i]['value'], sum(l['columns'][i]['value'] or 0 for l in under), 2,
                                       head['name'])
            action = self.ledger.get_drill_action(dict(options, hierarchy=True), head['id'], 'balance')
            self.assertAlmostEqual(self._sum(action['domain']), head['columns'][3]['value'], 2, head['name'])
        for line in grouped['lines']:
            if line.get('parent_id'):
                self.assertLess(grouped['lines'].index(by_id[line['parent_id']]), grouped['lines'].index(line),
                                "a heading comes before what it holds")
                self.assertEqual(line['level'], by_id[line['parent_id']]['level'] + 1)
        self.assertEqual(sum(l['columns'][3]['value'] or 0 for l in grouped['lines'] if l['kind'] == 'total'),
                         sum(l['columns'][3]['value'] or 0 for l in flat['lines'] if l['kind'] == 'total'))
        return grouped, by_id

    def _is_under(self, line, head_id, by_id):
        up = line.get('parent_id')
        while up:
            if up == head_id:
                return True
            up = by_id[up].get('parent_id')
        return False

    def test_a_chart_without_account_groups_groups_by_type(self):
        """Neither the lab's chart nor Ortho's has one account group: the switch changed nothing."""
        self.env['account.group'].search([]).unlink()
        grouped, by_id = self._grouped(self.year)
        till = by_id['ac:%d' % self.till.id]
        self.assertEqual(till['parent_id'], 'ty:asset_current')
        self.assertEqual(by_id['ty:asset_current']['parent_id'], 'hd:asset')
        self.assertEqual(by_id['hd:asset']['name'], 'Assets')
        lid = till['id']
        kids = self.ledger.expand_line(dict(self.year, hierarchy=True), lid)['lines']
        self.assertTrue(all(k['level'] == till['level'] + 1 for k in kids), "an account's entries sit one step in")

    def test_account_groups_hold_their_accounts_and_add_up(self):
        group = self.env['account.group'].create({'name': 'EFG group', 'code_prefix_start': 'EFG',
                                                  'company_id': self.env.company.id})
        grouped, by_id = self._grouped(self.only)
        gid = 'grp:%d' % group.id
        self.assertEqual(by_id['ac:%d' % self.till.id]['parent_id'], gid)
        self.assertEqual(by_id['ac:%d' % self.loan.id]['parent_id'], gid)

    # ------------------------------------------------------------ opening it
    def test_the_ledger_opens_on_the_year_so_far(self):
        options = self.engine.normalize(self.ledger, {})
        self.assertEqual(options['date']['from'], fields.Date.to_string(self.fy_from))
        self.assertEqual(options['date']['to'], fields.Date.to_string(self.today))
