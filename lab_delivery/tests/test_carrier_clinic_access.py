# -*- coding: utf-8 -*-
from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import Form, TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestCarrierSeesTheClinic(TransactionCase):
    """An executive carrying work to a clinic on someone else's route opened the
    delivery and the visit, and was refused the clinic on both: "doesn't have
    'read' access to Contact". The clinics they carry work to or have been to are
    theirs to read; every other clinic off their route stays closed.
    (client, 2026-09-15)"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Partner = cls.env['res.partner']
        cls.carrier = cls.env['res.users'].create({
            'name': 'Carrier Executive', 'login': 'carrier_exec',
            # An internal user, as every real executive is.
            'group_ids': [(6, 0, [
                cls.env.ref('base.group_user').id,
                cls.env.ref('lab_fieldwork.group_fieldwork_executive').id])]})
        cls.env['crm.team'].create({'name': 'Carrier Home Route', 'user_id': cls.carrier.id})
        away = cls.env['crm.team'].create({'name': 'Colleague Route'})
        cls.clinic = Partner.create({
            'name': 'Off Route Clinic', 'is_company': True, 'team_id': away.id})
        cls.doctor = Partner.create({'name': 'Off Route Doctor', 'parent_id': cls.clinic.id})
        cls.stranger = Partner.create({
            'name': 'Untouched Clinic', 'is_company': True, 'team_id': away.id})
        cls.product = cls.env['product.product'].create({
            'name': 'Carried Appliance', 'type': 'consu', 'list_price': 100.0})

    def _can_read(self, partner):
        self.env.flush_all()
        try:
            partner.with_user(self.carrier).check_access('read')
        except AccessError:
            return False
        return True

    def test_a_clinic_off_the_route_is_closed_without_work_there(self):
        self.assertFalse(self._can_read(self.clinic))
        self.assertFalse(self._can_read(self.doctor))

    def test_a_delivery_carried_there_opens_the_clinic_and_its_doctor(self):
        order = self.env['sale.order'].create({
            'partner_id': self.clinic.id,
            'order_line': [(0, 0, {'product_id': self.product.id, 'product_uom_qty': 1})]})
        self.env['lab.delivery'].create({
            'sale_order_id': order.id, 'executive_id': self.carrier.id,
            'scheduled_date': fields.Datetime.now()})
        self.assertTrue(self._can_read(self.clinic))
        self.assertTrue(self._can_read(self.doctor))
        self.assertFalse(self._can_read(self.stranger))

    def test_a_visit_there_opens_the_clinic_on_the_visit_form(self):
        visit = self.env['lab.visit'].create({
            'partner_id': self.clinic.id, 'contact_id': self.doctor.id,
            'user_id': self.carrier.id})
        self.env.flush_all()
        values = visit.with_user(self.carrier).read(['partner_id', 'contact_id', 'call_number'])
        self.assertEqual(values[0]['partner_id'][0], self.clinic.id)
        self.assertFalse(self._can_read(self.stranger))


@tagged('post_install', '-at_install')
class TestHandOverOffRoute(TransactionCase):
    """Hand Over Work pressed on a visit to a clinic off the executive's route
    raised "no read access to Contact": the wizard read every clinic with work
    waiting, company-wide, as the executive - and the order list behind it was
    closed to them too. (client, 2026-09-15)"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Partner = cls.env['res.partner']
        cls.carrier = cls.env['res.users'].create({
            'name': 'Hand Over Executive', 'login': 'handover_exec',
            # An internal user, as every real executive is.
            'group_ids': [(6, 0, [
                cls.env.ref('base.group_user').id,
                cls.env.ref('lab_fieldwork.group_fieldwork_executive').id])]})
        cls.env['crm.team'].create({'name': 'Hand Over Home Route', 'user_id': cls.carrier.id})
        away = cls.env['crm.team'].create({'name': 'Hand Over Colleague Route'})
        cls.clinic = Partner.create({
            'name': 'Hand Over Away Clinic', 'is_company': True, 'team_id': away.id})
        cls.doctor = Partner.create({'name': 'Hand Over Doctor', 'parent_id': cls.clinic.id})
        cls.elsewhere = Partner.create({
            'name': 'Elsewhere Waiting Clinic', 'is_company': True, 'team_id': away.id})
        cls.product = cls.env['product.product'].create({
            'name': 'Hand Over Appliance', 'type': 'consu', 'list_price': 100.0})
        cls.order = cls._confirmed(cls.doctor)
        cls.elsewhere_order = cls._confirmed(cls.elsewhere)
        cls.visit = cls.env['lab.visit'].create({
            'partner_id': cls.clinic.id, 'user_id': cls.carrier.id})

    @classmethod
    def _confirmed(cls, partner):
        order = cls.env['sale.order'].create({
            'partner_id': partner.id,
            'order_line': [(0, 0, {'product_id': cls.product.id, 'product_uom_qty': 1})]})
        order.write({'state': 'sale'})
        return order

    def test_hand_over_on_an_off_route_visit(self):
        self.env.flush_all()
        action = self.visit.with_user(self.carrier).action_hand_over()
        wizard_form = Form(self.env[action['res_model']].with_user(self.carrier)
                           .with_context(**action['context']))
        self.assertIn(self.order.id, wizard_form.candidate_order_ids.ids)
        # The checkbox widget lists the candidates with a search as the executive.
        listed = self.env['sale.order'].with_user(self.carrier).search(
            [('id', 'in', wizard_form.candidate_order_ids.ids)])
        self.assertIn(self.order, listed)
        wizard_form.order_ids.add(self.order)
        result = wizard_form.save().action_create()
        delivery = self.env['lab.delivery'].browse(result['res_id'])
        self.assertEqual(delivery.sale_order_id, self.order)
        self.assertEqual(delivery.executive_id, self.carrier)

    def test_work_elsewhere_stays_closed(self):
        self.env.flush_all()
        self.assertFalse(self.env['sale.order'].with_user(self.carrier).search(
            [('id', '=', self.elsewhere_order.id)]))
        with self.assertRaises(AccessError):
            self.elsewhere.with_user(self.carrier).check_access('read')
