# -*- coding: utf-8 -*-
"""What a field executive may read, proven AS the executive (Odoo 20).

In Odoo 19 two Executive-group record rules confined an executive's contacts to
their round, and a third confined their menus to Field Work. Odoo 20's ir.access
turns a row with a group into a PERMISSION, OR-ed with base's "internal users
read every contact" - so the rows the converter produced confined nobody, and an
executive read all 6,000 contacts and every app again. They are now restrictions
(no group) whose domain bites only for a plain executive; these tests are the
proof, run as the people concerned, never as admin (admin bypasses all of it).
"""
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestPartnerAccess(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        G = cls.env.ref
        base = G('base.group_user').id

        def user(login, *groups):
            return cls.env['res.users'].create({
                'name': login.replace('_', ' ').title(), 'login': login,
                'group_ids': [(6, 0, [base] + [G(g).id for g in groups])]})

        cls.exec_a = user('pa_exec_a', 'lab_fieldwork.group_fieldwork_executive')
        cls.exec_b = user('pa_exec_b', 'lab_fieldwork.group_fieldwork_executive')
        cls.ops = user('pa_ops', 'lab_fieldwork.group_fieldwork_ops_manager')
        cls.mkt = user('pa_mkt', 'lab_fieldwork.group_fieldwork_marketing_manager')
        cls.sysadmin_exec = user('pa_sys', 'lab_fieldwork.group_fieldwork_executive',
                                 'base.group_system')
        Team = cls.env['crm.team']
        cls.route_a = Team.create({'name': 'PA Route A', 'user_id': cls.exec_a.id})
        cls.route_b = Team.create({'name': 'PA Route B', 'user_id': cls.exec_b.id})
        Partner = cls.env['res.partner']
        cls.clinic_a = Partner.create({'name': 'PA Clinic A', 'is_clinic': True,
                                       'is_company': True, 'team_id': cls.route_a.id,
                                       'phone': '+91 90000 11111'})
        cls.doctor_a = Partner.create({'name': 'PA Doctor A', 'parent_id': cls.clinic_a.id})
        cls.clinic_b = Partner.create({'name': 'PA Clinic B', 'is_clinic': True,
                                       'is_company': True, 'team_id': cls.route_b.id,
                                       'phone': '+91 90000 22222'})
        cls.doctor_b = Partner.create({'name': 'PA Doctor B', 'parent_id': cls.clinic_b.id})
        cls.stranger = Partner.create({'name': 'PA Vendor Nobody Visits'})
        cls.mine = cls.clinic_a | cls.doctor_a | cls.clinic_b | cls.doctor_b | cls.stranger

    def _seen(self, user):
        return self.env['res.partner'].with_user(user).search([('id', 'in', self.mine.ids)])

    # ------------------------------------------------------------ executives
    def test_an_executive_reads_only_their_own_route(self):
        seen = self._seen(self.exec_a)
        self.assertEqual(seen, self.clinic_a | self.doctor_a,
                         "own route's clinic and its doctor - nothing else")
        with self.assertRaises(AccessError):
            self.clinic_b.with_user(self.exec_a).read(['name'])
        with self.assertRaises(AccessError):
            self.stranger.with_user(self.exec_a).read(['name'])

    def test_an_executive_still_reads_the_people_the_system_speaks_as(self):
        """Chatter authors: colleagues, the company, their own contact card."""
        Partner = self.env['res.partner'].with_user(self.exec_a)
        wanted = (self.exec_a.partner_id | self.exec_b.partner_id
                  | self.env.company.partner_id)
        self.assertEqual(Partner.search([('id', 'in', wanted.ids)]), wanted)

    def test_a_clinic_visited_off_the_route_opens_on_the_visit_form(self):
        """Covering a colleague's clinic: the visit form must show its clinic
        (and the doctor), or the executive's central record will not open."""
        visit = self.env['lab.visit'].create({
            'partner_id': self.clinic_b.id, 'user_id': self.exec_a.id,
            'purpose': 'round'})
        self.assertIn(self.clinic_b, self._seen(self.exec_a))
        self.assertIn(self.doctor_b, self._seen(self.exec_a))
        self.assertNotIn(self.stranger, self._seen(self.exec_a))
        # Every field the form shows, read as the executive - many2ones read the
        # clinic's name, computes like the call number read its phone.
        Visit = self.env['lab.visit'].with_user(self.exec_a)
        form = Visit.get_views([(False, 'form')])
        fields = [f for f in form['models']['lab.visit']['fields'] if f in Visit._fields]
        values = Visit.browse(visit.id).read(fields)[0]
        self.assertEqual(values['partner_id'][0], self.clinic_b.id)
        # ...and the colleague whose route it is still reads it too.
        self.assertIn(self.clinic_b, self._seen(self.exec_b))

    def test_a_route_handed_over_moves_the_clinics_at_once(self):
        """No stale access cache: the round is resolved at search time."""
        self.assertNotIn(self.clinic_b, self._seen(self.exec_a))
        self.route_b.user_id = self.exec_a
        self.assertIn(self.clinic_b, self._seen(self.exec_a))

    def test_an_executive_sees_one_app(self):
        Menu = self.env['ir.ui.menu']
        root = self.env.ref('lab_fieldwork.menu_fieldwork_root')
        roots = Menu.with_user(self.exec_a).search([('parent_id', '=', False)])
        self.assertEqual(roots._filter_visible_menus(), root,
                         "an executive's home screen is Field Work alone")
        self.assertIn(root, Menu.with_user(self.ops).search(
            [('parent_id', '=', False)])._filter_visible_menus())

    # ------------------------------------------------------------ supervisors
    def test_both_manager_desks_read_every_clinic(self):
        for manager in (self.ops, self.mkt):
            self.assertEqual(self._seen(manager), self.mine, manager.login)

    def test_an_administrator_who_holds_executive_is_not_confined(self):
        """The 19 'all' twin for system / user administrators, now inside the
        restriction's own domain."""
        self.assertEqual(self._seen(self.sysadmin_exec), self.mine)
        roots = self.env['ir.ui.menu'].with_user(self.sysadmin_exec).search(
            [('parent_id', '=', False)])
        self.assertGreater(len(roots._filter_visible_menus()), 1)

    def test_someone_with_no_field_role_is_untouched(self):
        plain = self.env['res.users'].create({
            'name': 'PA Plain', 'login': 'pa_plain',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        self.assertEqual(self._seen(plain), self.mine)
