# -*- coding: utf-8 -*-
import re
from datetime import timedelta

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
    _inherit = ['lab.lock.mixin', 'mail.thread']

    # Submitted is locked as well as registered. The executive has said the slip is
    # complete, so changing it silently afterwards would make "submitted" mean nothing —
    # and the mixin allows a write that LEAVES a locked state, which is exactly what
    # Reopen does. Correcting a slip is therefore explicit rather than accidental.
    _lock_states = ('submitted', 'registered', 'cancel')
    _lock_exempt_fields = ('note',)
    _lock_bypass_groups = ('lab_fieldwork.group_fieldwork_manager',)

    name = fields.Char(compute='_compute_name', store=True)
    visit_id = fields.Many2one(
        'lab.visit', required=True, ondelete='cascade', index=True,
        string='Visit', copy=False)
    partner_id = fields.Many2one(
        related='visit_id.partner_id', store=True, index=True, string='Clinic')
    user_id = fields.Many2one(related='visit_id.user_id', store=True, string='Executive')
    date = fields.Date(related='visit_id.date', store=True, index=True)
    company_id = fields.Many2one(related='visit_id.company_id', store=True)
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
    impression_type = fields.Selection(
        [('Alginate', 'Alginate'), ('Rubber Base', 'Rubber Base')], string='Impression')
    impression_tray = fields.Selection(
        [('Upper', 'Upper'), ('Lower', 'Lower'), ('Both', 'Both')], string='Tray')
    scanned_impression = fields.Selection([('Yes', 'Yes'), ('No', 'No')], string='Scanned')
    wax_bite = fields.Selection([('Yes', 'Yes'), ('No', 'No')], string='Wax Bite')
    send_through_id = fields.Many2one('send.through', string='Send Through')

    # What came in the bag. Booleans rather than a many2many so they match the job card
    # and the existing sale order exactly.
    is_screw = fields.Boolean('Screw')
    is_bite = fields.Boolean('Bite')
    is_bands = fields.Boolean('Bands')
    is_wires = fields.Boolean('Wires')
    is_teeth = fields.Boolean('Teeth')
    is_facebow = fields.Boolean('Face Bow')

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

    modification = fields.Char('Modifications')
    instruction = fields.Char('Special Instructions')
    note = fields.Text('Notes')
    photo = fields.Image('Case Photo', max_width=1400, max_height=1400)

    # --- what became of it
    sale_order_id = fields.Many2one('sale.order', readonly=True, copy=False,
                                    string='Order')

    # --- duplicate control
    duplicate_ids = fields.Many2many(
        'sale.order', compute='_compute_duplicates', string='Possible Duplicates')
    duplicate_case_ids = fields.Many2many(
        'lab.case', 'lab_case_dup_rel', 'case_id', 'dup_id',
        compute='_compute_duplicates', string='Also Entered')
    duplicate_warning = fields.Char(compute='_compute_duplicates')
    duplicate_ack = fields.Boolean(
        'Different case', copy=False, tracking=True,
        help="Tick to confirm this is genuinely a separate case and not the same one "
             "entered twice.")

    # ------------------------------------------------------------------ computes
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
            case.duplicate_warning = _(
                "%(who)s already has %(n)s case(s) registered at %(clinic)s in the last "
                "%(days)s days — most recently %(ref)s. If this is the same case, delete "
                "this slip; if the doctor really has handed over a second one, tick "
                "“Different case”.",
                who=earlier.patient or case.patient, n=len(orders) + len(cases),
                clinic=case.partner_id.display_name, days=window, ref=where)

    # ------------------------------------------------------------------ duplicates
    @api.model
    def _duplicate_window(self):
        try:
            return max(0, int(self.env['ir.config_parameter'].sudo().get_param(
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
    @api.constrains('line_ids', 'state')
    def _check_has_work(self):
        """A slip records what the doctor handed over, so a slip with nothing on it is
        not an incomplete case — it is not a case at all.

        Checked on save rather than only at check-out: an empty slip that survives until
        the end of the visit has already cost the executive the trip back through the
        form, and by then the doctor has gone.
        """
        for case in self:
            if case.state != 'cancel' and not case.line_ids:
                raise ValidationError(_(
                    "Add what is being made for %s before saving — a case with no work "
                    "on it cannot become an order.", case.patient or _('this patient')))

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
        """
        late = self.browse()
        for case in self:
            if case.state != 'draft':
                raise UserError(_("%s has already been submitted.", case.patient))
            case._check_ready()
            case.state = 'submitted'
            if case.visit_id.state == 'done':
                late |= case
            else:
                case.message_post(body=_("Case submitted. It becomes an order when "
                                         "the visit is finished."))
        # A slip handed over after check-out still belongs to its visit, but
        # check-out is the only thing that turns submitted slips into orders and
        # it has already happened - so these sat 'Submitted' for ever behind a
        # Completed visit. Registered now, through the same path check-out uses,
        # with the same readiness and duplicate checks. (2026-09-15)
        for visit in late.visit_id:
            orders = late.filtered(lambda c: c.visit_id == visit).action_create_order()
            if orders:
                visit.message_post(body=_(
                    "%(n)s case(s) handed over after the visit was finished, sent "
                    "for verification: %(refs)s",
                    n=len(orders), refs=', '.join(orders.mapped('name'))))
        return True

    def action_reset_to_draft(self):
        """Reopen a submitted slip to correct it. Only before the order exists."""
        for case in self:
            if case.state != 'submitted':
                raise UserError(_(
                    "Only a submitted case can be reopened. %s has already produced an "
                    "order — change the order instead.", case.patient))
        self.write({'state': 'draft'})
        return True

    def action_cancel(self):
        self.write({'state': 'cancel'})

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
            if not case.line_ids:
                raise UserError(_(
                    "Add what is being made for %s before submitting it.", case.patient))
            missing = case.line_ids.filtered(lambda l: not l.product_id)
            if missing:
                raise UserError(_("A line on %s has no product.", case.patient))
            orders, cases = case._find_duplicates()
            if (orders or cases) and not case.duplicate_ack:
                # Same reasoning as the banner: name the EARLIER spelling, which is the
                # one the executive can recognise.
                earlier = orders[:1] or cases[:1]
                raise UserError(_(
                    "%(who)s already has a case registered at %(clinic)s within the "
                    "last %(days)s days (%(refs)s).\n\nIf it is the same case, delete "
                    "this slip. If the doctor has genuinely handed over another, open "
                    "the case and tick “Different case”.",
                    who=earlier.patient or case.patient,
                    clinic=case.partner_id.display_name,
                    days=case._duplicate_window(),
                    refs=', '.join(orders.mapped('name')[:3]) or _('an unsaved slip')))

    def _order_values(self):
        self.ensure_one()
        return {
            'partner_id': self.partner_id.id,
            'visit_id': self.visit_id.id,
            'company_id': self.company_id.id,
            'patient': self.patient,
            'age': self.age,
            'gender': self.gender,
            'priority': self.priority,
            # Carry the QUESTION as a flag, not only as prose in `instruction`:
            # lab_order_control's banner and verification gate read the flag, so a
            # query raised in the field must set it or it stops at the counter.
            'call_doctor_required': self.needs_doctor_call,
            'call_doctor_reason': 'unclear_prescription' if self.needs_doctor_call else False,
            'call_doctor_note': self.doctor_call_note or False,
            'impression_type': self.impression_type,
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
            'is_screw': self.is_screw, 'is_bite': self.is_bite,
            'is_bands': self.is_bands, 'is_wires': self.is_wires,
            'is_teeth': self.is_teeth, 'is_facebow': self.is_facebow,
            'order_line': [(0, 0, line._line_values()) for line in self.line_ids],
        }

    def action_create_order(self):
        """Turn the slip into an order awaiting verification.

        Idempotent: a case that already made an order returns it rather than making a
        second one. Finishing a visit is a button an executive will press twice on a bad
        connection, and the whole point of this model is to stop duplicate orders.
        """
        created = self.env['sale.order']
        for case in self:
            if case.sale_order_id:
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
            # Reuse the existing maker-checker entry point rather than writing the
            # field: that method owns what "submitted" means, including its own checks.
            order.action_submit_for_verification()
            # sudo for the same reason as the create above, and because the lock
            # mixin treats a submitted slip as read-only: this is the system
            # stamping the outcome of the executive's own action onto the slip, not
            # someone editing a closed record. `env.su` is the mixin's own bypass.
            case.sudo().write({'sale_order_id': order.id, 'state': 'registered'})
            case.message_post(body=_("Order %s created and sent for verification.",
                                     order.name))
            created |= order
        return created


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
    colour_id = fields.Many2one('product.colour', string='Colour')
    is_urgent = fields.Boolean('Urgent')
    note = fields.Char('Remark')

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
        self._check_parent_open()
        return super().write(vals)

    def unlink(self):
        self._check_parent_open()
        # The case's own constraint cannot see this: `@api.constrains('line_ids')` fires
        # on writes to the CASE, and deleting a line straight out of the o2m never
        # touches it. Without this the last line can be removed and the slip is left
        # describing no work at all.
        emptied = self.mapped('case_id').filtered(
            lambda c: c.state != 'cancel' and not (c.line_ids - self))
        if emptied:
            raise ValidationError(_(
                "%s would be left with no work on it. A case must say what is being "
                "made — delete the whole slip instead.",
                ', '.join(emptied.mapped('patient'))))
        return super().unlink()

    def _line_values(self):
        self.ensure_one()
        vals = {
            'product_id': self.product_id.id,
            'product_uom_qty': self.quantity,
            'ul': self.ul,
            'color_scheme': self.colour_id.id or False,
            'is_urgent': self.is_urgent or self.case_id.priority == 'urgent',
        }
        # `name` is left out unless the executive wrote a remark, so Odoo computes the
        # product's own sale description — including variant values and translations,
        # which a hand-set name would silently discard.
        if self.note:
            vals['name'] = self.note
        return vals
