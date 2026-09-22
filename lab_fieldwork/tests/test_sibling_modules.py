# -*- coding: utf-8 -*-
"""What must still work when the sibling modules are NOT installed.

Three defects found on 2026-09-12 by driving the real UI as a field executive on a
database carrying only `lab_fieldwork` and its declared dependencies. Every one of
them was invisible on the live database, because live has `lab_delivery`,
`lab_whatsapp` and `epg_whatsapp` installed and nobody had ever installed this
module on its own.

The pattern in all three: this module reaches for something a sibling provides
without declaring — or being able to declare — the dependency. These tests run in
exactly the environment that exposes it, which is this module's own test run.
"""
from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged

LAT, LON = 9.98160, 76.57790


@tagged('post_install', '-at_install')
class TestSiblingModulesAreOptional(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        G = cls.env.ref
        cls.route = cls.env['crm.team'].create({'name': 'Test Route'})
        cls.exec_user = cls.env['res.users'].create({
            'name': 'Sibling Exec', 'login': 'fw_sib_exec',
            'group_ids': [(6, 0, [G('base.group_user').id,
                                  G('lab_fieldwork.group_fieldwork_executive').id])]})
        # The route rules confine an executive through the teams they lead.
        cls.route.user_id = cls.exec_user
        cls.env.registry.clear_cache()
        cls.clinic = cls.env['res.partner'].create({
            'name': 'Sibling Clinic', 'is_clinic': True, 'team_id': cls.route.id,
            'phone': '9847011223', 'city': 'Kochi',
            'partner_latitude': LAT, 'partner_longitude': LON})

    # ------------------------------------------------------- lab_delivery
    def test_an_executive_can_open_their_own_visit(self):
        """The visit form carries an Orders page bound to `order_ids`.

        An x2many is read whenever the form is read, visible page or not, so
        without a sale.order grant of its own this module gave every executive an
        AccessError on the central record of the application. The grant used to
        live in lab_delivery, which depends on THIS module — so it could not be
        depended on back, and a plain install of lab_fieldwork was broken for
        its own primary role. (2026-09-12)
        """
        visit = self.env['lab.visit'].create({
            'partner_id': self.clinic.id, 'user_id': self.exec_user.id})
        mine = visit.with_user(self.exec_user)
        # Invalidate first, and this is load-bearing. `create` leaves order_ids in
        # the transaction cache as an empty recordset, and a cached x2many is
        # returned without ever touching sale.order — so the read quietly passes
        # and the test guards nothing. The browser hits a record it did not just
        # create, so it pays for the search, and the search is what raises.
        self.env.invalidate_all()
        try:
            mine.read(['name', 'state', 'order_ids', 'order_count', 'case_ids'])
        except AccessError as exc:
            self.fail("an executive cannot open their own visit: %s" % exc)

    def test_an_executive_may_read_orders_but_not_write_them(self):
        """Read-only is the whole point: an order carries pricing they are not shown."""
        Order = self.env['sale.order'].with_user(self.exec_user)
        Order.check_access('read')
        for mode in ('write', 'create', 'unlink'):
            with self.assertRaises(AccessError, msg="executives may only READ orders"):
                Order.check_access(mode)

    # ------------------------------------------------------ lab_whatsapp
    def test_the_clinic_list_survives_without_lab_whatsapp(self):
        """`res.partner.whatsapp_number` is added by lab_whatsapp, which this
        module does not depend on. Reading it unguarded is an AttributeError, and
        it took the whole Doctors / Clinics panel down with a server error.

        Deliberately NOT skipped where lab_whatsapp IS installed. A test that
        skips itself on the fuller database and runs only here is one edit away
        from skipping everywhere and guarding nothing, which is exactly how five
        tests in lab_track went quietly dead. With the field present this is a
        plain "the panel returns rows" regression; the missing-field branch it
        was written for is proved in this module's own suite, where the sibling
        is absent.
        """
        result = self.env['lab.my.day'].with_user(self.exec_user).get_clinics()
        self.assertFalse(result.get('no_route'), "the executive leads a route")
        names = [row['name'] for row in result['rows']]
        self.assertIn(self.clinic.display_name, names)

    def test_wa_source_reads_the_whatsapp_number_where_there_is_one(self):
        """The WhatsApp Number where lab_whatsapp adds it - never the phone -
        and the phone where it does not. Never raises either way."""
        from odoo.addons.lab_fieldwork.models.lab_visit import _wa_source
        if 'whatsapp_number' in self.clinic._fields:
            self.assertFalse(_wa_source(self.clinic),
                             "a clinic with only a phone has no WhatsApp number")
            self.clinic.whatsapp_number = '+91 98470 55555'
            self.assertEqual(_wa_source(self.clinic), '+91 98470 55555')
        else:
            self.assertEqual(_wa_source(self.clinic), '9847011223')
        blank = self.env['res.partner'].create({'name': 'No Phone'})
        self.assertFalse(_wa_source(blank))

    # ---------------------------------------------------------- petty_cash
    def _float_for(self, user, amount=5000.0):
        """An allocated petty-cash float in the holder's hands."""
        journal = self.env['account.journal'].create({
            'name': 'Test Petty Cash', 'type': 'cash', 'code': 'TPTY',
            'company_id': self.env.company.id, 'is_petty_cash': True})
        allocation = self.env['petty.cash.allocation'].create({
            'partner_id': user.partner_id.id,
            'journal_id': journal.id,
            'amount_limit': amount,
            'company_id': self.env.company.id})
        # The approval chain is an accounts job and refuses to run for somebody
        # else; this test is about what happens once the float exists.
        allocation.state = 'allocated'
        return allocation

    def _finished_visit_with_cash(self, amount=2500.0):
        """A completed visit carrying cash, as the executive sees it.

        Built directly rather than through do_check_in / do_check_out: those
        require an employee record and an open attendance, which is a different
        feature's precondition and would make this test fail for a reason that
        has nothing to do with the money.
        """
        visit = self.env['lab.visit'].create({
            'partner_id': self.clinic.id, 'user_id': self.exec_user.id,
            'outcome': 'payment', 'collected': amount, 'pay_mode': 'cash'})
        now = fields.Datetime.now()
        visit.write({'state': 'done', 'check_in': now, 'check_out': now})
        mine = visit.with_user(self.exec_user)
        self.assertEqual(mine.state, 'done')
        return mine

    def test_an_executive_can_bank_their_own_collection(self):
        """Cash into My Float, end to end, as the person who pressed it.

        Two defects sat on this one button, and the first hid the second:

        1. `action_cash_to_float` creates the transaction with sudo, so
           `action_post`'s `has_group` check saw OdooBot — which holds no petty
           cash group — and refused. Odoo 19's `has_group` has no superuser
           shortcut, so this failed for everybody, every time.
        2. With that fixed, the very next line stamps `cash_txn_id` on a visit
           that is normally already Completed, and the lock mixin refuses it:
           `cash_txn_id` is not one of the fields a completed visit leaves open.

        On the live database when this was found: 4 visits had collected cash
        and 0 had ever reached a float. (2026-09-12)
        """
        self._float_for(self.exec_user)
        visit = self._finished_visit_with_cash(2500.0)
        self.assertFalse(visit.cash_banked, "nothing banked yet")

        visit.action_cash_to_float()

        self.assertTrue(visit.cash_banked, "the visit says the money has moved")
        txn = visit.cash_txn_id.sudo()
        self.assertEqual(txn.state, 'posted')
        self.assertEqual(txn.type, 'collection')
        self.assertEqual(txn.amount, 2500.0)
        self.assertEqual(txn.visit_id, visit)

    def test_banking_the_same_visit_twice_does_not_double_count(self):
        """It returns the existing movement rather than making a second one."""
        self._float_for(self.exec_user)
        visit = self._finished_visit_with_cash(1200.0)
        visit.action_cash_to_float()
        first = visit.cash_txn_id
        visit.action_cash_to_float()
        self.assertEqual(visit.cash_txn_id, first, "same movement, not a new one")
        self.assertEqual(
            self.env['petty.cash.transaction'].sudo().search_count(
                [('visit_id', '=', visit.id)]), 1)

    def test_a_person_still_cannot_post_a_transaction_themselves(self):
        """The guard is about who the USER is, so it must still bite for one.

        Honouring `env.su` widens this to system code only. An executive acting
        as themselves is exactly who it was written to stop.
        """
        allocation = self._float_for(self.exec_user)
        txn = self.env['petty.cash.transaction'].sudo().create({
            'allocation_id': allocation.id,
            'partner_id': self.clinic.id,
            'type': 'collection',
            'amount': 100.0,
            'company_id': self.env.company.id})
        txn.action_approve()
        with self.assertRaises(UserError, msg="an executive is not an officer"):
            txn.with_user(self.exec_user).action_post()
