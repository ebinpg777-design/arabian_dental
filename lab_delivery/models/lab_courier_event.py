# -*- coding: utf-8 -*-
"""Checkpoints on a consignment.

The lab has no API into any of the couriers it uses, so this is not a scraped feed —
it is what the office learns and writes down: the booking, the pickup call, what the
tracking page said this morning, the exception nobody would otherwise remember. That is
worth recording because the alternative is a WhatsApp thread, and a doctor asking "where
is my case" three days later cannot be answered from a WhatsApp thread.

The single entry point for writing one is `lab.delivery.record_event`, so a future
courier integration goes through the same door as a person typing.
"""
from odoo import api, fields, models

# Ordered as a parcel actually moves, so the latest event is also the furthest along.
STATUS = [
    ('booked', 'Booked'),
    ('picked_up', 'Picked Up'),
    ('in_transit', 'In Transit'),
    ('out_for_delivery', 'Out for Delivery'),
    ('delivered', 'Delivered'),
    ('exception', 'Exception'),
    ('returned', 'Returned to Sender'),
]
# Anything past this means the parcel has stopped moving on its own.
CLOSED_STATUS = ('delivered', 'returned')
TROUBLE_STATUS = ('exception', 'returned')


class LabCourierEvent(models.Model):
    _name = 'lab.courier.event'
    _description = 'Consignment Tracking Event'
    _order = 'event_datetime desc, id desc'

    delivery_id = fields.Many2one(
        'lab.delivery', required=True, index=True, ondelete='cascade',
        string='Delivery')
    courier_id = fields.Many2one(related='delivery_id.courier_id', store=True)
    awb = fields.Char(related='delivery_id.courier_awb', store=True,
                      string='Consignment No.')

    event_datetime = fields.Datetime(
        'When', required=True, default=fields.Datetime.now)
    status = fields.Selection(STATUS, required=True, default='in_transit')
    location = fields.Char(help="Where the parcel was when this was recorded.")
    note = fields.Char('Detail')
    source = fields.Selection(
        [('manual', 'Typed in'), ('import', 'Imported'), ('api', 'Courier feed')],
        default='manual', required=True, readonly=True)
    user_id = fields.Many2one('res.users', string='Recorded By', readonly=True,
                              default=lambda s: s.env.user)

    @api.model_create_multi
    def create(self, vals_list):
        events = super().create(vals_list)
        # One place where a checkpoint changes the delivery, whoever wrote it.
        events.delivery_id._apply_courier_events()
        return events

    def write(self, vals):
        res = super().write(vals)
        if {'status', 'event_datetime'} & set(vals):
            self.delivery_id._apply_courier_events()
        return res

    def unlink(self):
        deliveries = self.delivery_id
        res = super().unlink()
        deliveries.exists()._apply_courier_events()
        return res

    def name_get(self):
        labels = dict(STATUS)
        return [(e.id, "%s — %s" % (labels.get(e.status, ''), e.location or ''))
                for e in self]
