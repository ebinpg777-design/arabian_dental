# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

VEHICLES = [('bike', 'Two Wheeler'), ('car', 'Four Wheeler'), ('public', 'Public Transport')]


class LabTrip(models.Model):
    """A day's travel: two odometer readings and what the lab owes for it.

    One record per person per day, enforced by a constraint. That single rule removes a
    whole class of confusion — there is never a question of which trip today's
    kilometres belong to, and the executive cannot accidentally open a second one.
    """
    _name = 'lab.trip'
    _description = 'Day Trip'
    _order = 'date desc, id desc'
    _inherit = ['lab.lock.mixin', 'lab.own.record.mixin', 'mail.thread']

    _lock_states = ('approved', 'cancel')
    _lock_exempt_fields = ('note',)
    _lock_bypass_groups = ('lab_fieldwork.group_fieldwork_manager',)

    name = fields.Char(default=lambda self: _('New'), copy=False, readonly=True)
    state = fields.Selection(
        [('draft', 'Not Started'), ('open', 'On the Road'), ('closed', 'Submitted'),
         ('approved', 'Approved'), ('cancel', 'Cancelled')],
        default='draft', required=True, tracking=True, copy=False)

    user_id = fields.Many2one(
        'res.users', string='Executive',
        domain="[('fw_is_field_person', '=', True)]", required=True, index=True,
        default=lambda self: self.env.user, tracking=True)
    date = fields.Date(required=True, index=True,
                       default=fields.Date.context_today, tracking=True)
    vehicle = fields.Selection(VEHICLES, default='bike', required=True)

    odo_start = fields.Float('Start Reading', tracking=True)
    odo_end = fields.Float('End Reading', tracking=True)
    photo_start = fields.Image('Start Photo', max_width=1024, max_height=1024)
    photo_end = fields.Image('End Photo', max_width=1024, max_height=1024)

    distance = fields.Float('Distance (km)', compute='_compute_amount', store=True)
    rate = fields.Float('Rate / km', compute='_compute_rate', store=True, readonly=False)
    amount = fields.Monetary('Claim', compute='_compute_amount', store=True,
                             currency_field='currency_id')
    note = fields.Text('Remarks')
    auto_closed = fields.Boolean(readonly=True, copy=False,
                                 help="Submitted by the end-of-day job, not by the executive.")
    auto_close_failed = fields.Boolean(
        readonly=True, copy=False, string='Left Open',
        help="Still on the road at the end of the day with no closing reading.")

    visit_count = fields.Integer(compute='_compute_visits')
    # What the day's travel actually produced. A trip that costs the lab money and a
    # trip that brought back eleven cases should not look identical on the form the
    # manager approves.
    case_count = fields.Integer(compute='_compute_visits', string='Cases')
    case_value = fields.Monetary(compute='_compute_visits', string='Case Value',
                                 currency_field='currency_id')
    collected_total = fields.Monetary(compute='_compute_visits', string='Collected',
                                      currency_field='currency_id')
    can_edit_rate = fields.Boolean(compute='_compute_can_edit_rate')
    approved_by = fields.Many2one('res.users', readonly=True, copy=False)
    company_id = fields.Many2one(
        'res.company', default=lambda self: self.env.company, required=True)
    currency_id = fields.Many2one(related='company_id.currency_id', readonly=True)

    _one_per_day = models.Constraint(
        'unique(user_id, date, company_id)',
        'There is already a trip for this person on this date.')

    @api.depends('vehicle')
    def _compute_rate(self):
        params = self.env['ir.config_parameter'].sudo()
        rates = {
            'bike': float(params.get_param('lab_fieldwork.rate_bike', 4.0)),
            'car': float(params.get_param('lab_fieldwork.rate_car', 9.0)),
            'public': float(params.get_param('lab_fieldwork.rate_public', 0.0)),
        }
        for trip in self:
            trip.rate = rates.get(trip.vehicle, 0.0)

    @api.depends('odo_start', 'odo_end', 'rate')
    def _compute_amount(self):
        for trip in self:
            trip.distance = max(0.0, (trip.odo_end or 0.0) - (trip.odo_start or 0.0))
            trip.amount = trip.distance * (trip.rate or 0.0)

    def _day_visits(self):
        """The visits this day's travel paid for.

        A trip and its visits are joined by person-and-date rather than by a foreign
        key: there is exactly one trip per person per day by constraint, so the join is
        already unambiguous and a link field would be a second thing to keep in step.
        """
        self.ensure_one()
        return self.env['lab.visit'].search([
            ('user_id', '=', self.user_id.id), ('date', '=', self.date),
            ('state', '=', 'done')])

    def _compute_visits(self):
        """All the day's visits for every trip on the page, in one query.

        `_day_visits()` is still the single definition of the join; it is just not run
        once per row any more — on the travel-approval list that was a query per claim.
        """
        dated = self.filtered(lambda t: t.user_id and t.date)
        buckets = {}
        if dated:
            visits = self.env['lab.visit'].search([
                ('user_id', 'in', dated.user_id.ids),
                ('date', 'in', list(set(dated.mapped('date')))),
                ('state', '=', 'done')])
            for v in visits:
                buckets.setdefault((v.user_id.id, v.date), self.env['lab.visit'])
                buckets[(v.user_id.id, v.date)] |= v
        for trip in self:
            visits = buckets.get((trip.user_id.id, trip.date), self.env['lab.visit'])
            trip.visit_count = len(visits)
            trip.case_count = sum(visits.mapped('order_count'))
            trip.case_value = sum(visits.mapped('order_value'))
            trip.collected_total = sum(visits.mapped('collected'))

    @api.constrains('odo_start', 'odo_end')
    def _check_odometer(self):
        for trip in self:
            if trip.odo_end and trip.odo_end < trip.odo_start:
                raise ValidationError(_(
                    "The closing reading (%(end)s) is below the opening one (%(start)s).",
                    end=trip.odo_end, start=trip.odo_start))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'lab.trip') or _('New')
        return super().create(vals_list)

    # ------------------------------------------------------------------ workflow
    def action_start(self):
        self.ensure_one()
        if not self.odo_start:
            raise UserError(_("Enter the opening odometer reading."))
        if self._photo_required() and not self.photo_start:
            raise UserError(_("A photo of the opening reading is required."))
        self.state = 'open'

    def action_close(self):
        self.ensure_one()
        if not self.odo_end:
            raise UserError(_("Enter the closing odometer reading."))
        if self._photo_required() and not self.photo_end:
            raise UserError(_("A photo of the closing reading is required."))
        self.state = 'closed'

    MANAGER_GROUP = 'lab_fieldwork.group_fieldwork_manager'

    def write(self, vals):
        """The two fields that decide what a claim is worth, and whether it is signed.

        `can_edit_rate` and a hidden button are UI, and UI is not a control: both this
        model's fields are reachable over RPC by the executive who owns the record. The
        rate is policy and the approval is somebody else's signature, so both are
        checked here, where the write actually happens. Closing the day still writes
        `state` — that is the executive's own transition and stays theirs.
        (client, 2026-08-27)
        """
        if not (self.env.su
                or self.env.user.has_group(self.MANAGER_GROUP)):
            if 'rate' in vals and not self.env.user.has_group(
                    'lab_fieldwork.group_fieldwork_admin'):
                raise UserError(_(
                    "The mileage rate is set by the lab, not on the claim. Ask a field "
                    "work administrator to change it."))
            if 'approved_by' in vals or vals.get('state') == 'approved':
                raise UserError(_(
                    "A travel claim is approved by a field work manager, not by the "
                    "person who made the journey."))
        return super().write(vals)

    def _check_may_approve(self, what):
        """Approving a travel claim is a manager's act, checked in the METHOD.

        The button is hidden from an executive, and hiding a button is not a control:
        `action_approve` is reachable over RPC by anybody who can read the record, so
        without this an executive could approve their own mileage — and, with `rate`
        editable, set what it was worth first. (client, 2026-08-27)
        """
        if self.env.su or self.env.user.has_group(self.MANAGER_GROUP):
            return
        raise UserError(_(
            "Only a field work manager can %s a travel claim. Yours goes to them when "
            "you close the day.", what))

    def action_approve(self):
        self._check_may_approve(_('approve'))
        # Only a closed day can be approved: approving an open one signs off a reading
        # the executive has not finished taking.
        wrong = self.filtered(lambda t: t.state != 'closed')
        if wrong:
            raise UserError(_(
                "Only a closed trip can be approved. %s is still %s.",
                wrong[0].display_name, wrong[0].state))
        self.write({'state': 'approved', 'approved_by': self.env.user.id})

    def action_reject(self):
        self._check_may_approve(_('reject'))
        # Back to the executive rather than cancelled: a wrong reading is corrected,
        # not thrown away, and the day's travel still happened.
        self.write({'state': 'open'})

    def _check_may_undo(self, what):
        """Undoing a submitted or signed claim is the approver's call.

        Reset and Cancel had no check, so an executive could put their own
        approved claim back to draft, change the odometer and resubmit - with
        the old approver's name still on it. (2026-09-15)
        """
        if self.filtered(lambda t: t.state in ('approved', 'closed')):
            self._check_may_approve(what)

    def action_cancel(self):
        self._check_may_undo(_('cancel'))
        self.write({'state': 'cancel'})

    def action_reset(self):
        self._check_may_undo(_('reset'))
        self.write({'state': 'draft'})
        # A claim back in draft is unsigned: whoever approved it approved
        # different readings. sudo, because the write guard refuses the field
        # to an executive resetting a trip that was only ever rejected.
        signed = self.filtered('approved_by')
        if signed:
            signed.sudo().write({'approved_by': False})

    def action_view_visits(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': _('Visits that day'),
            'res_model': 'lab.visit', 'view_mode': 'list,form',
            'domain': [('user_id', '=', self.user_id.id), ('date', '=', self.date)],
        }

    def _photo_required(self):
        return self.env['ir.config_parameter'].sudo().get_param(
            'lab_fieldwork.require_odo_photo', 'True') in ('True', 'true', '1')

    def _compute_can_edit_rate(self):
        """The rate is policy, so only the configuration role may override it.

        A computed flag rather than two group-gated copies of the field: with the roles
        parallel an administrator holds Manager too, so two <field> elements would both
        render and the form would show the rate twice.
        """
        allowed = self.env.user.has_group('lab_fieldwork.group_fieldwork_admin')
        for trip in self:
            trip.can_edit_rate = allowed

    def action_view_cases(self):
        self.ensure_one()
        orders = self._day_visits().mapped('order_ids')
        return {
            'type': 'ir.actions.act_window', 'name': _('Cases from this Day'),
            'res_model': 'sale.order', 'view_mode': 'list,form',
            'domain': [('id', 'in', orders.ids)],
        }

    def action_view_collections(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': _('Cash Taken this Day'),
            'res_model': 'petty.cash.transaction', 'view_mode': 'list,form',
            'domain': [('visit_id', 'in', self._day_visits().ids)],
            'context': {'create': False},
        }
