# -*- coding: utf-8 -*-
"""The follow-up column, and sending, must not ask the database once per row.

`_ebshel_judge` resolves which follow-up level a customer has reached, and every
caller of it is a loop: the column on a partner list, the collections desk, the
nightly job. It used to SEARCH the level table for each one, so a 200-row list
issued 118 searches for an answer that is identical every time. The levels are a
handful of configuration rows: the callers read them once (`_of_company`) and hand
them down. Sending a reminder did the same thing a second time - `_next()` searched
for the following level once per customer - and takes the same ladder now.

Asserted as a query COUNT, because that is the thing that regresses: the figures
were always right, they were just bought one query at a time. (client, 2026-10-04;
ported from 19.0, where the ladder is an ormcache instead)
"""
from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.sql_db import Cursor
from odoo.tests import TransactionCase, tagged
from odoo.tools import SQL


@contextmanager
def counted(cr):
    """Count the queries issued inside the block."""
    box = {'n': 0}
    start = cr.sql_log_count
    try:
        yield box
    finally:
        box['n'] = cr.sql_log_count - start


@contextmanager
def queries_on(table):
    """The queries that read `table` inside the block (its quoted name, so a column
    such as res_partner.ebshel_followup_level_id does not count)."""
    seen = []
    original = Cursor.execute
    quoted = '"%s"' % table

    def spy(self, query, params=None, log_exceptions=True):
        text = query._sql_tuple[0] if isinstance(query, SQL) else str(query)
        if quoted in text:
            seen.append(text)
        return original(self, query, params, log_exceptions)

    with patch('odoo.sql_db.Cursor.execute', spy):
        yield seen


@tagged('post_install', '-at_install')
class TestFollowupQueries(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company.write({'fiscalyear_lock_date': False, 'sale_lock_date': False, 'ebshel_followup_min_amount': 0.0})
        cls.today = fields.Date.context_today(cls.env.user)
        # A company of its own for the ladder: this database already ships levels,
        # and a test that asserts which one is reached has to own the whole ladder.
        cls.own = cls.env['res.company'].create({'name': 'Ladder Co'})
        Level = cls.env['ebshel.followup.level']
        cls.levels = Level.create([
            {'name': 'Reminder', 'days': 7, 'company_id': cls.own.id},
            {'name': 'Chase', 'days': 30, 'company_id': cls.own.id},
            {'name': 'Final', 'days': 60, 'company_id': cls.own.id},
        ])
        cls.partners = cls.env['res.partner'].create(
            [{'name': 'Query Clinic %d' % i, 'customer_rank': 1} for i in range(40)])

    def test_the_level_ladder_is_read_once_not_once_per_customer(self):
        self.env.invalidate_all()
        with counted(self.env.cr) as box:
            self.partners._compute_ebshel_followup()
        self.assertLess(
            box['n'], 20,
            "40 customers must not cost a query each - this was 131 for 200 rows, "
            "118 of them the same search against ebshel_followup_level")

    def test_the_ladder_still_answers_the_same(self):
        """Read once, but not wrong: the level reached is the highest at or below,
        whether it is searched for or picked from the ladder read beforehand."""
        Level = self.env['ebshel.followup.level']
        ladder = Level._of_company(self.own)
        for days, expected in ((0, False), (6, False), (7, 'Reminder'),
                               (29, 'Reminder'), (30, 'Chase'), (90, 'Final')):
            for reached in (Level._for_days(days, self.own), Level._for_days(days, self.own, levels=ladder)):
                self.assertEqual(reached.name if reached else False, expected, 'at %s days overdue' % days)

    def test_changing_a_level_is_noticed(self):
        """There is no cache across transactions to go stale: a level moved is seen at once."""
        Level = self.env['ebshel.followup.level']
        self.assertEqual(Level._for_days(45, self.own).name, 'Chase')
        self.levels.filtered(lambda l: l.name == 'Final').write({'days': 40})
        self.assertEqual(Level._for_days(45, self.own).name, 'Final')
        self.assertEqual(Level._for_days(45, self.own, levels=Level._of_company(self.own)).name, 'Final')

    def test_the_next_level_comes_from_the_ladder_without_a_query(self):
        Level = self.env['ebshel.followup.level']
        ladder = Level._of_company(self.own)
        ladder.mapped('days')                                   # the ladder is read, as its callers have it
        reminder, chase, final = self.levels
        with counted(self.env.cr) as box:
            waits = [level._wait_days(ladder) for level in (reminder, chase, final)]
        self.assertEqual(box['n'], 0, "the following level is picked from the ladder, not searched for")
        self.assertEqual(waits, [23, 30, final.repeat_days])
        self.assertEqual(waits, [level._wait_days() for level in (reminder, chase, final)],
                         "and agrees with the search it replaces")
        self.assertEqual(reminder._next(ladder), chase)
        self.assertFalse(final._next(ladder))

    def test_sending_to_many_customers_does_not_search_the_levels_for_each(self):
        Level = self.env['ebshel.followup.level']
        # the company's own ladder is this one level: nothing follows it, so it repeats
        Level.search([('company_id', '=', self.company.id)]).write({'active': False})
        letter = Level.create({'name': 'Q letter', 'days': 5, 'action': 'letter', 'repeat_days': 20})
        income = self.env['account.account'].search([('code', '=', 'ZZ7010')], limit=1) or self.env['account.account'].create(
            {'code': 'ZZ7010', 'name': 'Test income', 'account_type': 'income'})
        customers = self.partners[:10]
        for partner in customers:
            move = self.env['account.move'].create({
                'move_type': 'out_invoice', 'partner_id': partner.id, 'invoice_date': self.today - timedelta(days=20),
                'invoice_payment_term_id': False, 'invoice_date_due': self.today - timedelta(days=10),
                'invoice_line_ids': [(0, 0, {'name': 'Service', 'quantity': 1, 'price_unit': 100.0,
                                             'account_id': income.id, 'tax_ids': [(6, 0, [])]})]})
            move.action_post()
        self.env.invalidate_all()
        with queries_on('ebshel_followup_level') as seen:
            result = customers.action_ebshel_send_reminder(level_id=letter.id)
        self.assertEqual(sorted(result['sent']), sorted(customers.ids))
        self.assertLess(len(seen), len(customers),
                        "the level after the one sent was searched for once per customer:\n%s" % '\n'.join(seen))
        self.assertEqual(set(customers.mapped('ebshel_followup_next_date')), {self.today + timedelta(days=20)})
