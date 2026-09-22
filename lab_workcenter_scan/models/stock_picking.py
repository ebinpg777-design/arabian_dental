# -*- coding: utf-8 -*-
"""Close the manufacturing order when its appliance actually goes out.

The lab's manufacturing orders never got closed. 20,978 of them sit open against cases
the doctor has had for months, because nothing ever connected "the box left" to "the job
is finished" — so every production figure the lab reads is inflated roughly tenfold and
every floor sheet has to filter the ghosts out to tell the truth.

This is the connection. Validating a delivery closes the manufacturing orders for the
appliances on it, and only those.

Two rules it will not break:

MATCHED BY PRODUCT, NOT BY ORDER. A case with two appliances where one ships today must
not close the one still on the bench. Closing per order is exactly the mistake that put
113 unmade cases on the "delivered" list before this was written.

MATCHED BY QUANTITY. Two identical appliances on one order, one delivered, closes one —
the older. Anything else quietly finishes work nobody has done.
"""
import time
from collections import defaultdict

import psycopg2

import odoo.modules.module

from odoo import _, api, fields, models

# Concurrency failures Odoo's request layer retries for us; they must never be caught.
SERIALIZATION_ERRORS = (psycopg2.errors.SerializationFailure,
                        psycopg2.errors.DeadlockDetected,
                        psycopg2.extensions.TransactionRollbackError)

# What a job may be in when a delivery closes it. 'draft' is deliberately absent: an
# unconfirmed order is not work in progress, and finishing one would invent a history.
CLOSABLE = ('confirmed', 'progress', 'to_close')

AUTO_CLOSE_PARAM = 'lab_workcenter_scan.close_mo_on_delivery'


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    def _action_done(self):
        """Hooked here, not on button_validate: this is the one place every path to a
        finished delivery passes through — the button, a backorder wizard, a scanner, an
        API call — so no route out of the building can miss it."""
        res = super()._action_done()
        self._lab_close_delivered_productions()
        return res

    def _lab_close_delivered_productions(self):
        going_out = self.filtered(
            lambda p: p.state == 'done'
            and p.picking_type_id.code == 'outgoing'
            and p.sale_id)
        if not going_out or not self._lab_auto_close_enabled():
            return
        # One order may be delivered by several pickings; do its arithmetic once.
        for order in going_out.sudo().mapped('sale_id'):
            self.env['mrp.production']._lab_close_for_order(order)

    @api.model
    def _lab_auto_close_enabled(self):
        # OFF unless someone switches it on. A job is finished when the bench
        # finishes it, not when the box leaves the building or the invoice is
        # raised: validating a delivery was marking MOs completed that the floor
        # had never closed. (client, 2026-09-17)
        param = self.env['ir.config_parameter'].sudo().get_param(AUTO_CLOSE_PARAM, '0')
        return str(param).strip().lower() not in ('0', 'false', 'no', 'off')


class MrpProduction(models.Model):
    _inherit = 'mrp.production'

    # A job the automatic close could not finish — it needs a serial number, or an
    # answer only a person can give. Flagged rather than retried: the backlog runs
    # oldest-first, so one permanently stuck job would otherwise be picked at the head
    # of every batch for ever, and the scheduled action would grind without ever
    # reaching the 20,000 behind it. (client, 2026-08-26)
    lab_close_failed = fields.Boolean(
        'Auto-close Failed', readonly=True, copy=False, index='btree_not_null',
        help="Set when a delivery could not close this job automatically. Clear it to "
             "let the scheduled catch-up try again.")

    def action_clear_close_failed(self):
        """Let the catch-up try this job again.

        The flag is a note that a person is needed, not a verdict: once whatever it was
        waiting for has been sorted out — usually a serial number — clearing it puts the
        job back in the sweep. (client, 2026-08-27)
        """
        self.sudo().write({'lab_close_failed': False})
        return True

    def _lab_close_for_order(self, order):
        """Close the jobs on this order whose appliance has actually been delivered.

        Runs under sudo: a delivery is validated by a storeman who has no business
        writing to manufacturing orders, and refusing to close the job because of who
        happened to press Validate is how the backlog started.
        """
        order = order.sudo()
        Production = self.env['mrp.production'].sudo()
        open_jobs = Production.search(
            [('sale_id', '=', order.id), ('state', 'in', CLOSABLE)],
            order='date_start, id')
        if not open_jobs:
            return Production

        # How much of each appliance has actually left the building.
        #
        # `quantity` on a DONE move, never `product_uom_qty`: the latter is what was
        # ORDERED. Validate-with-no-backorder leaves undelivered moves sitting on a done
        # picking, and counting their demand would close the job for an appliance that
        # is still on the bench — the precise failure this whole rule exists to avoid.
        #
        # A RETURN comes back on an incoming picking and must be subtracted, or an
        # appliance the doctor sent back still counts as delivered and its job is
        # closed as finished work.
        delivered = defaultdict(float)
        for picking in order.picking_ids.filtered(lambda p: p.state == 'done'):
            direction = {'outgoing': 1.0, 'incoming': -1.0}.get(
                picking.picking_type_id.code, 0.0)
            if not direction:
                continue
            for move in picking.move_ids.filtered(lambda m: m.state == 'done'):
                delivered[move.product_id.id] += direction * move.quantity

        # What earlier closings already accounted for, so re-validating a second
        # picking on the same order cannot close the same appliance twice.
        for job in Production.search([('sale_id', '=', order.id),
                                      ('state', '=', 'done')]):
            delivered[job.product_id.id] -= job.product_qty

        # When the case actually went out. `button_mark_done` stamps date_finished with
        # NOW, which is right for work finished today and a lie for a backlog delivered
        # months ago — and the flow board's "finished today" reads that column, so a
        # catch-up run would report 21,000 appliances finished this afternoon.
        shipped_on = max(
            (p.date_done for p in order.picking_ids
             if p.state == 'done' and p.picking_type_id.code == 'outgoing'
             and p.date_done),
            default=False)

        closed = Production
        for job in open_jobs:
            outstanding = delivered.get(job.product_id.id, 0.0)
            if outstanding < job.product_qty:
                # Not a failure — the appliance genuinely has not gone out — but the
                # catch-up runs oldest-first over the same selection every time, so a
                # job that can never satisfy the quantity rule would sit at the head of
                # every batch for ever and the backlog behind it would never be reached.
                # Flagged so the sweep moves on; the delivery path still closes it the
                # moment the appliance actually ships. (client, 2026-08-27)
                if self.env.context.get('lab_backlog_sweep'):
                    job.sudo().lab_close_failed = True
                continue
            if job._lab_mark_done_quietly(finished_on=shipped_on):
                delivered[job.product_id.id] = outstanding - job.product_qty
                closed |= job
        return closed

    def _lab_mark_done_quietly(self, finished_on=False):
        """Finish one job without ever putting a dialog in a storeman's way.

        `button_mark_done` is interactive by design: rather than finishing, it can RETURN
        a wizard — asking about component consumption, about a backorder, about a serial
        number — and a caller that ignores the return value believes it closed a job that
        is still open. The two skip flags are Odoo's own way of saying "no questions"
        (mrp_production.py:1771 and :1822), and the state is checked afterwards because a
        returned action is not an exception and would otherwise pass silently.

        A savepoint per job: one case that cannot be finished must not roll back the
        delivery that triggered this, nor the other jobs on the same order.
        """
        self.ensure_one()
        started_at = fields.Datetime.now()
        # `date_start` is the lab's ONLY per-job arrival date — every "days in lab"
        # figure on every floor sheet is measured from it — and marking a job done
        # overwrites it with the moment of the close. Kept and put back.
        # (client, 2026-08-27)
        arrived_at = self.date_start
        try:
            with self.env.cr.savepoint():
                self.with_context(
                    skip_consumption=True, skip_backorder=True,
                    # Without this, a successful close still returns a print or
                    # reception-report ACTION whenever an auto-print flag is ticked on
                    # the operation type, or the validating user holds the reception
                    # report group. Harmless now that the state is the test, but there
                    # is no reason to build a report nobody asked for.
                    skip_redirection=True,
                ).button_mark_done()
                # THE STATE IS THE ANSWER, not the return value.
                #
                # `button_mark_done` writes state='done' and only then decides what to
                # hand back (mrp_production.py:2249-2255), and on a complete success it
                # can still return an action dict rather than True. Treating that as a
                # failure rolled back a close that had genuinely worked, logged it as
                # broken, and flagged the job so the catch-up would never try it again.
                self.invalidate_recordset(['state'])
                if self.state != 'done':
                    raise UserWarning(
                        _("it needs an answer that only a person can give"))
                # Dated when the appliance actually left, not when this ran.
                restore = {}
                if finished_on and finished_on < fields.Datetime.now():
                    restore['date_finished'] = finished_on
                if arrived_at and self.date_start != arrived_at:
                    restore['date_start'] = arrived_at
                if restore:
                    # force_date, or the restore undoes the close it just made.
                    #
                    # Core refuses `date_start` on a job that is done or cancelled
                    # (mrp_production.py:1044) - and by this line the job IS done,
                    # because the close above worked. The UserError was caught by
                    # the handler below, which rolled the savepoint back: the job
                    # came out open again, flagged as needing a person, and the
                    # sweep never touched it after that. 219 delivered cases were
                    # closed and re-opened by this line on 2026-08-28, every one of
                    # them logged with that same message. The context key is core's
                    # own way of saying "this is a correction, not a reschedule".
                    # (client, 2026-09-09)
                    self.sudo().with_context(force_date=True).write(restore)
                # Remove the productive time this close just invented.
                #
                # `button_mark_done` finishes the work orders, and finishing one closes
                # its timer — creating a mrp.workcenter.productivity row per operation,
                # attributed to whoever ran the close. On a catch-up over 21,000 orders
                # that is ~95,000 fabricated "Time Tracking: OdooBot" records against
                # work nobody did, and they land in exactly the productivity figures
                # this module was built to report. A close that never happened at a
                # bench must leave no trace of having happened there. (client, 2026-08-27)
                invented = self.workorder_ids.sudo().time_ids.filtered(
                    lambda t: t.create_uid.id == self.env.uid
                    and t.create_date >= started_at)
                if invented:
                    invented.unlink()
                # Inside the savepoint: a mail-layer failure here would otherwise
                # escape and roll back the delivery that triggered all this.
                self.message_post(body=_(
                    "Closed automatically: this appliance has been delivered to the "
                    "doctor."))
        except SERIALIZATION_ERRORS:
            # Odoo retries the whole request on these. Swallowing one turns a retryable
            # concurrency blip into a delivery that silently half-closed its jobs.
            raise
        except Exception as exc:  # noqa: BLE001 - one bad job must not stop the rest
            self.env['ir.logging'].sudo().create({
                'name': 'lab_workcenter_scan', 'type': 'server', 'level': 'WARNING',
                'dbname': self.env.cr.dbname,
                'message': 'Could not close %s after delivery: %s' % (self.name, exc),
                'path': 'stock_picking', 'func': '_lab_mark_done_quietly',
                'line': '0',
            })
            self.sudo().lab_close_failed = True
            return False
        if self.lab_close_failed:
            self.sudo().lab_close_failed = False
        return True

    @api.model
    def _lab_close_delivered_backlog(self, limit=None):
        """Work through the jobs whose case was delivered before this rule existed.

        Batched, and driven by a scheduled action rather than a button, because the
        measured cost is 1.3 seconds a job: the 20,978 waiting on this database are seven
        and a half hours of work, and a web request is dead after two minutes. Each run
        takes a bite and stops; when the backlog is gone every run costs one query.
        """
        if not self.env['stock.picking']._lab_auto_close_enabled():
            # One switch, both paths: turning the rule off must stop the scheduled
            # catch-up too, or "off" only means "off for new deliveries".
            return 0
        limit = limit or int(self.env['ir.config_parameter'].sudo().get_param(
            'lab_workcenter_scan.close_batch', 200))
        Floor = self.env['report.lab.floor']
        batch = self.sudo().search(
            Floor._closeout_domain() + [('state', 'in', CLOSABLE),
                                        ('lab_close_failed', '=', False)],
            order='date_start, id', limit=limit)
        if not batch:
            return 0
        # Commit each order as it is finished. Measured at 1.7 seconds a job, a batch of
        # 200 is a single six-minute transaction, and anything that interrupts it — a
        # worker recycle, a deploy, an admin stopping the job — throws away every close
        # it had made. Committing per order means the work already done is kept, and the
        # next run simply carries on from there.
        in_test = getattr(odoo.modules.module, 'current_test', False)
        deadline = self.env.context.get('cron_end_time')
        closed = 0
        sweep = self.with_context(lab_backlog_sweep=True)
        for order in batch.mapped('sale_id'):
            closed += len(sweep._lab_close_for_order(order))
            if not in_test:
                self.env.cr.commit()
                # Stopping should take effect in seconds, not at the end of the batch.
                #
                # MONOTONIC, not time.time(): `cron_end_time` is set from
                # `time.monotonic()` (ir_cron.py:485) — seconds of uptime, a number
                # around 1e5 — while wall-clock epoch is around 1.8e9. Comparing the two
                # made this break after the very first order, so a "batch of 200" closed
                # exactly one and the backlog would have taken months.
                if deadline and time.monotonic() > deadline:
                    break
        return closed
