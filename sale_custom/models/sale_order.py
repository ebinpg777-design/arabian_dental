# -*- coding: utf-8 -*-
from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError
from odoo.tools.misc import formatLang


class SaleOrder(models.Model):
    _inherit = "sale.order"

    # Relabel the initial state as "Registered" (dental lab terminology) while
    # keeping the native v19 states (draft/sent/sale/cancel).
    state = fields.Selection(
        selection=[
            ('draft', 'Registered'),
            ('sent', 'Quotation Sent'),
            ('sale', 'Sales Order'),
            ('cancel', 'Cancelled'),
        ],
    )
    team_id = fields.Many2one(string='Sales Route')

    # --- Patient / clinical data -------------------------------------------------
    patient = fields.Char(string='Patient', tracking=True, index=True)
    age = fields.Integer(string='Age', tracking=True)
    gender = fields.Selection(
        [('male', 'Male'), ('female', 'Female')],
        string='Gender', tracking=True)
    modification = fields.Char(string='Modifications', tracking=True)
    instruction = fields.Char(string='Special Instructions', tracking=True)
    send_through = fields.Many2one('send.through', string='Send Through')

    # --- Appliance checkboxes ----------------------------------------------------
    is_dd_cheque = fields.Boolean(string='DD/Cheque')
    is_screw = fields.Boolean(string='Screw')
    is_bite = fields.Boolean(string='Bite')
    is_bands = fields.Boolean(string='Bands')
    is_wires = fields.Boolean(string='Wires')
    is_teeth = fields.Boolean(string='Teeth')
    is_facebow = fields.Boolean(string='Face Bow')
    # The lab's long-standing "ring the doctor before starting" flag, carried over
    # from v10. Field executives set it on the case slip; production reads it here.
    is_pending_work = fields.Boolean(
        string='Call Doctor', tracking=True,
        help="Do not start work on this case until the doctor has been called.")
    is_others = fields.Boolean(string='Others')
    is_3d_model_print = fields.Boolean(
        string='3D Model Print', tracking=True,
        help="This case is (or includes) a 3D-printed model.")
    is_rework = fields.Boolean(string='Rework', default=False)
    is_edit_number = fields.Boolean(string='Edit number')
    # v10 added `active` to sale.order (reworks are archived); kept for the
    # Reworks menu and to line up with the preserved column in the upgraded DB.
    active = fields.Boolean(string='Active', default=True)

    # What KIND of appliance the case is. The lab's four words for it, which every
    # other classification here (the Appliance tick-boxes, the product) only
    # approaches sideways: those say what is IN the box, this says what the box is.
    # No default - an unanswered question must read as unanswered, not as "Fixed".
    # (client, 2026-09-09)
    appliance_type = fields.Selection(
        [('fixed', 'Fixed'),
         ('removable', 'Removable'),
         ('clear_retainer', 'Clear Retainer'),
         ('other', 'Other')],
        string='Appliance Type', tracking=True, index=True, copy=True)

    priority = fields.Selection(
        [('low', 'Low'), ('normal', 'Normal'), ('urgent', 'Urgent')],
        string='Priority', default='normal', required=True, copy=False)

    # RUSH. `emergency` is added to this selection by lab_order_control - the
    # broken appliance for a patient in the chair - so the two rush levels are
    # `emergency` above `urgent`. Anything that treats one as rushed must treat
    # the other the same, or the MORE urgent case gets the slacker handling.
    # (client, 2026-09-19)
    RUSH = ('urgent', 'emergency')

    # WHEN THE LAB OWES THE WORK.
    #
    # Two days from the day the case is registered, and the same day for an
    # emergency - that is the promise the counter makes, and until now it was made
    # in somebody's head. Written down, it can be chased: the Due for Invoicing
    # list is every confirmed case whose day has come and which nobody has billed.
    # Set when the case is confirmed and editable afterwards - a doctor who asks
    # for a date is given it. (client, 2026-09-18)
    date_due = fields.Date(
        string='Due Date', tracking=True, index='btree_not_null', copy=False,
        help="The day the work is owed to the doctor. Set when the case is "
             "confirmed - the same day for urgent work, two days later otherwise - "
             "and yours to change.")
    days_overdue = fields.Integer(
        string='Days Over', compute='_compute_days_overdue', search='_search_days_overdue',
        help="How many days past its due date this case is.")

    # How long the lab takes, unless the case is an emergency.
    LEAD_DAYS = 2

    def _due_date_on_confirm(self, when=None):
        """The day this case is owed, counted from the day it is registered."""
        self.ensure_one()
        day = when or fields.Date.context_today(self)
        # `emergency` as well as `urgent`: an emergency is the MORE urgent of the
        # two, and comparing against 'urgent' alone quietly gave it the slower
        # promise - SO283635, an emergency, came out due two days after it was
        # registered. (client, 2026-09-19)
        if self.priority in self.RUSH or any(self.order_line.mapped('is_urgent')):
            return day               # an emergency is owed the same day
        return day + timedelta(days=self.LEAD_DAYS)

    def _compute_days_overdue(self):
        today = fields.Date.context_today(self)
        for order in self:
            order.days_overdue = (today - order.date_due).days if order.date_due else 0

    def _search_days_overdue(self, operator, value):
        """Searchable so a filter can say "over its day" without a stored column."""
        if operator not in ('=', '!=', '<', '<=', '>', '>=') or not isinstance(value, int):
            raise UserError(_("Days Over can only be compared with a number of days."))
        flip = {'<': '>', '<=': '>=', '>': '<', '>=': '<=', '=': '=', '!=': '!='}
        limit = fields.Date.context_today(self) - timedelta(days=value)
        return [('date_due', flip[operator], limit)]

    # --- Impression details ------------------------------------------------------
    impression_type = fields.Selection(
        [('Alginate', 'Alginate'), ('Rubber Base', 'Rubber Base')],
        string='Impression Type')
    scanned_impression = fields.Selection(
        [('Yes', 'Yes'), ('No', 'No')], string='Scanned Impression')
    impression_tray = fields.Selection(
        [('Upper', 'Upper'), ('Lower', 'Lower'), ('Both', 'Both')],
        string='Impression Tray')
    wax_bite = fields.Selection(
        [('Yes', 'Yes'), ('No', 'No')], string='Wax Bite')

    # Text of the green "Invoiced" banner on the form: which invoices, paid or not.
    invoice_banner = fields.Char(compute='_compute_invoice_banner')

    @api.depends('invoice_status', 'invoice_ids.name', 'invoice_ids.state', 'invoice_ids.payment_state')
    def _compute_invoice_banner(self):
        labels = {'paid': _('paid'), 'in_payment': _('in payment'), 'partial': _('partly paid'),
                  'not_paid': _('not paid'), 'reversed': _('reversed')}
        for order in self:
            if order.invoice_status != 'invoiced':
                order.invoice_banner = False
                continue
            parts = []
            for inv in order.invoice_ids.filtered(lambda i: i.state == 'posted' and i.move_type == 'out_invoice'):
                parts.append('%s (%s)' % (inv.name, labels.get(inv.payment_state, inv.payment_state or '')))
            order.invoice_banner = ' · '.join(parts) or _('all lines invoiced')

    # -- Deliver to the patient, not the clinic (client, 2026-08-21) ----------------
    # Most work goes back to the clinic that ordered it; occasionally an appliance is
    # couriered straight to the patient's home. When that is the case the address is
    # typed here - it belongs to this order, not to the clinic's contact record, and
    # it is what the dispatch sticker prints instead of the clinic address.
    deliver_to_patient = fields.Boolean(
        string='Deliver to Patient', copy=False,
        help="Send this case to the patient's own address instead of the clinic's.")
    patient_address = fields.Text(
        string="Patient's Address", copy=False,
        help="Where the patient wants it delivered - printed on the sticker in place "
             "of the clinic address.")

    # A third destination: somewhere that is neither the clinic nor the patient's
    # home - a hostel, a relative, a courier office, another surgery. The label
    # carries the ADDRESS AND NOTHING ELSE: putting the doctor's or the patient's
    # name on a parcel going to a third party tells a stranger who is being treated
    # and by whom, which is exactly what this option exists to avoid.
    # (client, 2026-08-28)
    deliver_to_custom = fields.Boolean(
        string='Deliver to Custom Address', copy=False,
        help="Send this case somewhere that is neither the clinic nor the patient's "
             "home. The label prints the address only - no doctor name, no patient "
             "name.")
    custom_address = fields.Text(
        string='Custom Address', copy=False,
        help="Exactly what the label should carry. Nothing else is printed with it.")

    @api.onchange('deliver_to_patient')
    def _onchange_deliver_to_patient(self):
        """Unticking it clears the address, so a stale one can never be printed."""
        for order in self:
            if not order.deliver_to_patient:
                order.patient_address = False
            elif order.deliver_to_custom:
                # One parcel, one destination.
                order.deliver_to_custom = False
                order.custom_address = False

    @api.onchange('deliver_to_custom')
    def _onchange_deliver_to_custom(self):
        for order in self:
            if not order.deliver_to_custom:
                order.custom_address = False
            elif order.deliver_to_patient:
                order.deliver_to_patient = False
                order.patient_address = False

    @api.constrains('deliver_to_custom', 'custom_address')
    def _check_custom_address(self):
        for order in self:
            if order.deliver_to_custom and not (order.custom_address or '').strip():
                raise ValidationError(_(
                    "This case is set to go to a custom address, so it needs one - "
                    "the label has nothing to print otherwise."))

    @api.constrains('deliver_to_patient', 'deliver_to_custom')
    def _check_one_destination(self):
        for order in self:
            if order.deliver_to_patient and order.deliver_to_custom:
                raise ValidationError(_(
                    "%s cannot go to the patient AND to a custom address. Pick one.",
                    order.name))

    @api.constrains('deliver_to_patient', 'patient_address')
    def _check_patient_address(self):
        for order in self:
            if order.deliver_to_patient and not (order.patient_address or '').strip():
                raise ValidationError(_(
                    "This case is set to go to the patient, so it needs the patient's "
                    "address - the sticker has nothing to print otherwise."))

    def lab_delivery_address_lines(self):
        """The address the sticker should carry, when it is not the clinic's."""
        self.ensure_one()
        typed = ''
        if self.deliver_to_custom and (self.custom_address or '').strip():
            typed = self.custom_address
        elif self.deliver_to_patient and (self.patient_address or '').strip():
            typed = self.patient_address
        return [' '.join(line.split())
                for line in typed.splitlines() if line.strip()] if typed else []

    def lab_delivery_is_anonymous(self):
        """Whether the label must carry the address ALONE.

        A parcel to a third party names nobody: the doctor's name identifies the
        practice and the patient's name identifies who is being treated, and neither
        belongs on a box going to a hostel desk or a courier counter.
        (client, 2026-08-28)
        """
        self.ensure_one()
        return bool(self.deliver_to_custom and (self.custom_address or '').strip())

    # -- Duplicate registration guard (client, 2026-08-21) --------------------------
    # The counter registers cases all day and the same bag can be typed twice - the
    # doctor rings back, two people take the same call. A duplicate is the SAME clinic,
    # the SAME patient and the SAME amount, entered shortly AFTER an earlier order.
    #
    # It warns, it never blocks: the lab genuinely repeats work. Measured on the live
    # ledger (23,442 orders over 142 days), this rule flags 98 orders - 0.7 a day, half
    # a percent of what is registered - and the tight end of that band is unmistakable
    # double entry: SO264183/SO264184, same patient, same 500 rupees, 33 seconds apart.
    # Every widening was tested and rejected: dropping the amount flags 227, including
    # the deliberate aligner-plus-retainer pairs booked in the same minute; a 30-day
    # window flags 213, by which point the hits are real re-orders months apart.
    DUPLICATE_WINDOW_PARAM = 'sale_custom.duplicate_window_days'
    DUPLICATE_WINDOW_DAYS = 7
    # Not patients: lab jargon and the placeholder the counter types when the name has
    # not arrived yet. 'NO NAME' alone is on 376 orders, and patient_key() would happily
    # match them all to each other.
    DUPLICATE_PLACEHOLDERS = ('no name', 'noname', 'name', 'cast', 'model', 'test', 'nil', 'na')

    lab_duplicate_ids = fields.Many2many(
        'sale.order', compute='_compute_lab_duplicates',
        string='Possible duplicates')
    lab_duplicate_warning = fields.Char(compute='_compute_lab_duplicates')
    lab_duplicate_ack = fields.Boolean(
        string='Checked - not a duplicate', copy=False,
        help="Tick once someone has looked: the duplicate banner stops showing on this "
             "order, and the earlier orders stay listed underneath it.")

    def _lab_duplicate_window(self):
        try:
            days = int(self.env['ir.config_parameter'].sudo().get_param(
                self.DUPLICATE_WINDOW_PARAM, self.DUPLICATE_WINDOW_DAYS))
        except (TypeError, ValueError):
            days = self.DUPLICATE_WINDOW_DAYS
        return max(days, 0)

    def _lab_patient_key(self, patient):
        """A patient name in comparable form.

        lab_fieldwork owns the canonical version (it sorts tokens, drops honorifics
        and lone initials, so "A Raj" and "Raj Anu" meet). Use it when that module is
        installed so the counter and the field slip agree on what a duplicate is;
        otherwise fall back to case- and spacing-insensitive matching.
        """
        try:
            from odoo.addons.lab_fieldwork.models.lab_case import patient_key
            return patient_key(patient) or False
        except ImportError:
            return ' '.join((patient or '').split()).lower() or False

    def _lab_duplicate_partner_ids(self):
        """Which clinics count as the same clinic.

        Exactly this contact today: no partner on any order has a parent, so widening
        to the commercial family would be a no-op now and pure noise later. The day a
        clinic gets branch contacts, widen here.
        """
        self.ensure_one()
        return [self.partner_id.id]

    def _lab_find_duplicates(self):
        """Earlier orders for the same clinic, patient and amount inside the window."""
        self.ensure_one()
        days = self._lab_duplicate_window()
        raw = ' '.join((self.patient or '').split()).lower()
        key = self._lab_patient_key(self.patient)
        if not (self.partner_id and key and self.amount_total and days is not None):
            return self.browse()
        if raw in self.DUPLICATE_PLACEHOLDERS or raw.rstrip('0123456789 ') in self.DUPLICATE_PLACEHOLDERS:
            return self.browse()
        if 'is_rework' in self._fields and self.is_rework:
            return self.browse()

        when = self.date_order or fields.Datetime.now()
        # Backward only, floored to midnight: the person who needs telling is the one
        # entering the SECOND order. Flagging both halves doubles the banners (193 vs 98
        # measured) and puts one on an order that was perfectly correct when it was made.
        floor = (when - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
        domain = [
            ('id', 'not in', (self._origin | self).ids),
            ('partner_id', 'in', self._lab_duplicate_partner_ids()),
            ('company_id', '=', (self.company_id or self.env.company).id),
            ('state', '!=', 'cancel'),
            ('date_order', '>=', floor),
            # strictly earlier - and for two orders stamped in the same second (bulk
            # creates, imports) the lower id is the earlier one, so exactly one of the
            # pair carries the banner instead of both pointing at each other
            '|', ('date_order', '<', when),
            '&', ('date_order', '=', when), ('id', '<', self._origin.id or self.id or 0),
        ]
        if 'is_rework' in self._fields:
            domain.append(('is_rework', '=', False))
        # patient_key is stored and indexed by lab_fieldwork; match on it when it is
        # there, and fall back to comparing in Python (names are typed by hand, so no
        # SQL operator collapses "MEERA  nair" onto "MEERA NAIR").
        if 'patient_key' in self._fields:
            domain.append(('patient_key', '=', key))
            candidates = self.search(domain, order='date_order desc')
        else:
            candidates = self.search(domain + [('patient', '!=', False)],
                                     order='date_order desc').filtered(
                lambda o: self._lab_patient_key(o.patient) == key)
        currency = self.currency_id or self.company_id.currency_id or self.env.company.currency_id
        return candidates.filtered(
            lambda o: not currency.compare_amounts(o.amount_total, self.amount_total))[:5]

    @api.depends('partner_id', 'patient', 'amount_total', 'date_order', 'state',
                 'lab_duplicate_ack')
    def _compute_lab_duplicates(self):
        for order in self:
            duplicates = order._lab_find_duplicates()
            order.lab_duplicate_ids = duplicates
            if not duplicates or order.lab_duplicate_ack:
                order.lab_duplicate_warning = False
                continue
            order.lab_duplicate_warning = _(
                '%(patient)s for %(clinic)s at %(amount)s was already registered: %(orders)s',
                patient=(order.patient or '').strip(),
                clinic=order.partner_id.display_name,
                amount=order._dental_amount_label(),
                orders=', '.join(duplicates.mapped('name')))

    def _dental_amount_label(self):
        self.ensure_one()
        currency = self.currency_id or self.company_id.currency_id or self.env.company.currency_id
        return formatLang(self.env, self.amount_total or 0.0, currency_obj=currency)

    @api.onchange('patient', 'partner_id', 'order_line')
    def _onchange_lab_duplicate(self):
        """Say it out loud the moment the case looks like one already taken.

        `type: notification` on purpose: the default onchange warning is a MODAL that
        has to be dismissed, and this fires on every line edit - the counter would be
        clicking it away all day. A notification says the same thing without stopping
        anyone, and the banner on the form stays as the durable record.
        """
        self.ensure_one()
        if self.lab_duplicate_ack:
            return None
        duplicates = self._lab_find_duplicates()
        if not duplicates:
            return None
        return {'warning': {
            'type': 'notification',
            'title': _('Possible duplicate'),
            'message': _(
                'Already registered for this clinic, patient and amount: %(orders)s',
                orders=', '.join(duplicates.mapped('name'))),
        }}

    register_person_id = fields.Many2one(
        'res.users', string='Registration User',
        default=lambda self: self.env.user, readonly=True)
    company_warning = fields.Boolean(string='Company Warning', copy=False)

    def _prepare_confirmation_values(self):
        # Core stamps the confirmation moment over the Order Date. Here that date is
        # when the case was registered - often back-dated to when the impression
        # arrived - so an order that already carries one keeps it. (client, 2026-09-14)
        values = super()._prepare_confirmation_values()
        if all(self.mapped('date_order')):
            values.pop('date_order', None)
        return values

    def action_print_order_list(self):
        """Action ▸ Print Order List on the orders ticked in the list: the Order
        List wizard opens on exactly those, grouping still to choose, and prints
        them - or their production slips. (client, 2026-09-18)"""
        orders = self.exists()
        if not orders:
            raise UserError(_("Tick the orders to print first."))
        days = [fields.Datetime.context_timestamp(self, order.date_order).date()
                for order in orders if order.date_order]
        today = fields.Date.context_today(self)
        wizard = self.env['sale.order.list.report'].create({
            'order_ids': [(6, 0, orders.ids)],
            'date_from': min(days) if days else today,
            'date_to': max(days) if days else today,
            'group_by': 'none',
            'state_filter': 'all',
        })
        return {
            'type': 'ir.actions.act_window', 'name': _('Order List'),
            'res_model': 'sale.order.list.report', 'res_id': wizard.id,
            'view_mode': 'form', 'target': 'new',
            'view_id': self.env.ref('sale_custom.view_order_list_report_form').id,
        }

    def action_confirm(self):
        # Company-level confirmation guard. Orders that need no warning go ahead, and
        # every flagged one goes to ONE dialog: returning on the first flagged order
        # left the rest of the selection unconfirmed without a word. The warning is
        # marked seen by the dialog's Confirm (confirm.wizard), not here - marking it
        # before the dialog opened let a cancelled dialog count as acknowledged.
        flagged = self.filtered(
            lambda o: o.company_id.company_warning and not o.company_warning)
        ready = self - flagged
        res = True
        if ready:
            res = super(SaleOrder, ready).action_confirm()
            # The promise the counter makes, written down. Never overwritten: a date
            # somebody agreed with the doctor stands. (client, 2026-09-18)
            for order in ready.filtered(lambda o: not o.date_due):
                order.date_due = order._due_date_on_confirm()
            # Auto-plan the generated Manufacturing Orders (dental lab workflow).
            #
            # As superuser: `mrp_production_ids` is restricted to Manufacturing / User,
            # and planning is something the lab does on the salesperson's behalf, not
            # something the salesperson is doing. Read unprivileged, this raised an
            # access error and a plain salesperson could not confirm an order at all.
            for order in ready.sudo():
                mos = order.mrp_production_ids.filtered(lambda m: m.state == 'confirmed')
                if mos:
                    mos.button_plan()
        if not flagged:
            return res
        message = _("The SO created in the company %s\nPlease confirm.") % ', '.join(
            flagged.company_id.mapped('name'))
        if len(flagged) > 1:
            message += '\n' + ', '.join(flagged.mapped('name'))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Confirmation'),
            'res_model': 'confirm.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_name': message,
                'default_sale_id': flagged[:1].id,
                'default_sale_ids': [(6, 0, flagged.ids)],
            },
        }

    # -- Finding a job by doctor / patient ------------------------------------------
    # With `sale_show_patient` in the context (the rework form's Original Order picker)
    # the many2one searches order number, doctor (partner) and patient, and shows all
    # three, so the counter can type "gopika" or "ravi" instead of a number.
    @property
    def _rec_names_search(self):
        if self.env.context.get('sale_show_patient'):
            return ['name', 'partner_id.name', 'patient']
        return super()._rec_names_search

    @api.depends('partner_id', 'patient')
    @api.depends_context('sale_show_patient')
    def _compute_display_name(self):
        if not self.env.context.get('sale_show_patient'):
            return super()._compute_display_name()
        for order in self:
            parts = [order.name, order.partner_id.name, order.patient]
            order.display_name = ' · '.join(p for p in parts if p)

    def _lab_route(self):
        """The clinic's own Sales Route."""
        self.ensure_one()
        return self.partner_id.team_id or self.partner_id.commercial_partner_id.team_id

    @api.onchange('partner_id')
    def _lab_partner_sets_route(self):
        """Choosing the clinic fills in its route and that route's leader.

        This is the ONLY moment either is written for the user: from here on the route
        and the salesperson are theirs to change, and nothing recomputes over the choice.
        It is an onchange rather than part of the computes below because core's team
        compute also depends on user_id — and Odoo merges an override's dependencies with
        its parent's, so a compute could not tell "the clinic changed" from "the
        salesperson changed", and picking a route by hand was undone the moment the
        salesperson moved with it (client, 2026-08-19).
        """
        for order in self:
            route = order._lab_route()
            if not route:
                continue
            order.team_id = route
            if route.user_id:
                order.user_id = route.user_id

    @api.depends('partner_id')
    def _compute_user_id(self):
        """Fill a blank salesperson only — never rewrite one that is already there.

        Orders created without the form (imports, the portal, tests) still get the route
        leader; see _lab_partner_sets_route for what the counter sees on screen.
        """
        blank = self.filtered(lambda o: not o.user_id)
        by_route = blank.filtered(lambda o: o.partner_id and o._lab_route().user_id)
        for order in by_route:
            order.user_id = order._lab_route().user_id
        rest = blank - by_route
        if rest:
            super(SaleOrder, rest)._compute_user_id()

    @api.depends('partner_id')
    def _compute_team_id(self):
        """Fill a blank route only.

        v10 stored the team on the partner and every order inherited it (23k of 23.4k
        orders in the last cycle matched the partner's route). Odoo's stock rule derives
        the team from the *salesperson*, which for this lab is usually the counter user,
        not the route — so a blank route is taken from the clinic first, and only then
        from Odoo's rule. A route already on the order is left alone.
        """
        blank = self.filtered(lambda o: not o.team_id)
        by_route = blank.filtered(lambda o: o.partner_id and o._lab_route())
        for order in by_route:
            order.team_id = order._lab_route()
        rest = blank - by_route
        if rest:
            super(SaleOrder, rest)._compute_team_id()

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            # A rework is numbered from the RE sequence (v10: RE27678, ...) whether it
            # comes from the legacy Reworks menu (context) or from lab_rework, which
            # creates the order with is_rework=True in the values.
            is_rework = vals.get('is_rework') or self.env.context.get('default_is_rework')
            if is_rework and vals.get('name', _('New')) == _('New'):
                seq = self.env['ir.sequence']
                if vals.get('company_id'):
                    seq = seq.with_company(vals['company_id'])
                vals['name'] = seq.next_by_code('sale.rework') or _('New')
            if vals.get('is_edit_number') and vals.get('name', _('New')) != _('New'):
                vals['name'] = _('Old Work')
        return super().create(vals_list)

    @api.onchange('priority')
    def _onchange_priority(self):
        if self.priority in self.RUSH and self.order_line:
            for line in self.order_line:
                line.is_urgent = True

    def compute_emergency(self):
        """Add an 'emergency service' line charging a % of the urgent lines' total."""
        # Collected across the whole set, then applied in one unlink and one create.
        # The old version deleted lines one at a time *while iterating the very
        # recordset it was deleting from*, which is both a query per line and a shape
        # that only works by accident.
        stale = self.env['sale.order.line']
        vals_list = []
        for sale in self:
            product = sale.company_id.emergency_service_id
            perc = sale.company_id.emergency_service_perc
            stale |= sale.order_line.filtered('is_urgent_service')
            amount = sum(sale.order_line.filtered(
                lambda line: line.is_urgent and not line.is_urgent_service
            ).mapped('price_total'))
            if amount and product:
                vals_list.append({
                    'name': product.name,
                    'product_id': product.id,
                    'product_uom_qty': 1,
                    'price_unit': (amount * perc) / 100 if perc else 0.0,
                    'is_urgent_service': True,
                    'order_id': sale.id,
                })
        # Old charge goes before the new one, so running this twice replaces the
        # emergency line instead of stacking a second one on top of it.
        stale.unlink()
        if vals_list:
            self.env['sale.order.line'].create(vals_list)
        self.priority = 'urgent'

    # What was ordered, in one cell: the counter scans the list for the work, not for
    # the line count. Stored so the list reads it in the same query as the rest.
    # (client, 2026-08-19)
    product_names = fields.Char(
        string='Products', compute='_compute_product_names', store=True,
        help="The works on this order, each named once.")

    @api.depends('order_line.product_id')
    def _compute_product_names(self):
        for order in self:
            names = []
            for line in order.order_line:
                if line.display_type or not line.product_id:
                    continue
                name = (line.product_id.name or '').strip()
                if name and name not in names:
                    names.append(name)
            order.product_names = ', '.join(names)

    # -- Validate the deliveries from the order (client, 2026-08-18) ---------------
    # The counter staff work from the sale order, not from Inventory. This does exactly
    # what pressing Validate on each transfer would do (stock_picking._lab_fill_done_from
    # _demand fills Done from Demand), only without leaving the order.
    lab_open_transfer_count = fields.Integer(
        string='Transfers to validate', compute='_compute_lab_open_transfer_count')

    @api.depends('picking_ids.state')
    def _compute_lab_open_transfer_count(self):
        for order in self:
            order.lab_open_transfer_count = len(order._lab_open_transfers())

    def _lab_open_transfers(self):
        return self.picking_ids.filtered(lambda p: p.state not in ('done', 'cancel'))

    # -- Invoice what was delivered, post it, print it (client, 2026-08-19) ---------
    # Three clicks at the counter — Create Invoice, Confirm, Print — are one button here.
    lab_has_delivered_to_invoice = fields.Boolean(
        string='Delivered items to invoice', compute='_compute_lab_has_delivered_to_invoice',
        help="There is at least one delivered item on this order that has not been "
             "invoiced yet.")

    @api.depends('order_line.qty_to_invoice', 'state')
    def _compute_lab_has_delivered_to_invoice(self):
        """Drives the Invoice & Print button: a confirmed order with something delivered
        and still unbilled. `invoice_status` alone is not enough — it also says "to
        invoice" when a return has left a line at a NEGATIVE quantity, which is a credit
        note, not an invoice."""
        for order in self:
            order.lab_has_delivered_to_invoice = order.state == 'sale' and any(
                line.qty_to_invoice > 0
                for line in order.order_line if not line.display_type)
    def action_lab_invoice_delivered(self):
        """Invoice the delivered items of this order, post the invoice and print it."""
        self.ensure_one()
        if self.state != 'sale':
            raise UserError(_('Confirm the order before invoicing it.'))
        # Only POSITIVE quantities: a line can sit at a negative "to invoice" after a
        # return, and core refuses to build an invoice out of those — better to say so
        # here than to hand the counter Odoo's four-bullet error.
        pending = self.order_line.filtered(
            lambda l: not l.display_type and l.qty_to_invoice > 0)
        if not pending:
            credit_due = self.order_line.filtered(
                lambda l: not l.display_type and l.qty_to_invoice < 0)
            if credit_due:
                raise UserError(_(
                    'This order has been invoiced for more than was delivered — it needs '
                    'a credit note, not an invoice.'))
            raise UserError(_(
                'Nothing is waiting to be invoiced on this order. An item is invoiced '
                'once it has been delivered.'))
        # Almost every work here is invoiced on delivery, but a product set to "ordered
        # quantities" would otherwise be billed before it leaves the lab: cap those at
        # what has actually gone out. Snapshot first — creating the invoice moves
        # qty_invoiced.
        capped = {line.id: max(line.qty_delivered - line.qty_invoiced, 0.0)
                  for line in pending
                  if line.product_id.invoice_policy == 'order'}

        invoices = self._create_invoices()
        for invoice in invoices:
            drop = invoice.invoice_line_ids.browse()
            for iline in invoice.invoice_line_ids:
                sale_line = iline.sale_line_ids[:1]
                allowed = capped.get(sale_line.id)
                if allowed is None:
                    continue
                if allowed <= 0:
                    drop |= iline
                elif iline.quantity > allowed:
                    iline.quantity = allowed
            if drop:
                drop.unlink()
        empty = invoices.filtered(
            lambda m: not m.invoice_line_ids.filtered(lambda l: l.display_type == 'product'))
        if empty:
            invoices -= empty
            empty.unlink()
        if not invoices:
            raise UserError(_('Nothing delivered on this order is waiting to be invoiced.'))

        invoices.action_post()
        # the standard invoice print: same layout, and it caches the PDF of a posted
        # invoice so a reprint is instant (see report/report_actions.xml)
        return self.env.ref('account.account_invoices').report_action(invoices)

    def action_lab_validate_transfers(self):
        """Validate every transfer of this order that is still open."""
        pickings = self._lab_open_transfers()
        if not pickings:
            raise UserError(_('There is no transfer left to validate on this order.'))
        result = pickings.button_validate()
        # button_validate returns an action when stock still needs an answer (a backorder
        # question on a non-delivery transfer, an immediate-transfer wizard). Show it
        # instead of pretending the job is finished.
        if isinstance(result, dict) and result.get('type'):
            return result
        done = len(pickings.filtered(lambda p: p.state == 'done'))
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'success' if done else 'warning',
                'message': _('%(done)s of %(total)s transfer(s) validated.',
                             done=done, total=len(pickings)),
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }


    def _create_invoices(self, grouped=False, final=False, date=None):
        """Resolve every line's work number once, then invoice as usual."""
        work_numbers = self.env['sale.order.line']._lab_work_numbers(self.order_line)
        return super(SaleOrder, self.with_context(
            lab_work_numbers=work_numbers))._create_invoices(
                grouped=grouped, final=final, date=date)


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    color_scheme = fields.Many2one('product.colour', string='Colour')
    is_urgent = fields.Boolean(string='Urgent', default=False)
    is_urgent_service = fields.Boolean(string='Urgent Service', default=False)
    priority = fields.Selection(
        related='order_id.priority', string='Priority', store=True)
    ul = fields.Selection(
        [('upper', 'U'), ('lower', 'L'), ('ul', 'UL')], string='U/L')
    # Which cast came with the case, which is not always the arch the appliance is
    # for: an upper appliance is often made on both casts. Optional - an
    # unanswered question reads as unanswered. (client, 2026-09-18)
    cast = fields.Selection(
        [('upper', 'U'), ('lower', 'L'), ('ul', 'UL')], string='Cast',
        help="The cast received for this work: upper, lower, or both.")

    @api.model
    def _lab_work_numbers(self, lines):
        """{line id: manufacturing order name} for `lines`, in one query.

        Same rule as a per-line ``search(..., limit=1)`` - the model's own order
        decides which MO wins when a line has several - but asked once for the whole
        invoicing run instead of once per line. Do NOT read this off
        ``order.mrp_production_ids``: that field does not carry every MO, and using
        it silently put the ORDER number on invoice lines that have a real work
        number (caught in testing, 2026-08-21).
        """
        mapping = {}
        if lines:
            for mo in self.env['mrp.production'].sudo().search(
                    [('sale_line_id', 'in', lines.ids)]):
                mapping.setdefault(mo.sale_line_id.id, mo.name)
        return mapping

    def _prepare_invoice_line(self, **optional_values):
        """Carry patient / U-L / work-number onto the generated invoice line.

        Core calls this once per line, so the work number is taken from the map
        `_create_invoices` puts in the context - invoicing a day's 100 orders used
        to fire several hundred searches on mrp_production. Outside that path
        (a single line invoiced on its own) it still asks directly.
        """
        res = super()._prepare_invoice_line(**optional_values)
        cached = self.env.context.get('lab_work_numbers')
        if cached is None:
            name = self.env['mrp.production'].sudo().search(
                [('sale_line_id', '=', self.id)], limit=1).name
        else:
            name = cached.get(self.id)
        res.update({
            'patient': self.order_id.patient,
            'ul': self.ul,
            'work_number': name or self.order_id.name,
        })
        return res

    def unlink(self):
        if self.filtered(lambda x: x.state in ('sale', 'done') and not x.is_urgent_service):
            raise UserError(_(
                'You can not remove a sale order line.\n'
                'Discard changes and try setting the quantity to 0.'))
        return super().unlink()


class ProductColour(models.Model):
    _name = 'product.colour'
    _description = "Colour"

    name = fields.Char(string='Colour Name', required=True)
    description = fields.Text(string='Description')
    active = fields.Boolean(string='Active', default=True)


class SendThrough(models.Model):
    _name = 'send.through'
    _description = "Send Through"

    name = fields.Char(string='Name', required=True)
    description = fields.Text(string='Description')
    active = fields.Boolean(string='Active', default=True)
