# -*- coding: utf-8 -*-
"""Epic D-1 — urgency (R10), and the field-raised doctor query reaching the gate.

`is_urgent_open` is what every urgent filter, menu and KPI tile reads, so the thing
worth pinning is that it goes false the moment the work stops being outstanding —
otherwise the "urgent" list slowly fills with finished jobs and stops being read.
"""
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestEpicD1Urgency(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.clinic = cls.env['res.partner'].create({'name': 'D1 Clinic'})
        cls.product = cls.env['product.product'].create({
            'name': 'D1 Appliance', 'type': 'consu', 'list_price': 100.0})

    def _order(self, priority='normal'):
        return self.env['sale.order'].create({
            'partner_id': self.clinic.id, 'priority': priority,
            'order_line': [(0, 0, {'product_id': self.product.id,
                                   'product_uom_qty': 1})]})

    def test_emergency_is_available_without_losing_the_existing_levels(self):
        """Extended, not renumbered — the old keys still work."""
        keys = dict(self.env['sale.order']._fields['priority'].selection)
        for expected in ('low', 'normal', 'urgent', 'emergency'):
            self.assertIn(expected, keys)

    def test_urgent_open_is_true_only_while_the_work_is_outstanding(self):
        order = self._order('urgent')
        self.assertTrue(order.is_urgent_open)
        order.state = 'cancel'
        order.invalidate_recordset()
        self.assertFalse(
            order.is_urgent_open,
            "a cancelled job must drop off the urgent list, or the list fills with "
            "work nobody has to do and stops being read")

    def test_a_normal_order_is_never_urgent_open(self):
        self.assertFalse(self._order('normal').is_urgent_open)

    def test_emergency_counts_as_urgent_open(self):
        self.assertTrue(self._order('emergency').is_urgent_open)

    def test_setting_emergency_warns_before_it_takes_effect(self):
        order = self._order('normal')
        order.priority = 'emergency'
        warning = order._onchange_priority_emergency()
        self.assertTrue(warning and warning.get('warning'),
                        "emergency has to be a deliberate act, not a mis-click")


@tagged('post_install', '-at_install')
class TestCaseDoctorCallHandover(TransactionCase):
    """A query raised at the clinic must reach the verification gate.

    It used to travel only as prose inside the order's instructions, which nothing
    reads programmatically — so the banner never lit and the gate never fired.
    """

    def test_a_case_needing_a_call_sets_the_flag_on_its_order(self):
        case = self.env['lab.case']
        if 'lab.case' not in self.env:
            self.skipTest("lab_fieldwork not installed")
        vals_fn = getattr(case, '_order_vals', None)
        if not vals_fn:
            self.skipTest("case does not expose _order_vals")

        clinic = self.env['res.partner'].create({'name': 'Handover Clinic'})
        visit = self.env['lab.visit'].create({
            'partner_id': clinic.id, 'user_id': self.env.user.id})
        slip = self.env['lab.case'].create({
            'visit_id': visit.id, 'partner_id': clinic.id, 'patient': 'Test Patient',
            'needs_doctor_call': True, 'doctor_call_note': 'Confirm the shade',
        })
        vals = slip._order_vals()
        self.assertTrue(vals.get('call_doctor_required'))
        self.assertEqual(vals.get('call_doctor_note'), 'Confirm the shade')
