# -*- coding: utf-8 -*-
from math import asin, cos, radians, sin, sqrt
from urllib.parse import quote_plus

from odoo import _, api, fields, models
from odoo.exceptions import UserError

EARTH_M = 6371000.0
COORD_EPSILON = 1e-6

PURPOSES = [
    ('round', 'Routine Round'),
    ('order', 'Collect Cases'),
    ('payment', 'Payment Follow-up'),
    ('complaint', 'Complaint / Remake'),
    ('new', 'New Clinic'),
    ('delivery', 'Delivery'),
    ('marketing', 'Marketing / Promotion'),
]

OUTCOMES = [
    ('order', 'Cases Collected'),
    ('rework', 'Reworks Collected'),
    ('payment', 'Payment Collected'),
    ('followup', 'Follow-up Needed'),
    ('absent', 'Doctor Unavailable'),
    ('complaint', 'Complaint Logged'),
    ('delivered', 'Delivered'),
    ('partial_delivery', 'Partially Delivered'),
    ('none', 'No Requirement Today'),
]


def _wa_number(phone, partner):
    """A wa.me link needs the country code and digits only.

    Indian numbers are commonly stored as ten digits with no country code, and wa.me
    silently opens an empty chat for those rather than reporting an error — so the code
    is filled in from the partner's country, then the company's.
    """
    if not phone:
        return False
    digits = ''.join(c for c in phone if c.isdigit())
    if not digits:
        return False
    if phone.strip().startswith('+') or digits.startswith('00'):
        return digits.lstrip('0') if digits.startswith('00') else digits
    country = partner.country_id or partner.company_id.country_id \
        or partner.env.company.country_id
    code = (country.phone_code and str(country.phone_code)) or ''
    if code and digits.startswith(code):
        return digits
    return '%s%s' % (code, digits.lstrip('0'))


def _wa_source(partner):
    """The number to open a WhatsApp chat on for a partner, if there is one.

    `res.partner.whatsapp_number` is added by `lab_whatsapp`, which this module
    does NOT depend on. On a lab that never installed it the field is absent, and
    reading it is an AttributeError rather than a falsy value — which took the
    Doctors / Clinics panel and the order tracker's detail down with a server
    error, on exactly the install they were written to work on. (2026-09-12)

    Same guard the order tracker already uses for mrp and lab_delivery: ask the
    model whether the field is there, never assume the sibling is installed.
    """
    # Where lab_whatsapp is installed the WhatsApp Number is the ONLY number a
    # chat opens on: a clinic's phone is usually its landline, and wa.me opens an
    # empty chat for it instead of failing. (client, 2026-09-15)
    if 'whatsapp_number' in partner._fields:
        return partner.whatsapp_number or False
    return partner.phone


def _map_url_and_address(clinic):
    """Directions to a clinic, and the address to show alongside them.

    `geo:`, not `https://www.google.com/maps/dir/?api=1&destination=...` — the field
    app is a plain WebView wrapper (mobo FullSuite), not a real browser, and a bare
    WebView has no `shouldOverrideUrlLoading` override for the `intent://...#Intent;
    ...package=com.google.android.apps.maps;...end;` URI that Android's OS-level App
    Links verification rewrites a tapped google.com/maps link into. The result was a
    hard "net::ERR_UNKNOWN_URL_SCHEME" page - reported from the field 2026-08-29.
    `geo:` is not an http(s) URL at all, so it is never subject to that App Links
    rewrite; it goes through Android's older, plainer custom-scheme dispatch instead,
    which even a minimal wrapper's boilerplate ordinarily forwards to whatever the
    device offers for navigation.

    The address travels back alongside the link rather than being folded into it,
    because a `geo:` URI has no visible text of its own for a wrapper that turns out
    to handle NEITHER scheme - shown as plain text next to Navigate, the executive
    can still read and act on it by hand. (client, 2026-08-29)
    """
    if not clinic:
        return False, ''
    address = ' '.join((clinic.contact_address or '').split())
    if clinic.is_geolocated:
        lat, lon = clinic.partner_latitude, clinic.partner_longitude
        label = quote_plus(clinic.name or '')
        return 'geo:%s,%s?q=%s,%s(%s)' % (lat, lon, lat, lon, label), address
    if address:
        return 'geo:0,0?q=%s' % quote_plus(address), address
    return False, ''


def metres_between(lat1, lon1, lat2, lon2):
    """Great-circle distance, or None when either fix is missing.

    Returning None rather than 0.0 for a missing fix matters: zero is a real distance
    and would read as 'standing in the clinic', which is the opposite of the truth.
    """
    if not (has_fix(lat1, lon1) and has_fix(lat2, lon2)):
        return None
    p1, p2 = radians(lat1), radians(lat2)
    dp, dl = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return 2 * EARTH_M * asin(sqrt(a))


def has_fix(lat, lon):
    """True when a coordinate pair is a position, not the absence of one.

    None and 0,0 are both 'no fix': a wrapper that could not get a position
    sends zeros, and Null Island is not in Kerala. This used to be asked as
    metres_between(lat, lon, lat, lon) is None - a great-circle distance from
    a point to itself, computed only to be thrown away. (2026-09-09)
    """
    if lat is None or lon is None:
        return False
    return not (abs(lat) < COORD_EPSILON and abs(lon) < COORD_EPSILON)


class LabVisit(models.Model):
    """One clinic call, from planned to closed.

    Everything an executive does in a day happens on this record, so it is the only
    form they need to understand. Orders, money and travel all attach here rather than
    living in parallel places the user has to remember to keep in step.
    """
    _name = 'lab.visit'
    _description = 'Clinic Visit'
    _order = 'date desc, id desc'
    _inherit = ['lab.lock.mixin', 'lab.own.record.mixin',
                'mail.thread', 'mail.activity.mixin']

    # Closed visits are read-only in the server, not merely in the form: a list edit,
    # an import or an RPC call all pass through write(). The note stays open so a
    # supervisor can still record what came to light afterwards.
    _lock_states = ('done', 'cancel')
    _lock_exempt_fields = ('note',)
    # WHAT HAPPENED, after the fact. The executive checks out at the counter
    # with the doctor still talking, and the cases, the outcome chips and the
    # money are often only settled a minute later - by then the visit is
    # Completed and the whole record was frozen, so the day's figures were
    # wrong and only a manager could fix them. These six stay writable on a
    # COMPLETED visit; a cancelled one stays frozen, because nothing happened
    # at a visit that did not happen. (client, 2026-09-12)
    _LOCK_DONE_EXEMPT = ('outcome', 'outcome_ids', 'cases_counted',
                         'reworks_collected', 'collected', 'pay_mode')
    _lock_bypass_groups = ('lab_fieldwork.group_fieldwork_manager',)

    def _lock_exempt_fields_for(self, state):
        """A completed visit still accepts what happened; a cancelled one does not."""
        exempt = super()._lock_exempt_fields_for(state)
        if state == 'done':
            return tuple(exempt) + self._LOCK_DONE_EXEMPT
        return exempt

    def _compute_lock_state(self):
        """The banner must not say "can no longer be edited" where it can.

        A completed visit is read-only except for what happened at the counter,
        and a banner that overstates the lock teaches people to ask a manager
        for a change they could make themselves. (client, 2026-09-12)
        """
        super()._compute_lock_state()
        for visit in self:
            if visit.state == 'done' and visit.lock_is_locked and not visit.lock_can_edit:
                visit.lock_message = _(
                    "This visit is Completed. The cases, the outcome and the payment "
                    "can still be recorded; everything else is read-only - reset it to "
                    "an open state, or ask a user with the supervising role.")

    name = fields.Char(default=lambda self: _('New'), copy=False, readonly=True)
    state = fields.Selection(
        [('planned', 'Planned'), ('open', 'At Clinic'),
         ('done', 'Completed'), ('cancel', 'Cancelled')],
        default='planned', required=True, tracking=True, copy=False,
        group_expand='_expand_states')

    partner_id = fields.Many2one(
        'res.partner', string='Clinic', required=True, tracking=True,
        # An executive is offered only the clinics on their own sales route; a
        # manager sees every clinic. See res.partner._search_lab_on_my_route.
        domain="[('lab_on_my_route', '=', True)]")
    contact_id = fields.Many2one(
        'res.partner', string='Doctor / Contact',
        domain="[('parent_id', '=', partner_id)]")
    beat_id = fields.Many2one('lab.beat', string='Beat', index=True, ondelete='set null')
    user_id = fields.Many2one(
        'res.users', string='Executive',
        domain="[('fw_is_field_person', '=', True)]", required=True, index=True, tracking=True,
        default=lambda self: self.env.user)
    date = fields.Date(
        string='Visit Date', required=True, index=True,
        default=fields.Date.context_today, tracking=True)

    purpose = fields.Selection(PURPOSES, default='round', required=True)
    outcome = fields.Selection(OUTCOMES, tracking=True)
    note = fields.Text('What happened')
    photo = fields.Image('Proof Photo', max_width=1024, max_height=1024)

    # --- timing
    check_in = fields.Datetime(readonly=True, copy=False)
    check_out = fields.Datetime(readonly=True, copy=False)
    minutes = fields.Integer('Minutes at Clinic', compute='_compute_minutes', store=True)

    # --- location
    gps_lat = fields.Float(digits=(10, 7), readonly=True, copy=False)
    gps_lon = fields.Float(digits=(10, 7), readonly=True, copy=False)
    distance_m = fields.Float('Metres from Clinic', readonly=True, copy=False)
    gps_state = fields.Selection(
        [('ok', 'At the clinic'), ('far', 'Away from the clinic'),
         ('nofix', 'No location'), ('nopin', 'Recorded — clinic not pinned')],
        string='Location Check', compute='_compute_gps', store=True)
    gps_reason = fields.Char(
        'Reason', tracking=True,
        help="Required when checking in away from the clinic.")
    # The SECOND fix. A visit had one location - where it started - so a phone
    # carried to the door, checked in and then driven away still produced a
    # clean record, and nothing said how long the executive really stayed or
    # where they were when they closed it. (client, 2026-09-05)
    out_gps_lat = fields.Float(digits=(10, 7), readonly=True, copy=False)
    out_gps_lon = fields.Float(digits=(10, 7), readonly=True, copy=False)
    out_distance_m = fields.Float('Metres from Clinic (finish)', readonly=True,
                                  copy=False)
    out_gps_state = fields.Selection(
        [('ok', 'At the clinic'), ('far', 'Away from the clinic'),
         ('nofix', 'No location'), ('nopin', 'Recorded — clinic not pinned')],
        string='Location Check (finish)', compute='_compute_gps', store=True)
    # The two fixes as links the phone can open. A `geo:` URI for the same
    # reason the clinic's own Navigate link is one (see partner_map_uri): it is
    # not an http URL, so an app-wrapper cannot swallow it.
    gps_map_url = fields.Char('Start location', compute='_compute_fix_urls')
    out_gps_map_url = fields.Char('Finish location', compute='_compute_fix_urls')

    # An automatic close is not the same event as somebody pressing Finish, and a report
    # that cannot tell them apart is a report that overstates how disciplined the day was.
    auto_closed = fields.Boolean(readonly=True, copy=False,
                                 help="Closed by the end-of-day job, not by the executive.")
    auto_close_failed = fields.Boolean(
        readonly=True, copy=False, string='Left Open',
        help="Still open at the end of the day with no outcome recorded, so it could "
             "not be closed without inventing one.")

    # --- outputs
    # The slips an executive fills in at the counter, and the orders they became.
    case_ids = fields.One2many('lab.case', 'visit_id', string='Case Slips', copy=False)
    case_count = fields.Integer(compute='_compute_cases', store=True)
    case_draft_count = fields.Integer(compute='_compute_cases', store=True,
                                      string='Not Submitted')
    case_ready_count = fields.Integer(compute='_compute_cases', store=True,
                                      string='Submitted')
    # The count the executive gives at the door - "how many cases did you
    # take?" - typed into a widget big enough for a thumb. Deliberately a hand
    # count and not the registered slips (case_count): slips are entered one
    # by one and often later, and the two disagreeing is information for the
    # desk, not an error to prevent. (client, 2026-09-08)
    cases_counted = fields.Integer(
        'Number of cases', copy=False, tracking=True,
        help="How many cases were taken from this clinic, as counted by the "
             "executive at the door. Typed in, not computed - the case slips "
             "registered in Odoo are counted separately as Cases.")
    _cases_counted_not_negative = models.Constraint(
        'CHECK(cases_counted >= 0)',
        'The number of cases cannot be negative.')
    # Reworks are cases coming BACK - work the doctor returned for correction.
    # Counted the same way, in the same widget, but only once the executive
    # has said the visit collected reworks (the outcome chip): a rework count
    # without that outcome is a contradiction, so the count is cleared when
    # the chip goes out - see visit_tags.py. (client, 2026-09-08)
    reworks_collected = fields.Integer(
        'No. of reworks collected', copy=False, tracking=True,
        help="How many reworks (cases returned for correction) were taken from "
             "this clinic, as counted by the executive. Shown once the outcome "
             "'Rework(s) Collected' is selected.")
    _reworks_collected_not_negative = models.Constraint(
        'CHECK(reworks_collected >= 0)',
        'The number of reworks cannot be negative.')
    order_ids = fields.One2many('sale.order', 'visit_id', string='Cases', copy=False)
    order_count = fields.Integer(compute='_compute_orders', store=True)
    # How often this clinic has been seen. On the visit form it answers the question an
    # executive actually has at the door — "when were we last here, and how did it go" —
    # without making them leave the record to find out.
    clinic_visit_count = fields.Integer(compute='_compute_clinic_history')
    clinic_last_visit = fields.Date(compute='_compute_clinic_history',
                                    string='Previously Visited')
    order_value = fields.Monetary(compute='_compute_orders', store=True,
                                  currency_field='currency_id')
    collected = fields.Monetary('Payment Collected', currency_field='currency_id',
                                tracking=True)
    pay_mode = fields.Selection(
        [('cash', 'Cash'), ('cheque', 'Cheque'), ('online', 'UPI / Online')],
        string='Received As')

    # --- what a phone needs to act on this visit
    # Computed on the server rather than assembled in the browser: a wrong number or a
    # broken map link is a person standing in the street, and this way it is testable.
    clinic_latitude = fields.Float(related='partner_id.partner_latitude', readonly=True)
    clinic_longitude = fields.Float(related='partner_id.partner_longitude', readonly=True)
    call_number = fields.Char(compute='_compute_reachability')
    whatsapp_number = fields.Char(compute='_compute_reachability')
    map_url = fields.Char(compute='_compute_reachability')
    map_address = fields.Char(
        compute='_compute_reachability',
        help="Shown next to Navigate so the visit is never a dead end for an "
             "executive whose phone can't open the map link.")

    company_id = fields.Many2one(
        'res.company', default=lambda self: self.env.company, required=True)
    currency_id = fields.Many2one(related='company_id.currency_id', readonly=True)

    _name_uniq = models.Constraint('unique(name)', 'Visit reference must be unique.')

    @api.model
    def _expand_states(self, states, domain):
        # Every column shows on the kanban even when empty, so the board keeps its shape
        # and "nothing planned" is visible rather than absent.
        return [s[0] for s in self._fields['state'].selection]

    # ------------------------------------------------------------------ computes
    @api.depends('check_in', 'check_out')
    def _compute_minutes(self):
        for v in self:
            if v.check_in and v.check_out:
                v.minutes = int((v.check_out - v.check_in).total_seconds() // 60)
            else:
                v.minutes = 0

    # `check_in` belongs here because the method branches on it: a phone that refuses
    # the location permission writes a check-in and no coordinates, and without this
    # dependency nothing invalidates the stored value — the visit keeps the blank state
    # it was created with instead of being recorded as 'nofix'.
    @api.depends('check_in', 'gps_lat', 'gps_lon',
                 'check_out', 'out_gps_lat', 'out_gps_lon',
                 'partner_id.partner_latitude',
                 'partner_id.partner_longitude', 'partner_id.visit_radius_m')
    def _compute_gps(self):
        default_radius = float(self.env['ir.config_parameter'].sudo().get_param(
            'lab_fieldwork.visit_radius_m', 300))
        for v in self:
            clinic = v.partner_id
            radius = clinic.visit_radius_m or default_radius
            v.distance_m, v.gps_state = v._judge_fix(
                v.check_in, v.gps_lat, v.gps_lon, clinic, radius)
            v.out_distance_m, v.out_gps_state = v._judge_fix(
                v.check_out, v.out_gps_lat, v.out_gps_lon, clinic, radius)

    def _judge_fix(self, when, lat, lon, clinic, radius):
        """(metres, state) for one end of the visit — start or finish.

        Both ends are judged the same way and by the same code: a rule that
        holds on arrival and is looser on the way out is not a rule.
        """
        if not when:
            return 0.0, False
        # The PHONE's fix is judged first, the clinic's pin second. The other
        # order read every visit on this database as "clinic not pinned" — 0 of
        # its 2,178 clinics carry coordinates — whether the executive's phone had
        # given a position or not, so a captured location and a missing one
        # looked identical on the sheet, and the lab concluded nothing was
        # being captured. (client, 2026-09-08)
        if not has_fix(lat, lon):
            # A denied location permission is a different problem from being in
            # the wrong place, and must never be reported as the wrong place.
            return 0.0, 'nofix'
        if not clinic.is_geolocated:
            # Recorded, but nothing to measure it against yet.
            return 0.0, 'nopin'
        metres = metres_between(lat, lon,
                                clinic.partner_latitude, clinic.partner_longitude)
        return round(metres, 1), ('ok' if metres <= radius else 'far')

    @api.depends('gps_lat', 'gps_lon', 'out_gps_lat', 'out_gps_lon',
                 'check_in', 'check_out')
    def _compute_fix_urls(self):
        for v in self:
            v.gps_map_url = v._fix_url(v.check_in, v.gps_lat, v.gps_lon)
            v.out_gps_map_url = v._fix_url(
                v.check_out, v.out_gps_lat, v.out_gps_lon)

    def _fix_url(self, when, lat, lon):
        """A map link for one fix, or nothing when there is no fix to open."""
        if not when or not has_fix(lat, lon):
            return False
        return 'geo:%s,%s?q=%s,%s(%s)' % (lat, lon, lat, lon,
                                          self.display_name or _('Visit'))

    @api.depends('contact_id.phone', 'partner_id.phone', 'partner_id.partner_latitude',
                 'partner_id.partner_longitude', 'partner_id.contact_address')
    def _compute_reachability(self):
        for v in self:
            clinic = v.partner_id
            # The named contact first: an executive rings the doctor they came to see,
            # not the clinic's front desk.
            v.call_number = v.contact_id.phone or clinic.phone or False
            wa_partner = v.contact_id if v.contact_id and _wa_source(v.contact_id) \
                else clinic
            v.whatsapp_number = _wa_number(_wa_source(wa_partner), wa_partner) \
                if wa_partner else False
            v.map_url, v.map_address = _map_url_and_address(clinic)

    @api.depends('partner_id')
    def _compute_clinic_history(self):
        """Every clinic's done-visit history in ONE query.

        Read per record this was a search per row of the visit list. The history is
        fetched once for all the clinics involved and each record then excludes itself
        in Python — which is also the only way to get "excluding me" right when this
        visit happens to be the most recent one.
        """
        partners = self.mapped('partner_id')
        history = {}
        if partners:
            rows = self.search_read(
                [('partner_id', 'in', partners.ids), ('state', '=', 'done')],
                ['partner_id', 'date'], order='date desc')
            for row in rows:
                history.setdefault(row['partner_id'][0], []).append(
                    (row['id'], row['date']))
        for v in self:
            mine = v._origin.id or 0
            others = [r for r in history.get(v.partner_id.id, []) if r[0] != mine]
            v.clinic_visit_count = len(others)
            v.clinic_last_visit = others[0][1] if others else False

    @api.depends('case_ids.state')
    def _compute_cases(self):
        for v in self:
            live = v.case_ids.filtered(lambda c: c.state != 'cancel')
            v.case_count = len(live)
            v.case_draft_count = len(live.filtered(lambda c: c.state == 'draft'))
            v.case_ready_count = len(live.filtered(lambda c: c.state == 'submitted'))

    @api.depends('order_ids.amount_untaxed', 'order_ids.state')
    def _compute_orders(self):
        for v in self:
            live = v.order_ids.filtered(lambda o: o.state != 'cancel')
            v.order_count = len(live)
            v.order_value = sum(live.mapped('amount_untaxed'))

    # ------------------------------------------------------------------ create
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'lab.visit') or _('New')
        return super().create(vals_list)

    # ------------------------------------------------------------------ workflow
    # A visit is now REFUSED without a location, at both ends.
    #
    # This reverses the module's original call, which recorded a locationless
    # visit and showed it as such rather than blocking - the reasoning being
    # that a lost visit is worse than an unexplained one. On the live data that
    # bet lost: day sheets were arriving with thirty-odd visits, every check-in
    # and check-out within the same minute and zero kilometres of travel, which
    # is a day typed at a desk and not a round that happened. A record nobody
    # can trust costs more than a missing one. (client, 2026-09-05)
    def _require_fix(self, latitude, longitude, what):
        """Refuse a location the phone did not actually provide.

        Judged on the COORDINATES, not on whether the browser answered: a
        denied permission and a wrapper that helpfully sends 0,0 must fail the
        same way, or the check is decoration. Null Island is not in Kerala.
        """
        if not has_fix(latitude, longitude):
            raise UserError(_(
                "Switch your location on to %(what)s.\n\n"
                "Your phone did not give a position, so this visit cannot be "
                "recorded. Turn on GPS / Location for the browser, wait until "
                "the map arrow appears, and try again.", what=what))

    def action_pin_clinic_here(self):
        """Make this visit's recorded position the clinic's pin.

        None of the lab's clinics carry coordinates, so "at the door" can be
        judged for none of them and every sheet reads "clinic not pinned".
        Geocoding 2,178 addresses is a project; a manager confirming one real
        visit is a tap. The pin is the executive's START fix — where they were
        when they said they had arrived — and once set, this and every later
        visit to the clinic is measured against it. Managers only: it is a
        statement about the clinic, not about the day. (client, 2026-09-08)
        """
        self.ensure_one()
        if not self.env.user.has_group('lab_fieldwork.group_fieldwork_manager'):
            raise UserError(_("Only a manager pins a clinic."))
        if not has_fix(self.gps_lat, self.gps_lon):
            raise UserError(_(
                "This visit recorded no position, so there is nothing to pin."))
        self.partner_id.sudo().write({
            'partner_latitude': self.gps_lat,
            'partner_longitude': self.gps_lon,
            'date_localization': fields.Date.context_today(self),
        })
        self.message_post(body=_(
            "Clinic pinned at this visit's recorded position (%(lat)s, %(lon)s) "
            "by %(who)s.", lat=self.gps_lat, lon=self.gps_lon,
            who=self.env.user.name))
        return True

    def do_check_in(self, latitude=False, longitude=False):
        """Arrive at the clinic. Called by the geo button, which supplies the fix."""
        self.ensure_one()
        if self.state != 'planned':
            raise UserError(_("This visit is not waiting to be started."))
        self._require_fix(latitude, longitude, _("start a visit"))
        self.write({
            'state': 'open',
            'check_in': fields.Datetime.now(),
            'gps_lat': latitude or 0.0,
            'gps_lon': longitude or 0.0,
        })
        # Being in the WRONG place is still recorded rather than blocked - the
        # executive may legitimately be at the doctor's other branch - but it
        # has to be explained before the visit can close.
        if self.gps_state == 'far':
            self.message_post(body=_(
                "Checked in %(m)s m from the clinic.", m=int(self.distance_m)))
        return True

    def do_check_out(self, latitude=False, longitude=False):
        self.ensure_one()
        if self.state != 'open':
            raise UserError(_("Check in before checking out."))
        if not self.outcome:
            raise UserError(_(
                "Record what happened at the clinic before closing the visit."))
        # The end-of-day job closes what the executive left open. It runs on a
        # server at midnight with no phone in its hand, so it is the one caller
        # that cannot be asked for a position - and its visits are already
        # stamped `auto_closed`, which is how a supervisor tells them apart.
        auto = self.env.context.get('fw_auto_close')
        if not auto:
            self._require_fix(latitude, longitude, _("finish a visit"))
        if self.gps_state == 'far' and not self.gps_reason:
            raise UserError(_(
                "This check-in was %(m)s m from the clinic. Add a short reason before "
                "closing.", m=int(self.distance_m)))
        if self.collected and not self.pay_mode:
            raise UserError(_("Say how the payment was received."))

        # Orders are created HERE, not when the slip is saved.
        #
        # An executive fills a slip in while the doctor is still talking and edits it
        # twice before leaving. Creating the order on save would mean a half-entered
        # case reaching verification, and a correction becoming somebody else's problem
        # rather than a tap on the same screen. Check-out is the moment the executive
        # says they are finished, so it is the only honest point to commit.
        #
        # Deliberately before the state write: if a slip is a suspected duplicate or has
        # no work on it, the visit stays open and the person is still at the counter to
        # fix it.
        self._register_cases()

        vals = {'state': 'done', 'check_out': fields.Datetime.now(),
                'out_gps_lat': latitude or 0.0, 'out_gps_lon': longitude or 0.0}
        if auto:
            # Closed by the end-of-day job, not by a person standing at the clinic.
            # The flag is set HERE so the marker survives going through the real
            # check-out path rather than a direct write. (client, 2026-08-26)
            vals['auto_closed'] = True
        self.write(vals)
        # Said out loud, the same way the arrival is: a visit started at the
        # door and closed two towns away is the shape this whole change exists
        # to make visible.
        if self.out_gps_state == 'far':
            self.message_post(body=_(
                "Finished %(m)s m from the clinic.", m=int(self.out_distance_m)))
        return True

    def _register_cases(self):
        """Turn this visit's submitted slips into orders awaiting verification."""
        self.ensure_one()
        # A slip still being entered is not silently swept into an order: the executive
        # either finished it or did not, and only they know which. Refusing here keeps
        # them at the counter, where the doctor can still answer.
        unsubmitted = self.case_ids.filtered(lambda c: c.state == 'draft')
        if unsubmitted:
            raise UserError(_(
                "%(n)s case(s) have not been submitted: %(who)s.\n\nSubmit each one so "
                "its details are confirmed, or delete the slip if it was started by "
                "mistake.",
                n=len(unsubmitted), who=', '.join(unsubmitted.mapped('patient'))))
        pending = self.case_ids.filtered(lambda c: c.state == 'submitted')
        if not pending:
            return self.env['sale.order']
        orders = pending.action_create_order()
        if orders:
            self.message_post(body=_(
                "%(n)s case(s) sent for verification: %(refs)s",
                n=len(orders), refs=', '.join(orders.mapped('name'))))
        return orders

    def unlink(self):
        """Deleting a visit must not take registered cases with it.

        `lab.case.visit_id` is ondelete='cascade', which is a Postgres foreign key:
        the rows go without `lab.case.unlink()` ever being called, so the guard on the
        case would be silently bypassed by deleting the visit instead.
        """
        registered = self.env['lab.case'].sudo().search([
            ('visit_id', 'in', self.ids), ('state', '=', 'registered')])
        if registered:
            raise UserError(_(
                "%(visits)s produced %(n)s registered case(s) that became orders "
                "(%(orders)s). Deleting the visit would delete them and leave those "
                "orders unexplained. Cancel the visit instead.",
                visits=', '.join(registered.mapped('visit_id.name')),
                n=len(registered),
                orders=', '.join(registered.mapped('sale_order_id.name'))))
        return super().unlink()

    def action_cancel(self):
        self.write({'state': 'cancel'})

    def action_reset(self):
        # Both fixes and both close markers: a reset visit that kept its finish
        # position or its "closed automatically" stamp would carry them into a
        # visit that has not happened yet. (2026-09-15)
        self.write({'state': 'planned', 'check_in': False, 'check_out': False,
                    'gps_lat': 0.0, 'gps_lon': 0.0,
                    'out_gps_lat': 0.0, 'out_gps_lon': 0.0,
                    'auto_closed': False, 'auto_close_failed': False})

    def action_new_case(self):
        """Open a blank case slip. Always a new one — a doctor commonly hands over
        several cases in one visit.

        A slip, not a sale order: pricing, taxes, delivery and invoicing policy are
        somebody else's job, and putting them in front of a person standing at a counter
        is how orders get made that have to be corrected afterwards.
        """
        self.ensure_one()
        if self.state == 'planned':
            raise UserError(_(
                "Check in at %s before registering a case.", self.partner_id.display_name))
        return {
            'type': 'ir.actions.act_window', 'name': _('Register a Case'),
            'res_model': 'lab.case', 'view_mode': 'form',
            'context': {'default_visit_id': self.id},
        }

    def action_view_case_slips(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': _('Case Slips'),
            'res_model': 'lab.case', 'view_mode': 'list,form',
            'domain': [('visit_id', '=', self.id)],
            'context': {'default_visit_id': self.id},
        }

    def action_view_clinic_history(self):
        """Every other visit to this clinic — the question an executive has at the door
        and would otherwise have to leave the record to answer."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Previous Visits to %s', self.partner_id.display_name),
            'res_model': 'lab.visit', 'view_mode': 'list,form',
            'domain': [('partner_id', '=', self.partner_id.id), ('id', '!=', self.id)],
        }

    def action_view_clinic(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': self.partner_id.display_name,
            'res_model': 'res.partner', 'res_id': self.partner_id.id,
            'view_mode': 'form',
        }

    def action_view_cases(self):
        """The sale orders this visit produced. Named 'Orders' in the UI: 'Cases' now
        means the slips, which is what an executive is actually looking for."""
        self.ensure_one()
        action = {
            'type': 'ir.actions.act_window', 'name': _('Orders'),
            'res_model': 'sale.order',
            'domain': [('visit_id', '=', self.id)],
            'context': {'default_partner_id': self.partner_id.id,
                        'default_visit_id': self.id},
        }
        if len(self.order_ids) == 1:
            action.update(view_mode='form', res_id=self.order_ids.id)
        else:
            action.update(view_mode='list,form')
        return action


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    visit_id = fields.Many2one(
        'lab.visit', string='Field Visit', copy=False, readonly=True,
        index='btree_not_null')

    @api.model_create_multi
    def create(self, vals_list):
        visit_id = self.env.context.get('default_visit_id')
        if visit_id:
            for vals in vals_list:
                vals.setdefault('visit_id', visit_id)
        orders = super().create(vals_list)
        # sudo: the visit may already be closed, and a case handed over late still
        # belongs to it. The lock deliberately lets a bypassing write through.
        #
        # ADDED to what happened, not written over it: a visit that delivered
        # and also took a case is both, and forcing outcome='order' erased the
        # delivery. The primary outcome is only set when there is none; the
        # tag sync is held off so it does not re-derive the primary from the
        # new chip. (2026-09-15)
        visits = orders.visit_id.sudo()
        tag = visits._tag_for('lab.visit.outcome', 'order') if visits else False
        for visit in visits:
            vals = {}
            if tag and tag not in visit.outcome_ids:
                vals['outcome_ids'] = [(4, tag.id)]
            if not visit.outcome:
                vals['outcome'] = 'order'
            if vals:
                visit.with_context(_fw_tag_sync=True).write(vals)
        return orders
