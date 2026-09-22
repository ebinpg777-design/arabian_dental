# -*- coding: utf-8 -*-
"""Handing a parcel to a courier.

Two facts have to be captured at this exact moment or they are never captured at all:
which courier took it, and the number on the receipt in the sender's hand. Everything
downstream — the tracking link, the stalled check, what the doctor is told — is built
from those two.
"""
import base64

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class LabCourierDispatchWizard(models.TransientModel):
    _name = 'lab.courier.dispatch.wizard'
    _description = 'Send by Courier'

    delivery_id = fields.Many2one('lab.delivery', required=True, readonly=True)
    courier_id = fields.Many2one('lab.courier', string='Courier', required=True)
    courier_awb = fields.Char('Consignment No.', required=True)
    dispatched_at = fields.Datetime('Handed Over', required=True,
                                    default=fields.Datetime.now)
    expected_date = fields.Date('Expected Arrival')
    notify_doctor = fields.Boolean(
        'Tell the doctor', default=True,
        help="Posts the consignment number and tracking link on the order, so the "
             "clinic can follow it themselves instead of ringing the lab.")
    tracking_url = fields.Char(compute='_compute_tracking_url')
    # The receipt, photographed at the counter, and where the number came from.
    # The scan widget fills courier_awb / courier_id / awb_source from it; a person
    # can still just type. (client, 2026-08-28)
    receipt_image = fields.Image('Receipt', max_width=1400, max_height=1400)
    awb_source = fields.Selection(
        [('typed', 'Typed'), ('barcode', 'Read from barcode'),
         ('vision', 'Read from receipt'), ('both', 'Barcode and receipt agree')],
        default='typed')
    awb_looks_wrong = fields.Boolean(compute='_compute_awb_looks_wrong')
    awb_example = fields.Char(related='courier_id.awb_example')

    @api.depends('courier_id', 'courier_awb')
    def _compute_awb_looks_wrong(self):
        """A warning, not a wall: the pattern is the lab's own guess at the
        courier's format, and a courier can change it without telling anyone."""
        for wizard in self:
            wizard.awb_looks_wrong = bool(
                wizard.courier_id and wizard.courier_awb
                and wizard.courier_id.matches_awb(wizard.courier_awb) is False)

    @api.depends('courier_id', 'courier_awb')
    def _compute_tracking_url(self):
        for wizard in self:
            wizard.tracking_url = (
                wizard.courier_id.track_url_for(wizard.courier_awb)
                if wizard.courier_id else '')

    @api.onchange('courier_id', 'dispatched_at')
    def _onchange_expected(self):
        """Suggest the arrival date rather than making somebody count days."""
        if self.courier_id and self.dispatched_at:
            self.expected_date = fields.Date.add(
                self.dispatched_at.date(), days=self.courier_id.transit_days or 0)

    def action_confirm(self):
        self.ensure_one()
        delivery = self.delivery_id
        if delivery.state in ('delivered', 'cancel'):
            raise UserError(_("%s is already finished.", delivery.name))
        awb = (self.courier_awb or '').strip()
        if not awb:
            raise UserError(_("The consignment number is the only way to find this "
                              "parcel again."))
        clash = self.env['lab.delivery'].search([
            ('courier_id', '=', self.courier_id.id),
            ('courier_awb', '=ilike', awb),
            ('id', '!=', delivery.id),
        ], limit=1)
        if clash:
            raise UserError(_(
                "%(awb)s is already on %(other)s. Two parcels cannot share one "
                "consignment number — check the receipt.",
                awb=awb, other=clash.name))

        vals = {
            'delivery_mode': 'courier',
            'courier_id': self.courier_id.id,
            'courier_awb': awb,
            'courier_dispatched_at': self.dispatched_at,
            'courier_expected_date': self.expected_date,
            'state': 'out',
            'out_datetime': self.dispatched_at,
            'courier_is_stale': False,
        }
        if self.receipt_image:
            vals['courier_receipt_image'] = self.receipt_image
        delivery.with_context(lab_awb_source=self.awb_source or 'typed').write(vals)
        if self.receipt_image:
            # The receipt on the parcel's own thread, not the doctor's: it carries
            # the sender's phone and the counter's stamp, which are the lab's.
            delivery.message_post(
                body=_("Courier receipt for consignment %s.", awb),
                attachments=[('courier-receipt-%s.jpg' % awb,
                              base64.b64decode(self.receipt_image))])
        # The booking is itself a checkpoint: without it the stalled check has no
        # starting point and the tracking page opens onto an empty history.
        delivery.record_event('booked', when=self.dispatched_at,
                              location=self.courier_id.name,
                              note=_('Handed to courier'))
        if self.notify_doctor:
            delivery._post_courier_details_to_order()
        return {'type': 'ir.actions.act_window_close'}


class LabCourierEventWizard(models.TransientModel):
    """A tracking update, typed by whoever just read the courier's page."""
    _name = 'lab.courier.event.wizard'
    _description = 'Tracking Update'

    delivery_id = fields.Many2one('lab.delivery', required=True, readonly=True)
    courier_id = fields.Many2one(related='delivery_id.courier_id')
    courier_awb = fields.Char(related='delivery_id.courier_awb')
    tracking_url = fields.Char(related='delivery_id.courier_tracking_url')

    status = fields.Selection(
        selection=lambda self: self.env['lab.courier.event']._fields[
            'status'].selection,
        required=True, default='in_transit')
    event_datetime = fields.Datetime('When', required=True,
                                     default=fields.Datetime.now)
    location = fields.Char()
    note = fields.Char('Detail')

    def action_confirm(self):
        self.ensure_one()
        self.delivery_id.record_event(
            self.status, when=self.event_datetime,
            location=self.location, note=self.note)
        return {'type': 'ir.actions.act_window_close'}
