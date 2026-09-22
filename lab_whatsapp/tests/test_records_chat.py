# -*- coding: utf-8 -*-
"""The chat from the record, the auto-send switch on the template, and the seed
refresh that redesigns the lab's messages without overwriting a reworded one.
(client, 2026-09-17)
"""
from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase, tagged

from ..models.epg_whatsapp_template import PREVIOUS_SEED


@tagged('post_install', '-at_install')
class TestRecordsChat(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.account = cls.env['epg.whatsapp.account'].create({
            'name': 'Records Lab', 'whatsapp_number': '+91 90000 00077'})
        cls.partner = cls.env['res.partner'].create({
            'name': 'Dr Records', 'whatsapp_number': '+91 98470 00077'})
        cls.invoice = cls.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': cls.partner.id})

    def test_a_record_opens_its_doctors_chat_with_its_own_messages_marked(self):
        action = self.invoice.action_whatsapp_chat()
        self.assertEqual(action['tag'], 'epg_whatsapp_chats')
        self.assertEqual(action['params'], {
            'partner_id': self.partner.id, 'res_model': 'account.move',
            'res_id': self.invoice.id})
        template = self.env.ref('lab_whatsapp.tmpl_account_move_manual')
        message = template.send(self.invoice, account=self.account)
        message.action_mark_sent()
        self.assertEqual(message.partner_id, self.partner)
        self.assertEqual(self.invoice.whatsapp_message_count, 1)
        Desk = self.env['epg.whatsapp.desk']
        chats = Desk.get_chats('', self.partner.id)
        thread = Desk.get_thread(chats['open_id'])
        bubbles = [b for b in thread['bubbles'] if b['kind'] == 'msg']
        self.assertEqual((bubbles[-1]['res_model'], bubbles[-1]['res_id']),
                         ('account.move', self.invoice.id),
                         "the chat can pick out the messages about the record")

    def test_the_whatsapp_button_offers_the_records_own_moment(self):
        ref = self.env.ref
        self.assertEqual(self.invoice._whatsapp_suggested_template(),
                         ref('lab_whatsapp.tmpl_account_move_manual'),
                         "a draft invoice: the generic message")
        self.env.cr.execute(
            "UPDATE account_move SET state = 'posted', payment_state = 'not_paid', "
            "invoice_date_due = %s WHERE id = %s",
            (fields.Date.today() + timedelta(days=5), self.invoice.id))
        self.invoice.invalidate_recordset()
        self.assertEqual(self.invoice._whatsapp_suggested_template(),
                         ref('lab_whatsapp.tmpl_invoice'), "posted: the invoice")
        self.env.cr.execute("UPDATE account_move SET invoice_date_due = %s WHERE id = %s",
                            (fields.Date.today() - timedelta(days=5), self.invoice.id))
        self.invoice.invalidate_recordset()
        self.assertEqual(self.invoice._whatsapp_suggested_template(),
                         ref('lab_whatsapp.tmpl_reminder'), "overdue: the reminder")
        action = self.invoice.action_whatsapp_send()
        self.assertEqual(action['context']['default_template_id'],
                         ref('lab_whatsapp.tmpl_reminder').id)
        self.assertEqual(self.invoice.whatsapp_to, '+91 98470 00077')
        order = self.env['sale.order'].create({'partner_id': self.partner.id})
        self.assertEqual(order._whatsapp_suggested_event(), 'manual')
        order.write({'state': 'sale'})
        self.assertEqual(order._whatsapp_suggested_template(),
                         ref('lab_whatsapp.tmpl_order_confirm'))

    def test_the_stat_button_tells_the_story(self):
        self.assertEqual(self.invoice.whatsapp_status, 'None yet')
        template = self.env.ref('lab_whatsapp.tmpl_account_move_manual')
        message = template.send(self.invoice, account=self.account)
        self.invoice.invalidate_recordset(['whatsapp_status'])
        self.assertEqual(self.invoice.whatsapp_status, 'On the Desk')
        message.action_mark_sent()
        self.invoice.invalidate_recordset(['whatsapp_status'])
        self.assertTrue(self.invoice.whatsapp_status.startswith('Sent '))
        message._mark_seen(fields.Datetime.now())
        self.invoice.invalidate_recordset(['whatsapp_status'])
        self.assertTrue(self.invoice.whatsapp_status.startswith('Opened '))
        message._create_reply('👍 Noted')
        self.invoice.invalidate_recordset(['whatsapp_status'])
        self.assertTrue(self.invoice.whatsapp_status.startswith('Replied '))

    def test_a_list_selection_goes_to_the_desk_at_once(self):
        other = self.env['res.partner'].create({
            'name': 'Dr Numberless'})
        second = self.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': other.id})
        self.env.ref('lab_whatsapp.tmpl_account_move_manual').account_id = self.account
        result = (self.invoice | second).action_whatsapp_send_batch()
        self.assertEqual(result['params']['type'], 'success')
        self.assertIn('1 message(s) ready', result['params']['message'])
        self.assertIn('1 record(s) have no WhatsApp number', result['params']['message'])
        self.assertEqual(result['params']['next']['tag'], 'epg_whatsapp_desk')
        self.assertEqual(self.invoice.whatsapp_message_count, 1)
        self.assertEqual(second.whatsapp_message_count, 0)

    def test_one_click_puts_the_message_in_the_send_dialog(self):
        template = self.env.ref('lab_whatsapp.tmpl_account_move_manual')
        template.write({'account_id': self.account.id, 'delay_hours': 5})
        action = self.invoice.action_whatsapp_quick()
        self.assertEqual(action['tag'], 'epg_whatsapp_send')
        self.assertTrue(action['params']['discard_on_close'])
        message = self.env['epg.whatsapp.message'].browse(action['params']['message_id'])
        self.assertEqual(message.state, 'ready', "a person pressed it: no template delay")
        self.assertEqual(message.template_id, template)
        self.invoice.invalidate_recordset(['whatsapp_status'])
        self.assertEqual(self.invoice.whatsapp_status, 'On the Desk')
        message.action_discard()
        self.assertFalse(message.exists(), "closed without sending: as if never written")
        self.invoice.invalidate_recordset(['whatsapp_status', 'whatsapp_message_count'])
        self.assertEqual(self.invoice.whatsapp_message_count, 0)
        other = self.env['res.partner'].create({'name': 'Dr Numberless'})
        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': other.id})
        self.assertEqual(invoice.action_whatsapp_quick()['res_model'], 'epg.whatsapp.composer',
                         "nothing to send to: the composer explains")

    def test_a_whatsapp_user_may_use_the_number_wizard(self):
        """The wizard is opened by a person at the counter, not by the admin: an
        ACL gap is invisible to a superuser and stops everyone else. (production,
        2026-09-18)"""
        g = self.env.ref
        clerk = self.env['res.users'].create({
            'name': 'Counter Clerk', 'login': 'wa_acl_clerk',
            'group_ids': [(6, 0, [g('base.group_user').id,
                                  g('sales_team.group_sale_salesman').id,
                                  g('epg_whatsapp.group_whatsapp_user').id])]})
        other = self.env['res.partner'].create({'name': 'Dr Numberless ACL'})
        order = self.env['sale.order'].create({'partner_id': other.id})
        action = order.with_user(clerk).action_whatsapp_add_number()
        wizard = self.env['lab.whatsapp.number'].with_user(clerk).with_context(
            action['context']).create({'number': '+91 98470 00123'})
        wizard.action_save()
        self.assertEqual(other.whatsapp_number, '+91 98470 00123')
        self.assertTrue(other.whatsapp_optin)

    def test_the_number_is_asked_for_on_the_record(self):
        other = self.env['res.partner'].create({'name': 'Dr Numberless', 'phone': '+91 98470 00088'})
        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': other.id})
        self.assertFalse(invoice.whatsapp_to)
        action = invoice.action_whatsapp_add_number()
        self.assertEqual(action['res_model'], 'lab.whatsapp.number')
        self.assertEqual(action['context']['default_number'], '+91 98470 00088',
                         "the phone is offered, to be checked, not typed")
        wizard = self.env['lab.whatsapp.number'].with_context(action['context']).create({
            'number': '98470'})
        self.assertFalse(wizard.number_ok)
        wizard.number = '+91 98470 00099'
        self.assertTrue(wizard.number_ok)
        wizard.action_save()
        self.assertEqual(other.whatsapp_number, '+91 98470 00099')
        invoice.invalidate_recordset(['whatsapp_to'])
        self.assertEqual(invoice.whatsapp_to, '+91 98470 00099')

    def test_the_page_says_where_the_case_stands(self):
        """The doctor taps one link and sees what happened since the message.
        (client, 2026-09-18)"""
        template = self.env.ref('lab_whatsapp.tmpl_account_move_manual')
        message = template.send(self.invoice, account=self.account)
        facts = dict(message._page_facts())
        self.assertNotIn('Invoice', facts, "a draft has no number yet: no empty row")
        self.assertIn('Amount', facts)
        self.assertIn('Outstanding', facts)
        self.assertEqual(facts['Status'], 'Not Paid')
        self.env.cr.execute("UPDATE account_move SET name = 'OC000123', state = 'posted' "
                            "WHERE id = %s", (self.invoice.id,))
        self.invoice.invalidate_recordset()
        self.assertEqual(dict(message._page_facts())['Invoice'], 'OC000123')
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id, 'patient': 'ADHIDEV'})
        on_order = self.env.ref('lab_whatsapp.tmpl_sale_order_manual').send(
            order, account=self.account)
        facts = dict(on_order._page_facts())
        self.assertEqual((facts['Case'], facts['Patient']), (order.name, 'ADHIDEV'))
        self.assertEqual(facts['Stage'], 'Registered')
        self.assertIn('facts', on_order._page())

    def test_the_case_message_points_at_the_portal_only_for_a_doctor_who_can_sign_in(self):
        """No acknowledgement PDF any more: the doctor's own order list instead -
        and nothing at all for a doctor with no login. (client, 2026-09-18)"""
        template = self.env.ref('lab_whatsapp.tmpl_order_confirm')
        self.assertFalse(template.report_id, "the acknowledgement no longer rides along")
        self.assertIn('portal_orders_url', template.buttons)
        self.assertTrue(template.placeholder_ok, template.placeholder_warnings)
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id, 'patient': 'ADHIDEV'})
        self.assertFalse(self.partner.portal_orders_url, "no login, no link")
        message = template.send(order, account=self.account)
        self.assertFalse(message.attachment_id)
        self.assertEqual(message._button_rows(), [], "nothing to tap that leads nowhere")
        self.assertNotIn('My orders', message._link_text())
        # The same template, a doctor who can sign in.
        self.env['res.users'].create({
            'name': 'Dr Portal', 'login': 'wa_portal_doctor', 'partner_id': self.partner.id,
            'group_ids': [(6, 0, [self.env.ref('base.group_portal').id])]})
        self.partner.invalidate_recordset(['portal_orders_url'])
        self.assertTrue(self.partner.portal_orders_url.endswith('/my/orders'))
        second = template.send(order, account=self.account)
        [(label, url)] = second._button_rows()
        self.assertEqual(label, '📋 My orders')
        self.assertTrue(url.endswith('/my/orders'))
        page = second._page()
        self.assertIn('📋 My orders', [b['label'] for b in page['buttons']])

    def test_a_contact_can_be_asked_about_a_new_case(self):
        """A case arrives with the shade missing: the doctor is asked from their
        own contact, in one click. (client, 2026-09-18)"""
        template = self.env.ref('lab_whatsapp.tmpl_more_info')
        template.account_id = self.account
        self.assertEqual(template.model, 'res.partner')
        self.assertEqual(template.event, 'more_info')
        self.assertTrue(template.placeholder_ok, template.placeholder_warnings)
        action = self.partner.action_whatsapp_ask_details()
        self.assertEqual(action['tag'], 'epg_whatsapp_send')
        message = self.env['epg.whatsapp.message'].browse(action['params']['message_id'])
        self.assertEqual((message.template_id, message.partner_id), (template, self.partner))
        self.assertIn('shade', message.body)
        self.assertIn(self.partner.name, message.body)
        self.assertEqual(message.state, 'ready')
        # A question the doctor answers by writing back carries no link at all:
        # a page to tap would stand between them and the reply. (client, 2026-09-18)
        self.assertFalse(template.quick_replies)
        self.assertEqual(template.button_style, 'links')
        text = message._link_text()
        self.assertNotIn('/wa/', text)
        self.assertNotIn('http', text)
        # And a doctor with no number gets the composer instead of a dead message.
        numberless = self.env['res.partner'].create({'name': 'Dr No Number At All'})
        self.assertEqual(numberless.action_whatsapp_ask_details()['res_model'],
                         'epg.whatsapp.composer')

    def test_a_contact_has_a_send_button_and_a_message_to_start_from(self):
        """Press WhatsApp on a contact and the composer opens with the greeting
        already written - not an empty box, and not "SO284978: JUMANATH".
        (client, 2026-09-18)"""
        Template = self.env['epg.whatsapp.template']
        action = self.partner.action_send_whatsapp()
        self.assertEqual(action['res_model'], 'epg.whatsapp.composer')
        frame = Template._find('res.partner', 'manual')
        self.assertEqual(action['context']['default_template_id'], frame.id)
        composer = self.env['epg.whatsapp.composer'].with_context(
            action['context']).create({})
        self.assertIn(self.partner.name, composer.body, "the doctor is greeted by name")
        self.assertEqual(composer.number, '919847000077')
        # Every frame reads as a message now, not as a debug line.
        for model, record in (('sale.order', self.env['sale.order'].create(
                {'partner_id': self.partner.id, 'patient': 'ADHIDEV'})),
                ('account.move', self.invoice)):
            generic = Template._find(model, 'manual')
            body = generic.render(record)
            self.assertIn('Dear', body, generic.name)
            self.assertNotIn('{{', body, generic.name)
            self.assertGreater(len(body.splitlines()), 2, generic.name)

    def test_the_lab_can_ask_a_doctor_any_of_its_questions(self):
        """The questions that stop a case at the bench, ready to send from the
        contact. (client, 2026-09-18)"""
        Template = self.env['epg.whatsapp.template']
        enquiries = Template._enquiries()
        self.assertEqual(
            set(enquiries.mapped('event')),
            {'more_info', 'ask_impression', 'ask_shade', 'ask_pickup', 'ask_approval'})
        enquiries.account_id = self.account
        for template in enquiries:
            self.assertTrue(template.placeholder_ok, template.name)
            self.assertNotIn('{{', template.render(self.partner), template.name)
        action = self.partner.action_whatsapp_ask()
        self.assertEqual(action['res_model'], 'lab.whatsapp.enquiry')
        wizard = self.env['lab.whatsapp.enquiry'].with_context(
            action['context']).create({})
        self.assertEqual(wizard.partner_id, self.partner)
        self.assertEqual(wizard.template_id, enquiries[0],
                         "the first question is the one already chosen")
        # What the picker shows is the model's own order, so the sequence has to
        # be what puts them in it - not the alphabet. (client, 2026-09-18)
        listed = Template.search([('id', 'in', enquiries.ids)])
        self.assertEqual(listed.mapped('event'), enquiries.mapped('event'),
                         "the radio list reads in the order they are asked")
        self.assertEqual(enquiries[0].event, 'more_info',
                         "asked in the order they come up, not the alphabet's")
        wizard.template_id = Template._find('res.partner', 'ask_shade')
        self.assertIn('shade', wizard.preview.lower())
        wizard.about = 'SO284978 · patient JUMANATH'
        self.assertIn('*Re: SO284978 · patient JUMANATH*', wizard.preview,
                      "what it is about, where the doctor reads first")
        sent = wizard.action_send()
        message = self.env['epg.whatsapp.message'].browse(sent['params']['message_id'])
        self.assertEqual(message.state, 'ready')
        self.assertEqual(message.partner_id, self.partner)
        self.assertIn('SO284978', message.body)

    def test_asking_a_doctor_with_no_number_asks_for_the_number(self):
        numberless = self.env['res.partner'].create({'name': 'Dr Nothing To Dial'})
        self.assertEqual(numberless.action_whatsapp_ask()['res_model'],
                         'lab.whatsapp.number')

    def test_the_lab_answers_that_need_a_person(self):
        dispatch = self.env.ref('lab_whatsapp.tmpl_dispatch')
        self.assertIn('⚠️ Something is missing', dispatch._alert_labels())
        self.assertIn('📞 Please call me', dispatch._alert_labels())
        reminder = self.env.ref('lab_whatsapp.tmpl_reminder')
        self.assertIn('✅ Paid already', reminder._alert_labels())
        for template in self.env['epg.whatsapp.template'].search(
                [('alert_replies', '!=', False)]):
            answers = [line.strip() for line in (template.quick_replies or '').splitlines()
                       if line.strip()]
            for label in template._alert_labels():
                self.assertIn(label, answers,
                              "%s: an answer nobody can tap raises nothing" % template.name)

    def test_the_auto_send_switch_is_the_settings_switch(self):
        template = self.env.ref('lab_whatsapp.tmpl_invoice')
        params = self.env['ir.config_parameter'].sudo()
        params.set_param('lab_whatsapp.notify_invoice', 'True')
        template.invalidate_recordset(['auto_send'])
        self.assertTrue(template.auto_send)
        template.auto_send = False
        self.assertEqual(params.get_param('lab_whatsapp.notify_invoice'), 'False')
        self.assertFalse(self.env['account.move']._whatsapp_enabled('invoice'))
        self.assertFalse(self.env.ref('lab_whatsapp.tmpl_account_move_manual').auto_send,
                         "a manual template never sends on its own")

    def test_the_seed_refresh_respects_a_reworded_message(self):
        Template = self.env['epg.whatsapp.template']
        reminder = self.env.ref('lab_whatsapp.tmpl_reminder')
        current = reminder.body
        # Any text the seed carried before this one - the list is not in any order.
        older = [text for text in PREVIOUS_SEED['tmpl_reminder']
                 if text.strip() != (current or '').strip()]
        self.assertTrue(older, "the seed remembers what it used to say")
        reminder.write({'body': older[0], 'body_2': False,
                        'body_3': False, 'quick_replies': False, 'add_payment_link': False,
                        'button_style': 'links', 'page_label': False})
        Template._refresh_seeded_templates(PREVIOUS_SEED)
        self.assertEqual(reminder.body, current, "an old seed is brought up to date")
        self.assertEqual(reminder.button_style, 'page', "one link, buttons on the page")
        self.assertTrue(reminder.page_label, "and the link says what it opens")
        self.assertTrue(reminder.body_2 and reminder.body_3, "the reminder escalates")
        self.assertIn('Paid already', reminder.quick_replies)
        self.assertTrue(reminder.add_payment_link)
        self.assertEqual(reminder.payment_amount_field, 'amount_residual')
        # Every remembered text leads back to today's, whichever age it is.
        for text in older:
            reminder.write({'body': text})
            Template._refresh_seeded_templates(PREVIOUS_SEED)
            self.assertEqual(reminder.body, current)
        self.assertIn('{{partner_id.name|doctor}}', reminder.body)
        self.assertNotIn('{{currency_id.name}}', reminder.body, "money carries its own currency")
        reminder.write({'body': 'The lab\'s own words for {{name}}', 'body_2': False,
                        'quick_replies': False})
        Template._refresh_seeded_templates(PREVIOUS_SEED)
        self.assertEqual(reminder.body, 'The lab\'s own words for {{name}}')
        self.assertFalse(reminder.body_2, "no escalation grafted onto the lab's text")
        self.assertIn('Paid already', reminder.quick_replies, "but the answers are added")
        payment = self.env.ref('lab_whatsapp.tmpl_payment')
        self.assertEqual(payment.report_id, self.env.ref('account.action_report_payment_receipt'))
        feedback = self.env.ref('lab_whatsapp.tmpl_feedback')
        self.assertEqual(feedback.delay_hours, 20)
        for template in Template.search([('event', '!=', 'manual')]):
            self.assertTrue(template.placeholder_ok, template.name)
