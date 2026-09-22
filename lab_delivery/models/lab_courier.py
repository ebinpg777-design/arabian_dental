# -*- coding: utf-8 -*-
"""Courier companies, and where their tracking page lives.

`courier_name` used to be free text, which meant "DTDC", "Dtdc" and "dtdc courier" were
three couriers, none of them linkable to anything. A consignment number is only useful
if it can be turned into a page somebody can open, so the tracking URL belongs to the
courier, not retyped per parcel.
"""
import re
from urllib.parse import quote

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

AWB_TOKEN = '{awb}'


class LabCourier(models.Model):
    _name = 'lab.courier'
    _description = 'Courier Company'
    _order = 'sequence, name'

    name = fields.Char(required=True)
    code = fields.Char(help="Short code used on labels and in reports.")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    phone = fields.Char()
    email = fields.Char()
    website = fields.Char()

    tracking_url = fields.Char(
        'Tracking URL',
        help="The courier's tracking page, with %s where the consignment number "
             "goes. Example: https://example.com/track?no=%s\n"
             "Couriers change these; edit it here and every parcel follows."
             % (AWB_TOKEN, AWB_TOKEN))
    transit_days = fields.Integer(
        'Usual Transit Days', default=2,
        help="Used to suggest the expected delivery date at dispatch.")

    # What this courier's consignment numbers look like. A receipt carries half a
    # dozen numbers — booking ref, phone, GST, pin code, weight — and the one that
    # matters is recognisable by its shape. The shape is data, not code: couriers
    # change formats and the lab adds couriers, and neither should need a release.
    # (client, 2026-08-28)
    awb_pattern = fields.Char(
        'Consignment No. Pattern',
        help="A regular expression the whole consignment number must match, e.g. "
             "^[0-9]{8,11}$ for an 8–11 digit number. Used to recognise the number on "
             "a scanned receipt and to warn when a typed number does not look right. "
             "Leave empty to accept anything.")
    awb_example = fields.Char(
        'Example', help="A sample number shown as a placeholder when typing.")
    awb_s10 = fields.Boolean(
        'UPU S10 (postal) numbers',
        help="Postal consignments (Speed Post, Registered Post) follow the UPU S10 "
             "standard: two letters, nine digits, a country code — and the ninth "
             "digit is a check digit, so a misread number can be detected.")

    @api.constrains('awb_pattern')
    def _check_awb_pattern(self):
        for courier in self.filtered('awb_pattern'):
            try:
                re.compile(courier.awb_pattern)
            except re.error as exc:
                raise ValidationError(_(
                    "The consignment pattern for %(name)s is not a valid regular "
                    "expression: %(err)s", name=courier.name, err=exc)) from exc

    def matches_awb(self, awb):
        """Does `awb` look like one of this courier's numbers?

        Returns None when the courier has no pattern (nothing to say), True/False
        otherwise. Whitespace inside the number is ignored: receipts print numbers
        in groups of four and people type them the same way.
        """
        self.ensure_one()
        if not self.awb_pattern:
            return None
        token = re.sub(r'\s+', '', awb or '')
        return bool(re.fullmatch(self.awb_pattern, token, re.IGNORECASE))

    delivery_count = fields.Integer(compute='_compute_delivery_count')

    def _compute_delivery_count(self):
        counts = dict(self.env['lab.delivery']._read_group(
            [('courier_id', 'in', self.ids)], ['courier_id'], ['__count']))
        for courier in self:
            courier.delivery_count = counts.get(courier, 0)

    @api.constrains('tracking_url')
    def _check_tracking_url(self):
        for courier in self:
            url = (courier.tracking_url or '').strip()
            if not url:
                continue
            if AWB_TOKEN not in url:
                raise ValidationError(_(
                    "The tracking URL for %(name)s has nowhere to put the consignment "
                    "number. Put %(token)s where it belongs, e.g. "
                    "https://example.com/track?no=%(token)s",
                    name=courier.name, token=AWB_TOKEN))
            if not url.lower().startswith(('http://', 'https://')):
                raise ValidationError(_(
                    "The tracking URL for %s must start with http:// or https://.",
                    courier.name))

    def track_url_for(self, awb):
        """The page a person can actually open for this consignment."""
        self.ensure_one()
        awb = (awb or '').strip()
        if not awb or not self.tracking_url:
            return ''
        return self.tracking_url.replace(AWB_TOKEN, quote(awb, safe=''))

    def action_view_deliveries(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': _('Consignments'),
            'res_model': 'lab.delivery', 'view_mode': 'list,form',
            'views': [(False, 'list'), (False, 'form')],
            'domain': [('courier_id', '=', self.id)],
            'context': {'default_courier_id': self.id,
                        'default_delivery_mode': 'courier'},
        }
