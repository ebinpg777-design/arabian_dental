# -*- coding: utf-8 -*-
"""Who may pull the collection figures, and for which company and round.

Every figure is raw SQL over the ledger, which no ACL or record rule ever sees, and
the methods are public RPCs. The menus were the only fence: any internal login
could ask for the whole receivable book, for any company id it cared to name, and
an executive's PDF printed every route. (2026-09-15)
"""
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestCollectionsAccessGate(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Perf = cls.env['lab.collection.performance']
        base = cls.env.ref('base.group_user').id

        def user(login, *xmlids):
            return cls.env['res.users'].create({
                'name': login, 'login': login,
                'group_ids': [(6, 0, [base] + [cls.env.ref(x).id for x in xmlids])]})

        cls.outsider = user('gate.outsider@test')
        cls.accountant = user('gate.accountant@test', 'account.group_account_readonly')
        cls.executive = user('gate.executive@test',
                             'lab_fieldwork.group_fieldwork_executive',
                             'sales_team.group_sale_salesman')
        cls.other_company = cls.env['res.company'].create({'name': 'Gate Other Co'})

    def test_a_login_with_no_collections_role_is_refused(self):
        Perf = self.Perf.with_user(self.outsider)
        calls = {
            'dashboard_data': lambda: Perf.dashboard_data({}, with_trend=False),
            'report_rows': lambda: Perf.report_rows('team_id', {}),
            'extras': lambda: Perf.extras({}),
            'outstanding_summary': lambda: Perf.outstanding_summary(),
            'action_drill': lambda: Perf.action_drill('open'),
        }
        for name, call in calls.items():
            with self.subTest(method=name), self.assertRaises(AccessError):
                call()

    def test_the_accounts_desk_still_reads_them(self):
        summary = self.Perf.with_user(self.accountant).outstanding_summary()
        self.assertIn('total', summary)

    def test_a_company_the_viewer_does_not_belong_to_is_refused(self):
        Perf = self.Perf.with_user(self.accountant)
        self.assertNotIn(self.other_company, self.accountant.company_ids)
        with self.assertRaises(AccessError):
            Perf.report_rows('team_id', {'company_id': self.other_company.id})
        with self.assertRaises(AccessError):
            Perf.outstanding_summary(company=self.other_company.id)
        self.assertIsInstance(
            Perf.report_rows('team_id', {'company_id': self.env.company.id}), list)

    def test_the_pdf_keeps_an_executive_to_their_own_round(self):
        Render = self.env['report.lab_collections.report_collection']
        everyone = Render.with_user(self.accountant)._get_report_values(
            [], {'options': {}})['rows']
        routes = [r['key'] for r in everyone if r['key']]
        self.assertTrue(routes, "fixture: last month has routed invoices to print")
        route = self.env['crm.team'].browse(routes[0])
        self.assertTrue([r for r in everyone if r['key'] != route.id],
                        "fixture: more on the company PDF than this one route")
        # As the route's leader: adding a member re-checks every existing
        # membership on a real route and trips the one-team rule for them.
        route.sudo().user_id = self.executive
        mine = Render.with_user(self.executive)._get_report_values(
            [], {'options': {}})['rows']
        self.assertTrue({r['key'] for r in mine} <= {route.id},
                        "an executive's PDF prints their own route only")
