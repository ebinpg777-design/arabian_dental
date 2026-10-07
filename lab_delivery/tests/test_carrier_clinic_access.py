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

    def test_a_box_they_raised_for_a_colleague_keeps_the_clinic_open(self):
        """The second half of the carried clause: the executive who RAISED the
        delivery and handed it to a colleague still reads where it went."""
        colleague = self.env['res.users'].create({
            'name': 'Colleague Executive', 'login': 'colleague_exec',
            'group_ids': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref('lab_fieldwork.group_fieldwork_executive').id])]})
        order = self.env['sale.order'].create({
            'partner_id': self.clinic.id,
            'order_line': [(0, 0, {'product_id': self.product.id, 'product_uom_qty': 1})]})
        delivery = self.env['lab.delivery'].create({
            'sale_order_id': order.id, 'executive_id': colleague.id,
            'scheduled_date': fields.Datetime.now()})
        self.assertFalse(self._can_read(self.clinic))
        self.env.cr.execute("UPDATE lab_delivery SET create_uid = %s WHERE id = %s",
                            [self.carrier.id, delivery.id])
        self.env.invalidate_all()
        self.assertTrue(self._can_read(self.clinic))
        self.assertTrue(self._can_read(self.doctor))
        self.assertFalse(self._can_read(self.stranger))

    def test_the_restriction_still_confines_the_carrier_in_a_search(self):
        """Odoo 20: the carried clause widens lab_fieldwork's one partner
        restriction (`fw_reachable`); a search as the carrier returns the clinic
        they carry to and never the one they do not."""
        order = self.env['sale.order'].create({
            'partner_id': self.clinic.id,
            'order_line': [(0, 0, {'product_id': self.product.id, 'product_uom_qty': 1})]})
        self.env['lab.delivery'].create({
            'sale_order_id': order.id, 'executive_id': self.carrier.id,
            'scheduled_date': fields.Datetime.now()})
        self.env.flush_all()
        found = self.env['res.partner'].with_user(self.carrier).search(
            [('id', 'in', (self.clinic | self.doctor | self.stranger).ids)])
        self.assertEqual(found, self.clinic | self.doctor)

    def test_the_delivery_form_opens_for_the_carrier_with_every_field(self):
        """The production fault of 2026-09-15 was a FORM: the clinic on the
        delivery refused. Open the real form view as the carrier, the way the web
        client does (every field of the view, sub-views included), off their
        route."""
        order = self.env['sale.order'].create({
            'partner_id': self.doctor.id,
            'order_line': [(0, 0, {'product_id': self.product.id, 'product_uom_qty': 1})]})
        order.write({'state': 'sale'})
        delivery = self.env['lab.delivery'].create({
            'sale_order_id': order.id, 'executive_id': self.carrier.id,
            'scheduled_date': fields.Datetime.now()})
        self.env.flush_all()
        self.env.invalidate_all()
        mine = delivery.with_user(self.carrier)
        form = Form(mine)
        self.assertEqual(form.sale_order_id, order)
        self.assertEqual(form.partner_id, self.doctor)
        # web_read of every field the form names, as the browser asks for them
        view = mine.get_views([(False, 'form')])
        names = view['models']['lab.delivery']['fields']
        spec = {name: ({'fields': {'display_name': {}}}
                       if mine._fields[name].relational else {})
                for name in names}
        values = mine.web_read(spec)[0]
        self.assertEqual(values['partner_id']['display_name'], self.doctor.display_name)
        self.assertTrue(values['sale_order_id'])

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
