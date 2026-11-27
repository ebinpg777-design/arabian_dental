# -*- coding: utf-8 -*-
"""Starting a case again, from the first bench.

An appliance that comes back ill-fitting, or breaks on the bench, is not a new case —
it is the same case done again. The lab had no way to say that: a job could be pushed
back to Wire Bending one work order at a time, which left every earlier operation still
marked finished, the timers still counting the first attempt, and nothing anywhere
recording WHY it went round twice.

So a redo is one act. It resets every operation to the start, stamps the case as having
been redone, and records the reason from the lab's own list — which is a list of things
that go wrong on a bench, not of who is to blame. Blame is the commercial question and
`lab_rework` already answers it, on a rework SALES order.

The old timers are deliberately kept. They are a true record of work that really
happened, and a lab that wants to know what redos cost needs the first attempt to still
be counted. A redone job therefore shows the time of both attempts, which is the honest
figure. (client, 2026-08-27)
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError

# A case can be redone from any stage up to and including 'to_close' — finished on the
# bench but not yet posted. Once the stock moves are done Odoo's own `_compute_state`
# forces the order back to 'done' on every write, and reopening it means an unbuild;
# that case is a rework ORDER, which this lab already has.
REDOABLE = ('draft', 'confirmed', 'progress', 'to_close')


class LabRedoReason(models.Model):
    _name = 'lab.redo.reason'
    _description = 'Redo Reason'
    _order = 'sequence, id'

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    # Where the fault showed itself, when the reason names a bench. Lets the analysis
    # answer "which station are we redoing most", which is the question behind the list.
    workcenter_id = fields.Many2one('mrp.workcenter', string='Usually At')
    redo_count = fields.Integer(compute='_compute_redo_count', string='Times Used')

    def _compute_redo_count(self):
        counts = dict(self.env['lab.mrp.redo']._read_group(
            [('reason_id', 'in', self.ids)], ['reason_id'], ['__count']))
        for reason in self:
            reason.redo_count = counts.get(reason, 0)


class LabMrpRedo(models.Model):
    _name = 'lab.mrp.redo'
    _description = 'Redo'
    _order = 'date desc, id desc'
    _rec_name = 'production_id'

    production_id = fields.Many2one('mrp.production', string='Manufacturing Order',
                                    required=True, ondelete='cascade', index=True)
    reason_id = fields.Many2one('lab.redo.reason', string='Reason', required=True,
                                index=True)
    note = fields.Char('What happened')
    date = fields.Datetime('Redone On', required=True, index=True,
                           default=fields.Datetime.now)
    user_id = fields.Many2one('res.users', string='Redone By', required=True,
                              default=lambda s: s.env.user)
    # Where the case had got to when it was sent back. The whole point of the list:
    # a case redone at Polishing cost the lab every bench before it.
    stage_id = fields.Many2one('mrp.workcenter', string='Sent Back From')
    operations_reset = fields.Integer('Operations Reset')
    attempt = fields.Integer('Attempt', help="1 is the first redo of this case.")

    # Carried for the analysis, so the list reads without opening anything.
    sale_id = fields.Many2one('sale.order', related='production_id.sale_id',
                              store=True, string='Sales Order')
    partner_id = fields.Many2one('res.partner', related='production_id.sale_id.partner_id',
                                 store=True, string='Clinic')
    team_id = fields.Many2one('crm.team', related='production_id.sale_id.team_id',
                              store=True, string='Sales Route')
    product_id = fields.Many2one('product.product', related='production_id.product_id',
                                 store=True, string='Appliance')
    patient = fields.Char(related='production_id.sale_id.patient', store=True)
    company_id = fields.Many2one('res.company', related='production_id.company_id',
                                 store=True)


class MrpProductionRedo(models.Model):
    _inherit = 'mrp.production'

    lab_redo_ids = fields.One2many('lab.mrp.redo', 'production_id', string='Redos')
    lab_redo_count = fields.Integer(compute='_compute_lab_redo',
                                      store=True, string='Times Redone')
    # Stored so the floor sheets and the boards can flag a redone case without a join
    # per row, and so it can be searched and grouped.
    lab_is_redo = fields.Boolean(compute='_compute_lab_redo', store=True,
                                   string='Redone', index='btree_not_null')
    lab_redo_reason_id = fields.Many2one('lab.redo.reason', compute='_compute_lab_redo',
                                           store=True, string='Last Redo Reason')
    # A FINISHED case done again is a new job (see `lab_redo_as_new`): each points
    # at the other. (client, 2026-11-27)
    lab_redo_of_id = fields.Many2one(
        'mrp.production', 'Remake Of', copy=False, readonly=True, index='btree_not_null',
        help="The finished job this one makes again.")
    lab_remake_ids = fields.One2many('mrp.production', 'lab_redo_of_id', string='Remakes')

    @api.depends('lab_redo_ids')
    def _compute_lab_redo(self):
        for production in self:
            redos = production.lab_redo_ids.sorted('date')
            production.lab_redo_count = len(redos)
            production.lab_is_redo = bool(redos)
            production.lab_redo_reason_id = redos[-1].reason_id if redos else False

    # ------------------------------------------------------------------ the act
    def action_lab_redo(self):
        """Ask why, then start the case again."""
        self.ensure_one()
        self._check_redoable()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Start %s again', self.name),
            'res_model': 'lab.mrp.redo.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_production_id': self.id},
        }

    def _check_redoable(self):
        for production in self:
            if production.state not in REDOABLE:
                raise UserError(_(
                    "%(name)s is %(state)s, so its operations cannot be started again.\n\n"
                    "A case that has already been produced and delivered comes back as a "
                    "rework order — tick Rework on a new order and point it at this one — "
                    "because redoing this record would have to unpick the stock it has "
                    "already posted.",
                    name=production.display_name, state=production.state))

    def lab_redo(self, reason, note=False):
        """Reset every operation to the start and record why.

        Returns the redo log entry. The reset is deliberately total: an appliance that
        is ill-fitting at Polishing is wrong from the wire up, and leaving the earlier
        benches marked finished is how a case goes back out with the same fault.
        """
        self.ensure_one()
        self._check_redoable()
        if not reason:
            raise UserError(_("Say why it is being done again."))

        workorders = self.workorder_ids.sorted(lambda w: (w.sequence, w.id))
        # Where it had got to — the last bench that finished something, or the one it
        # is sitting at now.
        finished = workorders.filtered(lambda w: w.state == 'done')
        stage = (finished[-1].workcenter_id if finished
                 else workorders[:1].workcenter_id)

        for workorder in workorders:
            workorder._lab_reset_for_redo()

        redo = self.env['lab.mrp.redo'].create({
            'production_id': self.id,
            'reason_id': reason.id if hasattr(reason, 'id') else int(reason),
            'note': note or False,
            'stage_id': stage.id if stage else False,
            'operations_reset': len(workorders),
            'attempt': self.lab_redo_count + 1,
        })
        self.invalidate_recordset(['lab_redo_count', 'lab_is_redo',
                                   'lab_redo_reason_id'])
        self.message_post(body=_(
            "Started again from the first bench — %(reason)s.%(note)s "
            "%(count)s operation(s) reset%(stage)s.",
            reason=redo.reason_id.name,
            note=(' “%s”' % note) if note else '',
            count=len(workorders),
            stage=(', sent back from %s' % stage.display_name) if stage else ''))
        return redo

    def lab_redo_as_new(self, reason, note=False):
        """Do a FINISHED case again: a new job for the same order line, from the
        first bench. Returns (new job, redo log entry).

        A finished job cannot be reset in place - its finished product has already
        been posted to stock, and Odoo forces such a job back to done on every
        write. So the remake is a job of its own: the same product, bill of
        materials and quantity, raised for the same order line, every step waiting
        at the first bench. The finished job is left exactly as it was - it is the
        record of the first attempt - and each points at the other. The remake
        carries the redo, so it shows as done again on every board.
        (client, 2026-11-27: "Able to do the rework of finished MOs")
        """
        self.ensure_one()
        if self.state != 'done':
            raise UserError(_("%s is not finished: start it again where it stands.", self.name))
        if not reason:
            raise UserError(_("Say why it is being done again."))
        finished = self.workorder_ids.filtered(lambda w: w.state == 'done').sorted(
            lambda w: (w.sequence, w.id))
        stage = finished[-1:].workcenter_id
        # A FRESH job, not a copy: a copy carries the finished job's own steps -
        # the route as it was when it was made (benches since merged or renamed),
        # and both arches of a two-arch case already split, which the confirm then
        # splits again. A new job is laid out from the product's route as it is now.
        product = self.product_id
        bom = self.env['mrp.bom']._bom_find(
            product, company_id=self.company_id.id, bom_type='normal')[product] \
            or self.bom_id
        remake = self.create({
            'product_id': product.id,
            'product_qty': self.product_qty,
            'uom_id': self.uom_id.id,
            'bom_id': bom.id or False,
            'company_id': self.company_id.id,
            'picking_type_id': self.picking_type_id.id,
            'origin': self.origin or self.name,
            'sale_line_id': self.sale_line_id.id or False,
            'lab_redo_of_id': self.id,
        })
        if not remake.workorder_ids:
            # a job whose steps were laid by hand, with no route to lay them from:
            # the finished job's own steps, waiting again
            Workorder = self.env['mrp.workorder']
            for step in self.workorder_ids.filtered(lambda w: w.state != 'cancel').sorted(
                    lambda w: (w.sequence, w.id)):
                Workorder.create({
                    'name': step.name, 'production_id': remake.id,
                    'workcenter_id': step.workcenter_id.id,
                    'operation_id': step.operation_id.id or False,
                    'sequence': step.sequence,
                    'duration_expected': step.duration_expected,
                    **({'arch': step.arch} if 'arch' in step._fields and step.arch else {}),
                })
        if remake.state == 'draft':
            remake.action_confirm()
        redo = self.env['lab.mrp.redo'].create({
            'production_id': remake.id,
            'reason_id': reason.id if hasattr(reason, 'id') else int(reason),
            'note': note or False,
            'stage_id': stage.id if stage else False,
            'operations_reset': len(remake.workorder_ids),
            'attempt': self.lab_redo_count + 1,
        })
        remake.invalidate_recordset(['lab_redo_count', 'lab_is_redo', 'lab_redo_reason_id'])
        why = redo.reason_id.name + ((' “%s”' % note) if note else '')
        self.message_post(body=_("Finished, and made again as %(new)s - %(why)s.",
                                 new=remake.name, why=why))
        remake.message_post(body=_(
            "Makes %(old)s again, from the first bench - %(why)s. %(count)s operation(s).",
            old=self.name, why=why, count=len(remake.workorder_ids)))
        order = self.sale_id if 'sale_id' in self._fields else False
        if order:
            order.sudo().message_post(body=_(
                "%(old)s was finished and is being made again as %(new)s - %(why)s.",
                old=self.name, new=remake.name, why=why), subtype_xmlid='mail.mt_note')
        return remake, redo

    def action_view_redos(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': _('Redos'),
            'res_model': 'lab.mrp.redo', 'view_mode': 'list,form',
            'domain': [('production_id', '=', self.id)],
            'context': {'create': False},
        }


class MrpWorkorderRedo(models.Model):
    _inherit = 'mrp.workorder'

    def _lab_reset_for_redo(self):
        """Put this operation back to the start.

        The productivity records are NOT deleted: the work really happened and a lab
        measuring what redos cost needs the first attempt to still count. What is
        cleared is everything that says the operation is FINISHED or that somebody is
        carrying it.
        """
        self.ensure_one()
        if self.state == 'cancel':
            return False
        # A timer still running would keep counting into the second attempt at a bench
        # nobody is standing at.
        # end_all: button_pending closes only the current user's timer, and the one
        # still running is whoever was at the bench. (review, 2026-09-15)
        if self.time_ids.filtered(lambda t: not t.date_end):
            self.end_all()
        # State FIRST, in its own write. Core refuses a change to `qty_produced` while
        # the operation is still in done or cancel (mrp_workorder.py:496), and it reads
        # the state as it stands at the start of the write — so both in one dict is
        # refused however they are ordered in the dict. (client, 2026-08-27)
        self.write({'state': 'ready'})
        self.write({
            'qty_produced': 0.0,
            'accepted_at': False,
            'accepted_by_id': False,
            'handed_over_at': False,
            'handed_over_by_id': False,
            'bench_user_id': False,
            'finisher_user_id': False,
            'bench_assigned_at': False,
            'bench_assigned_by_id': False,
            'handed_from_workcenter_id': False,
            'handed_from_user_id': False,
        })
        return True
