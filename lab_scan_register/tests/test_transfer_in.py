# -*- coding: utf-8 -*-
"""File-transfer platforms register their scans by themselves, like the scanner
clouds do: from the share notification, or from a share link in any mail.
(client, 2026-10-13)"""
from datetime import date
from html import escape

from odoo.tests.common import TransactionCase, tagged

from ..models import parsers
from . import fixtures as F

LAB = 'LAB SCANS <lab.scans@example.com>'


@tagged('post_install', '-at_install')
class TestTransferNotifications(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Scan = cls.env['lab.scan']
        cls.arun = cls.env['res.partner'].create({
            'name': 'Dr Arun K R', 'is_company': True, 'email': 'arun.pm.dentist@example.com'})

    def _in(self, subject, body):
        html = '<div>' + '<br/>'.join(escape(l) for l in body.split('\n')) + '</div>'
        return self.Scan.message_new({'subject': subject, 'body': html, 'email_from': LAB, 'message_id': 'x'})

    # ------------------------------------------------------------------ parsers
    def test_the_notifications_are_read(self):
        drive = parsers.parse_email(F.DRIVE_SUBJECT, F.DRIVE_BODY, LAB)
        self.assertEqual((drive['platform'], drive['doctor_email'], drive['patient'], drive['delivery']),
                         ('google_drive', 'arun.pm.dentist@example.com', 'Rahim VP', date(2026, 10, 16)))
        self.assertNotIn('appliance', drive, "a folder named after the patient adds nothing")
        dropbox = parsers.parse_email(F.DROPBOX_SUBJECT, F.DROPBOX_BODY, LAB)
        self.assertEqual((dropbox['platform'], dropbox['doctor_name'], dropbox['doctor_email'], dropbox['appliance']),
                         ('dropbox', 'Dr Priya Nair', 'priya.dental@example.com', 'Diya scans'))
        self.assertIn('/scl/fo/', dropbox['link'], "the share, not the help page")
        onedrive = parsers.parse_email(F.ONEDRIVE_SUBJECT, F.ONEDRIVE_BODY, LAB)
        self.assertEqual((onedrive['platform'], onedrive['doctor_name'], onedrive['appliance'], onedrive['link']),
                         ('onedrive', 'Dr Jose', 'Tanvi expansion', 'https://1drv.ms/f/s!AbCdEfGh12345'))

    # ------------------------------------------------------------------ registers
    def test_a_drive_share_becomes_a_file_transfer_register(self):
        scan = self._in(F.DRIVE_SUBJECT, F.DRIVE_BODY)
        self.assertEqual(scan.source, 'transfer')
        self.assertEqual(scan.channel_id, self.env.ref('lab_scan_register.channel_google_drive'))
        self.assertEqual(scan.partner_id, self.arun, "matched by the sharer's own e-mail")
        self.assertEqual(scan.line_ids.patient_name, 'Rahim VP')
        self.assertEqual(scan.line_ids.date_delivery, date(2026, 10, 16))
        self.assertIn('drive.google.com/drive/folders/', scan.download_url)

    def test_dropbox_and_onedrive_too(self):
        for subject, body, xmlid, url_part in (
                (F.DROPBOX_SUBJECT, F.DROPBOX_BODY, 'channel_dropbox', 'dropbox.com/scl/'),
                (F.ONEDRIVE_SUBJECT, F.ONEDRIVE_BODY, 'channel_onedrive', '1drv.ms/')):
            scan = self._in(subject, body)
            with self.subTest(platform=xmlid):
                self.assertEqual(scan.source, 'transfer')
                self.assertEqual(scan.channel_id, self.env.ref('lab_scan_register.' + xmlid))
                self.assertIn(url_part, scan.download_url)
                self.assertEqual(scan.download_platform_id, scan.channel_id)

    def test_a_share_link_in_a_plain_mail_is_recognised_by_the_link(self):
        """A doctor writing from their own mailbox, with nothing but the link."""
        scan = self._in('Fwd: scans', "From: Dr Rahul <rahul@clinic.test>\n\nPatient name - Cini\n"
                        "here: https://drive.google.com/drive/folders/1ZyXwVuTs")
        self.assertEqual(scan.source, 'transfer')
        self.assertEqual(scan.channel_id, self.env.ref('lab_scan_register.channel_google_drive'))
        self.assertEqual(scan.line_ids.patient_name, 'Cini')

    def test_a_platform_the_lab_adds_is_recognised_without_code(self):
        swiss = self.env['lab.scan.channel'].create({
            'name': 'SwissTransfer', 'kind': 'transfer', 'link_match': 'swisstransfer.com/d/',
            'link_validity_days': 30})
        scan = self._in('Fwd: files', "From: Someone <someone@clinic.test>\n\n"
                        "https://www.swisstransfer.com/d/0a1b2c3d-4e5f")
        self.assertEqual(scan.channel_id, swiss)
        self.assertTrue(scan.download_expiry, "and its links' lifetime with it")

    def test_several_link_patterns_on_one_platform(self):
        onedrive = self.env.ref('lab_scan_register.channel_onedrive')
        Channel = self.env['lab.scan.channel']
        for url in ('https://1drv.ms/f/s!Abc', 'https://onedrive.live.com/redir?resid=1',
                    'https://clinic-my.sharepoint.com/:f:/g/personal/x'):
            with self.subTest(url=url):
                self.assertEqual(Channel._match_link(url), onedrive)


@tagged('post_install', '-at_install')
class TestTransferThroughTheMailServer(TransactionCase):
    """With the server's 'only scans it recognises' filter on, as it is by default."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Scan = cls.env['lab.scan']
        cls.server = cls.env['fetchmail.server'].create({
            'name': 'Scans mailbox', 'server_type': 'imap', 'server': 'imap.example.com',
            'user': 'scans@example.com', 'password': 'x',
            'object_id': cls.env['ir.model']._get('lab.scan').id})

    def _fetch(self, sender, subject, body, msg_id):
        raw = ("From: %s\r\nTo: scans@example.com\r\nSubject: %s\r\nMessage-Id: <%s@example.com>\r\n"
               "MIME-Version: 1.0\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n%s\r\n") % (
            sender, subject, msg_id, body.replace('\n', '\r\n'))
        before = self.Scan.search([])
        self.env['mail.thread'].with_context(default_fetchmail_server_id=self.server.id) \
            .message_process('lab.scan', raw)
        return self.Scan.search([]) - before

    def test_a_drive_share_gets_through(self):
        new = self._fetch(LAB, F.DRIVE_SUBJECT, F.DRIVE_BODY, 'drive-share')
        self.assertEqual(len(new), 1)
        self.assertEqual(new.source, 'transfer')

    def test_a_share_link_from_an_unknown_sender_gets_through(self):
        new = self._fetch('Dr Rahul <rahul@clinic.test>', 'scans for Cini',
                          'here: https://www.dropbox.com/scl/fo/q1w2e3/AAA?dl=0', 'plain-share')
        self.assertEqual(new.channel_id, self.env.ref('lab_scan_register.channel_dropbox'))

    def test_a_platforms_newsletter_is_still_skipped(self):
        """A link to the site is not a share: the filter's whole point stands."""
        new = self._fetch('Google <no-reply@accounts.google.com>', 'Your storage is almost full',
                          'Manage storage: https://drive.google.com/settings/storage\n'
                          'Help: https://support.google.com/drive', 'storage-notice')
        self.assertFalse(new)
        new = self._fetch('Dropbox <marketing@mail.dropbox.com>', 'Try Dropbox Plus',
                          'Upgrade: https://www.dropbox.com/plans', 'dropbox-promo')
        self.assertFalse(new)
