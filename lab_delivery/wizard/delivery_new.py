# -*- coding: utf-8 -*-
"""Raising a delivery from where the work is: a clinic door, on a phone.

Most deliveries make themselves — `_lab_ensure_delivery` creates one the moment the
last manufacturing order finishes. This wizard is for the rest: the doctor who asks for
something on the spot, the case whose delivery was cancelled and needs re-raising, the
box that was simply never auto-created because the work was closed by hand.

The design point is that the executive picks *work*, not fields. Everything else —
who is carrying it, when it was promised, which visit it belongs to — is already known
from the fact that they are standing in a clinic with My Day open.
"""
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

CANDIDATE_LIMIT = 40


class LabDeliveryNewWizard(models.TransientModel):
    _name = 'lab.delivery.new.wizard'
    _description = 'Hand Over Finished Work'

    # Which way this trip goes. 'out' hands finished work to the doctor (the
    # original meaning); 'in' collects an impression from the doctor for the lab -
    # what the client asked Hand Over to become. One wizard for both, because the
    # executive is standing in the same doorway either way. (client, 2026-08-24)
    direction = fields.Selection(
        [('out', 'Hand over to doctor'), ('in', 'Collect for lab')],
        default='out', required=True)
    visit_id = fields.Many2one('lab.visit', string='During Visit', readonly=True)
    partner_id = fields.Many2one(
        'res.partner', string='Clinic', required=True,
        # Only clinics on a route this person works. An executive may hold several
        # routes, so this is a set test, not an equality. The record rule already
        # confines what they can READ; naming it here is what makes the dropdown
        # itself short and correct. (client, 2026-08-25)
        #
        # The hand-over side narrows it again to clinics that actually have work
        # waiting - that is done in the VIEW, where the domain can read `direction`,
        # because collecting must keep the full list: an impression can be picked up
        # from any clinic. (client, 2026-08-28)
        domain="[('lab_on_my_route', '=', True)]")
    waiting_partner_ids = fields.Many2many(
        'res.partner', 'lab_delivery_wiz_waiting_rel', 'wizard_id', 'partner_id',
        compute='_compute_waiting_partners')
    waiting_partner_count = fields.Integer(compute='_compute_waiting_partners')

    @api.depends('direction')
    def _compute_waiting_partners(self):
        """Clinics with confirmed work nobody is carrying yet.

        One grouped read over the stored flag rather than a search per clinic. On the
        collect side the domain must not narrow at all, so every readable clinic is
        offered - expressed as the id list the domain falls back to.
        """
        for wizard in self:
            if wizard.direction == 'in':
                # An id of 0 never matches, so the OR leaf beside it (`id = 0`) is what
                # keeps the domain open; here we simply offer everything on the route.
                wizard.waiting_partner_ids = False
                wizard.waiting_partner_count = 0
                continue
            groups = self.env['sale.order'].sudo()._read_group(
                [('lab_awaiting_delivery', '=', True)], ['partner_id'], ['__count'])
            # The clinics waiting are company-wide and an executive may read only
            # a few of them. Reading them as the executive raised "no read access
            # to Contact" on every Hand Over, and the form reads this list back as
            # them too - so it keeps only what they can read. The dropdown is
            # narrowed to their route anyway. (client, 2026-09-15)
            partners = self.env['res.partner'].sudo().browse([p.id for p, _c in groups])
            # A case is ordered by a doctor and delivered to their clinic; both the
            # contact and its commercial parent are offered so either can be picked.
            offered = (partners | partners.commercial_partner_id).with_env(
                self.env)._filtered_access('read')
            wizard.waiting_partner_ids = [(6, 0, offered.ids)]
            wizard.waiting_partner_count = len(partners.with_env(self.env) & offered)

    def action_open_worklist(self):
        """Everything waiting, whichever clinic it belongs to."""
        return self.env['sale.order'].action_lab_delivery_worklist()
    executive_id = fields.Many2one(
        'res.users', string='Carried By', required=True,
        default=lambda s: s.env.user,
        # Colleagues, not all 111 logins: work genuinely changes hands in the
        # field - one executive collects, another is driving past the lab - so an
        # executive may name any field person here, and only a field person.
        # (client, 2026-08-25, reversing the lock added the day before)
        domain="[('fw_is_field_person', '=', True)]")
    scheduled_date = fields.Datetime(
        string='Promised', required=True, default=fields.Datetime.now)
    delivery_mode = fields.Selection(
        [('executive', 'Executive'), ('courier', 'Courier'),
         ('staff', 'Lab Staff'), ('doctor_pickup', 'Doctor Pickup'),
         ('other', 'Bus / Parcel / Other')],
        default='executive', required=True)
    mode_note = fields.Char('How exactly')
    # What the bag carries on an inbound trip: today's slips for this clinic.
    case_ids = fields.Many2many(
        'lab.case', string='Case slips in the bag',
        domain="[('id', 'in', candidate_case_ids)]")
    candidate_case_ids = fields.Many2many(
        'lab.case', 'lab_pickup_wiz_case_rel', 'wizard_id', 'case_id',
        compute='_compute_candidate_cases')
    parcel_note = fields.Char(
        'What is in the parcel',
        help="When there is no slip yet: 'two impressions, Dr Priya's patients'.")

    @api.depends('partner_id', 'direction')
    def _compute_candidate_cases(self):
        """Slips written at this clinic whose impression has not reached the lab."""
        for wizard in self:
            if wizard.direction != 'in' or not wizard.partner_id:
                wizard.candidate_case_ids = False
                continue
            # sudo, exactly as the hand-over side does at _compute_candidates: the
            # "Case: own" rule limits an executive to slips they wrote themselves, so a
            # bag collected from a clinic could only ever list that one person's slips
            # — and a colleague covering the round saw an empty list. What they can pick
            # is still confined to the clinic they chose, and they can only choose a
            # clinic on their own route. (client, 2026-08-27)
            wizard.candidate_case_ids = self.env['lab.case'].sudo().search([
                ('partner_id', 'child_of',
                 wizard.partner_id.commercial_partner_id.id),
                ('state', '!=', 'cancel'),
                ('impression_received_at', '=', False),
                ('pickup_ids', 'not any',
                 [('state', 'in', ('draft', 'assigned', 'out'))]),
            ], order='id desc', limit=CANDIDATE_LIMIT)

    order_ids = fields.Many2many(
        'sale.order', string='Work to hand over',
        domain="[('id', 'in', candidate_order_ids)]")
    candidate_order_ids = fields.Many2many(
        'sale.order', 'lab_delivery_wiz_candidate_rel', 'wizard_id', 'order_id',
        compute='_compute_candidates')
    candidate_count = fields.Integer(compute='_compute_candidates')

    @api.depends('partner_id', 'direction')
    def _compute_candidates(self):
        """Confirmed work for this clinic that nobody is already carrying.

        Orders with a live delivery are deliberately excluded — offering them again is
        how one box ends up as two records and the delay engine chases a ghost.

        Work already handed over is excluded too, and by its own flag rather than by
        the absence of a dispatch: a delivery that later fails or is cancelled stops
        excluding its order — which is right, that case genuinely needs re-raising —
        but one the doctor has actually received must never come back onto the list.
        (client, 2026-08-22)
        """
        for wizard in self:
            if not wizard.partner_id or wizard.direction == 'in':
                # A pickup lists case slips, not orders - and skipping the search
                # matters beyond speed: it is what lets an executive with no order
                # access open the Collect side at all.
                wizard.candidate_order_ids = False
                wizard.candidate_count = 0
                continue
            # Work invoiced a week ago is not waiting in a bag to be handed over -
            # it left by some path nobody recorded, and offering it again only
            # manufactures a second delivery for a box the doctor already has.
            # (client, 2026-08-24)
            invoice_horizon = fields.Date.context_today(wizard) - timedelta(days=7)
            # sudo for the SEARCH only: the 7-day rule walks invoice_ids, and an
            # executive cannot read account.move. What lands in the list is still
            # just order ids for the clinic they picked - and partner scoping means
            # they can only pick their own route's clinics.
            orders = self.env['sale.order'].sudo().search([
                ('partner_id', 'child_of', wizard.partner_id.commercial_partner_id.id),
                ('state', 'in', ('sale', 'done')),
                ('lab_handed_over', '=', False),
                # Posted customer invoices only: `invoice_ids` alone also matches
                # drafts and credit notes, and a stray draft dated last month would
                # hide an order that was never actually billed.
                '!', ('invoice_ids', 'any', [
                    ('move_type', '=', 'out_invoice'),
                    ('state', '=', 'posted'),
                    ('invoice_date', '<', invoice_horizon),
                ]),
            ], order='date_order desc', limit=CANDIDATE_LIMIT)
            taken = dict(self.env['lab.delivery'].sudo()._read_group(
                [('sale_order_id', 'in', orders.ids),
                 ('direction', '=', 'out'),
                 ('state', 'not in', ('cancel', 'failed'))],
                ['sale_order_id'], ['__count']))
            free = orders.filtered(lambda o: o not in taken)
            wizard.candidate_order_ids = [(6, 0, free.ids)]
            wizard.candidate_count = len(free)

    @api.onchange('visit_id')
    def _onchange_visit(self):
        if self.visit_id:
            self.partner_id = self.visit_id.partner_id
            self.executive_id = self.visit_id.user_id

    def action_create(self):
        self.ensure_one()
        if self.direction == 'in':
            return self._action_create_pickup()
        if not self.order_ids:
            raise UserError(_(
                "Pick at least one case to hand over. If nothing is listed, the work "
                "for this clinic is either not confirmed yet or already on its way."))

        Delivery = self.env['lab.delivery']
        vals_list = []
        for order in self.order_ids:
            # Re-checked at the moment of writing, not just when the list was drawn:
            # two executives can be looking at the same clinic on two phones.
            if Delivery.sudo().search_count([
                    ('sale_order_id', '=', order.id),
                    ('direction', '=', 'out'),
                    ('state', 'not in', ('cancel', 'failed'))]):
                continue
            vals_list.append({
                'sale_order_id': order.id,
                'executive_id': self.executive_id.id,
                'scheduled_date': self.scheduled_date,
                'delivery_mode': self.delivery_mode,
                # Typed on both sides of the wizard, kept on only one: "bus, parcel
                # office, who is driving" is exactly the note the office needs when a
                # hand-over goes by anything other than an executive. (client, 2026-08-27)
                'mode_note': self.mode_note,
                'visit_id': self.visit_id.id or False,
                # Raised by the person who is about to carry it, so it is theirs
                # already — making them press Assign next would be theatre.
                'state': 'assigned',
            })
        if not vals_list:
            raise UserError(_(
                "Somebody is already carrying every case you picked. Refresh and check "
                "the delivery list before raising another."))
        deliveries = Delivery.create(vals_list)
        if self.visit_id:
            self.visit_id.message_post(body=_(
                "Handed over on this visit: %s", ', '.join(deliveries.mapped('name'))))
        return self._open_created(deliveries)

    def _action_create_pickup(self):
        """One bag, one journey: the slips picked plus whatever the note says."""
        if not self.case_ids and not self.parcel_note:
            raise UserError(_(
                "Say what you are collecting - pick the case slips, or describe "
                "the parcel if no slip has been written yet."))
        delivery = self.env['lab.delivery'].create({
            'direction': 'in',
            'partner_id': self.partner_id.id,
            'executive_id': self.executive_id.id,
            'scheduled_date': self.scheduled_date,
            'delivery_mode': self.delivery_mode,
            'mode_note': self.mode_note,
            'visit_id': self.visit_id.id or False,
            'case_ids': [(6, 0, self.case_ids.ids)],
            'fail_reason': False,
            # Collected by the person raising it, already in their hand.
            'state': 'assigned',
        })
        if self.parcel_note:
            delivery.message_post(body=_("In the parcel: %s", self.parcel_note))
        if self.visit_id:
            self.visit_id.message_post(body=_(
                "Collected for the lab on this visit: %s", delivery.name))
        return self._open_created(delivery)

    def _open_created(self, deliveries):
        return {
            'type': 'ir.actions.act_window', 'name': _('Deliveries'),
            'res_model': 'lab.delivery',
            'view_mode': 'form' if len(deliveries) == 1 else 'list,form',
            'views': ([(False, 'form')] if len(deliveries) == 1
                      else [(False, 'list'), (False, 'form')]),
            'res_id': deliveries.id if len(deliveries) == 1 else False,
            'domain': [('id', 'in', deliveries.ids)],
        }
