# -*- coding: utf-8 -*-
import re
from datetime import timedelta

from odoo.tools import SQL
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

# Honorifics and relationship words people put in front of a patient's name. They carry
# no identity, and leaving them in means "Mr Anu Raj" and "Anu Raj" read as two patients.
TITLES = {'mr', 'mrs', 'ms', 'miss', 'master', 'mstr', 'dr', 'baby', 'b', 'smt', 'sri',
          'shri', 'kum', 'kumari', 'md', 'mohd'}

UL = [('upper', 'U'), ('lower', 'L'), ('ul', 'UL')]


def patient_key(name):
    """A comparable form of a patient's name.

    Duplicate detection is the whole reason this exists, and it has to survive how
    people actually write names at a counter: initials before or after, an honorific,
    double spaces, a full stop, different capitalisation. Tokens are SORTED, so
    "Raj Anu" and "Anu Raj" collide — at one clinic, in one week, that is the same child
    far more often than it is two different ones.

    Deliberately not fuzzy beyond this. Anything looser starts merging siblings.
    """
    if not name:
        return False
    words = re.findall(r'[a-z0-9]+', (name or '').lower())
    words = [w for w in words if w not in TITLES]
    # A lone initial is noise for matching: "A Raj" and "Anu Raj" should still meet.
    words = [w for w in words if len(w) > 1] or words
    return ' '.join(sorted(words)) or False


class LabCase(models.Model):
    """A case slip, filled in at the counter and turned into an order at check-out.

    The executive does NOT get a sale order form. A sale order carries pricing, taxes,
    delivery, invoicing policy and a dozen fields that are somebody else's job, and
    putting that in front of a person standing at a clinic counter is how you get
    orders that have to be corrected afterwards. This model is only what the doctor
    actually hands over: who the patient is, what is being made, upper or lower.

    Everything commercial is decided later, by the people whose job it is, when the
    order reaches verification.
    """
    _name = 'lab.case'
    _description = 'Case Registration'
    _order = 'id desc'
    # The own-records rule: since a case can stand without a visit its executive is
    # its own field, and an executive may not put one in a colleague's name.
    _inherit = ['lab.lock.mixin', 'mail.thread', 'lab.own.record.mixin']

    # Submitted is locked as well as registered. The executive has said the slip is
    # complete, so changing it silently afterwards would make "submitted" mean nothing —
    # and the mixin allows a write that LEAVES a locked state, which is exactly what
    # Reopen does. Correcting a slip is therefore explicit rather than accidental.
    _lock_states = ('submitted', 'registered', 'cancel')
    _lock_exempt_fields = ('note',)
    _lock_bypass_groups = ('lab_fieldwork.group_fieldwork_manager',)

    name = fields.Char(compute='_compute_name', store=True)
    # Not every case is handed over at a counter. Some arrive by courier or on a bus
    # parcel with no visit behind them, and making the executive invent a visit to
    # register one put a call on their day that never happened. A case taken at a
    # visit still takes its clinic, executive and day from it; one without a visit
    # says them itself. (client, 2026-11-24)
    visit_id = fields.Many2one(
        'lab.visit', ondelete='cascade', index=True,
        string='Visit', copy=False)
    # 'clinic': collected at the clinic by the executive with no visit recorded
    # - the commonest way a case without a visit comes in, so it is the default.
    # (client, 2026-11-27: "Add 'Clinic' option in How it came and set it by default")
    received_via = fields.Selection(
        [('visit', 'At a visit'), ('clinic', 'Clinic'), ('courier', 'Courier'),
         ('transport', 'Transport')],
        string='Received via', compute='_compute_received_via', store=True,
        readonly=False, precompute=True, required=True, tracking=True,
        help="How the case reached the lab: handed over at a visit, collected at the "
             "clinic, or sent by courier or transport.")
    received_ref = fields.Char(
        'Docket / LR no.', tracking=True,
        help="The courier's docket number or the transport's LR number, so the "
             "parcel can be traced if it goes missing.")
    partner_id = fields.Many2one(
        'res.partner', compute='_compute_partner_from_visit', store=True, readonly=False,
        precompute=True, required=True, index=True, string='Clinic', tracking=True,
        # The clinics this person may work, as on a visit.
        domain="[('lab_on_my_route', '=', True)]")
    user_id = fields.Many2one(
        'res.users', compute='_compute_user_from_visit', store=True, readonly=False,
        precompute=True, string='Executive', index=True)
    date = fields.Date(
        compute='_compute_date_from_visit', store=True, readonly=False, precompute=True,
        index=True)
    company_id = fields.Many2one(
        'res.company', compute='_compute_company_from_visit', store=True, readonly=False,
        precompute=True)
    currency_id = fields.Many2one(related='company_id.currency_id')

    state = fields.Selection(
        [('draft', 'Being Entered'), ('submitted', 'Submitted'),
         ('registered', 'Order Created'), ('cancel', 'Cancelled')],
        default='draft', required=True, tracking=True, copy=False)

    # --- the patient
    patient = fields.Char(required=True, tracking=True)
    patient_key = fields.Char(
        compute='_compute_patient_key', store=True, index=True,
        help="Normalised patient name used to spot the same case entered twice.")
    age = fields.Integer()
    gender = fields.Selection([('male', 'Male'), ('female', 'Female')])

    # --- what was handed over
    line_ids = fields.One2many('lab.case.line', 'case_id', string='Work', copy=True)
    # THE SHADE OF THE CASE. A doctor gives one shade for a case far more often than
    # one per appliance, and the slip asked for it on every item: four taps for a
    # bridge and a crown, and the one that was missed went to the lab without a
    # shade. Said once here, every item takes it; an item that differs still says
    # so on its own card. (client, 2026-11-25: "field executive can add shades in
    # the case register view instead of items; do not remove shade in items")
    colour_id = fields.Many2one(
        'product.colour', string='Shade', tracking=True,
        help="The shade of the whole case. Every item takes it; change an item's "
             "own shade where one differs.")
    line_count = fields.Integer(compute='_compute_lines', store=True)

    # --- registration details, in the lab's own words
    # 'emergency' extends the lab's existing low/normal/urgent rather than renumbering
    # it: the keys are compared against by name in several modules, and a rename would
    # break them to gain nothing. See lab_order_control/models/sale_order_priority.py.
    priority = fields.Selection(
        [('low', 'Low'), ('normal', 'Normal'), ('urgent', 'Urgent'),
         ('emergency', 'Emergency')],
        default='normal', required=True, tracking=True)
    is_urgent_open = fields.Boolean(
        compute='_compute_is_urgent_open', store=True,
        help="Urgent or emergency and not yet turned into an order — the slips that "
             "need somebody to move.")
    # What came with the case, as the production sheet asks it: the impression (or a
    # poured cast), the tray, the wax bite and the two models. Taken here, at the
    # counter, so the sheet prints the answers instead of boxes the floor ticks by
    # hand. (client, 2026-11-26)
    impression_type = fields.Selection(
        [('Alginate', 'Alginate'), ('Rubber Base', 'Rubber Base'), ('Cast', 'Cast')],
        string='Impression')
    impression_tray = fields.Selection(
        [('Upper', 'Upper'), ('Lower', 'Lower'), ('Both', 'Both')], string='Tray')
    scanned_impression = fields.Selection([('Yes', 'Yes'), ('No', 'No')], string='Scanned')
    wax_bite = fields.Selection([('Yes', 'Yes'), ('No', 'No')], string='Wax Bite')
    opposing_model = fields.Selection(
        [('Yes', 'Yes'), ('No', 'No')], string='Opposing Model',
        help="Did the model of the opposing arch come with the case?")
    pre_cutting_model = fields.Selection(
        [('Yes', 'Yes'), ('No', 'No')], string='Pre Cutting Model',
        help="Is a model to be kept as it was before the dies are cut?")
    send_through_id = fields.Many2one('send.through', string='Send Through')

    # What came in the bag. Booleans rather than a many2many so they match the job card
    # and the existing sale order exactly.
    is_screw = fields.Boolean('Screw')
    is_bite = fields.Boolean('Bite')
    is_bands = fields.Boolean('Bands')
    is_wires = fields.Boolean('Wires')
    is_teeth = fields.Boolean('Teeth')
    is_facebow = fields.Boolean('Face Bow')
    # The rest of what the lab's order form ticks. They were on the order and not on
    # the slip, so the counter asked for them again by phone. (client, 2026-09-29)
    is_dd_cheque = fields.Boolean('DD / Cheque')
    is_others = fields.Boolean('Others')
    is_3d_model_print = fields.Boolean(
        '3D Model Print', help="This case is (or includes) a 3D-printed model.")

    # Where the finished work goes. One answer out of three on the slip; the order
    # keeps it as two switches and two addresses, and is written that way.
    deliver_to = fields.Selection(
        [('clinic', 'The clinic'), ('patient', 'The patient'), ('custom', 'Another address')],
        string='Deliver to', default='clinic', required=True, tracking=True)
    delivery_address = fields.Text(
        'Delivery address',
        help="House, street, town and PIN - and a phone number the courier can ring.")

    # A gate on production, not a note. The lab must not start cutting until somebody
    # has spoken to the doctor — usually because the prescription is ambiguous and
    # guessing costs a remake.
    needs_doctor_call = fields.Boolean(
        'Call the doctor first', tracking=True,
        help="The lab must speak to the doctor before starting this case.")
    doctor_call_note = fields.Char(
        'What to ask',
        help="What the lab needs to settle on that call. Without it the message reaches "
             "production as 'ring the doctor' and nobody knows what about.")

    # Some doctors deal with one person at the lab and nobody else. Which person, and
    # why, is written on the case, so the office rings through the right one rather than
    # finding out from the doctor. (client, 2026-11-24)
    # A login, not an employee: the lab's employee-privacy rule lets a user without HR
    # rights find only themselves among employees, so an executive's picker came up
    # empty - and everyone who rings doctors has a login, which is also where the
    # to-do lands. (2026-11-24)
    contact_user_id = fields.Many2one(
        'res.users', string='Contact person', tracking=True,
        domain=[('share', '=', False)],
        help="The one person at the lab who should contact this doctor or clinic "
             "about this case.")
    contact_note = fields.Char(
        'Why this person', tracking=True,
        help="e.g. the doctor speaks only to her about payments.")

    modification = fields.Char('Modifications')
    instruction = fields.Char('Special Instructions')
    note = fields.Text('Notes')
    photo = fields.Image('Case Photo', max_width=1400, max_height=1400)

    # --- what became of it
    sale_order_id = fields.Many2one('sale.order', readonly=True, copy=False,
                                    string='Order')
    # A submitted case can be corrected by its executive until the office has
    # verified its order: the order is still a quotation in the verification queue,
    # and "change the order instead" asked them for a form they cannot open.
    # (client, 2026-11-24: "executive can edit the submitted cases")
    order_editable = fields.Boolean(
        compute='_compute_order_editable',
        help="The order this case became has not been verified yet, so the case can "
             "still be corrected and sent again.")
    # The order's number, kept on the slip: an executive has no right to read sale
    # orders, and "which order did this become" is asked at the counter the next
    # day. Read through the related's sudo, stored. (client, 2026-11-24)
    order_name = fields.Char(related='sale_order_id.name', store=True, string='Order No')

    # --- duplicate control
    duplicate_ids = fields.Many2many(
        'sale.order', compute='_compute_duplicates', string='Possible Duplicates')
    duplicate_case_ids = fields.Many2many(
        'lab.case', 'lab_case_dup_rel', 'case_id', 'dup_id',
        compute='_compute_duplicates', string='Also Entered')
    duplicate_warning = fields.Char(compute='_compute_duplicates')
    duplicate_ack = fields.Boolean(
        'Different case', copy=False, tracking=True,
        help="Tick to say this is more work for the same patient, not the same case "
             "entered twice. Not required: the case goes through either way, and the "
             "office is told on the order.")

    # ------------------------------------------------------------------ computes
    # One compute per field, deliberately. With one shared method, Odoo treats the
    # four as a group at create: giving the clinic (which a case without a visit
    # does) leaves the executive, day and company EMPTY rather than computing them.
    #
    # From the visit when there is one. Without one, the case keeps what was entered
    # on it - not assigning keeps the stored value - and the person entering it,
    # today and their company fill in what was left empty.
    @api.depends('visit_id.partner_id')
    def _compute_partner_from_visit(self):
        for case in self:
            if case.visit_id:
                case.partner_id = case.visit_id.partner_id
            elif not case.partner_id:
                case.partner_id = False

    @api.depends('visit_id.user_id')
    def _compute_user_from_visit(self):
        for case in self:
            if case.visit_id:
                case.user_id = case.visit_id.user_id
            elif not case.user_id:
                case.user_id = self.env.user

    @api.depends('visit_id.date')
    def _compute_date_from_visit(self):
        for case in self:
            if case.visit_id:
                case.date = case.visit_id.date
            elif not case.date:
                case.date = fields.Date.context_today(case)

    @api.depends('visit_id.company_id')
    def _compute_company_from_visit(self):
        for case in self:
            if case.visit_id:
                case.company_id = case.visit_id.company_id
            elif not case.company_id:
                case.company_id = self.env.company

    @api.depends('visit_id')
    def _compute_received_via(self):
        for case in self:
            if case.visit_id:
                case.received_via = 'visit'
            elif case.received_via not in ('clinic', 'courier', 'transport'):
                case.received_via = 'clinic'

    @api.depends('patient', 'partner_id')
    def _compute_name(self):
        for case in self:
            case.name = case.patient or _('New case')

    @api.depends('patient')
    def _compute_patient_key(self):
        for case in self:
            case.patient_key = patient_key(case.patient)

    @api.depends('line_ids')
    def _compute_lines(self):
        for case in self:
            case.line_count = len(case.line_ids)

    # One decorator. A second @api.depends stacked above this one REPLACED it,
    # so the stored flag never heard about priority or state changing.
    # (2026-09-15)
    @api.depends('priority', 'state')
    def _compute_is_urgent_open(self):
        for case in self:
            case.is_urgent_open = bool(
                case.priority in ('urgent', 'emergency')
                and case.state in ('draft', 'submitted'))

    def _compute_duplicates(self):
        """Two queries for the whole page, not two per slip.

        `_find_duplicates` stays the single definition of what a duplicate IS — it is
        still what `_check_ready` calls for one slip at save time, where correctness
        matters more than the query count. This prefetches the same answer in bulk so
        the case LIST, which shows a duplicate warning on every row, does not run two
        searches per row.
        """
        window = self._duplicate_window()
        self = self.with_context(fw_dup_cache=self._prefetch_duplicates(window))
        for case in self:
            orders, cases = case._find_duplicates(window)
            # Assigned elevated: `_find_duplicates` searches orders under sudo — an
            # executive cannot read the office's sale orders — and writing that
            # recordset onto a plain m2m re-checks read access on every id, so the
            # compute raised AccessError and the slip could not be saved at all.
            # (client, 2026-08-27)
            case.sudo().duplicate_ids = orders
            case.sudo().duplicate_case_ids = cases
            if not orders and not cases:
                case.duplicate_warning = False
                continue
            # Say which, and when. "Possible duplicate" alone gets acknowledged blind.
            #
            # The name shown is the EARLIER record's, not what was just typed: the two
            # spellings are usually different — that is why the normalised key exists —
            # and the executive needs the version they would recognise from the previous
            # slip, not their own keystrokes read back at them.
            earlier = orders[:1] or cases[:1]
            where = orders[:1].name if orders else _('another slip on this visit')
            # Advisory, not a gate: the same patient's work does come from the same
            # doctor twice in a week - the other jaw, a repair, the next stage. The
            # case goes through; the office sees the earlier one on the order.
            # (client, 2026-11-26: "Some time same patients work will receive from
            # same doctor, so do not block")
            case.duplicate_warning = _(
                "%(who)s also has %(n)s case(s) at %(clinic)s in the last %(days)s "
                "days — most recently %(ref)s. More work for the same patient? Carry "
                "on: the office is shown the earlier case. Only if this is the same "
                "case entered twice, delete this slip.",
                who=earlier.patient or case.patient, n=len(orders) + len(cases),
                clinic=case.partner_id.display_name, days=window, ref=where)

    # ------------------------------------------------------------------ duplicates
    @api.model
    def _duplicate_window(self):
        try:
            return max(0, int(self.env['ir.config_parameter'].sudo().get_str(
                'lab_fieldwork.duplicate_days', 7)))
        except (TypeError, ValueError):
            return 7

    def _prefetch_duplicates(self, window):
        """Every candidate order and sibling slip for the whole recordset, in two
        queries, keyed by (commercial partner, patient key)."""
        keyed = [c for c in self if c.patient_key and c.partner_id]
        if not keyed:
            return {}
        keys = list({c.patient_key for c in keyed})
        commercials = {c.partner_id.commercial_partner_id.id or c.partner_id.id
                       for c in keyed}
        # The families of every commercial partner on the page, resolved in ONE
        # search. Per-commercial it was still an N+1 - just moved from the order
        # search to the partner search - and an 80-row list spanning 60 clinics ran
        # 60 recursive parent_path queries. `child_of` takes a list, so one query
        # returns every family member; grouping them by commercial_partner_id
        # rebuilds exactly the same mapping.
        families = {cid: {cid} for cid in commercials}
        for member in self.env['res.partner'].sudo().with_context(
                active_test=False).search([('id', 'child_of', list(commercials))]):
            root = member.commercial_partner_id.id or member.id
            families.setdefault(root, {root}).add(member.id)
        family_ids = set().union(*families.values()) if families else set()
        family = self.env['res.partner'].browse(sorted(family_ids))
        since = min(c.date or fields.Date.context_today(c) for c in keyed) \
            - timedelta(days=window)

        orders = self.env['sale.order'].sudo().search([
            ('partner_id', 'in', family.ids), ('patient_key', 'in', keys),
            ('state', '!=', 'cancel'),
            ('date_order', '>=', fields.Datetime.to_datetime(since))])
        slips = self.sudo().search([
            ('partner_id', 'in', family.ids), ('patient_key', 'in', keys),
            ('state', '=', 'draft'), ('date', '>=', since)])
        return {'orders': orders, 'slips': slips, 'window': window,
                'families': families}

    def _find_duplicates(self, window=None):
        """Orders and sibling slips for the same patient at the same clinic, recently.

        Matched on the COMMERCIAL partner, not the contact: a clinic with three branch
        contacts is still one doctor, and a case entered against a different contact of
        the same clinic is exactly the duplicate this is looking for.
        """
        self.ensure_one()
        if window is None:
            window = self._duplicate_window()
        Order = self.env['sale.order']
        if not self.patient_key or not self.partner_id:
            return Order, self.browse()

        # Served from the bulk prefetch when one is in flight, filtered to exactly what
        # the per-record query would have matched.
        cache = self.env.context.get('fw_dup_cache')
        if cache and cache.get('window') == window:
            return self._from_dup_cache(cache)
        since = (self.date or fields.Date.context_today(self)) - timedelta(days=window)
        commercial = self.partner_id.commercial_partner_id or self.partner_id
        family = self.env['res.partner'].search(
            [('id', 'child_of', commercial.id)]).ids or [self.partner_id.id]

        orders = Order.sudo().search([
            ('partner_id', 'in', family),
            ('patient_key', '=', self.patient_key),
            ('state', '!=', 'cancel'),
            ('date_order', '>=', fields.Datetime.to_datetime(since)),
            ('id', '!=', (self.sale_order_id.id or 0)),
        ])
        cases = self.sudo().search([
            ('partner_id', 'in', family),
            ('patient_key', '=', self.patient_key),
            ('state', '=', 'draft'),
            ('date', '>=', since),
            ('id', '!=', self._origin.id or 0),
        ])
        return orders, cases

    def _from_dup_cache(self, cache):
        self.ensure_one()
        commercial = self.partner_id.commercial_partner_id or self.partner_id
        family = cache['families'].get(commercial.id) or {self.partner_id.id}
        since = (self.date or fields.Date.context_today(self)) \
            - timedelta(days=cache['window'])
        orders = cache['orders'].filtered(
            lambda o: o.patient_key == self.patient_key
            and o.partner_id.id in family
            and o.date_order.date() >= since
            and o.id != (self.sale_order_id.id or 0))
        slips = cache['slips'].filtered(
            lambda c: c.patient_key == self.patient_key
            and c.partner_id.id in family
            and c.date >= since
            and c.id != (self._origin.id or 0))
        return orders, slips

    # ------------------------------------------------------------------ integrity
    @api.constrains('visit_id', 'received_via')
    def _check_received_via(self):
        for case in self:
            if not case.visit_id and case.received_via == 'visit':
                raise ValidationError(_(
                    "%s was not taken at a visit: say whether it was collected at the "
                    "clinic or came by courier or transport.", case.patient or _('This case')))

    # ------------------------------------------------------------------ nothing listed
    # A case may be registered with nothing on it: the box came by courier with no
    # prescription, or the doctor said "the usual" and the executive does not know what
    # that is. The lab cannot start it, so it goes to the office with the doctor call
    # on - whoever picks it up rings the doctor and writes what is to be made. The
    # flag stays on while the slip is empty; it was a hard refusal to save until
    # 2026-11-24, and that refusal is what made executives keep cases off the system.
    def _ask_doctor_when_empty(self):
        empty = self.filtered(lambda c: c.state == 'draft' and not c.line_ids
                              and not c.needs_doctor_call)
        for case in empty:
            vals = {'needs_doctor_call': True}
            if not case.doctor_call_note:
                vals['doctor_call_note'] = _(
                    "Nothing is listed on the case: ask the doctor what is to be made.")
            case.with_context(fw_empty_checked=True).write(vals)

    # Deliberately no onchange doing the same in the form: Odoo runs every onchange
    # when a NEW form opens, with no lines yet, so every case opened with the call
    # switched on. The save does it; the empty items box says so beforehand.

    @api.model_create_multi
    def create(self, vals_list):
        cases = super().create(vals_list)
        cases._ask_doctor_when_empty()
        return cases

    def write(self, vals):
        # The shade of the case, before it changes: the items that carry it follow
        # the new one; an item given a shade of its own keeps it.
        shade_before = {case.id: case.colour_id for case in self} \
            if 'colour_id' in vals else {}
        result = super().write(vals)
        for case in self if shade_before else ():
            was = shade_before[case.id]
            if was and was != case.colour_id:
                case.line_ids.filtered(lambda l: l.colour_id == was).write(
                    {'colour_id': case.colour_id.id or False})
        if ('line_ids' in vals or 'needs_doctor_call' in vals) \
                and not self.env.context.get('fw_empty_checked'):
            self._ask_doctor_when_empty()
        return result

    def unlink(self):
        """A slip that produced an order is not deletable, by anyone.

        The lock mixin guards editing, not deletion, so without this a registered case
        could simply be removed — leaving a live sale order in verification with nothing
        to say who registered it, at which visit, from which doctor. The order would
        still be there; only the explanation for it would be gone.

        Deliberately NOT a bypass for managers either. This is an integrity rule rather
        than a permission level: seniority does not make an orphaned order acceptable.
        The way to undo a registered case is to cancel its order, which leaves a record
        of the decision.
        """
        registered = self.filtered(lambda c: c.sale_order_id or c.state == 'registered')
        if registered:
            raise UserError(_(
                "%(cases)s already produced an order (%(orders)s), so the slip cannot be "
                "deleted — the order would be left with nothing to say where it came "
                "from.\n\nCancel the order instead. A slip that has not been ordered "
                "yet can still be deleted, which is how a duplicate is resolved.",
                cases=', '.join(registered.mapped('patient')),
                orders=', '.join(registered.mapped('sale_order_id.name')) or _('pending')))
        return super().unlink()

    # ------------------------------------------------------------------ actions
    def action_submit(self):
        """The executive says this slip is complete.

        Submitting is where the checks happen, because it is the last moment the person
        who took the case is still standing in front of the doctor who gave it to them.
        Everything caught here can be asked about; the same problem caught at check-out,
        in the car, cannot.

        SUBMITTING MAKES THE ORDER. It used to wait for the visit to be finished, on
        the reasoning that a slip is edited twice before the executive leaves. But a
        visit that was never finished - one slip left half-entered on it is enough to
        refuse the check-out - kept every submitted case of that clinic out of the
        lab's queue, with nothing on the screen to say so: the executive had pressed
        Submit and saw "Submitted". A case can be corrected after it is an order now
        (Edit Case puts the changes on the same order until the office verifies it),
        so there is nothing left to wait for. (client, 2026-11-25: "when submitting it
        will create order; when editing, the modification comes in the linked order")
        """
        fresh = self.browse()
        corrected = self.browse()
        for case in self:
            if case.state != 'draft':
                raise UserError(_("%s has already been submitted.", case.patient))
            case._ask_doctor_when_empty()
            case._check_ready()
            case.state = 'submitted'
            # Corrected after it became an order: that order is updated.
            if case.sale_order_id:
                corrected |= case
            else:
                fresh |= case
        # Registered through the one path that makes an order from a slip, with its
        # readiness and duplicate checks - whether the visit is still open, already
        # finished, or there never was one (courier, transport).
        for visit in fresh.visit_id:
            orders = fresh.filtered(lambda c: c.visit_id == visit).action_create_order()
            if not orders:
                continue
            if visit.state == 'done':
                visit.message_post(body=_(
                    "%(n)s case(s) handed over after the visit was finished, sent "
                    "for verification: %(refs)s",
                    n=len(orders), refs=', '.join(orders.mapped('name'))))
            else:
                visit.message_post(body=_(
                    "%(n)s case(s) sent for verification: %(refs)s",
                    n=len(orders), refs=', '.join(orders.mapped('name'))))
        fresh.filtered(lambda c: not c.visit_id).action_create_order()
        corrected.action_create_order()
        return True

    @api.depends('state', 'sale_order_id')
    def _compute_order_editable(self):
        # sudo: an executive has no right to read sale orders, only to know whether
        # theirs is still open to correction.
        for case in self:
            case.order_editable = case.state == 'registered' and \
                case.sudo()._order_open_to_correction()

    def _order_open_to_correction(self):
        """The order is still a quotation the office has not verified."""
        self.ensure_one()
        order = self.sudo().sale_order_id
        # An order the office took back to AMEND is a draft too, but it is not a
        # quotation: the lab is already making it, and its jobs hang on its lines.
        # Corrections from the field would replace those lines. (lab_order_control's
        # flag, which this module does not depend on; client, 2026-11-26)
        if order and 'lab_amending' in order._fields and order.lab_amending:
            return False
        return bool(order) and order.state in ('draft', 'sent') \
            and order.verification_state != 'verified'

    def action_reset_to_draft(self):
        """Open a submitted case again to correct it.

        Before its order exists that is all it takes. After, while the office has
        not verified the order, the order is taken out of the verification queue
        - so nobody verifies what is being changed - and Submit puts the
        corrections on that same order and sends it back. Once verified, the order
        is the office's. (client, 2026-11-24)
        """
        for case in self:
            if case.state == 'submitted':
                continue
            if case.state != 'registered' or not case._order_open_to_correction():
                raise UserError(_(
                    "%(patient)s cannot be edited any more: the office has already "
                    "verified its order %(order)s. Tell them what to change.",
                    patient=case.patient, order=case.sudo().sale_order_id.name or ''))
        for case in self.filtered('sale_order_id'):
            order = case.sudo().sale_order_id
            case._link_order_lines(order)
            order.write({'verification_state': 'draft'})
            order.message_post(body=_(
                "%s is correcting this case from the field. It comes back for "
                "verification when they submit it again.", self.env.user.name))
            case.message_post(body=_("Opened again to correct it. Submit it again to "
                                     "update order %s.", order.name))
        self.write({'state': 'draft'})
        return True

    def action_cancel(self):
        ordered = self.filtered('sale_order_id')
        if ordered:
            raise UserError(_(
                "%s already became an order, so cancelling the slip would leave the "
                "order behind. Ask the office to cancel the order.",
                ', '.join(ordered.mapped('patient'))))
        self.write({'state': 'cancel'})

    # ------------------------------------------------------------------ the clinic picker
    @api.model
    def clinic_picker(self, query='', limit=30):
        """What the Clinic / Doctor picker shows: the executive's starred clinics,
        the ones they took cases or made visits at lately, and - once something is
        typed - every clinic of theirs that matches by name, place or phone. Only
        the clinics the field offers anyway (`lab_on_my_route`).
        (client, 2026-11-27: "an attractive easy widget for adding Clinic/Doctor")
        """
        Partner = self.env['res.partner']
        scope = [('lab_on_my_route', '=', True)]
        starred_ids = self.env['lab.clinic.favourite'].my_partner_ids()

        def rows(partners):
            stars = set(starred_ids)
            return [{
                'id': p.id,
                'name': p.display_name,
                'place': ', '.join(filter(None, [(p.city or '').strip(),
                                                 (p.street or '').strip()]))[:60],
                'phone': p.phone or '',
                'route': p.team_id.name or '',
                'starred': p.id in stars,
            } for p in partners.sudo()]

        query = (query or '').strip()
        if query:
            found = Partner.search(scope + [
                '|', '|', '|', ('name', 'ilike', query), ('city', 'ilike', query),
                ('street', 'ilike', query), ('phone', 'ilike', query)],
                order='name', limit=limit)
            return {'query': query, 'results': rows(found), 'starred': [], 'recent': []}
        starred = Partner.search(scope + [('id', 'in', starred_ids or [0])], order='name')
        since = fields.Date.context_today(self) - timedelta(days=45)
        recent_ids = []
        for model, user_field in (('lab.case', 'user_id'), ('lab.visit', 'user_id')):
            for rec in self.env[model].sudo().search_read(
                    [(user_field, '=', self.env.uid), ('date', '>=', since),
                     ('partner_id', '!=', False)],
                    ['partner_id'], order='date desc, id desc', limit=60):
                pid = rec['partner_id'][0]
                if pid not in recent_ids and pid not in starred.ids:
                    recent_ids.append(pid)
        recent = Partner.search(scope + [('id', 'in', recent_ids or [0])])
        order = {pid: i for i, pid in enumerate(recent_ids)}
        recent = recent.sorted(lambda p: order.get(p.id, 0))[:5]
        return {'query': '', 'results': [], 'starred': rows(starred), 'recent': rows(recent)}

    def action_another_case(self):
        """Another case from the same clinic, without going back to find it.

        A doctor hands over several cases at once, and each is a slip of its own.
        After the first, the executive had to leave the case, find the visit (or
        the clinic, for a parcel) and start again - and the second slip was the one
        that got left for later. One tap opens a blank slip for the same clinic: on
        the same visit while that visit is the executive's to add to (under way, or
        finished - a slip handed over after check-out still belongs to it), and
        otherwise as a case sent to the lab the same way, under the same docket.
        (client, 2026-11-25: "a button in the case register to create another case
        of the same clinic")
        """
        self.ensure_one()
        visit = self.visit_id
        if visit and visit.state in ('open', 'done'):
            context = {'default_visit_id': visit.id}
        else:
            via = self.received_via if self.received_via in ('clinic', 'courier', 'transport') \
                else 'clinic'
            context = {
                'default_partner_id': self.partner_id.id,
                'default_received_via': via,
                'default_received_ref': self.received_ref or False,
            }
        return {
            'type': 'ir.actions.act_window', 'name': _('Register a Case'),
            'res_model': 'lab.case', 'view_mode': 'form', 'target': 'current',
            'context': context,
        }

    def action_view_order(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'res_model': 'sale.order',
            'res_id': self.sale_order_id.id, 'view_mode': 'form',
        }

    def action_view_duplicates(self):
        self.ensure_one()
        orders, _cases = self._find_duplicates()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Earlier cases for %s', self.patient),
            'res_model': 'sale.order', 'view_mode': 'list,form',
            'domain': [('id', 'in', orders.ids)],
        }

    # ------------------------------------------------------------------ to order
    def _check_ready(self):
        """Everything that would make an unusable order, said before it is created."""
        for case in self:
            # An empty case is allowed (see _ask_doctor_when_empty); a line that
            # names nothing is not.
            missing = case.line_ids.filtered(lambda l: not l.product_id)
            if missing:
                raise UserError(_("A line on %s has no product.", case.patient))
            if case.deliver_to != 'clinic' and not (case.delivery_address or '').strip():
                raise UserError(_(
                    "%(patient)s is to be delivered to %(where)s: write the address, or the "
                    "sticker has nowhere to go.", patient=case.patient,
                    where=dict(case._fields['deliver_to'].selection)[case.deliver_to].lower()))
            # The same patient at the same clinic recently is NOT a reason to stop
            # here any more: it is said on the order instead (`_repeat_note`), where
            # the office verifies it. (client, 2026-11-26: "do not block")

    def _repeat_note(self, order):
        """What the office should know when the same patient had a case at this
        clinic recently: which, and whether the executive said it is separate work.
        False when there is nothing to say."""
        self.ensure_one()
        orders, cases = self._find_duplicates()
        orders -= order
        if not orders and not cases:
            return False
        refs = ['%s (%s)' % (o.name, fields.Date.to_string(o.date_order.date()))
                for o in orders.sorted('date_order', reverse=True)[:3]]
        if cases:
            refs.append(_('%s slip(s) still being entered', len(cases)))
        said = _("the executive marked it as a different case") if self.duplicate_ack \
            else _("check it is not the same case entered twice")
        return _("Same patient at this clinic in the last %(days)s days: %(refs)s - %(said)s.",
                 days=self._duplicate_window(), refs=', '.join(refs), said=said)

    def _order_values(self):
        self.ensure_one()
        vals = {
            'partner_id': self.partner_id.id,
            'visit_id': self.visit_id.id,
            'case_received_via': self.received_via,
            'case_received_ref': self.received_ref or False,
            'case_contact_user_id': self.contact_user_id.id or False,
            'case_contact_note': self.contact_note or False,
            'company_id': self.company_id.id,
            'patient': self.patient,
            # The case's shade is the order's: every order line takes it unless the
            # line carries its own (sale_custom). (client, 2026-11-25)
            'color_scheme': self.colour_id.id or False,
            'age': self.age,
            'gender': self.gender,
            'priority': self.priority,
            # Carry the QUESTION as a flag, not only as prose in `instruction`:
            # lab_order_control's banner and verification gate read the flag, so a
            # query raised in the field must set it or it stops at the counter.
            'call_doctor_required': self.needs_doctor_call,
            'call_doctor_reason': 'unclear_prescription' if self.needs_doctor_call else False,
            'call_doctor_note': self.doctor_call_note or False,
            'impression_type': self._order_choice('impression_type', self.impression_type),
            'impression_tray': self.impression_tray,
            'scanned_impression': self.scanned_impression,
            'wax_bite': self.wax_bite,
            'send_through': self.send_through_id.id or False,
            'modification': self.modification,
            # The question goes with the flag. A flag with no question is an instruction
            # to ring somebody about something.
            'instruction': ', '.join(filter(None, [
                self.instruction,
                _('Call the doctor: %s', self.doctor_call_note)
                if self.needs_doctor_call and self.doctor_call_note else
                (_('Call the doctor before starting.') if self.needs_doctor_call else '')
            ])) or False,
            # `is_pending_work` is the lab's existing "Call Doctor" flag, carried over
            # from v10. Reused rather than adding a second boolean meaning the same
            # thing: two flags for one fact drift apart the first time somebody ticks
            # one of them.
            'is_pending_work': self.needs_doctor_call,
            'is_3d_model_print': self.is_3d_model_print,
            'is_dd_cheque': self.is_dd_cheque, 'is_others': self.is_others,
            'deliver_to_patient': self.deliver_to == 'patient',
            'patient_address': self.delivery_address if self.deliver_to == 'patient' else False,
            'deliver_to_custom': self.deliver_to == 'custom',
            'custom_address': self.delivery_address if self.deliver_to == 'custom' else False,
            'is_screw': self.is_screw, 'is_bite': self.is_bite,
            'is_bands': self.is_bands, 'is_wires': self.is_wires,
            'is_teeth': self.is_teeth, 'is_facebow': self.is_facebow,
            'order_line': [(0, 0, line._line_values()) for line in self.line_ids],
        }
        # the two models, where the order has them (the production sheet's fields)
        for name in ('opposing_model', 'pre_cutting_model'):
            if name in self.env['sale.order']._fields:
                vals[name] = self._order_choice(name, self[name])
        return vals

    def _order_choice(self, name, value):
        """`value` for the order's field `name`, where the order knows that value.

        The order learns "Cast" and the two models from the production sheet module,
        which this one does not depend on: without it, a case still makes its order
        and the answer the order cannot hold is left off."""
        field = self.env['sale.order']._fields.get(name)
        if not field or not value:
            return False
        keys = [key for key, _label in field._description_selection(self.env)]
        return value if value in keys else False

    def action_create_order(self):
        """Turn the slip into an order awaiting verification.

        Idempotent: a case that already made an order returns it rather than making a
        second one. Finishing a visit is a button an executive will press twice on a bad
        connection, and the whole point of this model is to stop duplicate orders.
        """
        created = self.env['sale.order']
        for case in self:
            if case.sale_order_id:
                if case.state == 'submitted':
                    case._update_order()
                created |= case.sale_order_id
                continue
            if case.state == 'cancel':
                continue
            if case.state == 'draft':
                raise UserError(_(
                    "%s has not been submitted yet. Submit it so its details are "
                    "confirmed, or delete the slip.", case.patient))
            case._check_ready()
            # sudo on the CREATE, deliberately. This is the whole point of the case
            # slip: an executive never gets a sale order form - they cannot read or
            # write one - and the order is raised for them from values this module
            # builds. Without it, closing a visit died with "You are not allowed to
            # create Sales Order" and an executive could not finish their own round.
            # Nothing user-supplied reaches these values except the slip's own fields,
            # which the executive is entitled to set. (client, 2026-08-24)
            order = self.env['sale.order'].sudo().create(case._order_values())
            case._link_order_lines(order)
            note = case._repeat_note(order)
            if note:
                order.write({'repeat_patient_note': note})
                order.message_post(body=note, subtype_xmlid='mail.mt_note')
            if order.order_line:
                # Reuse the existing maker-checker entry point rather than writing
                # the field: that method owns what "submitted" means, including its
                # own checks.
                order.action_submit_for_verification()
            else:
                # Nothing listed: that entry point refuses an order without lines,
                # and rightly for one keyed at the counter. This one is in the queue
                # FOR the doctor call - the call gate stops it being verified, and
                # sale.order.action_verify (below) refuses it while it is still empty.
                order.write({'verification_state': 'to_verify', 'verification_note': False})
            case._tell_contact_person(order)
            # sudo for the same reason as the create above, and because the lock
            # mixin treats a submitted slip as read-only: this is the system
            # stamping the outcome of the executive's own action onto the slip, not
            # someone editing a closed record. `env.su` is the mixin's own bypass.
            case.sudo().write({'sale_order_id': order.id, 'state': 'registered'})
            case.message_post(body=_("Order %s created and sent for verification.",
                                     order.name))
            created |= order
        return created


    def _link_order_lines(self, order):
        """Each line of the case, tied to the order line it became.

        The order is made from the lines in their order, so the order's own lines
        (not the urgent-service charge sale_custom adds, not a section) pair with
        them one for one. Cases ordered before the link existed are paired the same
        way when they are opened again; a pair whose product differs is not tied.
        """
        self.ensure_one()
        order = order.sudo()
        made = order.order_line.filtered(
            lambda l: not l.display_type and not l.is_urgent_service).sorted('id')
        taken = self.line_ids.sale_line_id
        free = made - taken
        for line in self.line_ids.filtered(lambda l: not l.sale_line_id):
            match = free.filtered(lambda l: l.product_id == line.product_id)[:1]
            if match:
                line.sale_line_id = match
                free -= match

    def _update_order(self):
        """Put a corrected case on the order it already became, and send it back.

        Only the case's own values are written: a line is changed only where the
        executive changed it, so a price the office set on an unchanged line stays;
        lines the office added themselves are left alone. What changed is written on
        the order for whoever verifies it.
        """
        self.ensure_one()
        if not self._order_open_to_correction():
            raise UserError(_(
                "The office has already verified %(order)s, so %(patient)s can no "
                "longer change it. Tell them what to change.",
                order=self.sudo().sale_order_id.name, patient=self.patient))
        order = self.sudo().sale_order_id
        Line = self.env['sale.order.line'].sudo()
        values = self._order_values()
        values.pop('order_line')
        values.pop('company_id', None)
        changes = []

        def differs(record, vals):
            out = {}
            for name, value in vals.items():
                old = record[name]
                old = old.id if hasattr(old, '_name') else old
                if (old or False) != (value or False):
                    out[name] = value
            return out

        # Only what changed: rewriting the clinic, even with the same one, would
        # recompute the order's pricelist and prices under the office's feet.
        header = differs(order, values)
        if header:
            order.write(header)
            changes.append(_("changed %s", ', '.join(
                order._fields[n].string for n in header)))
        for line in self.line_ids:
            vals = line._line_values()
            current = line.sale_line_id.sudo().exists()
            if current and current.order_id == order:
                diff = differs(current, vals)
                if diff:
                    current.write(diff)
                    changes.append(_("changed %(work)s (%(what)s)",
                                     work=current.product_id.display_name,
                                     what=', '.join(current._fields[n].string for n in diff)))
            else:
                line.sale_line_id = Line.create(dict(vals, order_id=order.id))
                changes.append(_("added %s", line.product_id.display_name))
        if order.order_line:
            order.action_submit_for_verification()
        else:
            order.write({'verification_state': 'to_verify', 'verification_note': False})
        order.message_post(body=_(
            "Corrected from the field by %(user)s and sent for verification again%(what)s.",
            user=self.env.user.name,
            what=(': ' + '; '.join(changes)) if changes else ''))
        self.sudo().write({'state': 'registered'})
        self.message_post(body=_("Corrections sent to order %s.", order.name))

    @api.model
    def _without_visit(self, user_ids, day):
        """Cases with no visit behind them, per executive, for one day.

        Everything that counts an executive's day counts visits, so a case that came
        by courier would otherwise be done and never seen. Each counter adds this to
        its visit figures: {user_id: {'cases', 'orders', 'value'}}.
        """
        out = {}
        cases = self.sudo().search([
            ('visit_id', '=', False), ('user_id', 'in', list(user_ids)),
            ('date', '=', day), ('state', '!=', 'cancel')])
        for case in cases:
            row = out.setdefault(case.user_id.id, {'cases': 0, 'orders': 0, 'value': 0.0})
            row['cases'] += 1
            order = case.sale_order_id
            if order and order.state != 'cancel':
                row['orders'] += 1
                row['value'] += order.amount_untaxed
        return out

    def _tell_contact_person(self, order):
        """A to-do for the person named to contact the doctor, on the order, so it is
        in their own activities and not only written on a slip they never open."""
        self.ensure_one()
        user = self.contact_user_id
        if not user:
            return
        order.sudo().activity_schedule(
            'mail.mail_activity_data_todo', user_id=user.id,
            summary=_("Contact %s about this case", self.partner_id.display_name),
            note=self.contact_note or _(
                "%(who)s named you as the person to contact %(clinic)s about %(patient)s.",
                who=self.env.user.name, clinic=self.partner_id.display_name,
                patient=self.patient))

    # ------------------------------------------------------------------ the phone's item picker
    @api.model
    def _usage(self, column, since_days, partner_id=None, limit=400):
        """How often each product (or shade) went on an order lately: {id: count}."""
        self.env['sale.order.line'].flush_model()
        self.env['sale.order'].flush_model()
        where = SQL("AND o.partner_id = %s", int(partner_id)) if partner_id else SQL("")
        rows = self.env.execute_query(SQL("""
            SELECT l.%s, COUNT(*) FROM sale_order_line l JOIN sale_order o ON o.id = l.order_id
             WHERE o.state IN ('sale', 'done') AND o.company_id = %s
               AND o.date_order >= (CURRENT_DATE - %s * INTERVAL '1 day') AND l.%s IS NOT NULL %s
             GROUP BY 1 ORDER BY 2 DESC LIMIT %s
        """, SQL.identifier(column), self.env.company.id, since_days, SQL.identifier(column), where, limit))
        return {r[0]: r[1] for r in rows}

    @api.model
    def picker_products(self, query='', categ_id=None, partner_id=None, limit=40):
        """What an executive can add to a case, the likely ones first.

        Three shelves: what THIS clinic usually orders, what the whole lab makes most,
        and the search or category results - each sorted by how often the product went
        on an order in the last half year, so the common appliance is one tap and the
        rare one is one search.
        """
        Product = self.env['product.product']
        base = [('sale_ok', '=', True)]
        usage = self._usage('product_id', 180)

        def rows(products):
            return [{'id': p.id, 'name': p.display_name, 'categ_id': p.categ_id.id, 'categ': p.categ_id.name,
                     'uses': usage.get(p.id, 0)} for p in products]

        def ranked(products, n):
            return rows(sorted(products, key=lambda p: (-usage.get(p.id, 0), p.display_name or ''))[:n])
        out = {'frequent': [], 'clinic': [], 'results': [], 'categories': []}
        query = (query or '').strip()
        if query or categ_id:
            domain = list(base)
            if categ_id:
                domain.append(('categ_id', '=', int(categ_id)))
            if query:
                domain += ['|', ('name', 'ilike', query), ('default_code', 'ilike', query)]
            out['results'] = ranked(Product.search(domain, limit=300), limit)
        else:
            top = [pid for pid in usage if pid][:60]
            out['frequent'] = ranked(Product.browse(top).exists().filtered(lambda p: p.sale_ok), 12)
            if partner_id:
                mine = self._usage('product_id', 365, partner_id=partner_id, limit=30)
                clinic = Product.browse(list(mine)).exists().filtered(lambda p: p.sale_ok)
                out['clinic'] = [dict(r, uses=mine.get(r['id'], 0)) for r in rows(
                    sorted(clinic, key=lambda p: (-mine.get(p.id, 0), p.display_name or ''))[:8])]
            groups = Product._read_group(base, ['categ_id'], ['__count'])
            cats = sorted(((c, n) for c, n in groups if c), key=lambda g: -g[1])
            out['categories'] = [{'id': c.id, 'name': c.name, 'count': n} for c, n in cats[:14]]
        return out

    @api.model
    def picker_colours(self, query='', limit=18):
        """Shades, the ones actually ordered first: 737 exist and a dozen do the work."""
        Colour = self.env['product.colour']
        usage = self._usage('color_scheme', 365)
        query = (query or '').strip()
        if query:
            colours = Colour.search([('name', 'ilike', query)], limit=200)
        else:
            colours = Colour.browse([cid for cid in usage if cid][:limit * 2]).exists()
            if len(colours) < limit:
                colours |= Colour.search([('id', 'not in', colours.ids)], limit=limit - len(colours))
        ranked = sorted(colours, key=lambda c: (-usage.get(c.id, 0), c.name or ''))[:limit]
        return [{'id': c.id, 'name': c.name, 'uses': usage.get(c.id, 0)} for c in ranked]


    @api.model
    def picker_add_colour(self, name):
        """Put a shade the doctor asked for on the list, from the case.

        The shade list is the office's (Sales managers create shades), so an
        executive at a counter met a doctor's shade that was not on it and had to
        leave the shade off. Created in sudo, for the field roles only, and through
        the list's own rule: another spelling of a shade that exists ("A2 ,A3" for
        "A2, A3") hands back that shade instead of adding a twin. (client, 2026-11-24)
        """
        if not (self.env.user.has_group('lab_fieldwork.group_fieldwork_executive')
                or self.env.user.has_group('lab_fieldwork.group_fieldwork_manager')):
            raise UserError(_("Only field work users can add a shade from a case."))
        name = ' '.join((name or '').split())
        if not name:
            raise UserError(_("Type the shade first."))
        if len(name) > 40:
            raise UserError(_("A shade name is a few letters, e.g. A2 or 2M2."))
        Colour = self.env['product.colour'].sudo().with_context(active_test=False)
        key = Colour._shade_key(name)
        twin = Colour.search([('name', '=ilike', name[:1] + '%')]).filtered(
            lambda c: Colour._shade_key(c.name) == key)[:1]
        if twin:
            if not twin.active:
                twin.active = True
            shade = twin
        else:
            shade = Colour.create({
                'name': name,
                'description': _("Added from a case by %s", self.env.user.name),
            })
        return {'id': shade.id, 'name': shade.name, 'uses': 0, 'created': not twin}


class LabCaseLine(models.Model):
    """One appliance, and which arch it is for."""
    _name = 'lab.case.line'
    _description = 'Case Work Line'
    _order = 'case_id, sequence, id'

    case_id = fields.Many2one('lab.case', required=True, ondelete='cascade',
                              index=True)
    sequence = fields.Integer(default=10)
    product_id = fields.Many2one(
        'product.product', string='Work', required=True,
        domain="[('sale_ok', '=', True)]")
    ul = fields.Selection(UL, string='Jaw', required=True, default='upper')
    quantity = fields.Float('Qty', default=1.0, required=True,
                            digits='Product Unit of Measure')
    # The item's shade: the case's own unless this item says otherwise, so a bridge
    # of four units is one choice and not four - and the one unit that differs can
    # still say so. (client, 2026-11-25: "the shades in the case come in all items;
    # if a shade change is needed for a specific item he can change it in the item")
    colour_id = fields.Many2one(
        'product.colour', string='Shade', compute='_compute_colour_id',
        store=True, readonly=False, precompute=True)

    @api.depends('case_id.colour_id')
    def _compute_colour_id(self):
        for line in self:
            if not line.colour_id and line.case_id.colour_id:
                line.colour_id = line.case_id.colour_id

    # Which teeth, the way the lab writes them (FDI: "14, 13, 46"), and which cast came
    # with the case. The order line has both; the slip picked them off nothing.
    teeth = fields.Char('Teeth (FDI)',  # no size: 19 silently cut it at 64 characters
                        help="Tooth numbers in FDI notation, e.g. 14, 13, 46, 47.")
    cast = fields.Selection(UL, string='Cast',
                            help="The cast received for this work: upper, lower, or both.")
    is_urgent = fields.Boolean('Urgent')
    note = fields.Char('Remark')
    # The order line this line became, so correcting the case changes that line
    # and no other. (client, 2026-11-24)
    sale_line_id = fields.Many2one('sale.order.line', string='Order line', copy=False,
                                   readonly=True, index='btree_not_null', ondelete='set null')

    company_id = fields.Many2one(related='case_id.company_id', store=True)

    def _check_parent_open(self):
        """A line may only change while its slip is still being entered.

        Without this the slip is protected and its lines are not: the case cannot be
        edited, but a line can be deleted straight out of it, and the slip then no longer
        describes the order it produced.
        """
        closed = self.filtered(lambda l: l.case_id.state != 'draft')
        if closed:
            raise UserError(_(
                "%s has already been sent for verification. Its work cannot be changed "
                "here — change the order instead.",
                ', '.join(closed.mapped('case_id.patient'))))

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        lines._check_parent_open()
        return lines

    def write(self, vals):
        # Tying a line to its order line is the system's bookkeeping, done as the
        # order is made or updated; anything else waits for the case to be open.
        if set(vals) != {'sale_line_id'}:
            self._check_parent_open()
        return super().write(vals)

    def unlink(self):
        self._check_parent_open()
        cases = self.case_id
        # Taken off a case being corrected: off its order too, while the order is
        # still the case's to change.
        dropped = self.env['sale.order.line'].sudo()
        for line in self.filtered('sale_line_id'):
            case = line.case_id
            if case._order_open_to_correction() \
                    and line.sudo().sale_line_id.order_id == case.sudo().sale_order_id:
                dropped |= line.sudo().sale_line_id
        removed = [(sol.order_id, sol.product_id.display_name) for sol in dropped]
        result = super().unlink()
        if dropped:
            dropped.unlink()
            for order, work in removed:
                order.message_post(body=_("%(user)s took %(work)s off this case.",
                                          user=self.env.user.name, work=work))
        # The last line taken off: the slip is empty now, and an empty slip goes to
        # the office with the doctor call on (LabCase._ask_doctor_when_empty).
        cases.exists()._ask_doctor_when_empty()
        return result

    def _line_values(self):
        self.ensure_one()
        vals = {
            'product_id': self.product_id.id,
            'product_uom_qty': self.quantity,
            'ul': self.ul,
            'color_scheme': self.colour_id.id or False,
            'teeth': self.teeth or False,
            'cast': self.cast or False,
            'is_urgent': self.is_urgent or self.case_id.priority == 'urgent',
        }
        # `name` is left out unless the executive wrote a remark, so Odoo computes the
        # product's own sale description — including variant values and translations,
        # which a hand-set name would silently discard.
        if self.note:
            vals['name'] = self.note
        return vals
