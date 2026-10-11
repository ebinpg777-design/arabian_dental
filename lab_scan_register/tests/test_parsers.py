# -*- coding: utf-8 -*-
"""The parsers against the messages the lab actually receives."""
from datetime import date, datetime, timedelta

from odoo.tests.common import BaseCase, tagged

from ..models import parsers
from . import fixtures as F


@tagged('post_install', '-at_install')
class TestWhatsAppCaption(BaseCase):

    def test_the_usual_three_lines(self):
        out = parsers.parse_whatsapp(F.WHATSAPP_RAHIM)
        self.assertEqual(out['patient'], 'Rahim VP')
        self.assertEqual(out['appliance'], "Upper &Lower Hawley's retainer")
        self.assertEqual(out['delivery'], date(2026, 10, 13))

    def test_a_name_with_an_initial_and_a_dot(self):
        out = parsers.parse_whatsapp(F.WHATSAPP_AYESHA)
        self.assertEqual(out['patient'], 'Ayesha Nadia. P')
        self.assertEqual(out['appliance'], 'Upper Exis Retainer 2set')

    def test_appliance_for_patient_on_one_line(self):
        out = parsers.parse_whatsapp(F.WHATSAPP_ARJUN)
        self.assertEqual(out['patient'], 'Arjun Mehta')
        self.assertEqual(out['appliance'], 'long band n loop')
        self.assertNotIn('delivery', out)

    def test_a_caption_it_cannot_read_is_not_guessed(self):
        """Nothing says which words are the patient: leave it for a person."""
        for text in (F.WHATSAPP_SAMEER, F.WHATSAPP_TANVI):
            with self.subTest(text=text):
                out = parsers.parse_whatsapp(text)
                self.assertNotIn('patient', out)
                self.assertEqual(out['appliance'], text)

    def test_other_ways_the_clinics_write_it(self):
        out = parsers.parse_whatsapp("Pt name: Anu\nDue: 1/11/2026\nTPA")
        self.assertEqual(out, {'patient': 'Anu', 'delivery': date(2026, 11, 1), 'appliance': 'TPA'})
        self.assertEqual(parsers.parse_whatsapp(''), {})


@tagged('post_install', '-at_install')
class TestEmailParsers(BaseCase):

    def test_forwarding_is_seen_through(self):
        self.assertEqual(parsers.strip_subject('Fwd: Fw: RE: hello'), 'hello')
        self.assertEqual(parsers.forwarded_from(F.FREQTY_BODY), 'Freqty <freqty_cloud@freqtek.com>')
        out = parsers.parse_email(F.FREQTY_SUBJECT, F.FREQTY_BODY,
                                  'LAB SCANS <lab.scans@example.com>')
        self.assertEqual(out['platform'], 'freqty',
                         "the lab's own address forwarded it; the platform is in the body")

    def test_allied_star(self):
        out = parsers.parse_email(F.ALLIEDSTAR_SUBJECT, F.ALLIEDSTAR_BODY)
        self.assertEqual(out['platform'], 'alliedstar')
        self.assertEqual(out['external_ref'], '261010025423947624448')
        self.assertEqual(out['clinic_name'], "Dr Meera's centre for prosthodontics and implant")
        self.assertIn('allied-star.com/orders', out['link'])

    def test_wetransfer(self):
        out = parsers.parse_email(F.WETRANSFER_SUBJECT, F.WETRANSFER_BODY)
        self.assertEqual(out['platform'], 'wetransfer')
        self.assertEqual(out['doctor_email'], 'rubydoctor@example.com')
        self.assertEqual(out['appliance'], 'KIRAN FIXED ANTERIOR BITE PLATE')
        self.assertEqual(out['link_expires'], date(2026, 10, 13))
        self.assertEqual(out['file_names'], ['bite1.stl', 'bite2.stl', 'lower.stl', 'upper.stl'])
        self.assertTrue(out['link'].startswith('https://wetransfer.com/downloads/'))

    def test_freqty(self):
        out = parsers.parse_email(F.FREQTY_SUBJECT, F.FREQTY_BODY)
        self.assertEqual(out['patient'], 'DIYA S NAIR')
        self.assertEqual(out['doctor_name'], 'JOHN MATHEW')
        self.assertEqual(out['clinic_name'], 'BRIGHTSMILE TOOTH AFFAIR PRIVATE LTD VKM001017')
        self.assertEqual(out['notes'], 'URP WITH MILD AIBP PLS CALL DR KAVYA')
        self.assertEqual(out['external_ref'], 'lab_2026-10-07_165237')
        self.assertIn('freqtek.com', out['link'])

    def test_dexis(self):
        out = parsers.parse_email(F.DEXIS_SUBJECT, F.DEXIS_BODY)
        self.assertEqual(out['platform'], 'dexis')
        self.assertEqual(out['doctor_name'], 'Dr.Arun K R')
        self.assertEqual(out['doctor_email'], 'arun.pm.dentist@example.com')
        self.assertEqual(out['external_ref'], 'OJF-5570')
        self.assertEqual(out['link_expires'], date.today() + timedelta(days=365))
        self.assertIn('dentalconnect.dexis.com', out['link'])

    def test_an_unknown_sender_still_yields_something_to_work_from(self):
        out = parsers.parse_email(F.UNKNOWN_SUBJECT, F.UNKNOWN_BODY)
        self.assertEqual(out['platform'], 'other')
        self.assertEqual(out['appliance'], 'scan for Rahul')
        self.assertEqual(out['doctor_email'], 'priya.dental@example.com')
        self.assertIn('upper hawley', out['notes'])


@tagged('post_install', '-at_install')
class TestWhatsAppChat(BaseCase):
    """Messages as they leave WhatsApp: who sent them, when, and their files."""

    def test_a_copy_from_whatsapp_web_gives_poster_and_time(self):
        msgs = [parsers.read_chat_message(m) for m in parsers.split_chat(F.CHAT_WEB_COPY)]
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[0]['poster'], 'ANJALI MENON K')
        self.assertEqual(msgs[0]['when'], datetime(2026, 10, 11, 12, 36))
        self.assertEqual(msgs[0]['patient'], 'Rahim VP')
        self.assertEqual(msgs[1]['poster'], '+91 90000 12345')
        self.assertEqual(msgs[1]['phone'], '919000012345', "an unsaved contact is its number")
        self.assertEqual(msgs[1]['when'], datetime(2026, 10, 11, 16, 2))

    def test_an_android_export_folds_the_stl_messages_into_the_caption(self):
        msgs = [parsers.read_chat_message(m) for m in parsers.split_chat(F.CHAT_ANDROID_EXPORT)]
        self.assertEqual(len(msgs), 2, "caption + two STL messages are one scan, then a question")
        self.assertEqual(msgs[0]['poster'], 'ANJALI MENON K', "the ~ and invisible spaces are gone")
        self.assertEqual(msgs[0]['files'], ['Rahim P LowerJawScan.stl', 'Rahim P UpperJawScan.stl'])
        self.assertTrue(msgs[0]['looks_like_scan'])
        self.assertFalse(msgs[1]['looks_like_scan'], "a question is chat, not a scan")

    def test_an_iphone_export_reads_the_attachment(self):
        msgs = [parsers.read_chat_message(m) for m in parsers.split_chat(F.CHAT_IOS_EXPORT)]
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]['files'], ['2026 scan.zip'])
        self.assertEqual(msgs[0]['when'], datetime(2026, 10, 6, 10, 49, 12))
        self.assertTrue(msgs[0]['looks_like_scan'], "it carries a file")
        self.assertNotIn('patient', msgs[0], "and its caption names no patient for certain")

    def test_files_from_somebody_else_are_not_folded_in(self):
        text = ("11/10/26, 12:36 pm - A: Patient name - X\n"
                "11/10/26, 12:37 pm - B: upper.stl (file attached)")
        self.assertEqual(len(parsers.split_chat(text)), 2)

    def test_files_an_hour_later_are_a_message_of_their_own(self):
        text = ("11/10/26, 12:36 pm - A: Patient name - X\n"
                "11/10/26, 1:40 pm - A: upper.stl (file attached)")
        self.assertEqual(len(parsers.split_chat(text)), 2)

    def test_text_with_no_header_is_one_message(self):
        msgs = parsers.split_chat(F.WHATSAPP_RAHIM)
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]['poster'], '')
        self.assertIsNone(msgs[0]['when'])
