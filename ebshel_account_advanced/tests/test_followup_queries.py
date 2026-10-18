# -*- coding: utf-8 -*-
"""The follow-up column, and sending, must not ask the database once per row.

`_ebshel_judge` resolves which follow-up level a customer has reached, and every
caller of it is a loop: the column on a partner list, the collections desk, the
nightly job. Ortho found it SEARCHING the level table for each one - a 200-row list
issued 118 searches for an answer that is identical every time (Ortho 9b330cd,
2026-10-04). The lab's Odoo 20 review had fixed the same fault its own way: the
ladder is read once per call (`Level._of_company`) and handed to every judgement.
Sending missed it: the level after the one sent (`_next`) was still searched for once
per customer, by the nightly job and the desk's Send alike; it takes the ladder now.

Asserted as a query COUNT against the level table, because that is the thing that
regresses: the figures were always right, they were just bought one query at a time.
"""
from contextlib import contextmanager
from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase, tagged
from odoo.tools import SQL


@contextmanager
def counted(cr, needle):
    """Count the queries issued inside the block, and those that name `needle`."""
    box = {'n': 0, 'hits': 0}
    original = cr.execute

    def spy(query, params=None, log_exceptions=True):
        box['n'] += 1
        text = query._sql_tuple[0] if isinstance(query, SQL) else str(query)
        if needle in text:
            box['hits'] += 1
            box.setdefault('seen', []).append(text)
        return original(query, params, log_exceptions)

    cr.execute = spy
    try:
        yield box
    finally:
        cr.execute = original


@tagged('post_install', '-at_install')
class TestFollowupQueries(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company.write({'fiscalyear_lock_date': False, 'tax_lock_date': False, 'sale_lock_date': False,
                           'ebshel_followup_min_amount': 0.0})
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
        Account = cls.env['account.account']
        cls.income = Account.search([('code', '=', 'ZZ7011')], limit=1) or Account.create(
            {'code': 'ZZ7011', 'name': 'Ladder test income', 'account_type': 'income'})
        # customers who are really overdue - with nothing overdue no level is ever looked up,
        # and a count over them would pass whatever the ladder lookup cost
        cls.partners = cls.env['res.partner'].create(
            [{'name': 'Query Clinic %d' % i, 'customer_rank': 1, 'property_payment_term_id': False} for i in range(10)])
        day = cls.today - timedelta(days=40)
        moves = cls.env['account.move'].create([{
            'move_type': 'out_invoice', 'partner_id': partner.id, 'invoice_date': day, 'date': day,
            'invoice_payment_term_id': False, 'invoice_date_due': day,
            'invoice_line_ids': [(0, 0, {'name': 'Crown', 'quantity': 1, 'price_unit': 100 + i,
                                         'account_id': cls.income.id, 'tax_ids': [(6, 0, [])]})],
        } for i, partner in enumerate(cls.partners)])
        moves.action_post()

    def test_the_level_ladder_is_read_once_not_once_per_customer(self):
        self.env.flush_all()
        self.partners.invalidate_recordset()
        # the table, quoted as the ORM writes it - not res_partner's ebshel_followup_level_id column
        with counted(self.env.cr, 'FROM "ebshel_followup_level"') as box:
            self.partners._compute_ebshel_followup()
        self.assertTrue(all(p.ebshel_overdue_total > 0 for p in self.partners),
                        "the fixture must be overdue, or no level is looked up and the count proves nothing")
        self.assertLessEqual(
            box['hits'], 3,
            "10 overdue customers must not cost a ladder query each - Ortho measured 118 of them "
            "on a 200-row list, all the same search against ebshel_followup_level")

    def test_the_ladder_still_answers_the_same(self):
        """Read once, but not wrong: the level reached is the highest at or below - by
        a search, and by the ladder read once and handed in."""
        Level = self.env['ebshel.followup.level']
        ladder = Level._of_company(self.own)
        for days, expected in ((0, False), (6, False), (7, 'Reminder'),
                               (29, 'Reminder'), (30, 'Chase'), (90, 'Final')):
            for how, reached in (('searched', Level._for_days(days, self.own)),
                                 ('read once', Level._for_days(days, self.own, levels=ladder))):
                self.assertEqual(reached.name if reached else False, expected,
                                 'at %s days overdue (%s)' % (days, how))

    def test_changing_a_level_is_noticed(self):
        """Whatever is ever cached here, editing the ladder must be seen at once."""
        Level = self.env['ebshel.followup.level']
        self.assertEqual(Level._for_days(45, self.own).name, 'Chase')
        self.levels.filtered(lambda l: l.name == 'Final').write({'days': 40})
        self.assertEqual(Level._for_days(45, self.own).name, 'Final', 'the level moved')
        self.assertEqual(Level._for_days(45, self.own, levels=Level._of_company(self.own)).name, 'Final')

    def test_the_next_level_comes_from_the_ladder_without_a_query(self):
        Level = self.env['ebshel.followup.level']
        ladder = Level._of_company(self.own)
        ladder.mapped('days')                                   # read, as its callers have it
        reminder, chase, final = self.levels
        with counted(self.env.cr, 'FROM "ebshel_followup_level"') as box:
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
        self.env.flush_all()
        self.env.invalidate_all()
        with counted(self.env.cr, 'FROM "ebshel_followup_level"') as box:
            result = self.partners.action_ebshel_send_reminder(level_id=letter.id)
        self.assertEqual(sorted(result['sent']), sorted(self.partners.ids))
        self.assertLess(box['hits'], len(self.partners),
                        "the level after the one sent was searched for once per customer:\n%s"
                        % '\n'.join(box.get('seen', [])))
        self.assertEqual(set(self.partners.mapped('ebshel_followup_next_date')), {self.today + timedelta(days=20)})
