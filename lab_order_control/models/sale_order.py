# -*- coding: utf-8 -*-
import logging

from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class SaleOrder(models.Model):
    """Maker-checker verification (gaps 16 & 17) and credit-limit gate (gap 18).

    Verification is deliberately a *separate* axis from the native sale state
    rather than an extra value inside it: ``sale_custom`` already relabels
    draft/sent/sale as Registered / Quotation Sent / Sales Order, and folding a
    check into that selection would break every report, filter and integration
    keyed on the native states.
    """
    _inherit = 'sale.order'

    verification_state = fields.Selection(
        [('draft', 'Not Submitted'), ('to_verify', 'To Verify'),
         ('on_hold', 'Pending Information'),
         ('verified', 'Verified'), ('rejected', 'Sent Back')],
        default='draft', required=True, copy=False, tracking=True, index=True,
        string='Verification')
    entered_by_id = fields.Many2one(
        'res.users', string='Entered By', copy=False, readonly=True,
        default=lambda self: self.env.user,
        help="The maker — whoever registered this case.")
    verified_by_id = fields.Many2one(
        'res.users', string='Verified By', copy=False, readonly=True, tracking=True)
    verification_date = fields.Datetime(copy=False, readonly=True)
    verification_note = fields.Char(
        'Verification Remark', copy=False, tracking=True,
        help="Reason shown to the maker when a case is sent back.")

    # ------------------------------------------------------------------ credit
    # credit_limit is a plain Float on res.partner (company-dependent), not Monetary.
    partner_credit_limit = fields.Float(
        related='partner_id.credit_limit', string='Clinic Credit Limit', readonly=True)
    partner_credit_exposure = fields.Monetary(
        related='partner_id.credit_exposure', string='Clinic Exposure', readonly=True)
    credit_warning = fields.Char(compute='_compute_credit_warning')
    credit_override_by_id = fields.Many2one(
        'res.users', string='Credit Released By', copy=False, readonly=True, tracking=True)
    credit_override_reason = fields.Char('Credit Release Reason', copy=False, tracking=True)

    @api.depends('partner_id', 'amount_total', 'state')
    def _compute_credit_warning(self):
        for order in self:
            order.credit_warning = order._credit_limit_message() or False

    verification_needed = fields.Boolean(
        compute='_compute_verification_needed', store=True,
        help="Whether this particular order has to be checked by a second person.")

    # ------------------------------------------------------------------ policy
    @api.model
    def _maker_group_ids(self):
        """Roles whose orders are subject to the maker-checker check.

        Held as a setting rather than a hard reference to the field-work executive
        group, because this module does not depend on `lab_fieldwork` — and a lab that
        wants a different role checked should not need a code change to say so.
        """
        raw = self.env['ir.config_parameter'].sudo().get_param(
            'lab_order_control.verification_maker_groups', '')
        return [int(x) for x in raw.split(',') if x.strip().isdigit()]

    @api.depends('entered_by_id', 'create_uid')
    def _compute_verification_needed(self):
        """Verification applies only to orders entered by the checked roles.

        The point of maker-checker is that somebody other than the person at the clinic
        looks at what was entered. An office user keying an order at the counter already
        IS the checker; sending it to themselves adds a step and controls nothing, and a
        queue full of orders that never needed checking is how a real one gets ignored.

        With no roles configured the check applies to everyone — the same behaviour as
        before this was scoped. An empty setting must not silently switch a control off.
        """
        groups = self._maker_group_ids()
        enabled = self._verification_enabled()
        if not enabled:
            for order in self:
                order.verification_needed = False
            return
        if not groups:
            # Client rule (2026-08-09): "direct creation of a sale order doesn't need
            # verification; any sale order from field work does." That is a rule about
            # ORIGIN, so with no maker roles configured the origin hook is the whole
            # test — an empty list no longer means "check everybody".
            for order in self:
                order.verification_needed = order._verification_origin_requires()
            return
        # One query for the whole set rather than has_group() per order.
        makers = self.mapped('entered_by_id') | self.mapped('create_uid')
        checked = set(self.env['res.users'].sudo().search([
            ('id', 'in', makers.ids), ('all_group_ids', 'in', groups)]).ids)
        for order in self:
            maker = order.entered_by_id or order.create_uid
            order.verification_needed = bool(
                order._verification_origin_requires()
                or (maker and maker.id in checked))

    def _verification_origin_requires(self):
        """Does WHERE this order came from make it subject to checking?

        Base answer: no. `lab_fieldwork` overrides it so that anything raised at a
        clinic is checked whoever happens to have created the record — the maker's group
        is a good proxy but it breaks the moment an order is created in `sudo()` or by a
        cron on the executive's behalf, and the client's rule is about origin: orders
        keyed at the counter go straight through, orders from the field do not.
        """
        self.ensure_one()
        return False

    @api.model
    def _verification_enabled(self):
        """The master switch, independent of who entered any given order."""
        return self.env['ir.config_parameter'].sudo().get_param(
            'lab_order_control.require_order_verification', 'False') == 'True'

    def _verification_required(self):
        self.ensure_one()
        return self._verification_enabled() and self.verification_needed

    def _allow_self_verification(self):
        return self.env['ir.config_parameter'].sudo().get_param(
            'lab_order_control.allow_self_verification', 'False') == 'True'

    def _credit_limit_message(self):
        """Return the over-limit message, or None when the order is within limit.

        Read as superuser because `credit_limit` and `credit_exposure` are restricted to
        the accounting roles. A salesperson must not be SHOWN the clinic's limit — and
        the related fields on the form still enforce that — but the control has to run
        for them, and reading it unprivileged means an ordinary salesperson cannot
        confirm any order at all: the gate fails with an access error instead of a
        credit decision.
        """
        self.ensure_one()
        partner = self.partner_id.commercial_partner_id or self.partner_id
        partner = partner.sudo()
        if not partner or not partner.credit_limit:
            return None
        if partner._get_credit_limit_policy() == 'none':
            return None
        # The order's own value is only additional exposure while it is still a
        # quotation; once confirmed it is already inside partner.credit_exposure.
        pending = self.amount_total if self.state in ('draft', 'sent') else 0.0
        projected = partner.credit_exposure + pending
        if projected <= partner.credit_limit:
            return None
        return _(
            "%(clinic)s would be %(over)s over its credit limit of %(limit)s "
            "(exposure %(exposure)s + this order %(order)s).",
            clinic=partner.display_name,
            over=self.currency_id.format(projected - partner.credit_limit),
            limit=self.currency_id.format(partner.credit_limit),
            exposure=self.currency_id.format(partner.credit_exposure),
            order=self.currency_id.format(pending))

    def _check_credit_limit(self):
        for order in self:
            message = order._credit_limit_message()
            if not message:
                continue
            partner = order.partner_id.commercial_partner_id or order.partner_id
            policy = partner._get_credit_limit_policy()
            if policy == 'block' and not order.credit_override_by_id:
                raise UserError(_(
                    "%s\n\nThis clinic is set to block over-limit orders. Collect payment, "
                    "raise the limit, or ask a manager to release the order "
                    "(Release Credit Block).", message))
            order.message_post(body=_("Credit limit warning: %s", message))

    # ------------------------------------------------------------------ actions
    def action_submit_for_verification(self):
        for order in self:
            if order.state not in ('draft', 'sent'):
                raise UserError(_("Only a registered case can be submitted for verification."))
            if not order.order_line:
                raise UserError(_("Add at least one line before submitting %s.", order.name))
            order.write({'verification_state': 'to_verify', 'verification_note': False})
        return True

    def action_verify(self):
        if not self.env.user.has_group('lab_order_control.group_lab_order_checker'):
            raise UserError(_("Only an Order Checker can verify a registered case."))
        for order in self:
            if order.verification_state != 'to_verify':
                raise UserError(_("%s is not awaiting verification.", order.name))
            if order.entered_by_id == self.env.user and not order._allow_self_verification():
                raise UserError(_(
                    "%s was registered by you. A second person must verify it — that is "
                    "the point of the maker-checker control. (It can be relaxed in "
                    "Settings if the lab runs a single-person counter.)", order.name))
            order.write({
                'verification_state': 'verified',
                'verified_by_id': self.env.user.id,
                'verification_date': fields.Datetime.now(),
            })
        return True

    def action_reject_verification(self):
        if not self.env.user.has_group('lab_order_control.group_lab_order_checker'):
            raise UserError(_("Only an Order Checker can send a case back."))
        for order in self:
            if not order.verification_note:
                raise UserError(_(
                    "Record what needs correcting in 'Verification Remark' before sending "
                    "%s back.", order.name))
            order.write({'verification_state': 'rejected', 'verified_by_id': False,
                         'verification_date': False})
        return True

    def action_reset_verification(self):
        self.write({'verification_state': 'draft', 'verified_by_id': False,
                    'verification_date': False})
        return True

    def action_release_credit_block(self):
        if not self.env.user.has_group('lab_order_control.group_lab_credit_override'):
            raise UserError(_("Only a Credit Controller can release a credit block."))
        for order in self:
            if not order.credit_override_reason:
                raise UserError(_(
                    "Record why the credit block is being released on %s.", order.name))
            order.write({'credit_override_by_id': self.env.user.id})
            order.message_post(body=_(
                "Credit block released by %(user)s: %(reason)s",
                user=self.env.user.name, reason=order.credit_override_reason))
        return True

    def action_set_to_draft(self):
        """Take a confirmed case back to Registered so it can be corrected.

        Confirmed lines are frozen (sale_custom form). To change what is being made,
        the case comes back to draft: its open delivery transfers and not-yet-started
        manufacturing orders are cancelled, verification is reset (a second person
        checks it again), and the same order number is kept. Refused once anything
        irreversible exists — a posted invoice, a done delivery, or an MO already in
        progress — because those need a credit note / a rework, not a rewrite.
        """
        if not (self.env.user.has_group('lab_order_control.group_lab_order_checker')
                or self.env.user.has_group('sales_team.group_sale_manager')):
            raise UserError(_("Only an Order Checker or a Sales Manager can set a "
                              "confirmed case back to draft."))
        for order in self:
            if order.state != 'sale':
                raise UserError(_("%s is not a confirmed order.", order.name))
            if order.invoice_ids.filtered(lambda i: i.state == 'posted'):
                raise UserError(_(
                    "%s already has a posted invoice. Cancel the invoice with a credit "
                    "note first, or raise a rework.", order.name))
            if order.picking_ids.filtered(lambda p: p.state == 'done'):
                raise UserError(_(
                    "%s has already been delivered. Raise a rework instead.", order.name))
            productions = order.mrp_production_ids if 'mrp_production_ids' in order._fields \
                else self.env['mrp.production']
            started = productions.filtered(lambda m: m.state in ('progress', 'to_close', 'done'))
            if started:
                raise UserError(_(
                    "Work on %(order)s has already started (%(mos)s). Raise a rework "
                    "instead of changing the order.",
                    order=order.name, mos=', '.join(started.mapped('name'))))
            # cancel what confirmation created, then reopen
            productions.filtered(lambda m: m.state not in ('done', 'cancel')).action_cancel()
            order.with_context(disable_cancel_warning=True)._action_cancel()
            order.write({'locked': False})
            order.action_draft()
            order.action_reset_verification()
            order.message_post(body=_(
                "Set back to draft by %(user)s for correction; production and delivery "
                "documents cancelled.", user=self.env.user.name))
        return True

    # ------------------------------------------------- production follows the order
    def _lab_open_productions(self):
        """Every unfinished job the lab is making for this order.

        Two ways in, because one of them dies exactly when it is needed:
        `mrp_production_ids` walks the stock moves of the order's lines, and a
        correction deletes those lines, so the MOs made for the old version become
        invisible to it. The order number stamped on the MO's Source Document
        survives that, so both are taken. (client, 2026-09-16)
        """
        self.ensure_one()
        if 'mrp.production' not in self.env:
            return None
        Production = self.env['mrp.production'].sudo()
        linked = self.sudo().mrp_production_ids if 'mrp_production_ids' in self._fields \
            else Production.browse()
        by_number = Production.search([('origin', '=', self.name)])
        return (linked | by_number).filtered(lambda m: m.state not in ('done', 'cancel'))

    def _lab_stop_productions(self):
        """Stop that work, and say on the order what was stopped.

        Core cancels the delivery transfers of a cancelled order and leaves its
        manufacturing orders running, so the floor kept building the old version of a
        corrected case while the new one was confirmed underneath it, and the old MOs
        were cancelled by hand. A job already finished is history and is left alone;
        one already in progress is stopped and named, because somebody worked on it.
        """
        self.ensure_one()
        productions = self._lab_open_productions()
        if not productions:
            return productions
        started = productions.filtered(lambda m: m.state in ('progress', 'to_close'))
        productions.with_context(skip_activity=True).action_cancel()
        body = _("Production stopped with the order: %s cancelled.",
                 ', '.join(productions.mapped('name')))
        if started:
            body += ' ' + _("Work had already started on %s.",
                            ', '.join(started.mapped('name')))
        self.message_post(body=body)
        return productions

    def _action_cancel(self):
        # Before the state changes: `mrp_production_ids` is computed, and reading it
        # after the order is cancelled finds nothing to stop.
        for order in self:
            order._lab_stop_productions()
        return super()._action_cancel()

    def action_draft(self):
        # Set to Draft is reached through Cancel, so this is a second net: a draft
        # carrying an open MO (an older correction, or a job made by hand) is put
        # right on the way back rather than left running.
        res = super().action_draft()
        for order in self:
            order._lab_stop_productions()
        return res

    def write(self, vals):
        # THE THIRD NET: a state written straight to 'cancel'. `_action_cancel` is
        # the door the buttons use, and it is not the only door - an import, a
        # server action, a data fix or `write({'state': 'cancel'})` from a script
        # all walk past it, and the floor is left building a cancelled case. Four
        # orders on staging were cancelled that way on 2026-09-19 and kept six live
        # MOs between them, two of them already in progress. (client, 2026-09-25)
        if vals.get('state') == 'cancel':
            for order in self.filtered(lambda o: o.state != 'cancel'):
                order._lab_stop_productions()
        return super().write(vals)

    @api.model
    def _lab_stop_orphan_productions(self, limit=None, orders=None):
        """Stop work still running under orders that are already cancelled.

        The hook below only fires AS an order is cancelled, so everything
        cancelled before 19.0.1.9.0 shipped kept whatever the floor had already
        been told to make - four such orders were still open on the live
        database in September 2026, two of them with a step in progress. A
        straggler like that is reachable by nothing except a sweep. (client,
        2026-09-25)

        Goes through `_lab_stop_productions`, so it obeys the same rules a
        cancel does today: finished work is left alone, and what was stopped is
        written on the order. Idempotent - the second run has nothing to find.
        """
        empty = {'orders': self.browse(), 'names': []}
        if 'mrp.production' not in self.env:
            return empty
        stopped, names = self.browse(), []
        # `orders` narrows the sweep to a given set, which is what a test hands
        # it. Run database-wide from a test against a copy of the live database,
        # this cancelled six real jobs under four real orders and the chatter it
        # posted outlived the rollback. A sweep is a maintenance action; a test
        # gives it its own rows. (2026-09-25)
        candidates = orders.filtered(lambda o: o.state == 'cancel') \
            if orders is not None \
            else self.sudo().search([('state', '=', 'cancel')], limit=limit)
        for order in candidates:
            if not order._lab_open_productions():
                continue
            names += order._lab_stop_productions().mapped('name')
            stopped |= order
        return {'orders': stopped, 'names': names}

    def action_confirm(self):
        for order in self:
            if order._verification_required() and order.verification_state != 'verified':
                raise UserError(_(
                    "%s has not been verified. A second person must check the case "
                    "before it goes into production.", order.name))
        self._check_credit_limit()
        return super().action_confirm()
