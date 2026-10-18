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
        self.assertEqual(scan.line_ids.description, 'KIRAN FIXED ANTERIOR BITE PLATE')
        self.assertEqual(scan.doctor_email, 'rubydoctor@example.com')
        self.assertEqual(scan.download_expiry, date(2026, 10, 13))
        self.assertIn('wetransfer.com/downloads', scan.download_url)
        self.assertIn('upper.stl', scan.note)
        self.assertFalse(scan.partner_id, "nobody by that address: left for a person")
        self.assertEqual(scan.state, 'draft')

    def test_dexis_matches_the_doctor_by_e_mail(self):
        scan = self._in(F.DEXIS_SUBJECT, F.DEXIS_BODY)
        self.assertEqual(scan.source, 'email')
        self.assertEqual(scan.channel_id, self.env.ref('lab_scan_register.channel_dexis'))
        self.assertEqual(scan.partner_id, self.arun)
        self.assertEqual(scan.ref, 'OJF-5570')
        self.assertEqual(scan.download_expiry, date.today() + timedelta(days=365))

    def test_freqty_reads_patient_doctor_clinic_and_notes(self):
        scan = self._in(F.FREQTY_SUBJECT, F.FREQTY_BODY)
        self.assertEqual(scan.channel_id, self.env.ref('lab_scan_register.channel_freqty'))
        self.assertEqual(scan.line_ids.patient_name, 'DIYA S NAIR')
        self.assertEqual(scan.doctor_name, 'JOHN MATHEW')
        self.assertEqual(scan.note, 'URP WITH MILD AIBP PLS CALL DR KAVYA')
        self.assertEqual(scan.ref, 'lab_2026-10-07_165237')
        self.assertEqual(scan.partner_id, self.brightsmile,
                         "one contact matches the clinic name, so it is taken")

    def test_allied_star_keeps_the_order_number_and_the_clinic_as_written(self):
        scan = self._in(F.ALLIEDSTAR_SUBJECT, F.ALLIEDSTAR_BODY)
        self.assertEqual(scan.channel_id, self.env.ref('lab_scan_register.channel_alliedstar'))
        self.assertEqual(scan.ref, '261010025423947624448')
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
        self.assertEqual(scan.line_ids.description, 'scan for Rahul')
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
        self.assertEqual(scan.line_ids.patient_name, 'DIYA S NAIR')
        self.assertEqual(scan.attachment_count, 1, "the attachment is in its Files")
        self.assertTrue(scan.has_attachments)


@tagged('post_install', '-at_install')
class TestMailServerRules(TransactionCase):
    """A server set to create Scan Registers registers only what an e-mail source
    recognises by sender or subject; the rest of the mailbox is skipped.
    (client, 2026-10-13)"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Scan = cls.env['lab.scan']
        cls.server = cls.env['fetchmail.server'].create({
            'name': 'Scans mailbox', 'server_type': 'imap', 'server': 'imap.example.com',
            'user': 'scans@example.com', 'password': 'x',
            'object_id': cls.env['ir.model']._get('lab.scan').id})
        cls.doctors = cls.env['lab.scan.channel'].create({
            'name': 'Doctors by mail', 'kind': 'email',
            'sender_match': 'priya.dental@example.com, smiles-clinic.test'})
        cls.cases = cls.env['lab.scan.channel'].create({
            'name': 'New cases', 'kind': 'email', 'subject_match': 'New case; scan request'})

    def _fetch(self, sender, subject, body='please find the scan', msg_id=None, **headers):
        raw = "From: %s\r\nTo: odoo.lab@example.com\r\nSubject: %s\r\nMessage-Id: <%s@example.com>\r\n" % (
            sender, subject, msg_id or subject.replace(' ', '-').lower())
        raw += ''.join('%s: %s\r\n' % item for item in headers.items())
        raw += "MIME-Version: 1.0\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n%s\r\n" % body
        before = self.Scan.search([])
        self.env['mail.thread'].with_context(default_fetchmail_server_id=self.server.id) \
            .message_process('lab.scan', raw)
        return self.Scan.search([]) - before

    def test_the_switch_is_on_by_default(self):
        self.assertTrue(self.server.scan_matching_only)

    def test_a_mail_nobody_recognises_is_skipped(self):
        new = self._fetch('Newsletter <news@shop.test>', 'Big sale this week')
        self.assertFalse(new, "a newsletter in the mailbox is not a scan")

    def test_a_recognised_sender_is_registered_on_its_source(self):
        new = self._fetch('Dr Priya <priya.dental@example.com>', 'upper hawley for Rahul')
        self.assertEqual(len(new), 1)
        self.assertEqual(new.channel_id, self.doctors)

    def test_any_of_several_senders_counts(self):
        new = self._fetch('Front desk <desk@smiles-clinic.test>', 'two patients today')
        self.assertEqual(new.channel_id, self.doctors)

    def test_a_recognised_subject_is_registered_on_its_source(self):
        new = self._fetch('Someone <someone@unknown.test>', 'Fwd: NEW CASE - Diya')
        self.assertEqual(len(new), 1)
        self.assertEqual(new.channel_id, self.cases, "subject rules ignore case and Fwd:")

    def test_a_sender_rule_wins_over_a_subject_rule(self):
        new = self._fetch('Dr Priya <priya.dental@example.com>', 'Scan request for Diya')
        self.assertEqual(new.channel_id, self.doctors)

    def test_with_the_switch_off_every_mail_is_registered(self):
        self.server.scan_matching_only = False
        new = self._fetch('Newsletter <news@shop.test>', 'Big sale this week')
        self.assertEqual(len(new), 1)

    def test_a_reply_to_a_register_is_never_held_back(self):
        scan = self._fetch('Dr Priya <priya.dental@example.com>', 'upper hawley for Rahul',
                           msg_id='first-scan')
        messages = len(scan.message_ids)
        new = self._fetch('Unknown <nobody@unknown.test>', 'Re: upper hawley for Rahul',
                          msg_id='reply-scan', **{'In-Reply-To': '<first-scan@example.com>'})
        self.assertFalse(new, "a reply is not a new register")
        self.assertEqual(len(scan.message_ids), messages + 1, "it lands on the register it answers")

    def test_mail_not_from_a_server_is_unchanged(self):
        """The alias and direct callers keep registering an unknown sender."""
        raw = ("From: news@shop.test\r\nTo: scans@example.com\r\nSubject: Big sale\r\n"
               "Message-Id: <no-server@example.com>\r\nContent-Type: text/plain\r\n\r\nhello\r\n")
        before = self.Scan.search([])
        self.env['mail.thread'].message_process('lab.scan', raw)
        self.assertEqual(len(self.Scan.search([]) - before), 1)
