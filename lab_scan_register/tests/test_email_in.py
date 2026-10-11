# -*- coding: utf-8 -*-
"""Notifications from the scanner clouds become scans by themselves."""
from datetime import date, timedelta
from html import escape

from odoo.tests.common import TransactionCase, tagged

from . import fixtures as F

LAB = 'LAB SCANS <lab.scans@example.com>'


@tagged('post_install', '-at_install')
class TestEmailIn(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Scan = cls.env['lab.scan']
        cls.arun = cls.env['res.partner'].create({
            'name': 'Dr Arun K R', 'is_company': True, 'email': 'arun.pm.dentist@example.com'})
        cls.brightsmile = cls.env['res.partner'].create({
            'name': "Brightsmile Tooth Affair Private Ltd", 'is_company': True})

    def _in(self, subject, body, email_from=LAB, **msg):
        # as a mail client sends it: the "<addr>" in a forwarded From: line is escaped,
        # or html2plaintext would take it for a tag and eat it
        html = '<div>' + '<br/>'.join(escape(l) for l in body.split('\n')) + '</div>'
        return self.Scan.message_new(dict({'subject': subject, 'body': html,
                                           'email_from': email_from, 'message_id': 'x'}, **msg))

    def test_wetransfer_lands_on_its_channel_with_the_link_and_its_expiry(self):
        scan = self._in(F.WETRANSFER_SUBJECT, F.WETRANSFER_BODY)
        self.assertEqual(scan.channel_id, self.env.ref('lab_scan_register.channel_wetransfer'))
        self.assertEqual(scan.source, 'transfer',
                         "a WeTransfer notice arrives by mail, but the scan is a file transfer")
        self.assertEqual(scan.appliance, 'KIRAN FIXED ANTERIOR BITE PLATE')
        self.assertEqual(scan.doctor_email, 'rubydoctor@example.com')
        self.assertEqual(scan.link_expires, date(2026, 10, 13))
        self.assertIn('wetransfer.com/downloads', scan.link)
        self.assertIn('upper.stl', scan.notes)
        self.assertFalse(scan.partner_id, "nobody by that address: left for a person")
        self.assertEqual(scan.state, 'new')

    def test_dexis_matches_the_doctor_by_e_mail(self):
        scan = self._in(F.DEXIS_SUBJECT, F.DEXIS_BODY)
        self.assertEqual(scan.source, 'email')
        self.assertEqual(scan.channel_id, self.env.ref('lab_scan_register.channel_dexis'))
        self.assertEqual(scan.partner_id, self.arun)
        self.assertEqual(scan.external_ref, 'OJF-5570')
        self.assertEqual(scan.link_expires, date.today() + timedelta(days=365))

    def test_freqty_reads_patient_doctor_clinic_and_notes(self):
        scan = self._in(F.FREQTY_SUBJECT, F.FREQTY_BODY)
        self.assertEqual(scan.channel_id, self.env.ref('lab_scan_register.channel_freqty'))
        self.assertEqual(scan.patient, 'DIYA S NAIR')
        self.assertEqual(scan.doctor_name, 'JOHN MATHEW')
        self.assertEqual(scan.notes, 'URP WITH MILD AIBP PLS CALL DR KAVYA')
        self.assertEqual(scan.external_ref, 'lab_2026-10-07_165237')
        self.assertEqual(scan.partner_id, self.brightsmile,
                         "one contact matches the clinic name, so it is taken")

    def test_allied_star_keeps_the_order_number_and_the_clinic_as_written(self):
        scan = self._in(F.ALLIEDSTAR_SUBJECT, F.ALLIEDSTAR_BODY)
        self.assertEqual(scan.channel_id, self.env.ref('lab_scan_register.channel_alliedstar'))
        self.assertEqual(scan.external_ref, '261010025423947624448')
        self.assertEqual(scan.clinic_name, "Dr Meera's centre for prosthodontics and implant")

    def test_a_channel_tied_to_a_clinic_wins_over_guessing(self):
        channel = self.env['lab.scan.channel'].create({
            'name': 'Dr Arun via DEXIS', 'kind': 'email', 'sender_match': 'no_reply@dexis.com',
            'partner_id': self.brightsmile.id})
        scan = self._in(F.DEXIS_SUBJECT, F.DEXIS_BODY)
        self.assertEqual(scan.channel_id, channel, "the longer, more specific sender pattern")
        self.assertEqual(scan.partner_id, self.brightsmile)

    def test_an_unknown_sender_is_registered_rather_than_dropped(self):
        scan = self._in(F.UNKNOWN_SUBJECT, F.UNKNOWN_BODY)
        self.assertEqual(scan.channel_id, self.env.ref('lab_scan_register.channel_email_other'))
        self.assertEqual(scan.appliance, 'scan for Rahul')
        self.assertEqual(scan.email_subject, F.UNKNOWN_SUBJECT)

    def test_a_name_matching_two_contacts_is_left_for_a_person(self):
        self.env['res.partner'].create({
            'name': 'Brightsmile Tooth Affair Private Ltd — Branch 2', 'is_company': True})
        scan = self._in(F.FREQTY_SUBJECT, F.FREQTY_BODY)
        self.assertFalse(scan.partner_id)
        self.assertEqual(scan.clinic_name, 'BRIGHTSMILE TOOTH AFFAIR PRIVATE LTD VKM001017')

    def test_the_whole_route_from_a_raw_mail(self):
        """As the fetched mailbox hands it over: through message_process, with the
        attachment ending up on the scan."""
        raw = (
            "From: %s\r\nTo: scans@example.com\r\nSubject: %s\r\n"
            "Message-Id: <scan-route-test@example.com>\r\n"
            "MIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=\"b\"\r\n\r\n"
            "--b\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n%s\r\n"
            "--b\r\nContent-Type: application/octet-stream; name=\"DIYA.stl\"\r\n"
            "Content-Disposition: attachment; filename=\"DIYA.stl\"\r\n"
            "Content-Transfer-Encoding: base64\r\n\r\nc29saWQgdGVzdA==\r\n--b--\r\n"
        ) % (LAB, F.FREQTY_SUBJECT, F.FREQTY_BODY)
        # Odoo 20's mail router hands back the record (19 returned its id)
        scan = self.env['mail.thread'].message_process('lab.scan', raw)
        self.assertEqual(scan.patient, 'DIYA S NAIR')
        self.assertEqual(scan.file_count, 1, "the attachment is on the scan and counted")
        self.assertTrue(scan.files_received)
