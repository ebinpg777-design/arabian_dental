# -*- coding: utf-8 -*-
"""Pay now, from the message - without a payment gateway. (client, 2026-09-17)

An invoice message carries a private *Pay now* link. The page behind it shows the
amount and opens the doctor's own UPI app (Google Pay, PhonePe, Paytm, any other)
with the lab's UPI ID and the amount already filled in, or a UPI QR code for a
desk computer. The doctor pays there, taps *I've paid*, and the invoice's chatter
says so - the accountant then matches the receipt. Nothing is charged for any of
it: UPI is free, and the link is the lab's own.

A number safety meter lives here too: WhatsApp watches a number that suddenly sends
a great deal, and the Desk says how far today's sending is from a comfortable limit.
"""
from urllib.parse import quote

from markupsafe import Markup

from odoo import _, api, fields, models

UPI_APPS = [
    ('upi', 'Any UPI app', 'upi://pay?'),
    ('gpay', 'Google Pay', 'tez://upi/pay?'),
    ('phonepe', 'PhonePe', 'phonepe://pay?'),
    ('paytm', 'Paytm', 'paytmmp://pay?'),
]
DEFAULT_DAILY_LIMIT = 150


class EpgWhatsappAccount(models.Model):
    _inherit = 'epg.whatsapp.account'

    upi_id = fields.Char(
        'UPI ID', help="The lab's UPI ID (VPA), e.g. arabiandentallab@upi. With one set, "
                       "an invoice message can carry a Pay now link.")
    upi_payee_name = fields.Char(
        'UPI Payee Name', help="Shown in the doctor's UPI app as who they are paying.")
    daily_send_limit = fields.Integer(
        'Comfortable Sends per Day', default=DEFAULT_DAILY_LIMIT,
        help="WhatsApp watches a number that suddenly sends a great deal. The Desk "
             "shows today's sending against this; passing it is warned about, not "
             "blocked.")


class EpgWhatsappTemplate(models.Model):
    _inherit = 'epg.whatsapp.template'

    add_payment_link = fields.Boolean(
        'Add a Pay Now Link',
        help="WhatsApp app / Web only. Adds a private link that opens the doctor's UPI "
             "app with the amount filled in, and a UPI QR code. Needs a UPI ID on the "
             "sender.")
    payment_amount_field = fields.Char(
        'Amount Field', default='amount_total',
        help="Where the amount to pay is on the record, as a dotted path.")
    payment_reference_field = fields.Char(
        'Payment Reference Field', default='name',
        help="What the payment is for - shown in the UPI app and the receipt.")

    @api.depends('add_payment_link', 'payment_amount_field', 'payment_reference_field')
    def _compute_placeholder_check(self):
        super()._compute_placeholder_check()
        for template in self:
            if not template.add_payment_link:
                continue
            problems = [template.placeholder_warnings] if template.placeholder_warnings else []
            for label, path in ((_('amount field'), template.payment_amount_field),
                                (_('payment reference field'),
                                 template.payment_reference_field)):
                ok, why = template._check_path(template.model, path)
                if not ok:
                    problems.append(_("%(label)s '%(path)s': %(why)s",
                                      label=label, path=path, why=why))
            template.placeholder_warnings = '\n'.join(problems)
            template.placeholder_ok = not problems


class EpgWhatsappMessage(models.Model):
    _inherit = 'epg.whatsapp.message'

    currency_id = fields.Many2one(related='company_id.currency_id')
    pay_amount = fields.Monetary('Amount to Pay', currency_field='currency_id',
                                 readonly=True, copy=False)
    pay_reference = fields.Char('Payment Reference', readonly=True, copy=False)
    pay_opened_at = fields.Datetime('Payment Page Opened', readonly=True, copy=False)
    paid_claimed_at = fields.Datetime('Says Paid', readonly=True, copy=False)

    # ------------------------------------------------------------------ prepare
    def _prepare_link(self):
        result = super()._prepare_link()
        for message in self.filtered(lambda m: m.state == 'ready' and not m.pay_amount):
            template = message.template_id
            account = message.account_id
            record = message._record()
            if not (template.add_payment_link and account.upi_id and record is not None):
                continue
            try:
                # The number itself, not the money as a message spells it.
                amount = float(template._field_value(
                    record, template.payment_amount_field or 'amount_total') or 0)
            except (TypeError, ValueError):
                amount = 0.0
            if amount <= 0:
                continue
            message.sudo().write({
                'pay_amount': amount,
                'pay_reference': (template._resolve(
                    record, template.payment_reference_field or 'name') or '')[:60],
            })
        return result

    def _pay_text(self, base=None, token=None):
        """The Pay now block of the message text, or ''."""
        self.ensure_one()
        token = token or self.sudo().access_token
        if not (self.pay_amount and token and self.account_id.upi_id):
            return ''
        return '💳 %s\n%s/wa/p/%s' % (
            _("Pay %s now", self.env['epg.whatsapp.template']._money(self.pay_amount, self.currency_id)),
            base or self._base_url(), token)

    # ------------------------------------------------------------------ the page
    def _upi_links(self):
        """One deep link per UPI app, with the payee, amount and reference filled in."""
        self.ensure_one()
        account = self.account_id.sudo()
        query = 'pa=%s&pn=%s&am=%.2f&cu=%s&tn=%s' % (
            quote(account.upi_id or '', safe='@.'),
            quote(account.upi_payee_name or self.company_id.name or '', safe=''),
            self.pay_amount,
            quote(self.currency_id.name or 'INR', safe=''),
            quote((self.pay_reference or '')[:50], safe=''))
        return [{'key': key, 'label': label, 'url': prefix + query}
                for key, label, prefix in UPI_APPS]

    def get_pay_page(self):
        self.ensure_one()
        links = self._upi_links()
        return {
            'amount': self.env['epg.whatsapp.template']._money(self.pay_amount, self.currency_id),
            'reference': self.pay_reference or '',
            'payee': self.account_id.sudo().upi_payee_name or self.company_id.name or '',
            'upi_id': self.account_id.sudo().upi_id or '',
            'apps': links,
            'qr': self._qr_svg(links[0]['url']),
            'claimed': bool(self.paid_claimed_at),
        }

    def _register_pay_open(self, user_agent=''):
        self.ensure_one()
        from .link_channel import PREVIEW_AGENTS
        message = self.sudo()
        if PREVIEW_AGENTS.search(user_agent or '') or not message._was_sent():
            return False
        if message.pay_opened_at:
            return True
        now = fields.Datetime.now()
        message._mark_seen(now, count=False)
        message.write({'pay_opened_at': now})
        message._chatter_post(Markup('<p>💳 <b>%s</b></p>') % _(
            "%(who)s opened the payment page for %(amount)s",
            who=message.partner_id.display_name or message.number,
            amount=self.env['epg.whatsapp.template']._money(message.pay_amount, message.currency_id)),
            author=message.partner_id)
        return True

    def _register_paid_claim(self):
        """The doctor says they have paid. Recorded, said on the chatter, and handed
        to business modules - never treated as money received."""
        self.ensure_one()
        message = self.sudo()
        if message.paid_claimed_at:
            return False
        if not message._was_sent():
            return False
        now = fields.Datetime.now()
        message.write({'paid_claimed_at': now})
        message._create_reply(_("💳 Paid %s - please confirm",
                                self.env['epg.whatsapp.template']._money(message.pay_amount, message.currency_id)))
        message._chatter_post(Markup('<p>💳 <b>%s</b> %s</p>') % (
            _("%(who)s says they have paid %(amount)s on UPI",
              who=message.partner_id.display_name or message.number,
              amount=self.env['epg.whatsapp.template']._money(message.pay_amount, message.currency_id)),
            _("- match the receipt to confirm.")), author=message.partner_id)
        message._on_payment_claimed()
        return True

    def _on_payment_claimed(self):
        """Hook: business modules turn the claim into work for whoever books money."""
        return True

    # ------------------------------------------------------------------ safety
    @api.model
    def _safety(self):
        """Today's sending from the lab's own WhatsApp against the comfortable limit."""
        Desk = self.env['epg.whatsapp.desk']
        today = Desk._today_start()
        accounts = self.env['epg.whatsapp.account'].sudo().search([('channel', '=', 'link')])
        limit = sum(a.daily_send_limit or DEFAULT_DAILY_LIMIT for a in accounts) \
            or DEFAULT_DAILY_LIMIT
        sent = self.sudo().search_count([
            ('channel', '=', 'link'), ('direction', '=', 'outbound'),
            ('state', 'in', ('sent', 'read', 'delivered')), ('sent_at', '>=', today)])
        pct = min(round(sent * 100 / limit), 100) if limit else 0
        return {'sent': sent, 'limit': limit, 'pct': pct,
                'level': 'high' if sent >= limit else 'mid' if pct >= 70 else 'ok'}

    def _send_warnings(self):
        warnings = super()._send_warnings()
        safety = self._safety()
        if safety['sent'] >= safety['limit']:
            warnings.append(_(
                "%(sent)s messages have gone from this WhatsApp today, past the "
                "comfortable %(limit)s a day. WhatsApp may flag a number that sends a "
                "great deal; consider leaving the rest for tomorrow.",
                sent=safety['sent'], limit=safety['limit']))
        return warnings
