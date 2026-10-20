# -*- coding: utf-8 -*-
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestFiscalYearNumbers(TransactionCase):
    """The lab's fiscal-year invoice numbers survive the replay, and only the replay."""

    def _move(self, env):
        journal = env['account.journal'].search(
            [('type', '=', 'sale'), ('company_id', '=', env.company.id)], limit=1)
        account = env['account.account'].search(
            [('account_type', '=', 'income'), ('company_ids', 'in', env.company.id)], limit=1)
        partner = env['res.partner'].create({'name': 'FY number clinic'})
        move = env['account.move'].create({
            'move_type': 'out_invoice', 'journal_id': journal.id, 'partner_id': partner.id,
            'invoice_date': '2026-04-01', 'date': '2026-04-01',
            'invoice_line_ids': [(0, 0, {'name': 'crown', 'quantity': 1, 'price_unit': 100.0,
                                         'account_id': account.id, 'tax_ids': [(5, 0, 0)]})],
        })
        move.name = '%s/2026-27/001' % journal.code
        return move

    def test_the_replay_keeps_a_fiscal_year_number(self):
        move = self._move(self.env(context=dict(self.env.context, migration_replay=True)))
        move.action_post()
        self.assertEqual(move.state, 'posted')
        self.assertTrue(move.name.endswith('/2026-27/001'))

    # Outside the replay the numbers go through Odoo's own date check - which, with
    # sale_custom (a dependency) reading "2026-27" as the lab's April-March fiscal
    # year, accepts a number of the invoice's own fiscal year and still refuses one
    # of another year. The company's fiscal year end decides which year a date is in,
    # so the tests set the lab's (March) rather than inherit the database's.
    def test_outside_the_replay_the_labs_fiscal_year_number_posts(self):
        self.env.company.fiscalyear_last_month = '3'
        move = self._move(self.env)
        move.action_post()
        self.assertEqual(move.state, 'posted')
        self.assertTrue(move.name.endswith('/2026-27/001'))

    def test_outside_the_replay_another_years_number_is_refused(self):
        self.env.company.fiscalyear_last_month = '3'
        move = self._move(self.env)
        move.name = '%s/2025-26/001' % move.journal_id.code
        with self.assertRaises(ValidationError):
            move.action_post()
