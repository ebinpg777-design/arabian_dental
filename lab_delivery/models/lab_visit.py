# -*- coding: utf-8 -*-
"""The visit ↔ delivery link, and the cross-check that keeps it honest (A-2.3).

A hand-delivery almost always happens *during* a clinic visit — the same person, the
same door, the same ten minutes. Recording them as unrelated objects means nobody can
answer "what did this trip actually achieve", and an executive who records "Delivered"
as a visit outcome while the delivery record still says "out for delivery" has created
two versions of the truth — with the delay engine escalating a delivery that already
happened.
"""
from datetime import timedelta

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.addons.lab_fieldwork.models.local_day import local_midnight_utc

OPEN_DELIVERY_STATES = ('draft', 'assigned', 'out')


class LabVisit(models.Model):
    _inherit = 'lab.visit'

    delivery_ids = fields.One2many(
        'lab.delivery', 'visit_id', string='Deliveries',
        help="Finished work handed over on this visit.")
    delivery_count = fields.Integer(compute='_compute_delivery_counts')
    # What is still in the bag for THIS clinic — the number that decides whether the
    # executive should be opening a delivery screen at all while standing here.
    pending_delivery_count = fields.Integer(compute='_compute_delivery_counts')
    open_pickup_count = fields.Integer(compute='_compute_delivery_counts')

    @api.depends('delivery_ids', 'partner_id', 'user_id', 'state')
    def _compute_delivery_counts(self):
        handed = dict(self.env['lab.delivery']._read_group(
            [('visit_id', 'in', self.ids)], ['visit_id'], ['__count']))
        # One grouped query for the whole set rather than a search per card: this is
        # read by My Day, which is opened on a phone on a bad connection.
        pending = dict(self.env['lab.delivery'].sudo()._read_group(
            [('partner_id', 'in', self.partner_id.ids),
             ('executive_id', 'in', self.user_id.ids),
             ('direction', '=', 'out'),
             ('state', 'in', OPEN_DELIVERY_STATES)],
            ['partner_id'], ['__count']))
        # And the other direction: bags collected here still on their way to the lab.
        pickups = dict(self.env['lab.delivery'].sudo()._read_group(
            [('partner_id', 'in', self.partner_id.ids),
             ('direction', '=', 'in'),
             ('state', 'in', OPEN_DELIVERY_STATES)],
            ['partner_id'], ['__count']))
        for visit in self:
            visit.delivery_count = handed.get(visit, 0)
            visit.pending_delivery_count = pending.get(visit.partner_id, 0)
            visit.open_pickup_count = pickups.get(visit.partner_id, 0)

    def action_view_deliveries(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': _('Deliveries'),
            'res_model': 'lab.delivery', 'view_mode': 'list,form',
            'views': [(False, 'list'), (False, 'form')],
            'domain': [('visit_id', '=', self.id)],
            'context': {'default_visit_id': self.id,
                        'default_executive_id': self.user_id.id},
        }

    def action_hand_over(self):
        """Open the handover wizard already knowing where it is standing."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': _('Hand Over Work'),
            'res_model': 'lab.delivery.new.wizard', 'view_mode': 'form',
            'views': [(False, 'form')], 'target': 'new',
            'context': {
                'default_visit_id': self.id,
                'default_partner_id': self.partner_id.id,
                'default_executive_id': self.user_id.id,
            },
        }

    def action_collect(self):
        """The other direction: collect work from the doctor for the lab."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': _('Collect for Lab'),
            'res_model': 'lab.delivery.new.wizard', 'view_mode': 'form',
            'views': [(False, 'form')], 'target': 'new',
            'context': {
                'default_direction': 'in',
                'default_visit_id': self.id,
                'default_partner_id': self.partner_id.id,
                'default_executive_id': self.user_id.id,
            },
        }

    def _attach_handovers(self):
        """The executive's hand-overs at this clinic earlier the same day, on this visit.

        A hand-over pressed during an open visit finds that visit itself
        (lab.delivery._attach_visit). Executives as often do it the other way round:
        scan the sticker at the door, press Delivered, and only then make the visit -
        two cases handed over at 11:41-11:42, the visit made at 11:42 - and the
        hand-over was then on no visit at all, as for nearly all of one executive's.
        So the visit takes them, when it starts and again when it ends.
        (client, 2026-10-28)
        """
        for visit in self:
            if not (visit.user_id and visit.partner_id and visit.date):
                continue
            start = local_midnight_utc(self.env, visit.date, visit.user_id)
            end = local_midnight_utc(self.env, visit.date + timedelta(days=1), visit.user_id)
            loose = self.env['lab.delivery'].sudo().search([
                ('visit_id', '=', False),
                ('executive_id', '=', visit.user_id.id),
                ('partner_id', '=', visit.partner_id.id),
                ('direction', '=', 'out'),
                ('state', '=', 'delivered'),
                ('delivered_datetime', '>=', start),
                ('delivered_datetime', '<', end),
            ])
            if loose:
                loose.write({'visit_id': visit.id})
        return True

    def do_check_in(self, *args, **kwargs):
        res = super().do_check_in(*args, **kwargs)
        self._attach_handovers()
        return res

    def do_check_out(self, *args, **kwargs):
        for visit in self:
            claims_delivery = (
                visit.outcome in ('delivered',)
                or any(o.is_delivery for o in visit.outcome_ids))
            if not claims_delivery:
                continue
            visit._attach_handovers()
            open_deliveries = self.env['lab.delivery'].search([
                ('partner_id', '=', visit.partner_id.id),
                ('executive_id', '=', visit.user_id.id),
                # A pickup still travelling to the lab is expected to be open at
                # checkout - it must never hold the executive hostage at the door.
                ('direction', '=', 'out'),
                ('state', 'in', ('assigned', 'out')),
            ])
            # A dispatch whose transfer the office already validated is paperwork
            # that never caught up, not work in the van: close it and let the
            # executive go, rather than refuse someone who really did deliver.
            open_deliveries = open_deliveries._close_if_shipped()
            if open_deliveries and visit.sudo().delivery_ids.filtered(
                    lambda d: d.direction == 'out' and d.state == 'delivered'):
                # The outcome is true: work was handed over on this visit. What else
                # is still open for the clinic - a case scanned at this door days ago
                # and never confirmed - is a question for the office, not a reason to
                # keep this executive at the door: one clinic's visits stayed open for
                # six days over two such cases. Said on the visit instead.
                # (client, 2026-10-28)
                visit.message_post(body=Markup('<br/>').join(
                    [_("Still open for %s after this visit:", visit.partner_id.display_name)]
                    + ['• ' + line for line in open_deliveries._work_lines()]))
                continue
            if open_deliveries:
                # Each case as the executive knows it - the order number, the patient
                # and the work, as on the case's sticker - not only DLV/..., a number
                # nobody at the clinic door can match to a box. (client, 2026-10-22)
                raise UserError(_(
                    "The outcome says Delivered, but this work for %(clinic)s is still "
                    "open:\n%(cases)s\n\nScan the sticker on each case you handed over "
                    "and press Delivered, then finish the visit - or correct the outcome. "
                    "Otherwise the delay engine keeps chasing work that already arrived.",
                    clinic=visit.partner_id.display_name,
                    cases='\n'.join('• ' + line for line in open_deliveries._work_lines())))
        return super().do_check_out(*args, **kwargs)
