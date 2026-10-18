# -*- coding: utf-8 -*-
# Copyright (C) 2025-2026 Ebshel Technologies (https://ebshel.com)
# License OPL-1. See LICENSE file for full copyright and licensing details.
from markupsafe import Markup

from odoo import api, fields, models, _
from odoo.exceptions import UserError


class PrBargainWizard(models.TransientModel):
    """Send a revised-price (bargaining) request to one or many vendors at once,
    carrying a global negotiation message plus per-item target prices/notes."""
    _name = 'ebshel.pr.bargain.wizard'
    _description = 'Request Revised Vendor Quotation (Bargain)'

    rfq_id = fields.Many2one('ebshel.purchase.rfq', required=True, ondelete='cascade')
    company_id = fields.Many2one(related='rfq_id.company_id')
    currency_id = fields.Many2one(related='rfq_id.currency_id')
    quotation_ids = fields.Many2many(
        'ebshel.purchase.vendor.quotation', string='Vendors to Bargain With',
        help='A revised-price request email is sent to each selected vendor.')
    available_quotation_ids = fields.Many2many(
        'ebshel.purchase.vendor.quotation', 'ebshel_bargain_avail_rel',
        compute='_compute_available')
    deadline = fields.Date(
        'Reply By', help='Optional deadline shown in the email for the revised quote.')
    global_message = fields.Html(
        'Message to Vendors', required=True,
        default=lambda self: self._default_global_message(),
        help='Overall negotiation message included in every vendor email.')
    line_ids = fields.One2many(
        'ebshel.pr.bargain.wizard.line', 'wizard_id', string='Items')

    @api.model
    def _default_global_message(self):
        return Markup(
            '<p>Thank you for your quotation. To move forward we would appreciate '
            'a <b>revised, more competitive offer</b> on the items below. '
            'Kindly review the target prices and send us your best price.</p>')

    @api.depends('rfq_id')
    def _compute_available(self):
        for wiz in self:
            wiz.available_quotation_ids = wiz.rfq_id.vendor_quotation_ids.filtered(
                lambda q: q.state in ('sent', 'received'))

    # ── Send ───────────────────────────────────────────────────────────────────
    def action_send_bargain(self):
        self.ensure_one()
        if not self.quotation_ids:
            raise UserError(_('Select at least one vendor to bargain with.'))
        # Persist target prices / item notes back onto the RFQ lines for reuse.
        for wl in self.line_ids:
            wl.rfq_line_id.write({
                'target_price': wl.target_price,
                'bargain_note': wl.item_message or False,
            })

        emailed, no_email = 0, 0
        for q in self.quotation_ids:
            body = self._build_email_body(q)
            recipient = q.email
            if recipient:
                self.env['mail.mail'].sudo().create({
                    'subject': _('Revised price request — %s', self.rfq_id.name),
                    'body_html': body,
                    'email_to': recipient,
                    'email_from': self.env.user.email_formatted or self.env.company.email,
                    'auto_delete': True,
                }).send()
                emailed += 1
            else:
                no_email += 1
            q.write({
                'bargain_round': q.bargain_round + 1,
                'bargain_last_date': fields.Datetime.now(),
            })
            q.message_post(
                body=Markup(_(
                    '💬 <b>Bargain round %(n)s</b> requested%(to)s.'
                )) % {'n': q.bargain_round,
                      'to': (_(' — emailed %s', recipient) if recipient
                             else _(' — no email on file'))}
                + body,
                subtype_xmlid='mail.mt_comment')

        self.rfq_id.message_post(
            body=Markup(_('💬 Revised-price request sent to %(e)s vendor(s).')) % {'e': emailed},
            subtype_xmlid='mail.mt_note')
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'success',
                'title': _('Bargaining request sent'),
                'message': (_('Emailed %(e)s vendor(s).', e=emailed) +
                            (_(' %(m)s had no email on file.', m=no_email) if no_email else '')),
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }

    def _fmt(self, amount):
        cur = self.currency_id
        return cur.symbol and ('%s %0.2f' % (cur.symbol, amount or 0.0)) or ('%0.2f' % (amount or 0.0))

    def _build_email_body(self, quotation):
        """Per-vendor email: greeting + global message + a table contrasting the
        vendor's current price with the buyer's target, plus per-item notes."""
        vendor_name = quotation.vendor_id.name or quotation.new_vendor_name or _('Vendor')
        rows = Markup('')
        # Map this vendor's current unit price per RFQ line.
        price_by_line = {ql.rfq_line_id.id: ql for ql in quotation.line_ids}
        for wl in self.line_ids:
            ql = price_by_line.get(wl.rfq_line_id.id)
            current = ql.price_total if ql else 0.0
            rows += Markup(
                '<tr>'
                '<td style="padding:6px 10px;border:1px solid #e5e7eb;">%(p)s</td>'
                '<td style="padding:6px 10px;border:1px solid #e5e7eb;text-align:right;">%(q)s</td>'
                '<td style="padding:6px 10px;border:1px solid #e5e7eb;text-align:right;">%(c)s</td>'
                '<td style="padding:6px 10px;border:1px solid #e5e7eb;text-align:right;color:#15803d;font-weight:700;">%(t)s</td>'
                '<td style="padding:6px 10px;border:1px solid #e5e7eb;color:#6b7280;">%(n)s</td>'
                '</tr>'
            ) % {
                'p': wl.product_id.display_name or '',
                'q': '%g %s' % (wl.qty, wl.uom_id.name or ''),
                'c': self._fmt(current),
                't': self._fmt(wl.total_target_price) if wl.total_target_price else '—',
                'n': wl.item_message or '',
            }
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        rfq_url = (
            f"{base_url}/web#id={quotation.rfq_id.id}"
            f"&model=ebshel.purchase.rfq"
            f"&view_type=form"
        )
        deadline = (_('Please reply by %s.', self.deadline.strftime('%d %b %Y'))
                    if self.deadline else '')
        return Markup(
            '<div style="font-family:sans-serif;color:#111827;">'
            '<p>Dear %(vendor)s,</p>'
            '%(msg)s'
            '<table style="border-collapse:collapse;margin:12px 0;font-size:13px;">'
            '<thead><tr style="background:#f3f4f6;">'
            '<th style="padding:6px 10px;border:1px solid #e5e7eb;text-align:left;">Item</th>'
            '<th style="padding:6px 10px;border:1px solid #e5e7eb;">Qty</th>'
            '<th style="padding:6px 10px;border:1px solid #e5e7eb;">Your Price</th>'
            '<th style="padding:6px 10px;border:1px solid #e5e7eb;">Target Price</th>'
            '<th style="padding:6px 10px;border:1px solid #e5e7eb;text-align:left;">Note</th>'
            '</tr></thead><tbody>%(rows)s</tbody></table>'
            '<p>%(deadline)s</p>'
            '<p style="margin-top:16px;">'
            '<a href="%(rfq_url)s" '
            'style="background-color:#875A7B;'
            'color:#ffffff;'
            'padding:10px 18px;'
            'text-decoration:none;'
            'border-radius:4px;'
            'display:inline-block;">'
            'View RFQ'
            '</a>'
            '</p>'
            '<p>Best regards,<br/>%(user)s</p>'
            '</div>'
        ) % {
            'vendor': vendor_name,
            'msg': self.global_message or '',
            'rows': rows,
            'deadline': deadline,
            'user': self.env.user.name,
            'rfq_url': rfq_url,
        }


class PrBargainWizardLine(models.TransientModel):
    _name = 'ebshel.pr.bargain.wizard.line'
    _description = 'Bargain Wizard Item'
    _order = 'id'

    wizard_id = fields.Many2one(
        'ebshel.pr.bargain.wizard', required=True, ondelete='cascade')
    rfq_line_id = fields.Many2one(
        'ebshel.purchase.rfq.line', string='RFQ Line', required=True)
    product_id = fields.Many2one(related='rfq_line_id.product_id', string='Product')
    qty = fields.Float(related='rfq_line_id.qty', string='Qty')
    uom_id = fields.Many2one(related='rfq_line_id.uom_id', string='UoM')
    currency_id = fields.Many2one(related='wizard_id.currency_id')
    target_price = fields.Monetary('Target Price', currency_field='currency_id')
    item_message = fields.Char('Item Message')
    total_target_price = fields.Monetary('Total Target Price', currency_field='currency_id', compute="_compute_total_price")


    @api.depends('qty', 'target_price')
    def _compute_total_price(self):
        for rec in self:
            rec.total_target_price = rec.qty * rec.target_price