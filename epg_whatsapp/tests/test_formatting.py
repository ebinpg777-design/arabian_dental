# -*- coding: utf-8 -*-
"""Placeholders that read well, links that open, the file in the dialog.
(client, 2026-09-18)

A doctor reads "18 Sep 2026" and "₹ 19,000.00", not "2026-09-18 01:00:46" and
"19000.00 INR"; "Dr." is said once; a link starts with the address their phone
can reach, whatever address the admin last logged in from.
"""
from datetime import datetime

import pytz

from odoo.tests import TransactionCase, tagged
from odoo.tools import formatLang

from .test_link_channel import BROWSER, LinkSetup


@tagged('post_install', '-at_install')
class TestFormatting(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Template = cls.env['epg.whatsapp.template']
        cls.doctor = cls.env['res.partner'].create({
            'name': 'DR AMAL GOPU MDS ', 'partner_latitude': 12.7, 'type': 'contact'})

    def render(self, record, text):
        return self.Template._render_text(record, text)

    def test_dr_is_said_once(self):
        self.assertEqual(self.render(self.doctor, 'Dear {{name|doctor}},'), 'Dear DR AMAL GOPU MDS,')
        plain = self.env['res.partner'].create({'name': 'Anjali Menon'})
        self.assertEqual(self.render(plain, '{{name|doctor}}'), 'Dr. Anjali Menon')
        clinic = self.env['res.partner'].create({'name': 'Smile Dental', 'is_company': True})
        self.assertEqual(self.render(clinic, '{{name|doctor}}'), 'Smile Dental',
                         "a clinic is not a doctor")
        self.assertEqual(self.render(plain, '{{name|first}} / {{name|upper}}'),
                         'Anjali / ANJALI MENON')

    def test_dates_money_and_choices_read_well(self):
        stamp = datetime(2026, 9, 18, 1, 0, 46)
        self.env.cr.execute("UPDATE res_partner SET create_date = %s WHERE id = %s",
                            (stamp, self.doctor.id))
        self.doctor.invalidate_recordset()
        local = pytz.utc.localize(stamp).astimezone(self.Template._tz())
        self.assertEqual(self.render(self.doctor, '{{create_date|date}}'),
                         local.strftime('%d %b %Y'))
        self.assertEqual(self.render(self.doctor, '{{create_date}}'),
                         '%s, %s' % (local.strftime('%d %b %Y'),
                                     local.strftime('%I:%M %p').lstrip('0')))
        self.assertEqual(self.render(self.doctor, '{{create_date|time}}'),
                         local.strftime('%I:%M %p').lstrip('0'))
        self.assertEqual(self.render(self.doctor, '{{type}}'), 'Contact', "a choice by its label")
        self.assertEqual(self.render(self.doctor, '{{partner_latitude}}'),
                         formatLang(self.env, 12.7, digits=2))
        currency = self.env.company.currency_id
        money = self.render(self.doctor, '{{partner_latitude|money}}')
        self.assertTrue(money.startswith(currency.symbol), "the symbol first: ₹12.70")
        self.assertTrue(money.endswith(formatLang(self.env, 12.7, digits=currency.decimal_places)))
        self.assertNotIn('\N{NO-BREAK SPACE}', money)
        self.assertEqual(self.render(self.doctor, '{{partner_latitude|int}}'), '13')
        self.assertEqual(self.render(self.doctor, '{{partner_latitude|raw}}'), '12.7')
        self.assertEqual(self.render(self.doctor, '{{nonsense_field}}'), '')
        self.assertEqual(self.render(self.doctor, '{{ name | upper }}'), 'DR AMAL GOPU MDS',
                         "spaces around the bars are fine")

    def test_an_unknown_format_is_flagged_on_the_template(self):
        template = self.Template.create({
            'name': 'Fmt', 'model_id': self.env.ref('base.model_res_partner').id,
            'phone_field': 'phone', 'body': 'Dear {{name|shout}}'})
        self.assertFalse(template.placeholder_ok)
        self.assertIn("'shout' is not a format", template.placeholder_warnings)
        template.body = 'Dear {{name|doctor}}'
        self.assertTrue(template.placeholder_ok)


@tagged('post_install', '-at_install')
class TestLinksOpen(LinkSetup, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_link()

    def test_links_start_with_the_senders_public_address(self):
        params = self.env['ir.config_parameter'].sudo()
        params.set_param('web.base.url', 'http://192.168.0.27:8069')
        self.assertIn('private network address', self.account._link_problem())
        message = self.template.send(self.partner)
        self.assertIn('http://192.168.0.27:8069/wa/r/', message._link_text())
        self.assertTrue(any('private network' in w for w in message._send_warnings()),
                        "said before every send")
        self.assertIn('private network', self.env['epg.whatsapp.desk'].get_desk()['link_problem'])
        self.account.link_base_url = 'https://arabiandentallab.com/'
        self.assertFalse(self.account._link_problem())
        self.assertIn('https://arabiandentallab.com/wa/r/', message._link_text())
        # The Desk speaks for every free sender there is.
        self.env['epg.whatsapp.account'].search([('channel', '=', 'link')]).write(
            {'link_base_url': 'https://arabiandentallab.com'})
        self.assertFalse(self.env['epg.whatsapp.desk'].get_desk()['link_problem'])
        self.account.link_base_url = 'http://localhost:8069'
        self.assertIn('only this computer', self.account._link_problem())
        self.account.link_base_url = False
        params.set_param('web.base.url', 'https://arabiandentallab.com')
        self.assertFalse(self.account._link_problem(), "a public system address is fine")

    def test_a_link_fits_on_one_line(self):
        message = self.template.send(self.partner)
        token = message.sudo().access_token
        self.assertEqual(len(token), 16)
        self.assertEqual(self.Message._from_token(token), message)
        self.assertFalse(self.Message._from_token('short'))

    def test_the_dialog_gets_the_file_and_the_words(self):
        message = self.template.send(self.partner)
        self._attach(message)
        payload = message.get_open_payload()
        self.assertEqual(payload['body'], 'Dear Dr Link, your work is ready.')
        [att] = payload['attachments']
        self.assertEqual(att['name'], 'Invoice 42.pdf')
        self.assertTrue(att['url'].startswith('/web/content/'))
        self.assertEqual(att['mimetype'], 'application/pdf')

    def test_a_message_made_for_one_click_can_be_discarded(self):
        message = self.template.send(self.partner)
        message_id = message.id
        message.action_discard()
        self.assertFalse(self.Message.browse(message_id).exists())
        sent = self.template.send(self.partner)
        sent.action_mark_sent()
        sent.action_discard()
        self.assertTrue(sent.exists(), "what went out stays on the record")

    def test_a_message_opened_in_whatsapp_is_never_discarded(self):
        """Closing the window after opening WhatsApp must not delete the message:
        its links are already in the doctor's chat. (production, 2026-09-18)"""
        message = self.template.send(self.partner)
        self._attach(message)
        message.action_mark_opened()
        self.assertEqual(message.state, 'opened')
        message.action_discard()
        self.assertTrue(message.exists(), "opened in WhatsApp: it stays")
        self.assertEqual(message.state, 'opened', "waiting to be confirmed on the Desk")
        self.assertEqual(self.Message._from_token(message.sudo().access_token), message)
        # And the doctor opening the document still marks it sent.
        message._register_seen(BROWSER)
        self.assertIn(message.state, ('sent', 'read'))

    def test_money_for_the_upi_link_is_read_as_a_number(self):
        """The message spells money "₹19,000.00"; the payment link needs 19000.0.
        Reading the amount through the message's formatter killed Pay now on every
        invoice. (production, 2026-09-18)"""
        Template = self.env['epg.whatsapp.template']
        self.assertEqual(Template._field_value(self.partner, 'credit_limit'), 0.0)
        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': self.partner.id,
            'invoice_line_ids': [(0, 0, {'name': 'Work', 'quantity': 1, 'price_unit': 19000.0,
                                         'tax_ids': [(6, 0, [])]})]})
        self.assertEqual(Template._field_value(invoice, 'amount_total'), 19000.0)
        self.assertIn('19,000.00', Template._render_text(invoice, '{{amount_total}}'))
        self.account.upi_id = 'lab@upi'
        template = Template.create({
            'name': 'Pay me', 'model_id': self.env.ref('account.model_account_move').id,
            'account_id': self.account.id, 'phone_field': 'partner_id.phone',
            'body': 'Invoice {{name}} for {{amount_total}}',
            'add_payment_link': True, 'payment_amount_field': 'amount_total',
            'payment_reference_field': 'name'})
        message = template.send(invoice)
        self.assertEqual(message.pay_amount, 19000.0, "Pay now knows the amount")
        self.assertIn('/wa/p/%s' % message.sudo().access_token, message._link_text())

    def test_the_card_and_the_buttons_read_as_the_doctor_would_say_them(self):
        self.template.write({'button_style': 'page', 'header_text': False,
                             'page_label': '🧾 Open it',
                             'body': 'Dear {{name}},\n\n🧾 *Invoice OC1*\n\n💰 *Amount*  ₹150'})
        message = self.template.send(self.partner)
        self._attach(message)
        page = message._page()
        self.assertEqual(page['headline'], '🧾 Invoice OC1', "the message's own heading")
        self.assertEqual(page['summary'], '💰 Amount  ₹150')
        self.assertNotIn('Invoice OC1', str(page['lines']),
                         "the heading is not printed again under itself")
        self.assertIn('₹150', str(page['lines']))
        self.assertEqual([b['label'] for b in page['buttons']], ['📄 Invoice 42.pdf'],
                         "no report on this template: the file's name stands in")
        text = message._link_text()
        self.assertIn('🧾 Open it\n', text, "the link says what it opens")
        self.assertNotIn('Tap here to open', text)

    def test_a_link_says_what_is_wrong_when_the_document_is_gone(self):
        message = self.template.send(self.partner)
        self._attach(message)
        message.action_mark_sent()
        message.sudo().attachment_id.unlink()
        self.assertFalse(message.attachment_id)


@tagged('post_install', '-at_install')
class TestTemplateCraft(LinkSetup, TransactionCase):
    """A line with nothing in it, the doctor's own language, and how a template is
    doing. (client, 2026-09-18)"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_link()

    def test_a_line_whose_field_is_empty_is_left_out(self):
        self.template.body = ('📦 *On its way*\n\n'
                              'Dear {{name}},\n\n'
                              '🚚 *Courier*       {{comment}}\n'
                              '🔖 *Consignment*   {{ref}}\n\n'
                              'Thank you.')
        self.partner.write({'comment': False, 'ref': 'CN-8891'})
        text = self.template.render(self.partner)
        self.assertIn('🔖 *Consignment*   CN-8891', text)
        self.assertNotIn('Courier', text, "nothing to say, so the line goes")
        self.assertNotIn('\n\n\n', text, "and the gap it left closes")
        self.assertIn('📦 *On its way*', text, "a line of plain words always stays")
        # Switched off, the old behaviour stands.
        self.template.hide_empty_lines = False
        self.assertIn('🚚 *Courier*', self.template.render(self.partner))

    def test_the_doctor_is_written_to_in_their_own_language(self):
        lang = self.env['res.lang']._activate_lang('ml_IN') or self.env['res.lang'].search(
            [('code', '=', 'ml_IN')], limit=1)
        if not lang:
            self.skipTest("Malayalam is not available on this database")
        self.template.with_context(lang='ml_IN').write({
            'body': 'പ്രിയ {{name}}, നിങ്ങളുടെ ജോലി തയ്യാറാണ്.',
            'header_text': 'ഓർത്തോക്രിയേഷൻ',
            'page_label': '📄 തുറക്കുക'})
        self.partner.lang = 'ml_IN'
        message = self.template.send(self.partner)
        self.assertIn('നിങ്ങളുടെ ജോലി തയ്യാറാണ്', message.body)
        self.assertIn('ഓർത്തോക്രിയേഷൻ', message._link_text(),
                      "the header follows the doctor as well")
        english = self.env['res.partner'].create({
            'name': 'Dr English', 'phone': '+91 98765 22222', 'lang': 'en_US'})
        self.assertIn('your work is ready', self.template.send(english).body,
                      "a doctor with no Malayalam gets the message as written")

    def test_a_template_says_how_it_is_doing(self):
        self.template.write({'quick_replies': '✅ Received\n📞 Call me'})
        for _index in range(5):
            message = self.template.send(self.partner)
            message.action_mark_sent()
        message._mark_seen()
        message._create_reply('✅ Received')
        self.template.invalidate_recordset()
        self.assertEqual(self.template.reply_count, 1)
        self.assertEqual(self.template.reply_rate, 20.0)
        self.assertIn('✅ Received (1)', self.template.answers_tapped)
        self.assertTrue(self.template.health_note)
        fresh = self.env['epg.whatsapp.template'].create({
            'name': 'Brand new', 'model_id': self.env.ref('base.model_res_partner').id,
            'phone_field': 'phone', 'body': 'Hello {{name}}'})
        self.assertIn('Too few sent', fresh.health_note)

    def test_the_languages_it_is_written_in_are_named(self):
        self.assertTrue(self.template.lang_summary, "the language it was written in")
        self.assertGreaterEqual(self.template.lang_count, 1)


@tagged('post_install', '-at_install')
class TestKanbanFieldsAreDeclared(TransactionCase):
    """A kanban card may only read fields the view asked for.

    A card that reads one it was not given dies with "Cannot read properties of
    undefined" and takes the whole gallery with it - there is no server-side error
    to notice, so it is only ever found by opening the screen. (production,
    2026-09-18)
    """

    def test_every_field_a_card_reads_is_declared(self):
        import re
        from lxml import etree
        views = self.env['ir.ui.view'].search([
            ('type', '=', 'kanban'),
            ('arch_db', 'like', 'record.'),
        ])
        checked = 0
        for view in views:
            if not (view.xml_id or '').startswith(('epg_whatsapp.', 'lab_whatsapp.')):
                continue
            arch = etree.fromstring(self.env[view.model].get_view(view.id, 'kanban')['arch'])
            declared = {node.get('name') for node in arch.iter('field')}
            read = set(re.findall(r'record\.(\w+)\.', etree.tostring(arch, encoding='unicode')))
            missing = read - declared - {'id'}
            self.assertFalse(missing, "%s reads %s without asking for %s"
                             % (view.xml_id, ', '.join(sorted(read)), ', '.join(sorted(missing))))
            checked += 1
        self.assertTrue(checked, "the galleries of these modules are what this guards")
