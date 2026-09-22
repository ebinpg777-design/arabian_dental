# -*- coding: utf-8 -*-
"""Collected cash going back to the office.

The other half of the float. A visit's cash goes INTO the executive's petty
cash float (petty_cash.py); until now nothing took it out again, so the figure
on My Day could only ever grow, and the office had no record of the envelope
it was handed on Friday except the accountant's memory.

A handover is that envelope. The executive says "here is 13,250" - on the
phone, standing at the desk - and gets a six-letter code; the office counts the
money, confirms it against the code, and only THEN does it leave the
executive's float. Custody changes hands when the count does, not when the
phone says so: a one-tap "I returned it" is the executive's word, and the count
is the office's. A count that differs is recorded as a difference with a
reason, and a shortfall stays on the executive's float, where it is owed.

What it books, and does not. The float is a CUSTODY record: a field collection
enters it with no journal entry (accounts still books the receipt against the
doctor's account when the money lands), so a handover leaves it the same way.
Nothing here touches the ledger. (client, 2026-09-18)
"""
import base64
import secrets
from collections import defaultdict

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare, formatLang

# Read out across a desk and typed by somebody who is not looking at the
# phone: no 0/O, no 1/I/L, nothing lower-case.
CODE_ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'
# Who may count and confirm: the operational desk (cash rides with travel
# there) and whoever keeps the petty cash box.
RECEIVERS = ('lab_fieldwork.group_fieldwork_ops_manager',
             'petty_cash.group_petty_cash_officer')
# The notes a bundle is counted in, largest first. Any other value can still
# be typed on a line.
DENOMINATIONS = (500, 200, 100, 50, 20, 10)


class CashHandover(models.Model):
    _name = 'lab.cash.handover'
    _description = 'Cash Handed to the Office'
    _inherit = ['lab.lock.mixin', 'lab.own.record.mixin',
                'mail.thread', 'mail.activity.mixin']
    _order = 'date desc, id desc'
    # Counted money is evidence: once the office has confirmed it, or the
    # slip is cancelled, nobody edits the amounts from a list or an import.
    _lock_states = ('received', 'cancelled')
    _lock_exempt_fields = ('note',)
    _lock_bypass_groups = ('lab_fieldwork.group_fieldwork_admin',)

    name = fields.Char(readonly=True, copy=False, default=lambda self: _('New'))
    user_id = fields.Many2one(
        'res.users', string='Handed Over By', required=True, index=True,
        default=lambda self: self.env.user, tracking=True)
    allocation_id = fields.Many2one(
        'petty.cash.allocation', string='Float', readonly=True, copy=False,
        index='btree_not_null')
    date = fields.Date(
        string='Handed Over On', required=True, tracking=True,
        default=lambda self: fields.Date.context_today(self))
    amount = fields.Monetary(
        string='Amount Handed Over', required=True, currency_field='currency_id',
        tracking=True)
    line_ids = fields.One2many(
        'lab.cash.handover.line', 'handover_id', string='Notes Counted', copy=False)
    lines_total = fields.Monetary(
        compute='_compute_lines_total', currency_field='currency_id')
    code = fields.Char(
        string='Handover Code', readonly=True, copy=False, index=True,
        help="Six letters the executive reads out, or the office scans, so the "
             "count is confirmed against THIS envelope and no other.")
    qr_image = fields.Binary(compute='_compute_qr', string='QR', attachment=False)

    counted_amount = fields.Monetary(
        string='Amount Counted', currency_field='currency_id', copy=False,
        tracking=True, help="What the office counted. Defaults to the amount "
                             "declared; change it if the envelope held something else.")
    difference = fields.Monetary(
        compute='_compute_difference', store=True, currency_field='currency_id',
        help="Counted minus declared. Negative: short, and still owed by the executive.")
    difference_reason = fields.Char(copy=False, tracking=True)
    received_by_id = fields.Many2one(
        'res.users', string='Counted By', readonly=True, copy=False, tracking=True)
    received_at = fields.Datetime(string='Counted At', readonly=True, copy=False)
    txn_id = fields.Many2one(
        'petty.cash.transaction', string='Float Movement', readonly=True, copy=False)

    state = fields.Selection([
        ('draft', 'Draft'), ('declared', 'Handed Over'),
        ('received', 'Received'), ('cancelled', 'Cancelled')],
        default='draft', required=True, tracking=True, copy=False, index=True)
    note = fields.Char(string='Note')
    company_id = fields.Many2one(
        'res.company', required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id', readonly=True)

    # What the phone needs to say while the slip is being filled in.
    available = fields.Monetary(
        string='Cash With You', compute='_compute_available',
        currency_field='currency_id',
        help="Collected cash you are holding that has not yet been counted by "
             "the office. What can be handed over now.")
    can_receive = fields.Boolean(compute='_compute_can_receive')

    _amount_positive = models.Constraint(
        'CHECK(amount > 0)', "A handover has to be for some money.")
    _code_unique = models.Constraint(
        'UNIQUE(code)', "That handover code is already in use.")

    # ------------------------------------------------------------ computes
    @api.depends('line_ids.subtotal')
    def _compute_lines_total(self):
        for rec in self:
            rec.lines_total = sum(rec.line_ids.mapped('subtotal'))

    @api.depends('counted_amount', 'amount', 'state')
    def _compute_difference(self):
        for rec in self:
            rec.difference = ((rec.counted_amount - rec.amount)
                              if rec.state == 'received' else 0.0)

    @api.depends_context('uid')
    def _compute_can_receive(self):
        allowed = self._is_receiver()
        for rec in self:
            rec.can_receive = allowed

    @api.depends('user_id')
    @api.depends_context('uid')
    def _compute_available(self):
        positions = self.cash_position(self.mapped('user_id'))
        for rec in self:
            row = positions.get(rec.user_id.id)
            # A slip already declared is part of "pending", not of what is
            # still free to hand over; shown on its own record it should read
            # as what it was when it was raised.
            rec.available = (row['available'] + (rec.amount if rec.state == 'declared' else 0.0)
                             if row else 0.0)

    @api.depends('code')
    def _compute_qr(self):
        Report = self.env['ir.actions.report']
        for rec in self:
            if not rec.code or not rec.id:
                rec.qr_image = False
                continue
            try:
                png = Report.barcode('QR', rec._deep_link(), width=240, height=240,
                                     humanreadable=0)
                rec.qr_image = base64.b64encode(png)
            except Exception:  # noqa: BLE001 - a missing renderer must not break the form
                rec.qr_image = False

    @api.depends('name', 'amount', 'user_id')
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = '%s · %s' % (rec.name or _('Handover'), rec.amount)

    # ------------------------------------------------------------ defaults
    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        # Everything they hold, unless they typed otherwise: an executive at the
        # desk is emptying the envelope, not picking a figure.
        if 'amount' in fields_list and not res.get('amount'):
            user = self.env['res.users'].browse(res.get('user_id') or self.env.uid)
            res['amount'] = self.cash_position(user)[user.id]['available']
        return res

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('name') or vals['name'] == _('New'):
                vals['name'] = (self.env['ir.sequence'].next_by_code('lab.cash.handover')
                                or _('New'))
        return super().create(vals_list)

    @api.onchange('line_ids')
    def _onchange_lines(self):
        # Count the notes and the amount writes itself; nobody adds up a bundle
        # twice at a desk.
        for rec in self:
            total = sum(rec.line_ids.mapped('subtotal'))
            if total:
                rec.amount = total

    @api.constrains('line_ids', 'amount', 'state')
    def _check_lines_match(self):
        for rec in self:
            if rec.state != 'draft' or not rec.line_ids:
                continue
            total = sum(rec.line_ids.mapped('subtotal'))
            # A sheet of zeros is a sheet not yet counted into, not a wrong one.
            if not total:
                continue
            if float_compare(total, rec.amount,
                             precision_rounding=rec.currency_id.rounding or 0.01):
                raise ValidationError(_(
                    "The notes counted add up to %(lines)s but the amount says "
                    "%(amount)s. One of them is wrong.",
                    lines=rec._money(total), amount=rec._money(rec.amount)))

    # ------------------------------------------------------------ the flow
    def action_fill_denominations(self):
        """One line per note, ready to count into."""
        for rec in self:
            if rec.state != 'draft':
                continue
            have = set(rec.line_ids.mapped('denomination'))
            rec.line_ids = [(0, 0, {'denomination': d, 'count': 0})
                            for d in DENOMINATIONS if d not in have]
        return True

    def action_declare(self):
        """The executive is at the desk with the envelope. Say so, get a code."""
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_("%s has already been handed over.", rec.name))
            if rec.amount <= 0:
                raise UserError(_("Enter how much you are handing over."))
            # Everything they hold goes to the float first, so the float is the
            # one place the office reads when it counts.
            problem = self._sweep_unbanked(rec.user_id)
            allocation = rec.user_id.sudo().petty_cash_allocation_id
            if not allocation:
                raise UserError(problem or _(
                    "%s has no petty cash float, so there is no record of the cash "
                    "to hand over. Ask accounts to open one.", rec.user_id.name))
            if allocation.company_id != rec.company_id:
                raise UserError(_(
                    "%(who)s holds a float in %(co)s; this handover is in %(here)s. "
                    "Cash does not move between companies.",
                    who=rec.user_id.name, co=allocation.company_id.display_name,
                    here=rec.company_id.display_name))
            available = self.cash_position(rec.user_id)[rec.user_id.id]['available']
            if float_compare(rec.amount, available,
                             precision_rounding=rec.currency_id.rounding or 0.01) > 0:
                raise UserError(_(
                    "You are holding %(held)s of collected cash, so %(amount)s cannot "
                    "be handed over. Returning an advance the lab gave you is a "
                    "different thing - ask accounts.",
                    held=rec._money(available), amount=rec._money(rec.amount)))
            rec.write({
                'state': 'declared',
                'code': self._new_code(),
                'allocation_id': allocation.id,
                'counted_amount': rec.amount,
            })
            rec.message_post(body=_(
                "%(amount)s handed to the office by %(who)s. Code %(code)s - waiting "
                "for the count.", amount=rec._money(rec.amount),
                who=rec.user_id.name, code=rec.code))
        return self._open()

    def action_confirm_receipt(self):
        """The office has counted the envelope. The money leaves the float NOW."""
        self.ensure_one()
        if not self._is_receiver():
            raise UserError(_(
                "Only the operational desk or a petty cash officer can confirm a "
                "handover - counting the money is their word, not the executive's."))
        if self.state != 'declared':
            raise UserError(_("%s is not waiting to be counted.", self.name))
        counted = self.counted_amount
        if counted <= 0:
            raise UserError(_("Enter the amount counted."))
        rounding = self.currency_id.rounding or 0.01
        short = float_compare(counted, self.amount, precision_rounding=rounding)
        if short and not (self.difference_reason or '').strip():
            raise UserError(_(
                "The count (%(counted)s) differs from what was declared (%(declared)s). "
                "Say why before confirming - it is the reason the executive will read.",
                counted=self._money(counted), declared=self._money(self.amount)))
        txn = self.env['petty.cash.transaction']._lab_hand_over(self, counted)
        self.write({
            'state': 'received',
            'received_by_id': self.env.uid,
            'received_at': fields.Datetime.now(),
            'txn_id': txn.id,
        })
        if short < 0:
            # Owed, and said so where the executive will see it: on their own
            # slip, in their own name.
            self.activity_schedule(
                'mail.mail_activity_data_todo', user_id=self.user_id.id,
                summary=_("Short by %s on cash handover",
                          self._money(self.amount - counted)),
                note=_("The office counted %(counted)s against the %(declared)s you "
                       "declared on %(name)s. Reason given: %(why)s. The difference "
                       "stays on your float until it is settled.",
                       counted=self._money(counted), declared=self._money(self.amount),
                       name=self.name, why=self.difference_reason))
        self.message_post(body=_(
            "Counted %(counted)s by %(who)s%(diff)s. Taken off float %(float)s.",
            counted=self._money(counted), who=self.env.user.name,
            diff=(_(" - %(d)s against the declared amount: %(why)s",
                    d=self._money(counted - self.amount), why=self.difference_reason)
                  if short else ''),
            float=self.allocation_id.display_name))
        return True

    def action_cancel(self):
        for rec in self:
            if rec.state not in ('draft', 'declared'):
                raise UserError(_("%s has been counted and cannot be cancelled.", rec.name))
            rec.write({'state': 'cancelled'})
        return True

    def action_draft(self):
        """Back to the phone: the slip was raised too soon."""
        for rec in self:
            if rec.state != 'declared':
                continue
            rec.write({'state': 'draft', 'code': False, 'counted_amount': 0.0})
        return True

    def action_print_receipt(self):
        self.ensure_one()
        return self.env.ref('lab_fieldwork.report_cash_handover').report_action(self)

    def _open(self):
        """The slip itself, full screen - where the code is big enough to read."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'current',
        }

    # ------------------------------------------------------------ helpers
    @api.model
    def _is_receiver(self):
        return self.env.su or any(self.env.user.has_group(g) for g in RECEIVERS)

    @api.model
    def _new_code(self):
        for _attempt in range(20):
            code = '-'.join(''.join(secrets.choice(CODE_ALPHABET) for _ in range(3))
                            for _ in range(2))
            if not self.sudo().search_count([('code', '=', code)]):
                return code
        raise UserError(_("Could not make a handover code. Try again."))

    @api.model
    def _normalise_code(self, raw):
        letters = ''.join(ch for ch in (raw or '').upper() if ch.isalnum())
        return '%s-%s' % (letters[:3], letters[3:6]) if len(letters) == 6 else letters

    @api.model
    def find_by_code(self, raw):
        """The slip a code names, whoever typed it and however they spaced it."""
        code = self._normalise_code(raw)
        if not code:
            return self.browse()
        return self.search([('code', '=', code)], limit=1)

    def _deep_link(self):
        self.ensure_one()
        base = self.env['ir.config_parameter'].sudo().get_param('web.base.url') or ''
        return '%s/odoo/action-lab_fieldwork.action_cash_handover_all/%d' % (
            base.rstrip('/'), self.id)

    def _money(self, amount):
        # Works on one slip, or on none at all (the desk asks for a total).
        currency = (self.currency_id if len(self) == 1 and self.currency_id
                    else self.env.company.currency_id)
        return formatLang(self.env, amount or 0.0, currency_obj=currency)

    @api.model
    def _sweep_unbanked(self, user):
        """Push every rupee this person took as cash and never sent to the float
        into it. Returns the reason the last one could not move, or ''."""
        problem = ''
        Visit = self.env['lab.visit'].sudo()
        Loose = self.env['lab.cash.collection'].sudo()
        held = Visit.search([
            ('user_id', '=', user.id), ('state', '=', 'done'),
            ('pay_mode', '=', 'cash'), ('collected', '>', 0),
            ('cash_banked', '=', False)])
        loose = Loose.search([
            ('user_id', '=', user.id), ('pay_mode', '=', 'cash'),
            ('cash_banked', '=', False)])
        for rec in list(held) + list(loose):
            try:
                with self.env.cr.savepoint():
                    rec.action_cash_to_float()
            except UserError as exc:
                problem = exc.args[0] if exc.args else str(exc)
        return problem

    # ------------------------------------------------------------ the one truth
    @api.model
    def cash_position(self, users):
        """{user id: what they hold of the lab's money}, from every place it can be.

        ``unbanked``   cash taken and never sent to the float (old visits, or a
                       push that failed) - still in the pocket;
        ``in_float``   collections in the float minus what the office has counted;
        ``pending``    handed over, waiting for the count;
        ``with_you``   unbanked + in_float: not yet in the office's hands;
        ``available``  with_you - pending: what can be handed over now;
        ``oldest``/``days``  the age of the oldest rupee still held, the
                       handovers covering the oldest collections first.

        ONE reading for My Day, the desks and the reminders, so no two screens
        can disagree about who is carrying what.
        """
        users = users.sudo()
        today = fields.Date.context_today(self)
        out = {u.id: dict(unbanked=0.0, in_float=0.0, pending=0.0, pending_count=0,
                          with_you=0.0, available=0.0, oldest=None, days=0,
                          allocation_id=False) for u in users}
        if not users:
            return out

        def older(row, day):
            if day and (row['oldest'] is None or day < row['oldest']):
                row['oldest'] = day

        Visit = self.env['lab.visit'].sudo()
        for visit in Visit.search([
                ('user_id', 'in', users.ids), ('state', '=', 'done'),
                ('pay_mode', '=', 'cash'), ('collected', '>', 0),
                ('cash_banked', '=', False)]):
            row = out[visit.user_id.id]
            row['unbanked'] += visit.collected
            older(row, visit.date)
        for loose in self.env['lab.cash.collection'].sudo().search([
                ('user_id', 'in', users.ids), ('pay_mode', '=', 'cash'),
                ('cash_banked', '=', False)]):
            row = out[loose.user_id.id]
            row['unbanked'] += loose.amount
            older(row, loose.date)

        allocations = {u: u.petty_cash_allocation_id for u in users}
        alloc_ids = [a.id for a in allocations.values() if a]
        by_alloc = defaultdict(list)
        if alloc_ids:
            for txn in self.env['petty.cash.transaction'].sudo().search([
                    ('allocation_id', 'in', alloc_ids),
                    ('type', 'in', ('collection', 'handover')),
                    ('state', '=', 'posted')], order='date, id'):
                by_alloc[txn.allocation_id.id].append(txn)
        for user, allocation in allocations.items():
            row = out[user.id]
            row['allocation_id'] = allocation.id
            if not allocation:
                continue
            rounding = allocation.currency_id.rounding or 0.01
            moves = by_alloc[allocation.id]
            handed = sum(t.amount for t in moves if t.type == 'handover')
            collected = [t for t in moves if t.type == 'collection']
            row['in_float'] = max(sum(t.amount for t in collected) - handed, 0.0)
            # FIFO: what was handed over covers the oldest collections first;
            # the first one not fully covered is the age of what is left.
            covered = handed
            for txn in collected:
                if float_compare(covered, txn.amount, precision_rounding=rounding) >= 0:
                    covered -= txn.amount
                    continue
                older(row, txn.date)
                break

        for handover in self.sudo().search([
                ('user_id', 'in', users.ids), ('state', '=', 'declared')]):
            row = out[handover.user_id.id]
            row['pending'] += handover.amount
            row['pending_count'] += 1

        for row in out.values():
            row['with_you'] = row['unbanked'] + row['in_float']
            row['available'] = max(row['with_you'] - row['pending'], 0.0)
            row['days'] = (today - row['oldest']).days if row['oldest'] else 0
        return out

    @api.model
    def _cash_policy(self):
        """(max days, ceiling) after which held cash is a thing to chase."""
        # The same defaults the settings pages show; an unset key is the
        # default, and only an explicit 0 switches a rule off.
        get = self.env['ir.config_parameter'].sudo().get_param
        raw_days, raw_ceiling = get('lab_fieldwork.cash_max_days'), get('lab_fieldwork.cash_ceiling')
        try:
            days = 2 if raw_days in (None, False, '') else int(float(raw_days))
        except (TypeError, ValueError):
            days = 2
        try:
            ceiling = 10000.0 if raw_ceiling in (None, False, '') else float(raw_ceiling)
        except (TypeError, ValueError):
            ceiling = 10000.0
        return days, ceiling

    @api.model
    def _is_overdue(self, row, policy=None):
        days, ceiling = policy or self._cash_policy()
        if row['available'] <= 0:
            return False
        return bool((days and row['days'] > days) or (ceiling and row['available'] >= ceiling))

    @api.model
    def _cron_cash_reminders(self):
        """A to-do, once, for anyone carrying cash past the policy.

        On the executive's own contact record, in their own name: the one place
        every user has an activity feed, and the slip that would carry it does
        not exist yet - that is the point of the reminder.
        """
        group = self.env.ref('lab_fieldwork.group_fieldwork_executive',
                             raise_if_not_found=False)
        if not group:
            return 0
        users = group.sudo().all_user_ids.filtered('active')
        policy = self._cash_policy()
        todo = self.env.ref('mail.mail_activity_data_todo', raise_if_not_found=False)
        raised = 0
        positions = self.cash_position(users)
        for user in users:
            row = positions[user.id]
            if not self._is_overdue(row, policy) or not todo:
                continue
            partner = user.partner_id.sudo()
            open_ones = partner.activity_ids.filtered(
                lambda a: a.user_id == user and a.activity_type_id == todo
                and (a.summary or '').startswith(self.env._('Hand over')))
            if open_ones:
                continue
            partner.activity_schedule(
                'mail.mail_activity_data_todo', user_id=user.id,
                summary=self.env._("Hand over %s of collected cash", self._money(row['available'])),
                note=self.env._(
                    "You are carrying %(amount)s collected from doctors, the oldest "
                    "from %(days)s day(s) ago. Hand it to the office from My Day - "
                    "tap Hand over cash.",
                    amount=self._money(row['available']), days=row['days']))
            raised += 1
        return raised


class CashHandoverLine(models.Model):
    _name = 'lab.cash.handover.line'
    _description = 'Notes Counted on a Cash Handover'
    _order = 'denomination desc, id'

    handover_id = fields.Many2one(
        'lab.cash.handover', required=True, ondelete='cascade', index=True)
    denomination = fields.Float(string='Note', required=True, digits=(12, 0))
    count = fields.Integer(string='How Many', default=0)
    subtotal = fields.Monetary(compute='_compute_subtotal', store=True,
                               currency_field='currency_id')
    currency_id = fields.Many2one(related='handover_id.currency_id', readonly=True)

    _count_positive = models.Constraint(
        'CHECK(count >= 0)', "A note count cannot be negative.")

    @api.depends('denomination', 'count')
    def _compute_subtotal(self):
        for line in self:
            line.subtotal = line.denomination * line.count


class PettyCashTransaction(models.Model):
    """The movement a counted handover becomes: cash OUT of the float, no entry."""
    _inherit = 'petty.cash.transaction'

    type = fields.Selection(
        selection_add=[('handover', 'Handed to Office')],
        ondelete={'handover': 'cascade'})
    handover_id = fields.Many2one(
        'lab.cash.handover', string='Handover', readonly=True, copy=False,
        index='btree_not_null')

    @api.model
    def _deduction_types(self):
        # The balance guards in petty_cash (approve and post) read this, so a
        # handover can never take more than the float holds.
        return super()._deduction_types() + ('handover',)

    @api.depends('type', 'amount')
    def _compute_amount_signed(self):
        super()._compute_amount_signed()
        # A collection RAISES the float and a handover lowers it; the base
        # signs everything but an allocation negative, which read a
        # collection as money leaving the executive's hands.
        for rec in self:
            if rec.type == 'collection':
                rec.amount_signed = rec.amount
            elif rec.type == 'handover':
                rec.amount_signed = -rec.amount

    @api.model
    def _lab_hand_over(self, handover, amount):
        """Take counted cash out of the executive's float, and post it.

        As the system, like `_lab_collect`: the officer confirming the count
        is not necessarily a petty cash manager, and the movement is the
        outcome of the count, not an approval anybody is being asked for.
        """
        allocation = handover.allocation_id
        if not allocation or allocation.state != 'allocated':
            raise UserError(_(
                "%s has no open float to take this cash out of.", handover.user_id.name))
        txn = self.sudo().create({
            'allocation_id': allocation.id,
            'partner_id': handover.user_id.partner_id.id,
            'type': 'handover',
            'amount': amount,
            'date': fields.Date.context_today(self),
            'company_id': allocation.company_id.id,
            'handover_id': handover.id,
            'note': _("Handed to the office on %(name)s, counted by %(who)s",
                      name=handover.name, who=self.env.user.name),
        })
        txn.action_approve()
        txn.action_post()
        return txn


class PettyCashAllocation(models.Model):
    _inherit = 'petty.cash.allocation'

    amount_handed_over = fields.Monetary(
        string='Handed to Office', currency_field='currency_id',
        compute='_compute_amounts', store=True,
        help="Collected cash the office has counted back in. Counted in the "
             "returned amount - it left the float - and shown on its own so the "
             "accountant can see collections and handovers side by side.")
    handover_count = fields.Integer(compute='_compute_handover_count')

    @api.depends('transaction_ids.type', 'transaction_ids.state',
                 'transaction_ids.amount')
    def _compute_amounts(self):
        super()._compute_amounts()
        for rec in self:
            posted = rec.transaction_ids.filtered(lambda t: t._counts_as_posted())
            handed = sum(posted.filtered(lambda t: t.type == 'handover').mapped('amount'))
            rec.amount_handed_over = handed
            # Folded into the returned figure so the base identity
            # (balance = allocated - utilized - returned) stays true.
            rec.amount_returned += handed
            rec.amount_balance -= handed

    @api.depends('transaction_ids.type', 'transaction_ids.state')
    def _compute_handover_count(self):
        for rec in self:
            rec.handover_count = len(rec.transaction_ids.filtered(
                lambda t: t.type == 'handover' and t.state != 'cancelled'))

    def action_view_handovers(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Cash Handed to Office'),
            'res_model': 'lab.cash.handover',
            'view_mode': 'list,form',
            'domain': [('allocation_id', '=', self.id)],
            'context': {'create': False},
        }
