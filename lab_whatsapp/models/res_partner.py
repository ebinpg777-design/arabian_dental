# -*- coding: utf-8 -*-
import re

from odoo import api, fields, models, _


class ResPartner(models.Model):
    _name = 'res.partner'
    _inherit = ['res.partner', 'lab.whatsapp.mixin']

    whatsapp_optin = fields.Boolean(
        'Send WhatsApp', default=True,
        help="Uncheck to stop all automatic WhatsApp notifications to this contact. "
             "Synced with the WhatsApp/phone blacklist (native), which is also "
             "checked directly before every send.")
    whatsapp_number = fields.Char(
        'WhatsApp Number',
        help="The only number WhatsApp messages go to - never the phone, which is "
             "often a landline. Include the country code, e.g. +91 98470 12345.")
    whatsapp_message_count = fields.Integer(compute='_compute_whatsapp_message_count')
    # The doctor's own order list on the portal - but only for a doctor who can
    # actually sign in. In a message it is a link button, and a button with an
    # empty address is dropped, so the same template serves both. (client, 2026-09-18)
    portal_orders_url = fields.Char(
        'My Orders (portal)', compute='_compute_portal_orders_url',
        help="The portal address of this contact's orders, empty when they have no "
             "login. Use it in a template as {{partner_id.portal_orders_url}}.")

    def _compute_portal_orders_url(self):
        base = self.env['epg.whatsapp.message']._base_url()
        for partner in self:
            can_sign_in = bool(partner.sudo().user_ids.filtered('active'))
            partner.portal_orders_url = ('%s/my/orders' % base
                                         if base and can_sign_in else '')

    def _compute_whatsapp_message_count(self):
        data = self.env['epg.whatsapp.message']._read_group(
            [('partner_id', 'in', self.ids)], ['partner_id'], ['__count'])
        mapped = {p.id: c for p, c in data}
        for rec in self:
            rec.whatsapp_message_count = mapped.get(rec.id, 0)

    def write(self, vals):
        res = super().write(vals)
        if 'whatsapp_optin' in vals:
            for partner in self:
                number = partner._whatsapp_number()
                if not number:
                    continue
                Blacklist = self.env['phone.blacklist'].sudo()
                # The public add/remove: they sanitise one number. _add/_remove
                # take a LIST of sanitised numbers, and a bare string was walked
                # character by character, so nothing was ever blacklisted.
                if vals['whatsapp_optin']:
                    Blacklist.remove('+' + number, message=_("Opted back in via contact form."))
                else:
                    Blacklist.add('+' + number, message=_("Opted out via contact form."))
        return res

    def _whatsapp_number(self):
        """Return the E.164-ish digits (no '+') Meta expects, or '' if none.

        The WhatsApp Number only. The phone is not a fallback: on a clinic it is
        usually the landline, and a message sent there is lost without an error.
        (client, 2026-09-15) Prepends the company country code (default India / 91)
        for a local number."""
        self.ensure_one()
        raw = self.whatsapp_number or ''
        digits = re.sub(r'\D', '', raw)
        if not digits:
            return ''
        cc = (self.country_id.phone_code or self.company_id.country_id.phone_code
              or self.env.company.country_id.phone_code or 91)
        cc = str(cc)
        local = digits.lstrip('0')
        if len(local) <= 10 and not digits.startswith(cc):
            return cc + local
        return digits

    # ------------------------------------------------------------------ actions
    def action_whatsapp_messages(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': _('WhatsApp Messages'),
            'res_model': 'epg.whatsapp.message', 'view_mode': 'list,form',
            'domain': [('partner_id', '=', self.id)],
        }

    def action_whatsapp_ask(self):
        """The questions the lab puts to a doctor: pick one, name the case, send.
        (client, 2026-09-18)"""
        self.ensure_one()
        if not self.whatsapp_number:
            return self.action_whatsapp_add_number()
        return {
            'type': 'ir.actions.act_window', 'name': _('Ask %s', self.display_name),
            'res_model': 'lab.whatsapp.enquiry', 'view_mode': 'form', 'target': 'new',
            'context': {'default_partner_id': self.id},
        }

    def action_whatsapp_ask_details(self):
        """Ask this doctor for what a new case is missing - the shade, the bite,
        the date it is wanted - in one click. (client, 2026-09-18)"""
        self.ensure_one()
        return self._whatsapp_quick_event('more_info')

    def action_send_whatsapp(self):
        """Open the composer on this contact, with the greeting already written."""
        self.ensure_one()
        template = self.env['epg.whatsapp.template']._find('res.partner', 'manual')
        return {
            'type': 'ir.actions.act_window', 'name': _('Send WhatsApp'),
            'res_model': 'epg.whatsapp.composer', 'view_mode': 'form', 'target': 'new',
            'context': {'default_res_model': 'res.partner', 'default_res_id': self.id,
                        'default_template_id': template.id or False},
        }
