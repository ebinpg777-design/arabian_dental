# -*- coding: utf-8 -*-
"""Amending a confirmed case without losing the work done on it.

The only way to change a confirmed order was Cancel, then Set to Quotation: the
cancel stops every job the lab is making for it, and confirming it again raises
new ones. Whatever the benches had done - who took which step, when it was handed
on - stayed behind on a cancelled job, and the case started from its first step
on a blank one. On the live database that happened to 244 orders in a month.

So an amendment is its own act. The order goes back to draft and NOTHING is
cancelled: the jobs go on, the delivery stays, the invoice stays. It is never
refused, at whatever stage the work is. When the order is confirmed again, each
line that already has a job keeps it:

* nothing changed on the line - nothing happens to the job;
* the product changed - a job still being made becomes that product; the steps
  somebody has taken or finished stay exactly as they are, and only the steps
  nobody has touched are re-laid on the new product's route;
* the quantity changed - a job still being made takes it;
* a finished job is history, and is left as it is whatever the line says now;
* a line taken off the order - its job, if still being made, is stopped;
* a line added - a job is raised for it, as for any confirmed line.

(client, 2026-11-26: "When doing Set to draft of a SO with MOs done or in
progress the working details are missing. Find a solution for amending SO which
should not affect the MOs. Product may change in the amendment. Do not block the
amendment at any stage")
"""
from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

# Whoever could take a confirmed order back with Cancel and Set to Quotation.
AMEND_GROUPS = ('sales_team.group_sale_salesman', 'lab_order_control.group_lab_order_checker')


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    lab_amending = fields.Boolean(
        'Being Amended', copy=False, readonly=True, tracking=True, index='btree_not_null',
        help="This confirmed order was taken back to draft to be amended. Its jobs, "
             "deliveries and invoices were left as they are, and the jobs follow "
             "the order's lines when it is confirmed again.")
    lab_amending_by_id = fields.Many2one(
        'res.users', 'Amendment Started By', copy=False, readonly=True)
    lab_amending_at = fields.Datetime('Amendment Started On', copy=False, readonly=True)
    # HAS IT BEEN AMENDED, and how often: the list's filter, after the fact.
    # (client, 2026-11-26: "filter of amended SO")
    lab_amend_count = fields.Integer(
        'Amendments', copy=False, readonly=True,
        help="How many times this order was taken back to draft to be amended.")
    lab_amended_on = fields.Datetime(
        'Last Amended', copy=False, readonly=True, index='btree_not_null',
        help="When the order was last confirmed again after an amendment.")
    lab_amended_by_id = fields.Many2one(
        'res.users', 'Last Amended By', copy=False, readonly=True)
    lab_amending_jobs = fields.Char(
        'Jobs Going On', compute='_compute_lab_amending_jobs',
        help="The jobs that go on while the order is being amended.")

    def _compute_lab_amending_jobs(self):
        for order in self:
            jobs = order._lab_live_productions() if order.lab_amending else False
            order.lab_amending_jobs = ', '.join(
                '%s (%s)' % (job.name, dict(job._fields['state']._description_selection(
                    self.env)).get(job.state, job.state)) for job in jobs) if jobs else False

    def _lab_live_productions(self):
        """Every job of these orders that has not been cancelled: by its order
        line, and by the order number it carries - a job whose line was deleted
        keeps only the number."""
        if 'mrp.production' not in self.env or not self:
            return None
        return self.env['mrp.production'].sudo().search([
            '|', ('sale_id', 'in', self.ids), ('origin', 'in', self.mapped('name')),
            ('state', '!=', 'cancel')])

    def _check_amend_role(self):
        if self.env.su or any(self.env.user.has_group(g) for g in AMEND_GROUPS):
            return
        raise AccessError(_("Only sales and order-checking users can amend a confirmed order."))

    def action_amend(self):
        """Take a confirmed order back to draft to amend it, cancelling nothing.

        Not refused for a job in progress or done, a delivery made or an invoice
        posted: those are left exactly as they are. The order is checked again by a
        second person where the lab verifies orders, like any order on its way to
        being confirmed.
        """
        self._check_amend_role()
        for order in self:
            if order.state != 'sale':
                raise UserError(_(
                    "%s is not a confirmed order: it can be changed as it is.", order.name))
            jobs = order._lab_live_productions()
            invoices = order.sudo().invoice_ids.filtered(
                lambda m: m.state == 'posted' and m.move_type == 'out_invoice')
            order.sudo().write({
                'state': 'draft', 'locked': False, 'lab_amending': True,
                'lab_amending_by_id': self.env.uid,
                'lab_amending_at': fields.Datetime.now(),
                'lab_amend_count': order.lab_amend_count + 1,
            })
            order.sudo().action_reset_verification()
            body = _("Taken back to draft by %(user)s to be amended. Nothing was "
                     "cancelled.", user=self.env.user.name)
            if jobs:
                body += ' ' + _("Production goes on: %s.", order.lab_amending_jobs)
            if invoices:
                body += ' ' + _("Already invoiced (%s): the invoice is not changed by "
                                "the amendment.", ', '.join(invoices.mapped('name')))
            order.message_post(body=body)
            for job in jobs or []:
                job.message_post(body=_(
                    "Order %(order)s is being amended by %(user)s. This job goes on as "
                    "it is.", order=order.name, user=self.env.user.name))
        return True

    def action_set_to_draft(self):
        """The form's own button, by its old name: an amendment now. It used to
        cancel the jobs nobody had started and refuse once one had."""
        return self.action_amend()

    def action_cancel(self):
        """Pressing Cancel on the form asks first - and on a confirmed order whose
        jobs are being made, offers the amendment instead: Cancel followed by Set
        to Quotation was how an order got corrected, and it stopped the work.
        Only from the button (its context says so): code that cancels an order
        means it."""
        if self.env.context.get('lab_offer_amend') and len(self) == 1 \
                and self.state in ('draft', 'sent', 'sale'):
            return {
                'type': 'ir.actions.act_window', 'name': _('Cancel Order'),
                'res_model': 'lab.order.cancel.choice', 'view_mode': 'form',
                'target': 'new', 'context': {'default_order_id': self.id},
            }
        return super().action_cancel()

    def action_confirm(self):
        amended = self.filtered('lab_amending')
        # what the order had before anybody confirms anything: a line deleted in
        # the amendment has left its job with only the order number on it
        for order in amended:
            order._lab_stop_jobs_of_removed_lines()
        res = super().action_confirm()
        done = amended.filtered(lambda o: o.state == 'sale')
        for order in done.sudo():
            order._lab_amend_reprice_invoices()
            order.write({'lab_amending': False, 'lab_amending_by_id': False,
                         'lab_amending_at': False,
                         'lab_amended_on': fields.Datetime.now(),
                         'lab_amended_by_id': order.lab_amending_by_id.id or self.env.uid})
        return res

    def _lab_amend_reprice_invoices(self):
        """The prices changed in the amendment, carried to the invoice.

        A unit price can be changed while the order is amended even where the line
        is already invoiced (core locks it as soon as an invoice exists). A DRAFT
        invoice takes the new price, as the counter expects; a POSTED one is the
        books, and is left as it is - the order says which line differs, for
        accounts to settle with a credit note. (client, 2026-11-27: "On amending a
        SO unit price also to be edited")
        """
        self.ensure_one()
        repriced, differs = [], []
        for line in self.order_line.filtered(lambda l: not l.display_type):
            for move_line in line.invoice_lines.filtered(
                    lambda ml: ml.move_id.move_type == 'out_invoice'
                    and ml.move_id.state != 'cancel'):
                price = line.currency_id._convert(
                    line.price_unit, move_line.currency_id, line.company_id,
                    move_line.move_id.invoice_date or fields.Date.context_today(self)) \
                    if move_line.currency_id != line.currency_id else line.price_unit
                if move_line.currency_id.compare_amounts(move_line.price_unit, price) == 0:
                    continue
                if move_line.move_id.state == 'draft':
                    move_line.price_unit = price
                    repriced.append('%s: %s' % (move_line.move_id.name or _('draft'),
                                                line.product_id.display_name))
                else:
                    differs.append(_("%(inv)s bills %(product)s at %(old)s, the order now "
                                     "says %(new)s", inv=move_line.move_id.name,
                                     product=line.product_id.display_name,
                                     old=move_line.price_unit, new=price))
        if repriced:
            self.message_post(body=_("Amended prices carried to the draft invoice: %s.",
                                     ', '.join(repriced)))
        if differs:
            self.message_post(body=_(
                "The invoice is already posted and keeps its prices - %s. Issue a credit "
                "note or a new invoice for the difference.", '; '.join(differs)))

    def _lab_stop_jobs_of_removed_lines(self):
        """A line taken off the order in the amendment: its job, if still being
        made, is stopped - the lab is not to go on making what was taken off. A
        finished one is history. The delivery line of it goes the same way."""
        self.ensure_one()
        jobs = self._lab_live_productions()
        if not jobs:
            return
        lines = self.order_line
        stray = jobs.filtered(lambda j: j.state != 'done' and j.sale_line_id not in lines)
        if stray:
            names = ', '.join(stray.mapped('name'))
            stray.with_context(skip_activity=True).action_cancel()
            self.message_post(body=_(
                "Taken off the order in the amendment, so production of it was "
                "stopped: %s.", names))
        moves = self.sudo().picking_ids.move_ids.filtered(
            lambda m: not m.sale_line_id and m.state not in ('done', 'cancel'))
        if moves:
            moves._action_cancel()


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    @api.depends('order_id.lab_amending')
    def _compute_product_updatable(self):
        """Core freezes the product of a line once anything has been made or moved
        for it - which, on an order being amended, is every line, because nothing
        was cancelled. The amendment is exactly where the product may change: the
        job follows it when the order is confirmed again."""
        super()._compute_product_updatable()
        for line in self:
            if line.order_id.lab_amending and line.state in ('draft', 'sent') \
                    and not line.is_downpayment:
                line.product_updatable = True

    def _lab_live_jobs(self):
        if 'mrp.production' not in self.env or not self.ids:
            return self.env['mrp.production'] if 'mrp.production' in self.env else None
        return self.env['mrp.production'].sudo().search(
            [('sale_line_id', 'in', self.ids), ('state', '!=', 'cancel')])

    def _action_launch_stock_rule(self, **kwargs):
        """An amended order confirmed again: a line that already has a job keeps
        it. Core would read a changed product as a line nothing has been made for
        yet, and raise a second job and a second delivery beside the first."""
        amended = self.filtered(
            lambda l: l.order_id.lab_amending and l.state == 'sale' and not l.display_type)
        kept = self.browse()
        for line in amended:
            if line._lab_live_jobs():
                line._lab_job_follows_line()
                kept |= line
        return super(SaleOrderLine, self - kept)._action_launch_stock_rule(**kwargs)

    def _lab_job_follows_line(self):
        """The job of this line, brought in step with what the line says now."""
        self.ensure_one()
        line = self.sudo()
        order = line.order_id
        product = line.product_id
        said = []
        for job in line._lab_live_jobs():
            if job.state == 'done':
                if job.product_id != product:
                    said.append(_(
                        "%(job)s was finished as %(old)s and is left as it is.",
                        job=job.name, old=job.product_id.display_name))
                continue
            if job.product_id != product:
                said.append(job._lab_become(product))
            if job.product_qty != line.product_uom_qty and line.product_uom_qty > 0:
                was = job.product_qty
                job._lab_take_quantity(line.product_uom_qty)
                said.append(_("%(job)s: quantity %(was)s -> %(now)s.", job=job.name,
                              was=was, now=line.product_uom_qty))
        # the delivery of it: what has not gone out yet says what the line says
        moves = line.move_ids.filtered(
            lambda m: m.state not in ('done', 'cancel') and m.location_dest_usage == 'customer')
        moves.filtered(lambda m: m.product_id != product).write(
            {'product_id': product.id, 'uom_id': product.uom_id.id})
        if len(moves) == 1:
            gone = sum(line.move_ids.filtered(
                lambda m: m.state == 'done' and m.location_dest_usage == 'customer'
            ).mapped('quantity'))
            left = line.product_uom_qty - gone
            if left > 0 and moves.product_uom_qty != left:
                moves.product_uom_qty = left
        for text in filter(None, said):
            order.message_post(body=_("Amendment: %s", text))
        return True


class MrpProduction(models.Model):
    _inherit = 'mrp.production'

    def _lab_step_touched(self, step):
        """Has anybody put a hand to this step - taken it, been given it, finished
        it, clocked time on it? The stamps are the station board's, where it is
        installed."""
        if step.state in ('progress', 'done') or step.duration:
            return True
        return any(step[name] for name in (
            'accepted_at', 'handed_over_at', 'bench_user_id', 'finisher_user_id')
            if name in step._fields)

    def _lab_become(self, product):
        """This job, still being made, becomes `product`: the order line it is made
        for now says so. Returns what to tell the order.

        What has been done on it stays: a step somebody has taken or finished is
        not touched. The steps nobody has touched are the plan, not the work, and
        the plan is the new product's (`_lab_relay_steps`).
        """
        self.ensure_one()
        job = self.sudo()
        old = job.product_id
        bom = self.env['mrp.bom'].sudo()._bom_find(
            product, company_id=job.company_id.id, bom_type='normal')[product]
        if not bom:
            return _("%(job)s is left as %(old)s: %(new)s has no bill of materials, so "
                     "there are no steps to make it by.", job=job.name,
                     old=old.display_name, new=product.display_name)
        if job.state == 'draft':
            # not on the floor yet: core lays a draft's steps from its product
            job.write({'product_id': product.id})
            return _("%(job)s is now %(new)s (was %(old)s).", job=job.name,
                     new=product.display_name, old=old.display_name)
        # Core drops `product_id` from a write once a job is confirmed, and there is
        # no other door: written straight, then everything computed from it told.
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE mrp_production SET product_id = %s, uom_id = %s WHERE id = %s",
            (product.id, product.uom_id.id, job.id))
        job.invalidate_recordset(['product_id', 'uom_id'])
        job.modified(['product_id', 'uom_id'])
        job.move_finished_ids.filtered(
            lambda m: m.state not in ('done', 'cancel') and m.product_id == old
        ).write({'product_id': product.id, 'uom_id': product.uom_id.id})
        how = job._lab_relay_steps(bom)
        job.message_post(body=_(
            "The order was amended: this job is now %(new)s (was %(old)s). %(how)s",
            new=product.display_name, old=old.display_name, how=how))
        return _("%(job)s is now %(new)s (was %(old)s). %(how)s", job=job.name,
                 new=product.display_name, old=old.display_name, how=how)

    def _lab_relay_steps(self, bom):
        """Lay the steps nobody has touched on `bom`'s route. Returns what was done.

        The route is read as benches, in order. The same benches in the same order:
        nothing moves, the steps only point at the new product's operations. A
        different route: every bench the job has already been through (or is at)
        is done with; of the rest of the new route, a waiting step already standing
        at a bench is kept, a bench with none gets one, and a waiting step at a
        bench the new route does not pass is taken off. They follow the last step
        somebody touched, in the new route's order.

        A two-arch job is two chains of the same steps: its steps are left as they
        are and the board is told, rather than one chain being re-laid over two.
        """
        self.ensure_one()
        job = self.sudo()
        Workorder = self.env['mrp.workorder'].sudo()
        order_of = lambda w: (w.sequence, w.id)                        # noqa: E731
        operations = bom.operation_ids.sorted(lambda o: (o.sequence, o.id))
        steps = job.workorder_ids.filtered(lambda w: w.state != 'cancel').sorted(order_of)
        first_at = {}
        for operation in operations:
            first_at.setdefault(operation.workcenter_id.id, operation)

        def point(records):
            for step in records:
                operation = first_at.get(step.workcenter_id.id)
                if operation and step.operation_id != operation:
                    step.operation_id = operation

        two_arch = 'arch' in Workorder._fields and any(steps.mapped('arch'))
        same = [w.workcenter_id.id for w in steps] == [o.workcenter_id.id for o in operations]
        if same or two_arch or not operations:
            point(steps)
            job.bom_id = bom
            if same:
                return _("Its steps are the same and stay as they are.")
            return _("Its steps were left as they are - re-lay them on the board if "
                     "the new product is made differently.")
        touched = steps.filtered(self._lab_step_touched)
        waiting = steps - touched
        been = set(touched.workcenter_id.ids)
        plan = [op for op in operations if op.workcenter_id.id not in been]
        keep = Workorder
        slot = {}
        for operation in plan:
            standing = (waiting - keep).filtered(
                lambda w, op=operation: w.workcenter_id == op.workcenter_id)[:1]
            if standing:
                keep |= standing
                slot[operation.id] = standing
        gone = waiting - keep
        gone_names = gone.mapped('name')
        gone.unlink()
        # numbered after the last step somebody touched, one apart: no two steps of
        # a job may share a number (core renumbers the whole job when they do)
        base = max((touched | keep).mapped('sequence'), default=0)
        added = []
        for index, operation in enumerate(plan, start=1):
            standing = slot.get(operation.id)
            if standing:
                standing.write({'sequence': base + index, 'operation_id': operation.id})
        for index, operation in enumerate(plan, start=1):
            if operation.id in slot:
                continue
            Workorder.create({
                'name': operation.name, 'production_id': job.id,
                'workcenter_id': operation.workcenter_id.id, 'operation_id': operation.id,
                'sequence': base + index, 'state': 'blocked',
            })
            added.append(operation.name)
        point(touched)
        job.bom_id = bom
        if job.state != 'draft':
            # each step waits for the one before it, by number, again
            job.workorder_ids._action_confirm()
        if 'is_outdated_bom' in job._fields:
            job.is_outdated_bom = False
        parts = [_("What was done on it stays.")]
        if gone_names:
            parts.append(_("Steps not needed any more, taken off: %s.", ', '.join(gone_names)))
        if added:
            parts.append(_("Steps added: %s.", ', '.join(added)))
        return ' '.join(parts)

    def _lab_take_quantity(self, quantity):
        """The job takes the line's quantity - core's own Change Quantity, which
        moves the finished and component moves and the steps' expected times."""
        self.ensure_one()
        job = self.sudo()
        if job.state == 'draft':
            job.product_qty = quantity
            return
        self.env['change.production.qty'].sudo().create(
            {'mo_id': job.id, 'product_qty': quantity}).change_prod_qty()
