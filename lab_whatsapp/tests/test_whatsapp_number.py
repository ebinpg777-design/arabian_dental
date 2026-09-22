# -*- coding: utf-8 -*-
"""WhatsApp goes to the WhatsApp Number only - never the phone.

A clinic's phone is usually its landline; a message sent there is lost without an
error. (client, 2026-09-15)
"""
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestWhatsappNumberOnly(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.account = cls.env['epg.whatsapp.account'].create({
            'name': 'Number Sender', 'phone_number_id': '515151',
            'access_token': 'tok', 'simulation_mode': True, 'channel': 'cloud_api'})
        cls.template = cls.env['epg.whatsapp.template'].create({
            'name': 'Number Check', 'account_id': cls.account.id,
            'model_id': cls.env['ir.model']._get_id('res.partner'),
            'body': 'Dear {{name}}'})
        cls.Message = cls.env['epg.whatsapp.message']

    def test_a_new_template_points_at_the_whatsapp_number(self):
        self.assertEqual(self.template.phone_field, 'partner_id.whatsapp_number')

    def test_the_seeded_templates_point_at_the_whatsapp_number(self):
        seeded = self.env['epg.whatsapp.template'].with_context(active_test=False).search(
            [('event', '!=', False)])
        self.assertTrue(seeded)
        for template in seeded:
            self.assertIn(template.phone_field,
                          ('partner_id.whatsapp_number', 'whatsapp_number'),
                          template.name)

    def test_the_message_goes_to_the_whatsapp_number_not_the_phone(self):
        clinic = self.env['res.partner'].create({
            'name': 'Landline Clinic', 'phone': '0469 2645210',
            'whatsapp_number': '+91 98470 33333'})
        self.template.phone_field = 'whatsapp_number'
        message = self.template.send(clinic, defer=True)
        self.assertEqual(message.number, '919847033333')
        self.assertEqual(clinic._whatsapp_number(), '919847033333')

    def test_a_contact_with_only_a_phone_gets_nothing(self):
        clinic = self.env['res.partner'].create({
            'name': 'Phone Only Clinic', 'phone': '+91 98470 44444'})
        self.template.phone_field = 'whatsapp_number'
        self.assertFalse(clinic._whatsapp_number())
        self.assertFalse(self.template.send(clinic, defer=True))
        self.assertFalse(self.Message.search([('res_model', '=', 'res.partner'),
                                              ('res_id', '=', clinic.id)]))

    def test_the_contact_list_shows_the_whatsapp_number(self):
        arch = self.env['res.partner'].get_view(
            self.env.ref('base.view_partner_tree').id, 'list')['arch']
        self.assertIn('whatsapp_number', arch)
