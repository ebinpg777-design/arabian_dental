# -*- coding: utf-8 -*-
"""What a field executive is shown of the room.

The company decides whether its executives see the leaderboards - Field Work >
Settings - and the methods behind the Collections screen keep an executive to
their own routes and their own row however they are asked. (client, 2026-10-22)
"""
from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestExecutiveBoards(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ref = cls.env.ref
        cls.company = cls.env.company
        Users = cls.env['res.users'].with_context(no_reset_password=True)
        cls.executive = Users.create({
            'name': 'Boards Exec', 'login': 'boards.exec@test',
            'group_ids': [(6, 0, [ref('base.group_user').id,
                                  ref('sales_team.group_sale_salesman').id,
                                  ref('lab_fieldwork.group_fieldwork_executive').id])]})
        cls.rival = Users.create({
            'name': 'Boards Rival', 'login': 'boards.rival@test',
            'group_ids': [(6, 0, [ref('base.group_user').id,
                                  ref('sales_team.group_sale_salesman').id])]})
        cls.office = Users.create({
            'name': 'Boards Office', 'login': 'boards.office@test',
            'group_ids': [(6, 0, [ref('base.group_user').id,
                                  ref('account.group_account_readonly').id])]})
        cls.fw_admin = Users.create({
            'name': 'Boards FW Admin', 'login': 'boards.fwadmin@test',
            'group_ids': [(6, 0, [ref('base.group_user').id,
                                  ref('lab_fieldwork.group_fieldwork_admin').id])]})
        cls.route = cls.env['crm.team'].create({'name': 'Boards Route',
                                                'user_id': cls.executive.id})
        cls.rival_route = cls.env['crm.team'].create({'name': 'Boards Rival Route',
                                                      'user_id': cls.rival.id})
        Partner = cls.env['res.partner']
        cls.clinic = Partner.create({'name': 'Boards Clinic', 'team_id': cls.route.id})
        cls.rival_clinic = Partner.create({'name': 'Boards Rival Clinic',
                                           'team_id': cls.rival_route.id})
        cls.product = cls.env['product.product'].create(
            {'name': 'Boards Crown', 'lst_price': 1000.0})
        # Last month's invoicing, this month's receipts. Not a fixed January: on the
        # lab's own ledger the invoice numbering has moved on to this financial
        # year, and an older date will not post.
        this_month = fields.Date.context_today(cls.env['res.users']).replace(day=1)
        last_month = this_month - relativedelta(months=1)
        cls.day = last_month + relativedelta(days=11)
        cls.options = {'sales_from': str(last_month),
                       'sales_to': str(this_month - relativedelta(days=1)),
                       'pay_from': str(this_month),
                       'pay_to': str(this_month + relativedelta(months=1, days=-1))}
        # The rival out-sells the executive - and, on the lab's own ledger, anybody
        # real that month - so a rank of #1 can only be wrong.
        cls._invoice(cls.clinic, cls.executive, 300.0, cls.route)
        cls._invoice(cls.rival_clinic, cls.rival, 50000000.0, cls.rival_route)
        cls.Perf = cls.env['lab.collection.performance']

    @classmethod
    def _invoice(cls, partner, user, amount, team):
        invoice = cls.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': partner.id,
            'invoice_user_id': user.id, 'team_id': team.id,
            'invoice_date': cls.day, 'date': cls.day,
            'company_id': cls.company.id,
            'invoice_line_ids': [(0, 0, {
                'product_id': cls.product.id, 'quantity': 1, 'price_unit': amount,
                'tax_ids': [(6, 0, [])]})],
        })
        invoice.action_post()
        return invoice

    def _screen(self, user):
        return self.Perf.with_user(user).dashboard_data(
            self.options, with_trend=False, with_extras=True)

    def _paper(self, user):
        return self.env['report.lab_collections.report_collection'].with_user(
            user)._get_report_values([], {'options': self.options, 'tab': 'user'})

    # ------------------------------------------------------------- the switch
    def test_executives_see_the_boards_until_the_company_says_otherwise(self):
        self.assertTrue(self.company.lab_collection_executive_boards,
                        "on by default: nothing changes until somebody decides")
        boards = self._screen(self.executive)['boards']
        self.assertIn('top_sales', boards)
        self.assertIn(self.rival.id, [e['key'] for e in boards['top_sales']],
                      "a ranking includes the room")

    def test_switched_off_an_executive_sees_no_boards_and_no_rank(self):
        self.company.lab_collection_executive_boards = False
        data = self._screen(self.executive)
        self.assertIsNone(data['boards'])
        [own] = data['people']
        self.assertEqual(own['key'], self.executive.id)
        self.assertEqual((own['rank_sales'], own['rank_collected']), (0, 0))
        Perf = self.Perf.with_user(self.executive)
        self.assertIsNone(Perf.extras(self.options)['boards'])
        self.assertEqual(Perf.leaderboards(self.options), {})
        self.assertEqual(self._paper(self.executive)['boards'], {},
                         "nor on the PDF")

    def test_switched_off_the_office_still_sees_the_boards(self):
        self.company.lab_collection_executive_boards = False
        self.assertIn('top_sales', self._screen(self.office)['boards'])
        self.assertIn('top_sales', self.Perf.with_user(self.office).leaderboards(self.options))
        self.assertIn('top_sales', self._paper(self.office)['boards'])

    # ------------------------------------------------------------- the rank
    def test_an_executive_is_ranked_against_the_room(self):
        """Ranked over their own row alone, every executive read #1."""
        office = {p['key']: p for p in self._screen(self.office)['people']}
        [own] = self._screen(self.executive)['people']
        self.assertGreater(office[self.executive.id]['rank_sales'], 1,
                           "fixture: the rival sold more")
        self.assertEqual(own['rank_sales'], office[self.executive.id]['rank_sales'])
        self.assertEqual(own['rank_collected'],
                         office[self.executive.id]['rank_collected'])

    # ------------------------------------------------------------- asked directly
    def test_asked_directly_an_executive_gets_only_their_own(self):
        """The screen fenced these; the methods behind it did not. (2026-10-22)"""
        Perf = self.Perf.with_user(self.executive)
        OfficePerf = self.Perf.with_user(self.office)
        self.assertIn(self.rival_route.id,
                      [r['key'] for r in OfficePerf.report_rows('team_id', self.options)],
                      "fixture: the office sees the rival's route")
        self.assertEqual({r['key'] for r in Perf.report_rows('team_id', self.options)},
                         {self.route.id})
        self.assertEqual({r['key'] for r in Perf.report_rows('user_id', self.options)},
                         {self.executive.id})
        self.assertEqual([p['key'] for p in Perf.performance(self.options)],
                         [self.executive.id])
        self.assertEqual([p['key'] for p in Perf.extras(self.options)['people']],
                         [self.executive.id])
        self.assertLessEqual(set(Perf.trend_data(self.options)) - {'total'},
                             {self.route.id})

    def test_asked_directly_an_executive_gets_their_own_book_only(self):
        mine = self.Perf.with_user(self.executive).outstanding_summary(top_debtors=50)
        self.assertEqual([d['id'] for d in mine['debtors']], [self.clinic.id])
        self.assertAlmostEqual(mine['total'], 300.0, 2)
        whole = self.Perf.with_user(self.office).outstanding_summary()
        self.assertGreaterEqual(whole['total'], 50000300.0, "the office reads the whole book")

    # ------------------------------------------------------------- the open items
    def test_an_executives_open_items_are_their_round_and_theirs_alone(self):
        """A card drill wrote every clinic's open items as the executive's rows, and
        the executive could read every row in the table. (2026-10-22)"""
        Item = self.env['lab.collection.open.item']
        office = self.Perf.with_user(self.office).action_drill('open', options=self.options)
        self.assertIn(self.rival_clinic, Item.search(office['domain']).partner_id,
                      "fixture: the office's drill has the rival's clinic")
        mine = self.Perf.with_user(self.executive).action_drill('open', options=self.options)
        rows = Item.search(mine['domain'])
        self.assertIn(self.clinic, rows.partner_id)
        self.assertEqual(Item.search([('id', 'in', rows.ids)]).team_id, self.route,
                         "the drill wrote their round only")
        readable = Item.with_user(self.executive).search([])
        self.assertEqual(readable.create_uid, self.executive,
                         "nothing of the office's drill is theirs to read")
        self.assertNotIn(self.rival_clinic, readable.sudo().partner_id)

    # ------------------------------------------------------------- the settings page
    def test_the_field_work_administrator_sets_it_for_the_company(self):
        Settings = self.env['lab.fieldwork.settings'].with_user(self.fw_admin)
        self.assertTrue(Settings.default_get(['fw_executive_boards'])['fw_executive_boards'])
        Settings.create({'fw_executive_boards': False}).action_save()
        self.assertFalse(self.company.lab_collection_executive_boards)
        self.assertFalse(Settings.default_get(['fw_executive_boards'])['fw_executive_boards'])
        self.assertIsNone(self._screen(self.executive)['boards'])
