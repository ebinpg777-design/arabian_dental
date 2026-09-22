# -*- coding: utf-8 -*-
"""A delivery as a first-class object (R4–R8).

Finished lab work is mostly hand-carried by the same executives who collected the
impression. `stock.picking` records the inventory truth; it cannot say *who is carrying
the box right now*, *when it was promised*, or *that it was left with the pharmacy next
door because the clinic had closed*. This model says exactly that, and the delay engine
watches it.
"""
from collections import defaultdict
from datetime import timedelta

from markupsafe import Markup, escape

import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from odoo.addons.lab_fieldwork.models.lab_visit import metres_between

from .lab_courier_event import CLOSED_STATUS, STATUS as COURIER_STATUS, TROUBLE_STATUS

_logger = logging.getLogger(__name__)

OPEN_STATES = ('draft', 'assigned', 'out')


class LabDelivery(models.Model):
    _name = 'lab.delivery'
    _description = 'Lab Work Delivery'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    # Emergencies first, then the most overdue: the board is a to-do list, and the top
    # of it must be the thing to do next.
    _order = 'is_emergency desc, scheduled_date, id'

    name = fields.Char(default=lambda s: _('New'), copy=False, readonly=True)
    # Which way the parcel travels. 'out' is the original meaning of this model -
    # finished work to the doctor. 'in' is the return leg the client asked Hand Over
    # to become: an impression or job COLLECTED at the clinic and carried or couriered
    # to the lab. One model for both on purpose: the courier ladder, the delay engine,
    # the stale-consignment cron and the tracking events are direction-neutral, and a
    # second model would have duplicated all of them. (client, 2026-08-24)
    direction = fields.Selection(
        [('out', 'Delivery to Doctor'), ('in', 'Pickup to Lab')],
        default='out', required=True, index=True, tracking=True)
    sale_order_id = fields.Many2one(
        'sale.order', index=True, ondelete='restrict',
        string='Order', tracking=True,
        help="Required for a delivery to the doctor. A pickup usually has no order "
             "yet - the work it carries only becomes an order at registration.")
    # Computed-with-fallback rather than related: a pickup has no order to relate
    # through, but every count, the visit matcher and the record scoping key on the
    # clinic, so the field must hold a value of its own.
    partner_id = fields.Many2one(
        'res.partner', compute='_compute_from_order', store=True, readonly=False,
        index=True, string='Doctor / Clinic', tracking=True)
    patient = fields.Char(compute='_compute_from_order', store=True, readonly=False)
    # The invoice a courier or a doctor's receptionist actually asks for by number.
    # Stored: it is the column the dispatch list is scanned by, not a form-only
    # lookup. Picks the order's own posted sale invoice, not a credit note.
    invoice_id = fields.Many2one(
        'account.move', compute='_compute_invoice_id', store=True,
        string='Invoice', readonly=True)
    # No `default=` on these: a default counts as a provided value at create, and a
    # provided value SUPPRESSES the compute - the order's priority then never synced
    # and an emergency order produced a normal delivery. The fallbacks live inside
    # the compute instead. (2026-08-24)
    priority = fields.Selection(
        [('normal', 'Normal'), ('urgent', 'Urgent'), ('emergency', 'Emergency')],
        compute='_compute_from_order', store=True, readonly=False)
    company_id = fields.Many2one(
        'res.company', compute='_compute_from_order', store=True, readonly=False)
    currency_id = fields.Many2one(related='company_id.currency_id')
    # What an inbound trip is carrying: the case slips written at the clinic. One
    # pickup is one bag - N slips, one clinic, one journey to the lab.
    case_ids = fields.Many2many(
        'lab.case', 'lab_delivery_case_rel', 'delivery_id', 'case_id',
        string='Case Slips', copy=False)

    executive_id = fields.Many2one(
        'res.users', string='Carried By', tracking=True, index=True,
        default=lambda s: s.env.user,
        # Field people only - the picker is colleagues, not every login.
        domain="[('fw_is_field_person', '=', True)]")
    # The trip the box actually travelled on. Most hand-deliveries happen *during* a
    # clinic visit, and without this the two records only ever met by coincidence of
    # partner and date — which is not something a report can be built on.
    visit_id = fields.Many2one(
        'lab.visit', string='Handed Over During', index=True, ondelete='set null',
        copy=False, tracking=True,
        help="The clinic visit this delivery was handed over on.")
    delivery_mode = fields.Selection(
        [('executive', 'Executive'), ('courier', 'Courier'),
         ('staff', 'Lab Staff'), ('doctor_pickup', 'Doctor Pickup'),
         ('other', 'Bus / Parcel / Other')],
        default='executive', required=True, tracking=True)
    mode_note = fields.Char(
        'How exactly', help="Bus service, parcel office, a relative driving down - "
        "whatever 'other' actually was, so the next person can chase it.")
    # --- courier (R4b)
    courier_id = fields.Many2one(
        'lab.courier', string='Courier', index=True, ondelete='restrict',
        tracking=True)
    courier_awb = fields.Char('Consignment No.', tracking=True, copy=False)
    courier_name = fields.Char(
        string='Courier (legacy)',
        help="Free-text courier from before the courier list existed. Kept so no "
             "record loses what was typed; use Courier instead.")
    courier_tracking_url = fields.Char(compute='_compute_courier_tracking_url')
    # The receipt, and where the number on it came from. A number that was READ
    # (barcode, or the receipt itself) carries its evidence; a typed one carries the
    # person. When a parcel goes missing the first question is "is that number even
    # right", and this answers it. (client, 2026-08-28)
    courier_receipt_image = fields.Image(
        'Courier Receipt', max_width=1400, max_height=1400, copy=False)
    courier_awb_source = fields.Selection(
        [('typed', 'Typed'), ('barcode', 'Read from barcode'),
         ('vision', 'Read from receipt'), ('both', 'Barcode and receipt agree')],
        string='Number Came From', copy=False, readonly=True)
    courier_awb_captured_by_id = fields.Many2one(
        'res.users', 'Number Entered By', readonly=True, copy=False)
    courier_awb_captured_at = fields.Datetime(
        'Number Entered At', readonly=True, copy=False)
    courier_dispatched_at = fields.Datetime(readonly=True, copy=False)
    courier_expected_date = fields.Date(
        'Expected', copy=False,
        help="When the courier says it should arrive. Drives the stalled check.")
    courier_event_ids = fields.One2many(
        'lab.courier.event', 'delivery_id', string='Tracking')
    courier_event_count = fields.Integer(compute='_compute_courier_event_count')
    courier_status = fields.Selection(
        COURIER_STATUS, compute='_compute_courier_progress', store=True,
        tracking=True, string='Consignment Status')
    courier_last_event_at = fields.Datetime(
        compute='_compute_courier_progress', store=True, string='Last Scan')
    # Set by the cron rather than computed: "nothing has happened for three days" is a
    # statement about the passage of time, and a compute would only be right at the
    # moment something else happened to recompute it.
    courier_is_stale = fields.Boolean(readonly=True, copy=False, index=True)
    courier_days_silent = fields.Integer(compute='_compute_courier_days_silent')

    # --- where the executive was standing when they DELIVERED it
    #
    # Visible to the field manager AND to an administrator: a pure administrator
    # holds neither field-work role, so security/ir.model.access.csv grants
    # base.group_system read on this model - without it "an administrator can see
    # the location" was simply untrue. (client, 2026-08-28)
    #
    # Captured at the handover, not at creation: the question a manager asks of this
    # field is "was it really handed over at the clinic", and only the moment the
    # Delivered button is pressed can answer that. A record raised in the morning and
    # delivered in the afternoon would otherwise carry the office's coordinates.
    # Written once, when the state becomes delivered, and never afterwards.
    # (client, 2026-08-28)
    delivered_lat = fields.Float(digits=(10, 7), readonly=True, copy=False,
                                 groups='lab_fieldwork.group_fieldwork_manager,base.group_system')
    delivered_lon = fields.Float(digits=(10, 7), readonly=True, copy=False,
                                 groups='lab_fieldwork.group_fieldwork_manager,base.group_system')
    delivered_accuracy_m = fields.Float(
        'Fix Accuracy (m)', readonly=True, copy=False, groups='lab_fieldwork.group_fieldwork_manager,base.group_system',
        help="How tight the phone's fix was. A 2,000 m reading is a tower, not a "
             "doorway, and should not be read as proof of anything.")
    delivered_by_id = fields.Many2one(
        'res.users', 'Handed Over By', readonly=True, copy=False, groups='lab_fieldwork.group_fieldwork_manager,base.group_system',
        help="Whoever pressed Delivered. Not always the executive the work was "
             "assigned to - work changes hands in the field.")
    delivered_distance_m = fields.Float(
        'From the Clinic (m)', compute='_compute_delivered_gps', store=True,
        groups='lab_fieldwork.group_fieldwork_manager,base.group_system')
    delivered_gps_state = fields.Selection(
        [('ok', 'At the clinic'), ('far', 'Away from the clinic'),
         ('nopin', 'Clinic not pinned'), ('nofix', 'No location')],
        string='Where It Was Delivered', compute='_compute_delivered_gps', store=True,
        groups='lab_fieldwork.group_fieldwork_manager,base.group_system')
    delivered_map_url = fields.Char(
        compute='_compute_delivered_gps', store=True,
        groups='lab_fieldwork.group_fieldwork_manager,base.group_system')

    scheduled_date = fields.Datetime(
        required=True, tracking=True, default=fields.Datetime.now,
        help="When the doctor was told it would be there.")
    state = fields.Selection(
        [('draft', 'To Assign'), ('assigned', 'Assigned'),
         ('out', 'Out for Delivery'), ('delivered', 'Delivered'),
         ('failed', 'Failed'), ('cancel', 'Cancelled')],
        default='draft', required=True, tracking=True, copy=False, index=True)
    out_datetime = fields.Datetime(readonly=True, copy=False)
    delivered_datetime = fields.Datetime(readonly=True, copy=False)

    # --- outcome (R5)
    delivery_outcome = fields.Selection(
        [('clinic', 'Delivered to Clinic / Doctor'),
         ('near_place', 'Delivered to Near Place'),
         ('lab', 'Received at Lab')],
        readonly=True, copy=False, tracking=True)
    # Reception detail for inbound work: who took it in and in what condition.
    received_condition = fields.Selection(
        [('ok', 'In good condition'), ('damaged', 'Damaged')], copy=False,
        readonly=True, tracking=True)
    near_place_name = fields.Char(
        'Left At', readonly=True, copy=False,
        help="Where exactly the package was left when the clinic could not take it.")
    received_by = fields.Char(readonly=True, copy=False)
    proof_image = fields.Image(max_width=1400, max_height=1400, copy=False)
    fail_reason = fields.Char(copy=False, tracking=True)

    # --- emergency (R6)
    is_emergency = fields.Boolean(
        compute='_compute_is_emergency', store=True, readonly=False, tracking=True)
    emergency_reason = fields.Char(copy=False)
    promised_datetime = fields.Datetime(
        copy=False, help="The hard promise an emergency is judged against.")
    authorized_by_id = fields.Many2one('res.users', readonly=True, copy=False)

    # --- delay engine (R7/R8)
    is_delayed = fields.Boolean(readonly=True, copy=False, index=True)
    delay_hours = fields.Float(readonly=True, copy=False, digits=(8, 1))
    late_delivered = fields.Boolean(
        readonly=True, copy=False,
        help="Delivered, but after the deadline — kept for the monthly report.")
    first_delayed_notified = fields.Boolean(readonly=True, copy=False)
    escalation_count = fields.Integer(readonly=True, copy=False)
    last_escalation_at = fields.Datetime(readonly=True, copy=False)

    @api.depends('delivered_lat', 'delivered_lon', 'partner_id.partner_latitude',
                 'partner_id.partner_longitude', 'partner_id.visit_radius_m')
    def _compute_delivered_gps(self):
        """How far from the clinic the handover was confirmed.

        Same vocabulary as a visit's location check, deliberately: a manager who has
        learned to read 'far' on a visit reads it the same way here. A clinic with no
        pin says so rather than reporting a distance from the equator.
        """
        # The same radius a visit is judged against, from the same setting: one
        # definition of "at the clinic" across the app.
        default_radius = float(self.env['ir.config_parameter'].sudo().get_param(
            'lab_fieldwork.visit_radius_m', 300))
        for delivery in self:
            clinic = delivery.partner_id
            delivery.delivered_map_url = (
                'https://www.google.com/maps/search/?api=1&query=%s,%s'
                % (delivery.delivered_lat, delivery.delivered_lon)
            ) if (delivery.delivered_lat or delivery.delivered_lon) else False
            metres = metres_between(delivery.delivered_lat, delivery.delivered_lon,
                                    clinic.partner_latitude, clinic.partner_longitude)
            if not (delivery.delivered_lat or delivery.delivered_lon):
                delivery.delivered_distance_m = 0.0
                delivery.delivered_gps_state = 'nofix'
            elif not (clinic and clinic.partner_latitude and clinic.partner_longitude):
                delivery.delivered_distance_m = 0.0
                delivery.delivered_gps_state = 'nopin'
            elif metres is None:
                delivery.delivered_distance_m = 0.0
                delivery.delivered_gps_state = 'nofix'
            else:
                radius = clinic.visit_radius_m or default_radius
                delivery.delivered_distance_m = round(metres, 1)
                delivery.delivered_gps_state = 'ok' if metres <= radius else 'far'

    def action_open_delivered_map(self):
        """The pin, on a map, in a new tab."""
        self.ensure_one()
        if not self.delivered_map_url:
            raise UserError(_("%s carries no handover location.", self.name))
        return {'type': 'ir.actions.act_url', 'url': self.delivered_map_url,
                'target': 'new'}

    @api.depends('sale_order_id', 'sale_order_id.priority', 'sale_order_id.patient',
                 'sale_order_id.partner_id', 'sale_order_id.company_id')
    def _compute_from_order(self):
        for delivery in self:
            order = delivery.sale_order_id
            if order:
                delivery.partner_id = order.partner_id
                delivery.patient = order.patient
                delivery.priority = order.priority
                delivery.company_id = order.company_id
            else:
                delivery.partner_id = delivery.partner_id
                delivery.patient = delivery.patient
                delivery.priority = delivery.priority or 'normal'
                delivery.company_id = delivery.company_id or self.env.company

    @api.depends('sale_order_id.invoice_ids', 'sale_order_id.invoice_ids.state',
                 'sale_order_id.invoice_ids.move_type')
    def _compute_invoice_id(self):
        for delivery in self:
            invoices = delivery.sale_order_id.invoice_ids.filtered(
                lambda m: m.move_type == 'out_invoice' and m.state == 'posted')
            delivery.invoice_id = invoices[-1] if invoices else False

    @api.constrains('direction', 'sale_order_id', 'partner_id')
    def _check_direction_anchor(self):
        # ValidationError, not UserError: a constraint is a rule about the record,
        # and Odoo reports it as one (and rolls the write back) only for this type.
        for delivery in self:
            if delivery.direction == 'out' and not delivery.sale_order_id:
                raise ValidationError(_(
                    "A delivery to the doctor needs its order - that is what says "
                    "which box this is."))
            if delivery.direction == 'in' and not delivery.partner_id:
                raise ValidationError(_(
                    "A pickup needs the clinic it was collected from."))

    @api.depends('priority')
    def _compute_is_emergency(self):
        for delivery in self:
            if delivery.priority == 'emergency':
                delivery.is_emergency = True

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                code = ('lab.delivery.pickup' if vals.get('direction') == 'in'
                        else 'lab.delivery')
                # sudo, as core does for every numbered document: an executive
                # raising a hand-over has no read right on ir.sequence.
                vals['name'] = self.env['ir.sequence'].sudo().next_by_code(code) or _('New')
        deliveries = super().create(vals_list)
        for delivery in deliveries.filtered('is_emergency'):
            delivery._announce_emergency()
        return deliveries

    def write(self, vals):
        became_emergency = vals.get('is_emergency') and self.filtered(
            lambda d: not d.is_emergency)
        if vals.get('is_emergency'):
            # A manual emergency needs its reason and promise; an automatic one (from
            # the order's priority) inherits the schedule as its promise.
            for delivery in self:
                if not (vals.get('emergency_reason') or delivery.emergency_reason) \
                        and delivery.priority != 'emergency':
                    raise UserError(_(
                        "Say why %s is an emergency — everything downstream reacts "
                        "to this flag.", delivery.name))
            vals.setdefault('authorized_by_id', self.env.uid)
        newly_delivered = self.env['lab.delivery']
        if vals.get('state') == 'delivered':
            newly_delivered = self.filtered(lambda d: d.state != 'delivered')
            # The phone that confirmed the handover puts its fix in the context.
            # Stamped here rather than in each wizard: the done wizard, the receive
            # wizard, the form and the courier feed all arrive at this one write, so
            # there is no path that can forget. Only on the transition, and only
            # where nothing is recorded yet - a re-save must not move the pin.
            context = self.env.context
            lat = context.get('lab_delivered_lat')
            lon = context.get('lab_delivered_lon')
            for delivery in newly_delivered:
                # Superuser for the whole stamp, reads included: the location
                # fields are restricted to managers with `groups=`, and a field
                # group blocks READING as much as writing - so the very executive
                # whose fix is being recorded crashed here with "not enough rights
                # on delivered_lat" the moment they confirmed a hand-over. The
                # stamp is system bookkeeping about the executive, not data the
                # executive edits, which is exactly what sudo is for.
                # (client, 2026-08-28)
                privileged = delivery.sudo()
                stamp = {'delivered_by_id': self.env.uid}
                # The moment, too: the wizards pass it, but a state change from a
                # list view or an automation does not, and "delivered when?" must
                # never be unanswerable.
                if not vals.get('delivered_datetime') and not privileged.delivered_datetime:
                    stamp['delivered_datetime'] = fields.Datetime.now()
                if (lat or lon) and not (privileged.delivered_lat or privileged.delivered_lon):
                    stamp.update(
                        delivered_lat=lat or 0.0, delivered_lon=lon or 0.0,
                        delivered_accuracy_m=context.get('lab_delivered_accuracy') or 0.0)
                super(LabDelivery, privileged).write(stamp)
        if vals.get('courier_awb'):
            # Whoever writes the number owns it. The source is what the scan widget
            # says it was; a plain form edit is a typed number.
            vals.setdefault('courier_awb_source',
                            self.env.context.get('lab_awb_source') or 'typed')
            vals.setdefault('courier_awb_captured_by_id', self.env.uid)
            vals.setdefault('courier_awb_captured_at', fields.Datetime.now())
        res = super().write(vals)
        if 'courier_awb' in vals or 'courier_id' in vals:
            self._lab_sync_courier_to_pickings()
        for delivery in became_emergency or []:
            delivery._announce_emergency()
        # Outbound only: 'delivered' on a pickup means the LAB took the parcel in,
        # and validating the order's outgoing stock transfer from that would ship
        # goods that never left. It also stamps the slips the bag carried.
        newly_delivered.filtered(
            lambda d: d.direction == 'out')._lab_close_stock_pickings()
        newly_delivered.filtered(
            lambda d: d.direction == 'in')._lab_stamp_cases_received()
        return res

    def _lab_stamp_cases_received(self):
        now = fields.Datetime.now()
        for delivery in self:
            cases = delivery.case_ids.filtered(lambda c: not c.impression_received_at)
            if cases:
                cases.sudo().write({'impression_received_at': now})
                for case in cases:
                    case.message_post(body=_(
                        "Impression received at the lab (%(name)s).",
                        name=delivery.name))

    @api.model
    def _live_ids(self, limit=None):
        """The dispatches that are genuinely still out — ids, not a domain.

        Every one of the 17,898 rows on this database is `draft`, bulk-created
        in one window on 2026-08, and 17,524 of them sit on a sale order whose
        outgoing picking is already DONE: the module was switched on after the
        work had already been delivered, so "not yet delivered" counted the
        whole history of the lab. Same shape as the manufacturing ghosts
        (report.lab.floor._live_domain) and the same rule: a dispatch whose
        order has shipped and has nothing still open is finished paperwork,
        not work in the van. (2026-09-02)

        Ids rather than a domain because the live set is small (a few hundred)
        while the ghost set is not — and an action domain has to carry
        whichever side it filters on.
        """
        open_ones = self.sudo().search(
            [('state', 'in', ('draft', 'assigned', 'out'))], order='create_date')
        orders = open_ones.mapped('sale_order_id')
        if not orders:
            return open_ones.ids[:limit] if limit else open_ones.ids
        Picking = self.env['stock.picking'].sudo()
        done, still_open = set(), set()
        for order, state in Picking._read_group(
                [('sale_id', 'in', orders.ids), ('state', '!=', 'cancel'),
                 ('picking_type_code', '=', 'outgoing')],
                ['sale_id', 'state']):
            (done if state == 'done' else still_open).add(order.id)
        # Shipped AND nothing left open: testing "has a done picking" alone
        # calls the second appliance of a two-line case delivered.
        shipped = done - still_open
        live = open_ones.filtered(
            lambda d: d.sale_order_id.id not in shipped)
        return live.ids[:limit] if limit else live.ids

    def _lab_sync_courier_to_pickings(self):
        """The transfer carries the same courier and number as the dispatch.

        The stock transfer has its own courier fields (sale_custom) and the office
        prints them on the delivery slip; the dispatch record has the ones the scan
        widget fills. Kept as two because they answer to two different screens,
        kept equal because a slip that disagrees with the tracking page is worse
        than either alone. One direction only — dispatch to transfer — so nothing
        can loop. Elevated for the same reason as the close-out: the person
        dispatching is a field executive who cannot read stock.picking.
        """
        for delivery in self.filtered(
                lambda d: d.direction == 'out' and d.sale_order_id
                and d.delivery_mode == 'courier'):
            pickings = delivery.sale_order_id.sudo().picking_ids.filtered(
                lambda p: p.picking_type_code == 'outgoing' and p.state != 'cancel')
            if not pickings:
                continue
            vals = {}
            if delivery.courier_awb:
                vals['consignment_number'] = delivery.courier_awb
                vals['consignment_source'] = delivery.courier_awb_source or 'typed'
            if delivery.courier_id:
                vals['courier_company'] = delivery.courier_id.name
            if delivery.courier_receipt_image:
                vals['courier_receipt_image'] = delivery.courier_receipt_image
            if vals:
                pickings.write(vals)

    def _lab_close_stock_pickings(self):
        """Handed over means shipped: validate the order's open delivery transfers.

        The dispatch (this record) is what the office marks as delivered; the stock
        picking is what feeds `qty_delivered` on the order and its invoicing. Left to
        two separate clicks, the second was skipped and every order read "0 delivered"
        (client, 2026-08-17). Done quantities follow demand (sale_custom's one-click
        validate); nothing here may fail the handover itself.
        """
        for delivery in self:
            # Elevated, and inside the guard.
            #
            # The person marking a delivery delivered is a field executive standing in a
            # clinic doorway. They cannot read stock.picking and have no business doing
            # so — but reading the order's transfers here was the FIRST thing this did,
            # outside the try, so it raised AccessError and no executive could mark any
            # delivery delivered at all. The elevation covers the stock work only; who
            # may mark a delivery delivered is decided on lab.delivery itself.
            # (client, 2026-08-27)
            try:
                with self.env.cr.savepoint():
                    order = delivery.sale_order_id.sudo()
                    pickings = order.picking_ids.filtered(
                        lambda p: p.picking_type_code == 'outgoing'
                        and p.state not in ('done', 'cancel'))
                    if not pickings:
                        continue
                    pickings.with_context(
                        skip_sms=True, skip_backorder=True).button_validate()
            except Exception as exc:                                    # noqa: BLE001
                names = ', '.join(
                    delivery.sale_order_id.sudo().picking_ids.filtered(
                        lambda p: p.picking_type_code == 'outgoing').mapped('name'))
                _logger.warning("delivery %s: could not validate %s: %s",
                                delivery.name, names, exc)
                delivery.message_post(body=_(
                    "Stock transfer %(picks)s could not be validated automatically: "
                    "%(err)s", picks=names, err=exc))

    def _announce_emergency(self):
        self.ensure_one()
        if self.executive_id:
            self.activity_schedule(
                'mail.mail_activity_data_todo',
                summary=_("EMERGENCY delivery %s", self.name),
                note=self.emergency_reason or '',
                user_id=self.executive_id.id)
        self.message_post(body=_(
            "Emergency delivery: %(why)s (promised %(when)s)",
            why=self.emergency_reason or _('order priority'),
            when=self.promised_datetime or self.scheduled_date))

    # ------------------------------------------------------------------ workflow
    def action_assign_to_me(self):
        for delivery in self:
            if delivery.state != 'draft':
                raise UserError(_("%s is already assigned.", delivery.name))
            delivery.write({'state': 'assigned', 'executive_id': self.env.uid})
        return True

    def action_start(self):
        for delivery in self:
            if delivery.state not in ('draft', 'assigned'):
                raise UserError(_("%s is not waiting to go out.", delivery.name))
            delivery.write({'state': 'out', 'out_datetime': fields.Datetime.now()})
        return True

    # ------------------------------------------------------------------ scanning
    @api.model
    def scan(self, code, latitude=False, longitude=False, accuracy=False):
        """One camera for every code the lab prints.

        The invoice carries a barcode of its own number; the job card carries the
        order number; a dispatch note carries the delivery number. An executive
        standing at a door should not have to know which of the three they are
        holding, so all three resolve here - and a mistyped or foreign code says so
        by name rather than opening the wrong box.

        What comes back is never a silent state change: a scan is a camera reading,
        and a camera can read the wrong sheet. It opens the handover popup, already
        carrying the fix taken at the moment of the scan, and a person confirms.
        (client, 2026-08-28)
        """
        token = (code or '').strip()
        _logger.info("scan: %r by uid %s", token, self.env.uid)
        if not token:
            raise UserError(_("Nothing was scanned."))

        # The invoice also carries a UPI payment QR, near the bottom. A camera held
        # back far enough to see the whole sheet will often read THAT instead - and
        # "nothing in the lab carries this code" is a useless thing to say about a
        # payment link. Name it, and say which mark to aim at. (client, 2026-08-28)
        if '://' in token or token.lower().startswith('upi'):
            raise UserError(_(
                "That is the payment QR code at the foot of the invoice, not the "
                "case barcode. Aim at the barcode just under the doctor's address, "
                "near the top of the sheet."))

        deliveries, orders = self._scan_resolve(token)
        if not deliveries and not orders:
            _logger.info("scan: %r matched nothing", token)
            raise UserError(_(
                "Nothing in the lab carries the code %s. Scan the barcode on the "
                "invoice, or type the invoice, order or delivery number.", token))

        # An invoice can cover several cases. Guessing which box is in the hand is
        # exactly the mistake this feature exists to prevent, so it asks.
        if len(deliveries) > 1 or (not deliveries and len(orders) > 1):
            _logger.info("scan: %r is %s deliveries / %s orders — asking",
                         token, len(deliveries), len(orders))
            return self._scan_choose(token, deliveries, orders)

        delivery = deliveries[:1]
        raised_now = not delivery
        claimed_from = ''
        # Which clinic this code belongs to, read through the SAME lens the rest
        # of the app already reads clinics through - res.partner's own ir.rule,
        # not a hand-copied group check. For a route-restricted executive that
        # rule is "clinics on their own route"; for a manager/admin it is
        # unrestricted, so this is a no-op for them. _scan_resolve is fully
        # sudo'd (an executive cannot read account.move/sale.order at all), so
        # nothing upstream of this line has checked route membership yet -
        # without it, scanning a stray invoice from another round would raise
        # the case, or take it over from whoever it really belongs to, with no
        # route check at all. (client, 2026-08-29)
        order = delivery.sudo().sale_order_id if delivery else orders[:1]
        scan_partner = order.partner_id if order else self.env['res.partner']
        if scan_partner and not self.env['res.partner'].search_count(
                [('id', '=', scan_partner.id)]):
            _logger.info("scan: %r -> %s outside uid %s's route — refused",
                         token, scan_partner.sudo().display_name, self.env.uid)
            raise UserError(_(
                "%(clinic)s is not on your Sales Route. Scanning stays within "
                "your own round — ask whoever covers that route to hand this "
                "case over.", clinic=scan_partner.sudo().display_name))
        if not delivery:
            # The executive is THE SCANNER, not the order's salesperson: the scan
            # happens with the box in hand at the clinic door, and the salesperson
            # may be a different person who cannot even read this record under the
            # "own deliveries" rule - which made every real executive's scan die
            # with an access error while the admin's worked. (client, 2026-08-28)
            delivery = self.create({
                'sale_order_id': order.id,
                'executive_id': self.env.uid,
                'scheduled_date': order.commitment_date or fields.Datetime.now(),
                'state': 'assigned',
            })
            delivery.message_post(body=_(
                "Raised by scanning %s at the clinic.", token))
        elif not delivery.has_access('read'):
            # The delivery exists but belongs to another executive - raised by the
            # office, or planned onto a different route. The box is nevertheless in
            # THIS person's hand at the door, so an undelivered outbound case
            # changes hands, on the record and in the chatter; a finished or
            # cancelled one only reports itself, since somebody else's history is
            # not the scanner's to open.
            privileged = delivery.sudo()
            if privileged.direction == 'out' \
                    and privileged.state in ('draft', 'assigned', 'out'):
                previous = privileged.executive_id
                claimed_from = previous.display_name or ''
                privileged.write({'executive_id': self.env.uid})
                # partner_ids: the colleague who just lost the box hears about it
                # in their inbox, not only in a chatter they will never open -
                # otherwise they spend the afternoon looking for a parcel that
                # left with someone else. (client, 2026-08-28)
                privileged.message_post(
                    body=_(
                        "Taken over by %(who)s by scanning %(token)s at the door "
                        "(was assigned to %(previous)s).",
                        who=self.env.user.display_name, token=token,
                        previous=claimed_from or _('nobody')),
                    partner_ids=previous.partner_id.ids)
                _logger.info("scan: %r -> %s claimed from %s by uid %s",
                             token, privileged.name, claimed_from, self.env.uid)
            else:
                _logger.info("scan: %r -> %s (%s) not readable by uid %s — "
                             "reported only", token, privileged.name,
                             privileged.state, self.env.uid)
                return self._scan_report_foreign(privileged)

        _logger.info("scan: %r -> %s (%s)%s", token, delivery.name, delivery.state,
                     ' raised now' if raised_now else '')
        if delivery.state == 'cancel':
            raise UserError(_(
                "%(name)s was cancelled. Raise a new hand-over for %(order)s rather "
                "than reviving this one.",
                name=delivery.name, order=delivery.sale_order_id.name or token))
        if delivery.state == 'delivered':
            return self._scan_already_delivered(delivery)
        if delivery.direction == 'in':
            action = delivery.action_receive_at_lab(latitude, longitude, accuracy)
            action['context'] = dict(action.get('context') or {},
                                     lab_scan_summary=delivery._scan_summary(
                                         'raised' if raised_now else 'ready',
                                         stage='deliver', claimed_from=claimed_from))
            return action

        # TWO SCANS, mirroring the two moments of the physical journey. The FIRST
        # scan happens at the lab with the bag open: it stages the box and asks -
        # load it onto the run, or hand it over right now. The SECOND scan happens
        # at the clinic door, on a box already out for delivery, and goes straight
        # to the hand-over. State carries the difference between the two moments,
        # so a run loaded on Monday still delivers on Tuesday's scan.
        # (client, 2026-08-28)
        if delivery.state == 'out':
            action = delivery.action_mark_delivered(latitude, longitude, accuracy)
            action['context'] = dict(action.get('context') or {},
                                     lab_scan_summary=delivery._scan_summary(
                                         'ready', stage='deliver',
                                         claimed_from=claimed_from))
            return action
        return {
            'type': 'ir.actions.act_window_close',
            'context': {'lab_scan_summary': delivery._scan_summary(
                'raised' if raised_now else 'ready',
                stage='first', claimed_from=claimed_from)},
        }

    def action_scan_load(self):
        """First scan's answer: onto the run. The second scan will deliver it."""
        for delivery in self:
            if delivery.state not in ('draft', 'assigned'):
                continue
            delivery.write({'state': 'out'})
            delivery.message_post(body=_(
                "Loaded for delivery — scanned onto the run by %s.",
                self.env.user.display_name))
            _logger.info("scan: %s loaded for delivery by uid %s",
                         delivery.name, self.env.uid)
        return True

    def _scan_summary(self, state, **extra):
        """What a scan found, for the phone to say out loud.

        The dialog a scan opens is easy to see; WHY it opened is not. Every outcome
        of a scan - raised, ready, already delivered, several cases - travels back
        under this one key so the phone can announce each one differently instead of
        leaving silence, which reads as a dead button. (client, 2026-08-28)
        """
        self.ensure_one()
        # Superuser for the DISPLAY STRINGS only: the clinic on the box may be
        # outside this executive's route, and the route rule on contacts would
        # otherwise kill the whole scan over a name the sheet in their hand
        # already shows. Nothing here grants access to the records themselves.
        # (client, 2026-08-28)
        privileged = self.sudo()
        order = privileged.sale_order_id
        summary = {
            'state': state,
            'id': self.id,
            'delivery': privileged.name,
            'clinic': privileged.partner_id.display_name or '',
            'patient': (order.patient or '') if order else '',
            'order': order.name if order else '',
            # Whether THIS scan raised it. Tracked, not guessed from create_date:
            # a record made moments earlier by something else is not this scan's
            # doing, and a thirty-second window cannot tell the two apart.
            'created': state == 'raised',
        }
        summary.update(extra)
        return summary

    @api.model
    def decode_photo(self, image_b64):
        """Read the barcodes in a photograph, with the lab's own eyes.

        The phone tries first, in the page, with the ZXing build the web client
        ships - but that reader gives up on photographs a stronger one still
        handles: a sheet a few degrees off square, dim, or slightly out of focus.
        Rather than telling the executive to take a better photo, the phone hands
        the (already downscaled) JPEG here and zxing-cpp reads it.

        The photo is decoded and dropped - nothing is stored. If zxing-cpp is not
        installed on this server the method just finds nothing, and the phone
        falls back to its own message; install the `zxing-cpp` wheel to turn this
        on. (client, 2026-08-28)
        """
        import base64
        import io
        try:
            import zxingcpp
            from PIL import Image
        except ImportError:
            _logger.info("decode_photo: zxing-cpp not installed; server-side "
                         "barcode reading is off")
            return []
        try:
            raw = base64.b64decode(image_b64 or b'')
            if not raw or len(raw) > 8 * 1024 * 1024:
                return []
            image = Image.open(io.BytesIO(raw))
            image.load()
            if image.mode not in ('RGB', 'L'):
                # Transparency flattens to BLACK by default - black bars on a
                # black ground, unreadable. Composite onto white first, the same
                # ground the page's canvas paints. (client, 2026-08-28)
                rgba = image.convert('RGBA')
                flat = Image.new('RGB', rgba.size, 'white')
                flat.paste(rgba, mask=rgba.split()[-1])
                image = flat
        except Exception:  # noqa: BLE001 - a broken upload is just "nothing found"
            return []
        found = []
        for _name, frame in self._decode_photo_frames(image):
            try:
                for code in zxingcpp.read_barcodes(frame):
                    if code.text and code.text not in found:
                        found.append(code.text)
            except Exception:  # noqa: BLE001
                continue
            if found:
                break
        # Two numbers about what arrived, on every line: how bright the middle
        # is (paper is bright) and how many dark/light alternations cross it (a
        # barcode's signature). They tell a face frame from a sheet frame in
        # the log itself, which is what "the scanner found nothing" needs to
        # become an answer. (client, 2026-08-28)
        brightness, transitions = self._photo_metrics(image)
        _logger.info("decode_photo: %s byte %sx%s photo, mid=%s bars=%s -> %s",
                     len(raw), image.width, image.height, brightness, transitions,
                     found or 'nothing')
        if not found and (brightness > 140 or transitions > 20):
            # Only frames that plausibly held a sheet are worth keeping: ten
            # face frames rotating every ten seconds buried the one that mattered.
            self._keep_failed_photo(raw)
        return found

    @staticmethod
    def _photo_metrics(image):
        """(mean luminance of the centre, transitions across the middle row)."""
        try:
            grey = image.convert('L')
            w, h = grey.size
            centre = grey.crop((w // 4, h // 4, 3 * w // 4, 3 * h // 4))
            pixels = list(centre.getdata())
            brightness = int(sum(pixels) / max(len(pixels), 1))
            row = [grey.getpixel((x, h // 2)) for x in range(0, w, 2)]
            mid = (max(row) + min(row)) / 2
            transitions = 0
            dark = row[0] < mid
            for value in row:
                is_dark = value < mid
                if is_dark != dark:
                    transitions += 1
                    dark = is_dark
            return brightness, transitions
        except Exception:  # noqa: BLE001
            return 0, 0

    @api.model
    def _decode_photo_frames(self, image):
        """The photo, then progressively harder-working views of it.

        A cheap printer lays the bars down ragged and a handheld photo blurs
        them; the raw frame often reads as nothing while an upscaled,
        Otsu-thresholded copy reads cleanly. Ordered cheapest first, and the
        caller stops at the first frame that answers. (client, 2026-08-28)
        """
        from PIL import ImageFilter, ImageOps
        yield 'raw', image
        grey = ImageOps.autocontrast(image.convert('L'), cutoff=1)
        yield 'grey', grey
        # The slot the camera loop frames, doubled and sharpened, before the
        # whole-frame passes: on a 720p webcam frame this reads a code a third
        # narrower than the raw pass does, and it is a fifth of the pixels.
        w, h = grey.size
        slot = grey.crop((int(w * 0.05), int(h * 0.15), int(w * 0.95), int(h * 0.85)))
        slot2 = slot.resize((slot.width * 2, slot.height * 2))
        yield 'slot2sharp', slot2.filter(
            ImageFilter.UnsharpMask(radius=3, percent=250, threshold=1))
        up2 = grey.resize((grey.width * 2, grey.height * 2))
        yield 'up2sharp', up2.filter(
            ImageFilter.UnsharpMask(radius=3, percent=250, threshold=1))
        threshold = self._otsu_threshold(grey)
        binarize = lambda img: img.point(lambda p: 255 if p > threshold else 0)  # noqa: E731
        yield 'otsu', binarize(grey)
        yield 'otsu2', binarize(up2)
        yield 'otsu2sharp', binarize(up2.filter(
            ImageFilter.UnsharpMask(radius=3, percent=250, threshold=1)))
        up3 = grey.resize((grey.width * 3, grey.height * 3))
        yield 'otsu3sharp', binarize(up3.filter(
            ImageFilter.UnsharpMask(radius=4, percent=300, threshold=0)))

    @staticmethod
    def _otsu_threshold(grey):
        """The classic between-class-variance threshold, from the histogram."""
        hist = grey.histogram()
        total = sum(hist)
        sum_all = sum(i * hist[i] for i in range(256))
        sum_bg = weight_bg = best = 0
        threshold = 127
        for i in range(256):
            weight_bg += hist[i]
            if not weight_bg:
                continue
            weight_fg = total - weight_bg
            if not weight_fg:
                break
            sum_bg += i * hist[i]
            mean_bg = sum_bg / weight_bg
            mean_fg = (sum_all - sum_bg) / weight_fg
            variance = weight_bg * weight_fg * (mean_bg - mean_fg) ** 2
            if variance > best:
                best, threshold = variance, i
        return threshold

    @api.model
    def _keep_failed_photo(self, raw):
        """Keep the photo nobody could read, so the failure can be looked at.

        'The scanner found nothing' is unanswerable without the picture it
        found nothing IN. Staging keeps the last few under the data dir;
        switched off by the config key on any instance that should not.
        (client, 2026-08-28)
        """
        import os
        if self.env['ir.config_parameter'].sudo().get_param(
                'lab_delivery.keep_failed_scans', '1') != '1':
            return
        try:
            from odoo.tools import config
            folder = os.path.join(config['data_dir'], 'lab_scan_failures')
            os.makedirs(folder, exist_ok=True)
            keep = sorted(os.listdir(folder))
            for stale in keep[:-9]:
                os.unlink(os.path.join(folder, stale))
            path = os.path.join(folder, fields.Datetime.now().strftime(
                'fail_%Y%m%d_%H%M%S.jpg'))
            with open(path, 'wb') as handle:
                handle.write(raw)
            _logger.info("decode_photo: unreadable photo kept at %s", path)
        except Exception:  # noqa: BLE001 - keeping evidence must never break the scan
            _logger.exception("decode_photo: could not keep the failed photo")

    @api.model
    def _scan_resolve(self, token):
        """(deliveries, orders) behind a scanned code, in order of directness.

        Elevated for the lookup only: an executive cannot read account.move at all,
        and the whole point of scanning the invoice is that they do not have to.
        What they are then handed is a delivery, which their own rules govern.
        """
        # Case-insensitive (a phone keyboard types oc243101; the sheet says
        # OC243101) and LIKE-escaped: '=ilike' reads its value as a pattern, so a
        # printed code containing % or _ must match itself, not everything. The
        # station scanner learned the escaping the hard way. (client, 2026-08-28)
        pattern = token.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        exact = [('name', '=ilike', pattern)]
        delivery = self.sudo().search(exact + [('state', '!=', 'cancel')],
                                      order='id desc')
        if delivery:
            return self.browse(delivery.ids), self.env['sale.order']

        Order = self.env['sale.order'].sudo()
        orders = Order.search(exact)
        if not orders:
            invoices = self.env['account.move'].sudo().search(
                exact + [('move_type', '=', 'out_invoice')])
            orders = invoices.line_ids.sale_line_ids.order_id
        if not orders:
            return self.browse(), Order
        deliveries = self.sudo().search([
            ('sale_order_id', 'in', orders.ids),
            ('direction', '=', 'out'),
            ('state', 'not in', ('cancel', 'failed')),
        ], order='id desc')
        return self.browse(deliveries.ids), orders

    @api.model
    def _scan_choose(self, token, deliveries, orders):
        """More than one case behind the code: show them rather than guess."""
        summary = {'state': 'several', 'token': token,
                   'count': len(deliveries) or len(orders)}
        if deliveries:
            return {
                'type': 'ir.actions.act_window',
                'name': _('Cases on %s', token),
                'res_model': 'lab.delivery', 'view_mode': 'list,form',
                'views': [(False, 'list'), (False, 'form')],
                'domain': [('id', 'in', deliveries.ids)],
                'context': {'create': False, 'lab_scan_summary': summary},
            }
        return {
            'type': 'ir.actions.act_window',
            'name': _('Cases on %s', token),
            'res_model': 'sale.order', 'view_mode': 'list',
            'views': [(self.env.ref(
                'lab_delivery.view_order_list_awaiting_delivery').id, 'list')],
            'domain': [('id', 'in', orders.ids)],
            'context': {'create': False, 'lab_scan_summary': summary},
        }

    @api.model
    def _scan_report_foreign(self, privileged):
        """Say what became of a box the scanner may not open.

        A delivered or cancelled case that belongs to someone else: the scan
        answers - when, and by whom - without handing over a record the "own
        deliveries" rule deliberately hides.
        """
        state = dict(privileged._fields['state']._description_selection(
            privileged.env)).get(privileged.state, privileged.state)
        detail = _('%(name)s is %(state)s and belongs to %(who)s.',
                   name=privileged.name, state=state,
                   who=privileged.executive_id.display_name or _('nobody yet'))
        if privileged.state == 'delivered':
            when = privileged.delivered_datetime
            who = privileged.delivered_by_id.name
            detail = _('%(name)s was already delivered', name=privileged.name)
            if when:
                detail += ' ' + _('on %s', fields.Datetime.to_string(when))
            if who:
                detail += ' ' + _('by %s', who)
            detail += '.'
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {'type': 'warning', 'title': _('Already handled'),
                       'message': detail, 'sticky': True},
        }

    @api.model
    def _scan_already_delivered(self, delivery):
        """Open the finished box read-only, saying when and by whom.

        This used to come back as a bare display_notification, which a phone that
        is mid-navigation can swallow without a trace - and a swallowed warning
        reads as a dead scanner. Now the delivery itself opens, and the summary
        rides the context so the phone shows a STICKY warning it controls.
        (client, 2026-08-28)
        """
        when = delivery.delivered_datetime
        who = delivery.sudo().delivered_by_id.name
        return {
            'type': 'ir.actions.act_window',
            'name': _('Already delivered'),
            'res_model': 'lab.delivery',
            'res_id': delivery.id,
            'view_mode': 'form',
            'views': [(False, 'form')],
            'context': {'create': False,
                        'lab_scan_summary': delivery._scan_summary(
                            'delivered',
                            when=fields.Datetime.to_string(when) if when else '',
                            who=who or '')},
        }

    def action_mark_delivered(self, latitude=False, longitude=False, accuracy=False):
        """Open the handover wizard, carrying the phone's fix.

        The fix is taken HERE, when the executive presses Delivered, because that is
        the moment the location means something. It rides the wizard's context into
        the write that sets the state. (client, 2026-08-28)
        """
        self.ensure_one()
        context = {'default_delivery_id': self.id}
        context.update(self._lab_fix_context(latitude, longitude, accuracy))
        return {
            'type': 'ir.actions.act_window', 'name': _('Delivered'),
            'res_model': 'lab.delivery.done.wizard', 'view_mode': 'form',
            'views': [(False, 'form')],
            'target': 'new', 'context': context,
        }

    @api.model
    def _lab_fix_context(self, latitude, longitude, accuracy):
        """The phone's fix as context keys, or nothing at all.

        Nothing rather than zeros: a refused or unavailable fix must read as 'no
        location', which is itself worth seeing, not as a pin on the equator.
        """
        if not (latitude or longitude):
            return {}
        return {'lab_delivered_lat': latitude or 0.0,
                'lab_delivered_lon': longitude or 0.0,
                'lab_delivered_accuracy': accuracy or 0.0}

    def action_mark_failed(self):
        for delivery in self:
            if not delivery.fail_reason:
                raise UserError(_(
                    "Record what went wrong with %s before marking it failed — the "
                    "office reschedules from that reason.", delivery.name))
            delivery.write({'state': 'failed'})
            delivery.activity_schedule(
                'mail.mail_activity_data_todo',
                summary=_("Reschedule failed delivery %s", delivery.name),
                note=delivery.fail_reason,
                user_id=(delivery.sale_order_id.verified_by_id or self.env.user).id)
        return True

    def action_cancel(self):
        self.filtered(lambda d: d.state not in ('delivered',)).write({'state': 'cancel'})
        return True

    # ------------------------------------------------------------------ delay engine
    @api.model
    def _grace_minutes(self, emergency=False):
        get = self.env['ir.config_parameter'].sudo().get_param
        key = ('lab_delivery.emergency_grace_minutes' if emergency
               else 'lab_delivery.grace_minutes')
        default = 30 if emergency else 120
        try:
            return max(0, int(get(key, default)))
        except (TypeError, ValueError):
            return default

    def _deadline(self):
        self.ensure_one()
        base = (self.promised_datetime or self.scheduled_date) if self.is_emergency \
            else self.scheduled_date
        return base + timedelta(minutes=self._grace_minutes(self.is_emergency))

    @api.model
    def _cron_delay_check(self, batch=400):
        """Hourly: find what is late, tell the right person, escalate — exactly once
        per window. A delay engine that double-notifies gets muted within a week."""
        now = fields.Datetime.now()
        get = self.env['ir.config_parameter'].sudo().get_param
        try:
            escalate_hours = max(1, int(get('lab_delivery.escalation_after_hours', 4)))
        except (TypeError, ValueError):
            escalate_hours = 4

        # Grace is a setting, not a per-parcel fact: read it twice, not once per row.
        grace = {False: timedelta(minutes=self._grace_minutes(False)),
                 True: timedelta(minutes=self._grace_minutes(True))}

        # Only the parcels genuinely still out. Nearly every open row is a dispatch
        # whose order shipped long ago (_live_ids); searching `state` took the first
        # 400 of those, flagged them late, and - a late parcel staying open - took the
        # same 400 again every run, believing more remained, and never reached the rest.
        live_ids = self._live_ids()
        ghosts = self.search([('state', 'in', OPEN_STATES), ('is_delayed', '=', True),
                              ('id', 'not in', live_ids)])
        if ghosts:
            ghosts.write({'is_delayed': False, 'delay_hours': 0.0})
        # In the model's order (emergencies first), a page at a time.
        live = self.search([('id', 'in', live_ids)])

        # Bounded by the WORK done, not the rows read. A parcel going late costs a
        # write, an activity and a log line, and after any downtime they cross the line
        # in a crowd — an unbounded run would sit past the cron worker's time limit and
        # be killed, having notified nobody. Progress is kept on the rows themselves
        # (first_delayed_notified, last_escalation_at), so the re-trigger skips what
        # this run already told and moves on to the next page.
        handled = 0
        for offset in range(0, len(live), batch):
            handled += live[offset:offset + batch]._delay_check_page(
                now, grace, escalate_hours)
            if handled >= batch and offset + batch < len(live):
                cron = self.env.ref('lab_delivery.ir_cron_delivery_delay_check',
                                    raise_if_not_found=False)
                if cron:
                    cron.sudo()._trigger()
                break
        return handled

    def _delay_check_page(self, now, grace, escalate_hours):
        """Flag, warn and escalate one page of live parcels; return how many were told."""
        handled = 0
        to_clear = self.browse()
        # Collected rather than done inside the loop. Parcels do not go late in a
        # trickle after any downtime — they cross the line in a crowd, and a per-record
        # message_post plus activity_schedule was ~24 queries each. One holiday weekend
        # was enough to put this cron over the worker's time limit.
        warn_bodies, warn_by_user = {}, defaultdict(list)
        escalation_bodies, manager_bodies = {}, []

        for delivery in self:
            base = ((delivery.promised_datetime or delivery.scheduled_date)
                    if delivery.is_emergency else delivery.scheduled_date)
            deadline = base + grace[bool(delivery.is_emergency)]
            if now <= deadline:
                if delivery.is_delayed:
                    to_clear |= delivery
                continue
            hours = (now - deadline).total_seconds() / 3600.0
            vals = {'is_delayed': True, 'delay_hours': round(hours, 1)}

            if not delivery.first_delayed_notified:
                # R8 — the executive hears first, on the record.
                warn_by_user[(delivery.executive_id or self.env.user).id].append(
                    delivery.id)
                warn_bodies[delivery.id] = _("Delay warning #1 — %(h).1f h late.",
                                             h=hours)
                vals.update(first_delayed_notified=True, last_escalation_at=now)
                handled += 1
            elif delivery.last_escalation_at and \
                    now >= delivery.last_escalation_at + timedelta(hours=escalate_hours):
                level = delivery.escalation_count + 1
                vals.update(escalation_count=level, last_escalation_at=now)
                escalation_bodies[delivery.id] = _(
                    "Delay escalation #%(n)s — %(h).1f h late.", n=level, h=hours)
                # R7 — from the escalation onwards the managers hear too.
                manager_bodies.append(_(
                    "%(name)s (%(doctor)s, %(exec)s) is %(h).1f h late.",
                    name=delivery.name,
                    doctor=delivery.partner_id.display_name,
                    exec=delivery.executive_id.name or _('unassigned'),
                    h=hours))
                handled += 1
            delivery.write(vals)

        if to_clear:
            to_clear.write({'is_delayed': False, 'delay_hours': 0.0})

        # One activity per parcel still, but scheduled a whole executive at a time.
        # The summary is generic because the activity already hangs off the delivery,
        # which is where its name and hours are; repeating them bought nothing and
        # cost a round trip per parcel.
        for user_id, ids in warn_by_user.items():
            self.browse(ids).activity_schedule(
                'mail.mail_activity_data_todo',
                summary=_("Delivery delayed"),
                note=_("Past the promised time — see the delivery for how far."),
                user_id=user_id)
        for bodies in (warn_bodies, escalation_bodies):
            if bodies:
                self.browse(list(bodies))._message_log_batch(bodies=bodies)
        if manager_bodies:
            self._notify_managers_digest(manager_bodies)
        return handled

    def _manager_partners(self):
        group = self.env.ref('lab_fieldwork.group_fieldwork_manager',
                             raise_if_not_found=False)
        if not group:
            return self.env['res.partner']
        return group.sudo().all_user_ids.filtered('active').partner_id

    def _notify_managers(self, body):
        """Tell the managers about THIS parcel, attached to it."""
        partners = self._manager_partners()
        if partners:
            self.message_notify(partner_ids=partners.ids, body=body,
                                subject=_("Lab: delivery delayed"))

    def _notify_managers_digest(self, lines):
        """One message covering every parcel in this run.

        Not attached to a document, because it is about many. A manager who receives
        forty separate notifications reads none of them.
        """
        partners = self._manager_partners()
        if not partners:
            return
        self.env['mail.thread'].message_notify(
            partner_ids=partners.ids,
            body=Markup('<br/>').join(lines),
            subject=_("Lab: %s delivery(s) delayed", len(lines)))

    def action_receive_at_lab(self, latitude=False, longitude=False, accuracy=False):
        """The lab takes the bag in. The inbound mirror of Mark Delivered."""
        self.ensure_one()
        context = {'default_delivery_id': self.id}
        context.update(self._lab_fix_context(latitude, longitude, accuracy))
        return {
            'type': 'ir.actions.act_window', 'name': _('Receive at Lab'),
            'res_model': 'lab.delivery.receive.wizard', 'view_mode': 'form',
            'views': [(False, 'form')],
            'target': 'new',
            'context': context,
        }

    def action_open_order(self):
        self.ensure_one()
        if not self.sale_order_id:
            raise UserError(_(
                "%s is a pickup - the work it carries has no order yet. The case "
                "slips are on the Slips tab.", self.name))
        return {'type': 'ir.actions.act_window', 'res_model': 'sale.order',
                'res_id': self.sale_order_id.id, 'view_mode': 'form',
                'views': [(False, 'form')]}

    # ------------------------------------------------------------------ courier
    @api.depends('courier_id', 'courier_id.tracking_url', 'courier_awb')
    def _compute_courier_tracking_url(self):
        for delivery in self:
            delivery.courier_tracking_url = (
                delivery.courier_id.track_url_for(delivery.courier_awb)
                if delivery.courier_id else '')

    @api.depends('courier_event_ids.status', 'courier_event_ids.event_datetime')
    def _compute_courier_progress(self):
        """The consignment's state is simply its latest checkpoint.

        Latest by *time*, not by id: the office often types this morning's scan before
        going back to fill in yesterday's, and a parcel must not travel backwards
        because of the order somebody entered things.
        """
        events = self.env['lab.courier.event'].search(
            [('delivery_id', 'in', self.ids)], order='event_datetime desc, id desc')
        latest = {}
        for event in events:
            latest.setdefault(event.delivery_id.id, event)
        for delivery in self:
            event = latest.get(delivery.id)
            delivery.courier_status = event.status if event else False
            delivery.courier_last_event_at = event.event_datetime if event else False

    # Its own method, not folded into the one above: that one writes stored fields, and
    # sharing it would let a read of this count trigger a recompute and write of them.
    @api.depends('courier_event_ids')
    def _compute_courier_event_count(self):
        counts = dict(self.env['lab.courier.event']._read_group(
            [('delivery_id', 'in', self.ids)], ['delivery_id'], ['__count']))
        for delivery in self:
            delivery.courier_event_count = counts.get(delivery, 0)

    def _compute_courier_days_silent(self):
        now = fields.Datetime.now()
        for delivery in self:
            since = delivery.courier_last_event_at or delivery.courier_dispatched_at
            delivery.courier_days_silent = (
                (now - since).days if since and delivery._courier_in_flight() else 0)

    def _courier_in_flight(self):
        """Out with a courier and not yet arrived or come back."""
        self.ensure_one()
        return bool(
            self.delivery_mode == 'courier'
            and self.state in OPEN_STATES
            and self.courier_status not in CLOSED_STATUS)

    def action_dispatch_courier(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': _('Send by Courier'),
            'res_model': 'lab.courier.dispatch.wizard', 'view_mode': 'form',
            'views': [(False, 'form')], 'target': 'new',
            'context': {'default_delivery_id': self.id,
                        'default_courier_id': self.courier_id.id},
        }

    def action_log_courier_event(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': _('Tracking Update'),
            'res_model': 'lab.courier.event.wizard', 'view_mode': 'form',
            'views': [(False, 'form')], 'target': 'new',
            'context': {'default_delivery_id': self.id},
        }

    def action_open_tracking(self):
        """Open the courier's own page. The whole point of storing the URL."""
        self.ensure_one()
        if not self.courier_tracking_url:
            raise UserError(_(
                "No tracking page for %(name)s. Either the consignment number is "
                "missing, or %(courier)s has no tracking URL set up under "
                "Deliveries ▸ Configuration ▸ Couriers.",
                name=self.name,
                courier=self.courier_id.name or _('this courier')))
        return {'type': 'ir.actions.act_url', 'url': self.courier_tracking_url,
                'target': 'new'}

    def _apply_courier_events(self):
        """React to whatever the latest checkpoint now says.

        A parcel the courier has delivered is delivered — asking the office to also
        walk the normal handover wizard would be asking them to say the same thing
        twice, and the second saying is the one that gets skipped.
        """
        for delivery in self:
            if not delivery.courier_event_ids:
                continue
            status = delivery.courier_status
            if status in TROUBLE_STATUS and delivery.state in OPEN_STATES:
                delivery._raise_courier_trouble(status)
            if status == 'delivered' and delivery.state in OPEN_STATES:
                delivery._close_from_courier()
            elif status and status != 'booked' and delivery.state in ('draft',
                                                                     'assigned'):
                # It has physically left; the board must not still say "to assign".
                delivery.write({'state': 'out',
                                'out_datetime': delivery.out_datetime
                                or delivery.courier_dispatched_at
                                or fields.Datetime.now()})
            if status and delivery.courier_is_stale:
                delivery.courier_is_stale = False
        return True

    def _close_from_courier(self):
        self.ensure_one()
        event = self.courier_event_ids.sorted(
            lambda e: (e.event_datetime, e.id), reverse=True)[:1]
        when = event.event_datetime or fields.Datetime.now()
        self.write({
            'state': 'delivered',
            'delivery_outcome': 'lab' if self.direction == 'in' else 'clinic',
            'received_by': self.received_by or (event.note or ''),
            'delivered_datetime': when,
            'is_delayed': False,
            'courier_is_stale': False,
            'late_delivered': when > self._deadline(),
        })
        self.message_post(body=_(
            "Courier reported %(what)s%(where)s.",
            what=_('arrival at the lab') if self.direction == 'in' else _('delivery'),
            where=(' — %s' % event.location) if event.location else ''))

    def _raise_courier_trouble(self, status):
        self.ensure_one()
        label = dict(COURIER_STATUS).get(status, status)
        event = self.courier_event_ids.sorted(
            lambda e: (e.event_datetime, e.id), reverse=True)[:1]
        detail = ' — '.join(x for x in (event.location, event.note) if x)
        self.activity_schedule(
            'mail.mail_activity_data_todo',
            summary=_("Courier %(status)s: %(name)s", status=label, name=self.name),
            note=detail or '',
            user_id=(self.executive_id or self.env.user).id)
        self.message_post(body=_("Courier reported %(status)s%(detail)s",
                                 status=label,
                                 detail=(' — %s' % detail) if detail else ''))

    def record_event(self, status, when=None, location=None, note=None,
                     source='manual'):
        """Log a checkpoint against these consignments.

        The single door in. Creating an `lab.courier.event` directly works too, but
        everything that reacts to a checkpoint hangs off `_apply_courier_events`, which
        both routes go through.
        """
        vals_list = [{
            'delivery_id': delivery.id,
            'status': status,
            'event_datetime': when or fields.Datetime.now(),
            'location': location or False,
            'note': note or False,
            'source': source,
        } for delivery in self]
        return self.env['lab.courier.event'].create(vals_list)

    def _post_courier_details_to_order(self):
        """Put the number and the link where the clinic will look for it.

        On the order's thread rather than a private note on the delivery: the doctor
        follows the case, not the lab's internal delivery record.
        """
        for delivery in self:
            # Escaped throughout: the courier name and the tracking URL are both
            # admin-entered, and this lands in a chatter that renders HTML.
            body = escape(_(
                "Sent by %(courier)s, consignment %(awb)s.",
                courier=delivery.courier_id.name, awb=delivery.courier_awb))
            if delivery.courier_tracking_url:
                body += Markup(' <a href="%s" target="_blank" rel="noopener">%s</a>') % (
                    delivery.courier_tracking_url, _('Track it'))
            if delivery.courier_expected_date:
                body += escape(
                    ' ' + _("Expected %s.", delivery.courier_expected_date))
            # A pickup has no order to tell: its thread is the visit it was collected
            # on and the slips in the bag. message_post on an empty recordset raises,
            # which is how notify_doctor on the dispatch wizard used to crash inbound.
            if delivery.sale_order_id:
                delivery.sale_order_id.message_post(body=body)
            else:
                for target in (delivery.visit_id or delivery.case_ids):
                    target.message_post(body=body)
        return True

    @api.model
    def _courier_stale_days(self):
        get = self.env['ir.config_parameter'].sudo().get_param
        try:
            return max(1, int(get('lab_delivery.courier_stale_days', 3)))
        except (TypeError, ValueError):
            return 3

    @api.model
    def _cron_courier_stale_check(self):
        """A consignment that has stopped moving is the one worth a phone call.

        Distinct from the delay engine on purpose: that one asks "is it late", this one
        asks "has anything happened at all". A parcel can be silent for four days and
        still not be late yet, and that is exactly when ringing the courier works.
        """
        days = self._courier_stale_days()
        cutoff = fields.Datetime.now() - timedelta(days=days)
        candidates = self.search([
            ('delivery_mode', '=', 'courier'),
            ('state', 'in', OPEN_STATES),
            ('courier_dispatched_at', '!=', False),
        ])
        flagged = 0
        for delivery in candidates:
            if delivery.courier_status in CLOSED_STATUS:
                continue
            since = delivery.courier_last_event_at or delivery.courier_dispatched_at
            if since > cutoff:
                continue
            if delivery.courier_is_stale:
                continue  # already chased; the delay engine escalates from here
            delivery.courier_is_stale = True
            delivery.message_post(body=_(
                "No courier movement for %(days)s day(s) — last was %(when)s.",
                days=(fields.Datetime.now() - since).days, when=since))
            delivery._notify_managers(_(
                "%(name)s (%(courier)s %(awb)s, %(doctor)s) has not moved for "
                "%(days)s day(s).",
                name=delivery.name,
                courier=delivery.courier_id.name or _('courier'),
                awb=delivery.courier_awb or '',
                doctor=delivery.partner_id.display_name,
                days=(fields.Datetime.now() - since).days))
            flagged += 1
        return flagged

    # ------------------------------------------------------------------ visit link
    def _find_visit(self):
        """The visit this delivery was plausibly handed over on.

        Only ever the carrier's own visit to this clinic, today, still open or just
        closed. Anything looser would attach a box to a trip somebody else made.
        """
        self.ensure_one()
        if not self.executive_id or not self.partner_id:
            return self.env['lab.visit']
        return self.env['lab.visit'].sudo().search([
            ('user_id', '=', self.executive_id.id),
            ('partner_id', '=', self.partner_id.id),
            ('date', '=', fields.Date.context_today(self)),
            ('state', 'in', ('open', 'done')),
        ], order='state, id desc', limit=1)

    def _attach_visit(self):
        """Stamp the visit at handover time, when it is still knowable.

        Left to a nightly job this would be guesswork; done here it is simply what the
        executive was doing at the moment they pressed Delivered.
        """
        for delivery in self.filtered(lambda d: not d.visit_id):
            visit = delivery._find_visit()
            if visit:
                delivery.visit_id = visit.id
        return True

    def action_open_visit(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': 'lab.visit',
                'res_id': self.visit_id.id, 'view_mode': 'form',
                'views': [(False, 'form')]}
