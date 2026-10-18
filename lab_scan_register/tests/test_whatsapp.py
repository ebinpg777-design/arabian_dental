# -*- coding: utf-8 -*-
"""The WhatsApp button: a template, filled in for the register, sent to the group
(opened from its invite link, message on the clipboard) or to the sender."""
from urllib.parse import unquote

from odoo.exceptions import AccessError, UserError
from odoo.tests import Form, tagged

from ..models.whatsapp_wizard import group_web_url
from .common import ScanCase

INVITE = 'https://chat.whatsapp.com/Fz3kQ8hLmN2pR7tVwXyZ01'


@tagged('post_install', '-at_install')
class TestWhatsApp(ScanCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.group.link = INVITE
        cls.member = cls.env['lab.scan.channel.member'].create({
            'channel_id': cls.group.id, 'name': 'ANJALI MENON K', 'phone': '+91 90000 67890'})
        cls.Template = cls.env['lab.scan.message.template']

    def _scan(self):
        scan = self._register(patients=(('Rahim VP', 'x'), ('Ayesha', 'y')),
                              member_id=self.member.id, ref='OJF-5570')
        scan.line_ids[0].date_delivery = '2026-10-13'
        return scan

    def _wizard(self, scan, **vals):
        form = Form(self.env['lab.scan.whatsapp'].with_context(default_scan_id=scan.id))
        for key, value in vals.items():
            setattr(form, key, value)
        return form.save()

    # ------------------------------------------------------------------ templates
    def test_a_template_is_filled_in_for_the_register(self):
        tpl = self.Template.create({'name': 't', 'body': 'For {patients} ({patient_count}) on {scan}, '
                                                          'delivery {delivery}, from {sender} at {clinic}.'})
        scan = self._scan()
        self.assertEqual(tpl.render(scan),
                         'For Rahim VP and Ayesha (2) on %s, delivery 13-10-2026, from ANJALI MENON K '
                         'at Smile Point Dental Care Feroke.' % scan.name)

    def test_a_template_cannot_reach_into_the_database(self):
        """Only a known name is swapped; anything else stays exactly as typed."""
        tpl = self.Template.create({'name': 't', 'body': '{scan.__class__} {unknown} {scan}'})
        scan = self._scan()
        self.assertEqual(tpl.render(scan), '{scan.__class__} {unknown} %s' % scan.name)

    def test_the_starting_templates_are_there(self):
        names = self.Template.search([]).mapped('name')
        for expected in ('Scans received', 'Files could not be opened', 'Download link expired',
                         'Sent for 3D printing', 'Cases booked'):
            self.assertIn(expected, names)

    # ------------------------------------------------------------------ the wizard
    def test_it_opens_on_the_group_with_the_first_template_filled_in(self):
        scan = self._scan()
        wiz = self._wizard(scan)
        self.assertEqual(wiz.target, 'group')
        self.assertTrue(wiz.can_group)
        self.assertIn('Rahim VP and Ayesha', wiz.message)

    def test_picking_another_template_rewrites_the_message(self):
        scan = self._scan()
        wiz = self._wizard(scan, template_id=self.env.ref('lab_scan_register.template_link_expired'))
        self.assertIn('download link', wiz.message.lower())

    def test_to_the_group_it_opens_whatsapp_web_and_copies_the_message(self):
        scan = self._scan()
        wiz = self._wizard(scan, message='Edited by hand')
        action = wiz.action_send()
        self.assertEqual(action['tag'], 'lab_scan_whatsapp_open')
        self.assertEqual(action['params']['url'],
                         'https://web.whatsapp.com/accept?code=Fz3kQ8hLmN2pR7tVwXyZ01')
        self.assertTrue(action['params']['copy'], "a group cannot be pre-filled")
        self.assertEqual(action['params']['text'], 'Edited by hand', "what was edited is what goes")
        self.assertIn('Edited by hand', scan.message_ids[0].body)
        self.assertTrue(scan.date_acknowledged)

    def test_to_the_sender_the_message_is_filled_in(self):
        scan = self._scan()
        action = self._wizard(scan, target='sender', message='Hi there').action_send()
        self.assertTrue(action['params']['url'].startswith('https://web.whatsapp.com/send?phone=919000067890&text='))
        self.assertEqual(unquote(action['params']['url'].split('text=')[1]), 'Hi there')
        self.assertFalse(action['params']['copy'])

    def test_a_group_with_no_invite_link_says_where_to_add_it(self):
        self.group.link = False
        scan = self._scan()
        wiz = self._wizard(scan, target='group')
        self.assertFalse(wiz.can_group)
        with self.assertRaises(UserError):
            wiz.action_send()

    def test_no_number_no_direct_chat(self):
        mail = self.env['lab.scan.channel'].create({'name': 'Mail', 'kind': 'email'})
        scan = self.Scan.create({'channel_id': mail.id, 'line_ids': [(0, 0, {'patient_name': 'A'})]})
        wiz = self._wizard(scan, target='sender')
        with self.assertRaises(UserError):
            wiz.action_send()

    def test_a_ten_digit_number_gets_the_country_code(self):
        self.env.company.country_id = self.env.ref('base.in')
        self.member.phone = '90000 67890'
        self.assertEqual(self._scan().whatsapp_to, '919000067890')

    def test_the_first_message_counts_as_the_acknowledgement(self):
        scan = self._scan()
        self._wizard(scan).action_send()
        first = scan.date_acknowledged
        self._wizard(scan, template_id=self.env.ref('lab_scan_register.template_printing')).action_send()
        self.assertEqual(scan.date_acknowledged, first)

    # ------------------------------------------------------------------ the link
    def test_invite_links_become_whatsapp_web_addresses(self):
        self.assertEqual(group_web_url(INVITE), 'https://web.whatsapp.com/accept?code=Fz3kQ8hLmN2pR7tVwXyZ01')
        self.assertEqual(group_web_url('chat.whatsapp.com/Fz3kQ8hLmN2pR7tVwXyZ01/?utm=x'),
                         'https://web.whatsapp.com/accept?code=Fz3kQ8hLmN2pR7tVwXyZ01')
        self.assertEqual(group_web_url('https://example.com/folder'), 'https://example.com/folder')

    def test_the_invite_link_is_only_for_people_who_register_scans(self):
        """Anyone holding it can join the group."""
        plain = self.env['res.users'].create({
            'name': 'Plain', 'login': 'scan_plain_user',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        with self.assertRaises(AccessError):
            self.group.with_user(plain).read(['link'])
        self.assertEqual(self.group.with_user(self.desk).read(['link'])[0]['link'], INVITE)
