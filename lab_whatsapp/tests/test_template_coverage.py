# -*- coding: utf-8 -*-
"""The lab's events and the templates behind them.

An event switched on in Settings with no template tagged for it sends nothing,
silently. The settings screen now names those events; the template form shows the
event that was previously set only by seed data. (client, 2026-08-28)
"""
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestTemplateCoverage(TransactionCase):

    def _settings(self):
        return self.env['res.config.settings'].create({})

    def test_the_seeded_events_are_all_covered(self):
        settings = self._settings()
        self.assertFalse(settings.wa_missing_templates,
                         "every switched-on event ships with a template")

    def test_archiving_an_events_template_is_named_on_the_settings_screen(self):
        invoice = self.env['epg.whatsapp.template'].search(
            [('model', '=', 'account.move'), ('event', '=', 'invoice')])
        self.assertTrue(invoice)
        invoice.write({'active': False})
        settings = self._settings()
        self.assertIn('Invoice Ready', settings.wa_missing_templates)
        self.assertNotIn('Dispatched', settings.wa_missing_templates)

    def test_an_event_switched_off_is_not_counted_as_missing(self):
        invoice = self.env['epg.whatsapp.template'].search(
            [('model', '=', 'account.move'), ('event', '=', 'invoice')])
        invoice.write({'active': False})
        settings = self.env['res.config.settings'].create({'wa_notify_invoice': False})
        self.assertNotIn('Invoice Ready', settings.wa_missing_templates or '')

    def test_the_event_is_on_the_template_form(self):
        view = self.env.ref('lab_whatsapp.view_template_form_event')
        self.assertEqual(view.inherit_id, self.env.ref('epg_whatsapp.view_template_form'))
        arch = self.env['epg.whatsapp.template'].get_view(view_type='form')['arch']
        self.assertIn('name="event"', arch)

    def test_one_template_per_event_and_model_still_holds(self):
        from psycopg2.errors import UniqueViolation
        from odoo.tools import mute_logger
        with self.assertRaises(UniqueViolation), mute_logger('odoo.sql_db'):
            self.env['epg.whatsapp.template'].create({
                'name': 'Second Invoice Ready',
                'model_id': self.env['ir.model']._get_id('account.move'),
                'event': 'invoice', 'body': 'x'})
