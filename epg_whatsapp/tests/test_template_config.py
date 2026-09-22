# -*- coding: utf-8 -*-
"""Configuring a template without sending a bad one.

The promise: a placeholder that would come out empty is named before the template
is saved; a real record shows the finished text and the number it would go to; the
author can send it to their own phone; and what Meta thinks of the approved
counterpart is one click (or one webhook) away. No network anywhere here — Meta is
a fake. (client, 2026-08-28)
"""
import json
from types import SimpleNamespace
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


class _Response:
    def __init__(self, payload, status=200):
        self.status_code = status
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


@tagged('post_install', '-at_install')
class TestTemplateConfig(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.account = cls.env['epg.whatsapp.account'].create({
            'name': 'Config Sender', 'phone_number_id': '555', 'access_token': 'tok',
            'business_account_id': '99887766', 'simulation_mode': True,
            'channel': 'cloud_api'})
        cls.partner = cls.env['res.partner'].create({
            'name': 'Dr Config', 'phone': '+91 98765 43210', 'city': 'Kochi'})
        cls.Template = cls.env['epg.whatsapp.template']
        cls.template = cls.Template.create({
            'name': 'Config Test', 'account_id': cls.account.id,
            'model_id': cls.env['ir.model']._get_id('res.partner'),
            'phone_field': 'phone',
            'body': 'Dear Dr. {{name}} of {{city}}',
        })

    # ------------------------------------------------------------ placeholders
    def test_a_good_template_passes_the_check(self):
        self.assertTrue(self.template.placeholder_ok)
        self.assertFalse(self.template.placeholder_warnings)

    def test_a_placeholder_that_is_not_a_field_is_named(self):
        self.template.body = 'Dear {{namee}}, {{country_id.nam}}'
        self.assertFalse(self.template.placeholder_ok)
        warnings = self.template.placeholder_warnings
        self.assertIn("{{namee}}", warnings)
        self.assertIn("'namee' is not a field", warnings)
        self.assertIn("{{country_id.nam}}", warnings)
        self.assertIn("'nam' is not a field", warnings)

    def test_walking_into_a_plain_field_is_explained(self):
        self.template.body = '{{city.zip}}'
        self.assertIn("plain char field", self.template.placeholder_warnings)

    def test_the_phone_field_and_meta_variables_are_checked_too(self):
        self.template.write({'phone_field': 'mobile_phone_typo',
                             'meta_variables': 'name, nope'})
        warnings = self.template.placeholder_warnings
        self.assertIn("phone field 'mobile_phone_typo'", warnings)
        self.assertIn("template variable 'nope'", warnings)

    def test_a_property_or_method_counts_as_resolvable(self):
        self.template.body = '{{display_name}}'
        self.assertTrue(self.template.placeholder_ok)

    def test_the_body_length_is_counted(self):
        self.template.body = 'x' * 1030
        self.assertEqual(self.template.body_length, 1030)

    def test_the_picker_offers_own_fields_and_one_step_into_relations(self):
        paths = {p['path']: p for p in self.Template.available_paths('res.partner')}
        self.assertIn('name', paths)
        self.assertIn('country_id.name', paths)
        self.assertIn('›', paths['country_id.name']['label'])
        self.assertNotIn('image_1920', paths)                     # binary: never text
        self.assertNotIn('country_id.state_ids', paths)           # one step only
        self.assertEqual(self.Template.available_paths('no.such.model'), [])

    # ------------------------------------------------------------ sample
    def test_a_sample_record_shows_the_finished_text_and_the_number(self):
        self.template.sample_res_id = self.partner.id
        self.assertEqual(self.template.sample_body, 'Dear Dr. Dr Config of Kochi')
        self.assertEqual(self.template.sample_number, '919876543210')
        self.assertTrue(self.template.sample_number_ok)

    def test_a_number_meta_would_reject_is_flagged(self):
        short = self.env['res.partner'].create({'name': 'Short', 'phone': '123'})
        self.template.sample_res_id = short.id
        self.assertFalse(self.template.sample_number_ok)

    def test_the_approved_templates_variables_are_shown_with_their_values(self):
        self.template.write({'meta_variables': 'name, city',
                             'sample_res_id': self.partner.id})
        self.assertEqual(self.template.sample_variables,
                         '{{1}} = Dr Config | {{2}} = Kochi')

    def test_no_sample_means_no_preview_not_an_error(self):
        self.template.sample_res_id = 0
        self.assertFalse(self.template.sample_body)
        self.assertFalse(self.template.sample_number_ok)

    # ------------------------------------------------------------ test send
    def test_a_test_goes_to_my_own_phone_through_the_sender(self):
        self.env.user.partner_id.phone = '+91 90000 00001'
        self.template.sample_res_id = self.partner.id
        result = self.template.action_send_test()
        self.assertEqual(result['tag'], 'display_notification')
        message = self.env['epg.whatsapp.message'].search(
            [('template_id', '=', self.template.id)], order='id desc', limit=1)
        self.assertEqual(message.number, '919000000001')
        self.assertEqual(message.state, 'simulated')       # the sender simulates
        self.assertTrue(message.body.startswith('[TEST] Dear Dr. Dr Config'))
        self.assertEqual(message.res_id, self.partner.id)

    def test_a_live_sender_applies_the_24_hour_window_to_the_test_too(self):
        # Nothing has ever come in from the author's phone, so a live sender may
        # only deliver an approved template - and the error says what to do.
        self.account.simulation_mode = False
        self.env.user.partner_id.phone = '+91 90000 00002'
        self.template.sample_res_id = self.partner.id
        with self.assertRaises(UserError) as caught:
            self.template.action_send_test()
        self.assertIn('24 hours', str(caught.exception))
        self.assertIn('Config Sender', str(caught.exception))

    def test_a_test_needs_a_sample_and_a_number_of_my_own(self):
        with self.assertRaises(UserError):
            self.template.action_send_test()
        self.template.sample_res_id = self.partner.id
        self.env.user.partner_id.write({'phone': False})
        with self.assertRaises(UserError):
            self.template.action_send_test()

    # ------------------------------------------------------------ stats
    def test_stats_come_from_the_messages_the_template_produced(self):
        Message = self.env['epg.whatsapp.message']
        for state in ('sent', 'read', 'read', 'error', 'draft'):
            Message.create({'account_id': self.account.id, 'template_id': self.template.id,
                            'number': '919876543210', 'body': 'x', 'state': state})
        self.template.invalidate_recordset()
        self.assertEqual(self.template.message_count, 5)
        self.assertEqual(self.template.read_count, 2)
        self.assertEqual(self.template.failed_count, 1)
        self.assertAlmostEqual(self.template.read_rate, 66.7, places=1)

    # ------------------------------------------------------------ Meta status
    def test_meta_status_is_applied_by_name(self):
        self.template.meta_template_name = 'invoice_ready'
        hit = self.Template._apply_meta_status('invoice_ready', 'en', 'REJECTED',
                                               category='UTILITY', reason='TAG_CONTENT_MISMATCH')
        self.assertEqual(hit, self.template)
        self.assertEqual(self.template.meta_status, 'rejected')
        self.assertEqual(self.template.meta_category, 'UTILITY')
        self.assertEqual(self.template.meta_reason, 'TAG_CONTENT_MISMATCH')
        self.assertTrue(self.template.meta_last_synced)
        self.Template._apply_meta_status('invoice_ready', 'en', 'APPROVED')
        self.assertEqual(self.template.meta_status, 'approved')
        self.assertFalse(self.template.meta_reason)

    def test_an_unknown_name_or_event_changes_nothing_useful(self):
        self.assertFalse(self.Template._apply_meta_status('nobody_has_this', 'en', 'APPROVED'))
        self.template.meta_template_name = 'x'
        self.Template._apply_meta_status('x', 'en', 'SOMETHING_NEW')
        self.assertEqual(self.template.meta_status, 'unknown')

    def test_sync_pulls_the_list_from_meta_and_matches_by_name(self):
        self.account.simulation_mode = False
        self.template.meta_template_name = 'invoice_ready'
        page1 = {'data': [
            {'name': 'invoice_ready', 'status': 'APPROVED', 'language': 'en',
             'category': 'UTILITY'},
            {'name': 'written_in_manager', 'status': 'PENDING', 'language': 'en'},
        ], 'paging': {'next': 'https://graph.facebook.com/next-page'}}
        page2 = {'data': [{'name': 'another_one', 'status': 'APPROVED', 'language': 'ml'}]}
        calls = []

        def fake_get(url, params=None, timeout=None, headers=None):
            calls.append((url, params))
            return _Response(page2 if url.endswith('next-page') else page1)

        with patch('odoo.addons.epg_whatsapp.models.epg_whatsapp_account.requests.get',
                   side_effect=fake_get):
            result = self.account.sync_templates()
        self.assertEqual(result['fetched'], 3)
        self.assertEqual(result['matched'], 1)
        self.assertEqual(sorted(result['unknown']), ['another_one', 'written_in_manager'])
        self.assertEqual(self.template.meta_status, 'approved')
        self.assertEqual(self.template.meta_category, 'UTILITY')
        # the business account, not the phone number, and the token in the header
        self.assertIn('/99887766/message_templates', calls[0][0])
        self.assertIsNone(calls[1][1], "the 'next' link carries its own query")

    def test_sync_refuses_in_simulation_or_without_the_business_account(self):
        with self.assertRaises(UserError):
            self.account.sync_templates()
        self.account.write({'simulation_mode': False, 'business_account_id': False})
        with self.assertRaises(UserError):
            self.account.sync_templates()

    def test_sync_reports_a_refusal_from_meta_instead_of_crashing(self):
        self.account.simulation_mode = False
        with patch('odoo.addons.epg_whatsapp.models.epg_whatsapp_account.requests.get',
                   return_value=_Response({'error': 'bad token'}, status=401)):
            with self.assertRaises(UserError):
                self.account.sync_templates()

    def test_the_template_button_summarises_the_sync(self):
        self.account.simulation_mode = False
        self.template.meta_template_name = 'not_on_meta'
        with patch('odoo.addons.epg_whatsapp.models.epg_whatsapp_account.requests.get',
                   return_value=_Response({'data': []})):
            result = self.template.action_sync_meta()
        self.assertEqual(result['params']['type'], 'warning')
        self.assertIn('not_on_meta', result['params']['message'])

    def test_a_business_account_callback_is_routed_by_waba_id(self):
        Account = self.env['epg.whatsapp.account']
        self.assertEqual(Account._for_business_account_id('99887766'), self.account)
        self.assertFalse(Account._for_business_account_id(None))
