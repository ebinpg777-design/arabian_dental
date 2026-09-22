# -*- coding: utf-8 -*-
"""The visit ↔ delivery link, and the cross-check that keeps it honest (A-2.3).

A hand-delivery almost always happens *during* a clinic visit — the same person, the
same door, the same ten minutes. Recording them as unrelated objects means nobody can
answer "what did this trip actually achieve", and an executive who records "Delivered"
as a visit outcome while the delivery record still says "out for delivery" has created
two versions of the truth — with the delay engine escalating a delivery that already
happened.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError

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

    def do_check_out(self, *args, **kwargs):
        for visit in self:
            claims_delivery = (
                visit.outcome in ('delivered',)
                or any(o.is_delivery for o in visit.outcome_ids))
            if not claims_delivery:
                continue
            open_deliveries = self.env['lab.delivery'].search([
                ('partner_id', '=', visit.partner_id.id),
                ('executive_id', '=', visit.user_id.id),
                # A pickup still travelling to the lab is expected to be open at
                # checkout - it must never hold the executive hostage at the door.
                ('direction', '=', 'out'),
                ('state', 'in', ('assigned', 'out')),
            ])
            if open_deliveries:
                raise UserError(_(
                    "The outcome says Delivered, but %(names)s for this clinic "
                    "%(is_are)s still open. Mark the delivery itself as delivered "
                    "first, or correct the outcome — otherwise the delay engine keeps "
                    "chasing work that already arrived.",
                    names=', '.join(open_deliveries.mapped('name')),
                    is_are=_('is') if len(open_deliveries) == 1 else _('are')))
        return super().do_check_out(*args, **kwargs)
