# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class PettyCashTransaction(models.Model):
    """Cash taken at a clinic gets its own movement type.

    It is still counted as **allocated to the holder**: the moment an executive takes a
    doctor's payment they are accountable for that cash exactly as for an advance, and
    the allocated figure is what the lab settles against them.

    The separate type exists for the accountant, not for the arithmetic. Without it the
    float shows one undifferentiated total and nobody can answer "how much of this is
    company money we advanced, and how much is customer money still to be banked?" —
    which is the only question that matters when the cash is counted.
    """
    _inherit = 'petty.cash.transaction'

    type = fields.Selection(
        selection_add=[('collection', 'Field Collection')],
        ondelete={'collection': 'cascade'})
    visit_id = fields.Many2one(
        'lab.visit', string='Field Visit', readonly=True, copy=False,
        index='btree_not_null')

    @api.model
    def _lab_collect(self, user, partner, amount, date=None, note=None, visit=None):
        """Take cash an executive has collected into their float, and post it.

        ONE path for both ways of recording it - the button on a visit, and the
        entry made straight on the Cash screen by somebody who took money at a
        door they did not raise a visit for. Cash reaches the float the same
        way, under the same guards, whichever was used; a second
        implementation would eventually disagree with this one about whose
        float, whose company, or whether to post. (client, 2026-09-12)
        """
        if not amount or amount <= 0:
            raise UserError(_("Enter how much was collected."))
        if not partner:
            raise UserError(_("Say which doctor or clinic the money came from."))
        allocation = user.petty_cash_allocation_id
        company = visit.company_id if visit else (
            allocation.company_id if allocation else self.env.company)
        if not allocation:
            # Nobody on this lab had a float - 0 allocations - so the button
            # refused everyone and four visits' cash never reached one. A float
            # is a custody record; it opens itself the first time somebody takes
            # cash, unless the setting says accounts opens them by hand.
            # (client, 2026-09-18)
            allocation = self.env['petty.cash.allocation']._lab_open_float(user, company)
        if not allocation:
            raise UserError(_(
                "%s has no open petty cash float, so there is nowhere to hold this "
                "money. Ask accounts to allocate one.", user.name))
        # Said plainly rather than letting the ORM raise its generic company
        # crossover error, which names a "Draft Payment" nobody asked for.
        if allocation.company_id != company:
            raise UserError(_(
                "This collection belongs to %(co)s but %(who)s holds a float in "
                "%(float_co)s. Cash cannot move between companies - ask accounts "
                "for a float in the right one.",
                co=company.display_name, who=user.name,
                float_co=allocation.company_id.display_name))
        txn = self.sudo().create({
            'allocation_id': allocation.id,
            'partner_id': partner.id,
            'type': 'collection',
            'amount': amount,
            'date': date or fields.Date.context_today(self),
            'company_id': allocation.company_id.id,
            'visit_id': visit.id if visit else False,
            'note': note or _("Collected from %(clinic)s",
                              clinic=partner.sudo().display_name),
        })
        txn.action_approve()
        txn.action_post()
        return txn


class PettyCashAllocation(models.Model):
    _inherit = 'petty.cash.allocation'

    @api.model
    def _lab_open_float(self, user, company):
        """A custody float for this person, opened on first use - or nothing,
        when the Field Work setting leaves floats to accounts.

        No advance and no journal: it holds cash collected from doctors until
        the office counts it back in, and never touches the ledger. Accounts
        can put a journal on it later if they want the float booked.
        """
        # get_param hands back Python False for a key nobody has ever saved,
        # and str(False) is 'False': read naively, an untouched setting meant
        # OFF. Only an explicit 'False'/'0' switches this off. (2026-09-18)
        raw = self.env['ir.config_parameter'].sudo().get_param('lab_fieldwork.auto_float')
        if raw not in (None, False, '') and str(raw).strip().lower() in ('false', '0'):
            return self.browse()
        Allocation = self.sudo()
        existing = Allocation.search([
            ('partner_id', '=', user.partner_id.id), ('company_id', '=', company.id),
            ('state', '=', 'allocated')], order='allocated_date desc', limit=1)
        if existing:
            return existing
        allocation = Allocation.create({
            'partner_id': user.partner_id.id,
            'company_id': company.id,
            'state': 'allocated',
            'allocated_date': fields.Date.context_today(self),
            'amount_limit': 0.0,
            'note': _("Opened by Field Work the first time %s took cash at a clinic. "
                      "It holds customer money until the office counts it back in: "
                      "no advance, no journal, nothing in the ledger. Set a journal "
                      "here if accounts wants the float booked.", user.name),
        })
        allocation.partner_id.sudo().is_petty_cash_holder = True
        allocation.message_post(body=_(
            "Opened automatically for cash collected in the field."))
        # The user's float is a compute that has already been read as empty.
        user.invalidate_recordset(['petty_cash_allocation_id', 'petty_cash_balance',
                                   'petty_cash_currency_id'])
        return allocation

    amount_collected = fields.Monetary(
        string='Collected in Field', currency_field='currency_id',
        compute='_compute_amounts', store=True,
        help="Customer payments collected on visits. Counted in the allocated amount — "
             "the holder is accountable for it — and shown separately so the advance "
             "and the collections can be told apart when the cash is counted.")

    collection_count = fields.Integer(compute='_compute_field_stats')
    collection_clinic_count = fields.Integer(compute='_compute_field_stats')

    @api.depends('transaction_ids.type', 'transaction_ids.visit_id')
    def _compute_field_stats(self):
        for rec in self:
            taken = rec.transaction_ids.filtered(
                lambda t: t.type == 'collection' and t.state != 'cancelled')
            rec.collection_count = len(taken)
            rec.collection_clinic_count = len(taken.mapped('partner_id'))

    def action_view_field_collections(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Field Collections'),
            'res_model': 'petty.cash.transaction',
            'view_mode': 'list,form',
            'domain': [('allocation_id', '=', self.id), ('type', '=', 'collection')],
            'context': {'create': False},
        }

    def action_view_field_visits(self):
        self.ensure_one()
        visits = self.transaction_ids.filtered(
            lambda t: t.type == 'collection').mapped('visit_id')
        return {
            'type': 'ir.actions.act_window',
            'name': _('Visits Behind This Cash'),
            'res_model': 'lab.visit',
            'view_mode': 'list,form',
            'domain': [('id', 'in', visits.ids)],
        }

    # Only the triggers this override ADDS need listing: Odoo unions the depends of
    # every definition of a compute along the inheritance chain, so the base's
    # `transaction_ids.move_state` still applies here and repeating it would just
    # duplicate an entry.
    @api.depends('transaction_ids.type', 'transaction_ids.state',
                 'transaction_ids.amount')
    def _compute_amounts(self):
        # Extend rather than replace, so the base stays the single definition of the
        # arithmetic. amount_balance is raised by the same amount to keep the base
        # identity (balance = allocated - utilized - returned) true.
        super()._compute_amounts()
        for rec in self:
            posted = rec.transaction_ids.filtered(lambda t: t._counts_as_posted())
            collected = sum(
                posted.filtered(lambda t: t.type == 'collection').mapped('amount'))
            rec.amount_collected = collected
            rec.amount_allocated += collected
            rec.amount_balance += collected


class ResUsers(models.Model):
    """An executive's float hangs off their own partner.

    The partner behind the USER, not an employee work-contact: a float is carried and
    spent by somebody who logs in, and the user's partner is the only record that
    reliably identifies that person.
    """
    _inherit = 'res.users'

    petty_cash_allocation_id = fields.Many2one(
        'petty.cash.allocation', string='Petty Cash Float',
        compute='_compute_petty_cash')
    fw_clinic_ids = fields.Many2many(
        'res.partner', string='Clinics on My Rounds', compute='_compute_fw_clinics',
        help="Every clinic on a beat this person works. Read by the coverage record "
             "rule, which is why it is computed rather than stored — a beat gains and "
             "loses clinics constantly, and a stale copy would leak or hide rows.")
    petty_cash_balance = fields.Monetary(
        string='Cash in Hand', compute='_compute_petty_cash',
        currency_field='petty_cash_currency_id')
    petty_cash_currency_id = fields.Many2one('res.currency', compute='_compute_petty_cash')

    def _compute_fw_clinics(self):
        # sudo: an executive cannot read other people's beats, and this must still
        # resolve their own without granting them beat access they do not have.
        Beat = self.env['lab.beat'].sudo()
        for user in self:
            user.fw_clinic_ids = Beat.search([('user_id', '=', user.id)]).partner_ids

    @api.depends('partner_id')
    def _compute_petty_cash(self):
        Allocation = self.env['petty.cash.allocation']
        # sudo: an executive reads their own float without holding the Petty Cash app,
        # and a manager reads the team's without holding it either.
        found = {}
        partners = self.mapped('partner_id')
        if partners:
            for alloc in Allocation.sudo().search(
                    [('partner_id', 'in', partners.ids), ('state', '=', 'allocated')],
                    order='allocated_date desc'):
                found.setdefault(alloc.partner_id.id, alloc)
        company_currency = self.env.company.currency_id
        for user in self:
            alloc = found.get(user.partner_id.id)
            user.petty_cash_allocation_id = alloc or False
            user.petty_cash_balance = alloc.amount_balance if alloc else 0.0
            user.petty_cash_currency_id = alloc.currency_id if alloc else company_currency


class LabVisit(models.Model):
    _inherit = 'lab.visit'

    petty_cash_allocation_id = fields.Many2one(
        related='user_id.petty_cash_allocation_id', string='Float', readonly=True)
    petty_cash_balance = fields.Monetary(
        related='user_id.petty_cash_balance', string='Cash in Hand',
        currency_field='currency_id', readonly=True)
    cash_txn_id = fields.Many2one(
        'petty.cash.transaction', string='Cash Movement', readonly=True, copy=False)
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
        # visit, so cash that had reached the float still counted as in the
        # pocket and the figure on My Day could never go down. (2026-09-18)
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
        # The cash goes to the float BY ITSELF at check-out. The button stayed a
        # second thing to remember at a clinic door, and money forgotten there
        # was invisible to the office. A failure is written on the visit, not
        # raised: the visit is closed and the person has left the counter;
        # the button is still there for once it is fixed. (client, 2026-09-18)
        for visit in self:
            if visit.pay_mode != 'cash' or visit.collected <= 0 or visit.cash_banked:
                continue
            try:
                with self.env.cr.savepoint():
                    visit.action_cash_to_float()
            except UserError as exc:
                visit.message_post(body=_(
                    "The cash did not go into the float by itself: %s Use "
                    "\"Cash into My Float\" once that is sorted.",
                    exc.args[0] if exc.args else exc))
        return result

    def action_cash_to_float(self):
        """Take this visit's cash into the executive's float, so the accountant can see
        where the money is."""
        self.ensure_one()
        if self.cash_banked:
            return self.action_view_cash_txn()
        if self.pay_mode != 'cash':
            raise UserError(_(
                "Only cash goes into the float. A cheque or online payment reaches the "
                "bank directly and is reconciled there — putting it here would count "
                "the same money twice."))
        if not self.collected:
            raise UserError(_("No money was collected on this visit."))
        txn = self.env['petty.cash.transaction']._lab_collect(
            self.user_id, self.partner_id, self.collected, visit=self,
            note=_("Collected at %(clinic)s on visit %(visit)s",
                   clinic=self.partner_id.display_name, visit=self.name))
        allocation = txn.allocation_id
        # sudo for the same reason the case slip stamps its order id that way:
        # this is the system recording the outcome of the executive's own action,
        # not somebody editing a closed record. The button is pressed on a visit
        # that is normally already Completed, and `cash_txn_id` is not one of the
        # fields a completed visit leaves writable — so without this the lock
        # mixin refuses the write on the line after the money has already moved.
        # (2026-09-12)
        self.sudo().cash_txn_id = txn
        self.message_post(body=_(
            "%(amount)s taken into petty cash float %(float)s.",
            amount=self.collected, float=allocation.display_name))
        return self.action_view_cash_txn()

    def action_view_cash_txn(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'petty.cash.transaction',
            'res_id': self.cash_txn_id.id,
            'view_mode': 'form',
        }
