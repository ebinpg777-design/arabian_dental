# -*- coding: utf-8 -*-
"""Deliveries on the executive's home screen.

My Day is the executive's whole application, and what is in their bag right now belongs
on it as plainly as the visits do. Delivered late is almost always delivered *forgotten*
— so the box has to be visible on the screen they already have open, not one menu away
in a module they have no reason to browse.

Added here rather than in lab_fieldwork because deliveries are the downstream module:
My Day must keep working for a lab that never installs this.
"""
from datetime import timedelta

from odoo import api, fields, models

from odoo.addons.lab_fieldwork.models.local_day import local_midnight_utc
from odoo.addons.lab_fieldwork.models.lab_visit import _map_url_and_address

OPEN_STATES = ('draft', 'assigned', 'out')
# More than this in one bag is a planning problem, not a screen problem — and a phone
# list that never ends is a list nobody reads to the bottom of.
MAX_ON_SCREEN = 12


class LabMyDay(models.AbstractModel):
    _inherit = 'lab.my.day'

    @api.model
    def get_day(self, day=None):
        data = super().get_day(day)
        Delivery = self.env['lab.delivery']
        mine = Delivery.search(
            [('executive_id', '=', self.env.uid), ('state', 'in', OPEN_STATES)],
            limit=MAX_ON_SCREEN)

        # Read elevated. The SEARCH above is already scoped to this executive, so
        # nothing extra becomes visible — but the office raises and confirms the
        # orders, and under Sales/User "Own Documents Only" the executive cannot read
        # the order behind a parcel they are carrying. Reaching through to it
        # unelevated takes their whole home screen down with an AccessError.
        # Once and translated, not a dict rebuilt from the raw selection per box.
        state_labels = dict(Delivery._fields['state']._description_selection(self.env))
        data['deliveries'] = [{
            'id': d.id,
            'name': d.name,
            'clinic': d.partner_id.display_name,
            'partner_id': d.partner_id.id,
            'patient': d.patient or '',
            'order': d.sale_order_id.name,
            'invoice': d.invoice_id.name or '',
            'state': d.state,
            'state_label': state_labels.get(d.state, ''),
            'scheduled': fields.Datetime.to_string(d.scheduled_date),
            'scheduled_label': self._when_label(d.scheduled_date),
            'emergency': d.is_emergency,
            'delayed': d.is_delayed,
            'delay_hours': round(d.delay_hours or 0.0, 1),
            'out': d.state == 'out',
            # Which WAY this parcel is going. Without it the card had one primary
            # button whose behaviour was decided by `out` alone, so finishing an
            # inbound pickup opened the hand-over-to-the-doctor wizard and recorded a
            # bag the lab had just taken IN as "delivered to the clinic".
            # (client, 2026-08-27)
            'direction': d.direction,
            # The same two phone jobs the visit cards offer: a delivery without a way
            # to reach the clinic is a delivery that gets driven to a locked door.
            'map_url': (nav := _map_url_and_address(d.partner_id))[0],
            'map_address': nav[1],
            'call': d.partner_id.phone or '',
            # A parcel with a courier is not something the executive drives to.
            'is_courier': d.delivery_mode == 'courier',
            'courier': d.courier_id.name or '',
            'awb': d.courier_awb or '',
            'tracking_url': d.courier_tracking_url or '',
            'courier_stale': d.courier_is_stale,
        } for d in mine.sudo()]
        data['delivery_summary'] = {
            'open': len(mine),
            'late': len(mine.filtered('is_delayed')),
            'emergency': len(mine.filtered('is_emergency')),
        }
        # The third door count on the strip, next to cases and reworks: boxes
        # this person handed to doctors on the day shown. (client, 2026-09-08)
        data['summary']['dispatched'] = self._dispatched_on(
            fields.Date.from_string(data['date']))

        # Per-card: what is in the bag for the clinic being visited right now.
        pending = dict(Delivery.sudo()._read_group(
            [('executive_id', '=', self.env.uid), ('state', 'in', OPEN_STATES)],
            ['partner_id'], ['__count']))
        by_partner = {p.id: c for p, c in pending.items()}
        visits = self.env['lab.visit'].browse(
            [v['id'] for v in data.get('visits', [])])
        partner_of = {v.id: v.partner_id.id for v in visits}
        for row in data.get('visits', []):
            row['deliveries_pending'] = by_partner.get(partner_of.get(row['id']), 0)
        return data

    @api.model
    def _dispatched_on(self, day):
        """Outbound boxes this user delivered on `day`, in their own time zone.

        Scoped to the user by the stamp the hand-over writes (delivered_by_id),
        so a stand-in manager opening My Day sees their own zero, not the
        executive's number. Read elevated: an aggregate over the user's own
        rows, and the order behind a box is not theirs to read.
        """
        # Not `user.tz or 'UTC'`: most users carry no tz, and UTC started their day
        # at 05:30, counting the boxes of the small hours towards yesterday.
        start = local_midnight_utc(self.env, day)
        return self.env['lab.delivery'].sudo().search_count([
            ('delivered_by_id', '=', self.env.uid),
            ('direction', '=', 'out'),
            ('state', '=', 'delivered'),
            ('delivered_datetime', '>=', start),
            ('delivered_datetime', '<', start + timedelta(days=1)),
        ])

    @api.model
    def _when_label(self, when):
        """"Today 15:30" beats a full timestamp on a 5-inch screen."""
        if not when:
            return ''
        local = fields.Datetime.context_timestamp(self, when)
        today = fields.Date.context_today(self)
        if local.date() == today:
            return local.strftime('%H:%M')
        if local.date() < today:
            return local.strftime('%d %b %H:%M')
        return local.strftime('%d %b %H:%M')

    # ------------------------------------------------------------------ actions
    @api.model
    def action_new_delivery(self, visit_id=None, direction='out'):
        """Open the handover wizard, prefilled from wherever it was pressed.

        `direction='in'` opens the same wizard as Collect for Lab: the return leg,
        an impression picked up at the clinic and carried or couriered to the lab.
        """
        context = {'default_executive_id': self.env.uid,
                   'default_direction': direction}
        visit = self.env['lab.visit'].browse(int(visit_id)) if visit_id else None
        if visit and visit.exists():
            context.update(default_visit_id=visit.id,
                           default_partner_id=visit.partner_id.id,
                           default_executive_id=visit.user_id.id)
        return {
            'type': 'ir.actions.act_window',
            'name': 'Collect for Lab' if direction == 'in' else 'Hand Over Work',
            'res_model': 'lab.delivery.new.wizard',
            'view_mode': 'form',
            # `views` is not optional here: this dict is handed straight to the JS
            # action service, which reads action.views and has no fallback to
            # view_mode the way a form-button round trip does.
            'views': [(False, 'form')],
            'target': 'new',
            'context': context,
        }
