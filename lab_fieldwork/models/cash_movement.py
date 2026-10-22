# -*- coding: utf-8 -*-
"""The executives' field cash: money taken from doctors, held, and counted back in.

A custody record - not petty cash, and not the ledger. Cash an executive takes at a
clinic becomes a COLLECTION here; the office counting their envelope becomes a
HANDOVER; what they hold is the difference. Accounts still books the doctor's receipt
when the money lands, so nothing here posts a journal entry.

It lived on the petty_cash module's floats until that module was retired for
ebshel_petty_cash (client, 2026-10-22): a float with no journal and no advance, kept
only for these two movements. Field Work keeps them itself now, so it needs no petty
cash module at all; lab_petty_cash joins the two where the lab wants them joined.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError

KINDS = [('collection', 'Collected'), ('handover', 'Handed to Office')]


class CashMovement(models.Model):
    _name = 'lab.cash.movement'
    _description = 'Field Cash Movement'
    _order = 'date desc, id desc'

    user_id = fields.Many2one(
        'res.users', string='Held By', required=True, index=True, readonly=True)
    kind = fields.Selection(KINDS, string='Movement', required=True, readonly=True)
    date = fields.Date(
        required=True, index=True, readonly=True,
        default=lambda self: fields.Date.context_today(self))
    amount = fields.Monetary(required=True, readonly=True, currency_field='currency_id')
    amount_signed = fields.Monetary(
        string='In / Out', compute='_compute_amount_signed', store=True,
        currency_field='currency_id',
        help="What the movement did to the cash held: a collection raises it, a "
             "handover lowers it.")
    partner_id = fields.Many2one(
        'res.partner', string='Clinic', readonly=True, index='btree_not_null',
        help="Who the money came from.")
    visit_id = fields.Many2one(
        'lab.visit', string='Field Visit', readonly=True, copy=False,
        index='btree_not_null')
    collection_id = fields.Many2one(
        'lab.cash.collection', string='Collection', readonly=True, copy=False,
        index='btree_not_null')
    handover_id = fields.Many2one(
        'lab.cash.handover', string='Handover', readonly=True, copy=False,
        index='btree_not_null')
    note = fields.Char(readonly=True)
    state = fields.Selection(
        [('posted', 'Posted'), ('cancelled', 'Cancelled')],
        default='posted', required=True, readonly=True, copy=False, index=True)
    company_id = fields.Many2one(
        'res.company', required=True, readonly=True,
        default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id', readonly=True)

    _amount_positive = models.Constraint(
        'CHECK(amount > 0)', "A cash movement has to be for some money.")

    @api.depends('kind', 'amount')
    def _compute_amount_signed(self):
        for rec in self:
            rec.amount_signed = -rec.amount if rec.kind == 'handover' else rec.amount

    @api.depends('kind', 'amount', 'partner_id', 'user_id')
    def _compute_display_name(self):
        labels = dict(KINDS)
        for rec in self:
            who = rec.partner_id.display_name if rec.kind == 'collection' else rec.user_id.name
            rec.display_name = ' · '.join(
                part for part in (labels.get(rec.kind), who, '%s' % rec.amount) if part)

    # ------------------------------------------------------------ the two ways in
    @api.model
    def _collect(self, user, partner, amount, date=None, note=None, visit=None,
                 collection=None):
        """Cash an executive collected, now held by them.

        ONE path for both ways of recording it - the button on a visit, and the
        entry made straight on the Cash screen by somebody who took money at a
        door they did not raise a visit for - so the two can never disagree about
        whose cash it is or in which company. (client, 2026-09-12)
        """
        if not amount or amount <= 0:
            raise UserError(_("Enter how much was collected."))
        if not partner:
            raise UserError(_("Say which doctor or clinic the money came from."))
        company = (visit or collection).company_id if (visit or collection) else self.env.company
        return self.sudo().create({
            'user_id': user.id,
            'kind': 'collection',
            'partner_id': partner.id,
            'amount': amount,
            'date': date or fields.Date.context_today(self),
            'company_id': company.id,
            'visit_id': visit.id if visit else False,
            'collection_id': collection.id if collection else False,
            'note': note or _("Collected from %(clinic)s",
                              clinic=partner.sudo().display_name),
        })

    @api.model
    def _hand_over(self, handover, amount):
        """Counted cash leaving the executive's hands for the office's.

        As the system: the person confirming the count is the office, and the
        movement is the outcome of that count, not something anybody approves.
        """
        return self.sudo().create({
            'user_id': handover.user_id.id,
            'kind': 'handover',
            'amount': amount,
            'date': fields.Date.context_today(self),
            'company_id': handover.company_id.id,
            'handover_id': handover.id,
            'note': _("Handed to the office on %(name)s, counted by %(who)s",
                      name=handover.name, who=self.env.user.name),
        })

    def action_cancel(self):
        """A collection entered twice, taken back. A counted handover is the
        office's word and stays: cancel the slip's correction on the slip."""
        if not (self.env.su or self.env.user.has_group('lab_fieldwork.group_fieldwork_manager')):
            raise UserError(_("Only a field work manager can cancel a cash movement."))
        for rec in self:
            if rec.kind == 'handover' and rec.handover_id.state == 'received':
                raise UserError(_(
                    "%s is a counted handover: it stays as the office recorded it.",
                    rec.display_name))
        self.sudo().write({'state': 'cancelled'})
        return True

    @api.model
    def _held_by(self, users):
        """{user id: cash held} - posted collections less posted handovers."""
        held = dict.fromkeys(users.ids, 0.0)
        if users:
            for user, total in self.sudo()._read_group(
                    [('user_id', 'in', users.ids), ('state', '=', 'posted')],
                    ['user_id'], ['amount_signed:sum']):
                held[user.id] = total or 0.0
        return held


class ResUsers(models.Model):
    _inherit = 'res.users'

    fw_clinic_ids = fields.Many2many(
        'res.partner', string='Clinics on My Rounds', compute='_compute_fw_clinics',
        help="Every clinic on a beat this person works. Read by the coverage record "
             "rule, which is why it is computed rather than stored — a beat gains and "
             "loses clinics constantly, and a stale copy would leak or hide rows.")
    fw_cash_in_hand = fields.Monetary(
        string='Cash in Hand', compute='_compute_fw_cash',
        currency_field='fw_cash_currency_id',
        help="Collected cash this person holds that the office has not counted back in.")
    fw_cash_currency_id = fields.Many2one('res.currency', compute='_compute_fw_cash')

    def _compute_fw_clinics(self):
        # sudo: an executive cannot read other people's beats, and this must still
        # resolve their own without granting them beat access they do not have.
        Beat = self.env['lab.beat'].sudo()
        for user in self:
            user.fw_clinic_ids = Beat.search([('user_id', '=', user.id)]).partner_ids

    def _compute_fw_cash(self):
        held = self.env['lab.cash.movement']._held_by(self)
        currency = self.env.company.currency_id
        for user in self:
            user.fw_cash_in_hand = held.get(user.id, 0.0)
            user.fw_cash_currency_id = currency


class LabVisit(models.Model):
    _inherit = 'lab.visit'

    fw_cash_in_hand = fields.Monetary(
        related='user_id.fw_cash_in_hand', string='Cash in Hand',
        currency_field='currency_id', readonly=True)
    cash_txn_id = fields.Many2one(
        'lab.cash.movement', string='Cash Movement', readonly=True, copy=False)
    cash_banked = fields.Boolean(compute='_compute_cash_banked',
                                 search='_search_cash_banked')

    @api.depends('cash_txn_id', 'cash_txn_id.state')
    def _compute_cash_banked(self):
        for v in self:
            v.cash_banked = bool(v.cash_txn_id) and v.cash_txn_id.state != 'cancelled'

    def _search_cash_banked(self, operator, value):
        # A non-stored computed field used in ANY domain needs this, or it raises
        # "Cannot convert ... to SQL because it is not stored" at query time — i.e. for
        # the user, not the developer.
        #
        # Odoo 19 never hands a search method '=' for a boolean: `= True` arrives
        # as ('in', OrderedSet([True])) and `= False` / `!= True` as ('not in',
        # {True}). Reading `in` as a negative made BOTH filters return every
        # visit, so cash that had been taken in still counted as in the pocket
        # and the figure on My Day could never go down. (2026-09-18)
        taken = [('cash_txn_id', '!=', False),
                 ('cash_txn_id.state', '!=', 'cancelled')]
        if operator in ('in', 'not in'):
            wanted = any(bool(v) for v in value)
            positive = wanted if operator == 'in' else not wanted
        elif operator in ('=', '!='):
            positive = bool(value) if operator == '=' else not bool(value)
        else:
            return NotImplemented
        if positive:
            return taken
        return ['!', ('id', 'in', self.sudo().search(taken).ids)]

    def do_check_out(self, latitude=False, longitude=False):
        result = super().do_check_out(latitude=latitude, longitude=longitude)
        # The cash is taken in BY ITSELF at check-out. The button stayed a second
        # thing to remember at a clinic door, and money forgotten there was
        # invisible to the office. A failure is written on the visit, not raised:
        # the visit is closed and the person has left the counter; the button is
        # still there for once it is fixed. (client, 2026-09-18)
        for visit in self:
            if visit.pay_mode != 'cash' or visit.collected <= 0 or visit.cash_banked:
                continue
            try:
                with self.env.cr.savepoint():
                    visit.action_cash_to_float()
            except UserError as exc:
                visit.message_post(body=_(
                    "The cash was not taken in by itself: %s Use \"Cash into My "
                    "Float\" once that is sorted.", exc.args[0] if exc.args else exc))
        return result

    def action_cash_to_float(self):
        """Record this visit's cash as held by the executive, so the office can see
        where the money is."""
        self.ensure_one()
        if self.cash_banked:
            return self.action_view_cash_txn()
        if self.pay_mode != 'cash':
            raise UserError(_(
                "Only cash is held. A cheque or online payment reaches the bank "
                "directly and is reconciled there — recording it here would count "
                "the same money twice."))
        if not self.collected:
            raise UserError(_("No money was collected on this visit."))
        move = self.env['lab.cash.movement']._collect(
            self.user_id, self.partner_id, self.collected, visit=self,
            note=_("Collected at %(clinic)s on visit %(visit)s",
                   clinic=self.partner_id.display_name, visit=self.name))
        # sudo for the same reason the case slip stamps its order id that way:
        # this is the system recording the outcome of the executive's own action,
        # not somebody editing a closed record. The button is pressed on a visit
        # that is normally already Completed, and `cash_txn_id` is not one of the
        # fields a completed visit leaves writable — so without this the lock
        # mixin refuses the write on the line after the money has already moved.
        # (2026-09-12)
        self.sudo().cash_txn_id = move
        self.message_post(body=_(
            "%(amount)s held by %(who)s as field cash.",
            amount=self.collected, who=self.user_id.name))
        return self.action_view_cash_txn()

    def action_view_cash_txn(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'lab.cash.movement',
            'res_id': self.cash_txn_id.id,
            'view_mode': 'form',
        }
