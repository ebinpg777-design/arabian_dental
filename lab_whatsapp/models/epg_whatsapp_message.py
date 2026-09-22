# -*- coding: utf-8 -*-
import re

from odoo import _, models


class EpgWhatsappMessage(models.Model):
    """A doctor's consent, checked on every send.

    Unticking WhatsApp Opt-in used to do one thing: add the number to phone.blacklist.
    Nothing read the tick or the blacklist before sending, so an opted-out doctor kept
    getting every invoice, dispatch and reminder. (2026-09-15)
    """
    _inherit = 'epg.whatsapp.message'

    def _consent_partner(self):
        """Whose consent governs this message: its contact, or the contact it is about."""
        self.ensure_one()
        if self.partner_id:
            return self.partner_id
        if self.res_model == 'res.partner' and self.res_id:
            return self.env['res.partner'].browse(self.res_id).exists()
        return self.env['res.partner']

    def _number_blacklisted(self):
        self.ensure_one()
        digits = re.sub(r'\D', '', self.number or '')
        if not digits:
            return False
        # Both spellings: phone.blacklist normalises what it stores only when the
        # `phonenumbers` library is installed, and the opt-in writes bare digits.
        return bool(self.env['phone.blacklist'].sudo().search_count(
            [('number', 'in', [digits, '+' + digits])], limit=1))

    def _send_blocked_reason(self):
        reason = super()._send_blocked_reason()
        if reason:
            return reason
        partner = self._consent_partner().sudo()
        if partner and not partner.whatsapp_optin:
            return _("%s has opted out of WhatsApp messages (WhatsApp Opt-in is "
                     "unticked on the contact).", partner.display_name)
        if self._number_blacklisted():
            return _("%s is on the phone blacklist.", self.number)
        return False

    def _page_facts(self):
        """Where the case stands, for the page the doctor opens.

        The message is already in their chat; what they cannot see there is what
        happened since - the case moved to production, the invoice was part paid,
        the parcel has a consignment number. (client, 2026-09-18)
        """
        self.ensure_one()
        Template = self.env['epg.whatsapp.template']
        record = self.sudo()._record()
        if record is None:
            return super()._page_facts()

        def money(amount, currency=None):
            return Template._money(amount, currency or record.company_id.currency_id)

        facts = []
        if record._name == 'sale.order':
            stages = dict(record._fields['state'].selection)
            facts = [(_('Case'), record.name), (_('Patient'), record.patient or '—'),
                     (_('Registered'), Template._format_value(record.date_order)),
                     (_('Stage'), stages.get(record.state, record.state))]
            picking = record.picking_ids.filtered(lambda p: p.state == 'done')[:1]
            if picking:
                facts.append((_('Dispatched'), Template._format_value(picking.date_done)))
        elif record._name == 'account.move':
            paid = dict(record._fields['payment_state'].selection)
            facts = [(_('Invoice'), record.name),
                     (_('Amount'), money(record.amount_total, record.currency_id)),
                     (_('Due by'), Template._format_value(record.invoice_date_due)),
                     (_('Outstanding'), money(record.amount_residual, record.currency_id)),
                     (_('Status'), paid.get(record.payment_state, ''))]
        elif record._name == 'stock.picking':
            facts = [(_('Delivery'), record.name), (_('For'), record.origin or '—'),
                     (_('Courier'), record.courier_company or '—'),
                     (_('Consignment'), record.consignment_number or '—')]
            if record.date_done:
                facts.append((_('Dispatched'), Template._format_value(record.date_done)))
        elif record._name == 'account.payment':
            facts = [(_('Reference'), record.name or '—'),
                     (_('Amount'), money(record.amount, record.currency_id)),
                     (_('Date'), Template._format_value(record.date))]
        return [(label, value) for label, value in facts if value not in (None, '', False)]

    def _on_opt_out(self):
        """A doctor who tapped "stop" is switched off on the contact too, which every
        automatic notification and the phone blacklist already respect."""
        result = super()._on_opt_out()
        for message in self.sudo():
            partner = message._consent_partner()
            if partner and 'whatsapp_optin' in partner._fields and partner.whatsapp_optin:
                partner.whatsapp_optin = False
        return result

    def _on_payment_claimed(self):
        """"I've paid" on an invoice becomes a to-do for whoever books money: match the
        UPI receipt and register the payment. The claim itself never marks anything paid."""
        result = super()._on_payment_claimed()
        for message in self.sudo():
            if message.res_model != 'account.move' or not message.res_id:
                continue
            move = self.env['account.move'].sudo().browse(message.res_id).exists()
            if not move or not hasattr(move, 'activity_schedule'):
                continue
            move.activity_schedule(
                'mail.mail_activity_data_todo',
                summary=_("WhatsApp: doctor says paid %s on UPI",
                          self.env['epg.whatsapp.template']._money(message.pay_amount, message.currency_id)),
                note=_("%(who)s tapped \"I've paid\" on the WhatsApp payment page for "
                       "%(ref)s. Find the UPI receipt and register the payment.",
                       who=message.partner_id.display_name or message.number,
                       ref=message.pay_reference or move.name),
                user_id=(move.invoice_user_id or move.create_uid).id)
        return result
