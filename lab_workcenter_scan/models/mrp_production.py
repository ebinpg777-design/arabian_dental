# -*- coding: utf-8 -*-
from odoo import _, api, fields, models


class MrpProduction(models.Model):
    """A job you can point a camera at.

    The station board's scan resolves a manufacturing reference, so the code printed on
    the job card IS the reference — no second numbering scheme for the bench to know
    about, and an existing job card with the MO number on it already works.
    """
    _inherit = 'mrp.production'

    qr_image = fields.Binary('Job QR', compute='_compute_qr_image')

    # TWO PIECES, TWO JOBS. A case sold as "UL" is one appliance in two
    # parts: the upper and the lower are bent, acrylised, trimmed and
    # polished separately, often days apart, and either can be remade
    # without the other. One chain of work orders made that impossible - the
    # bench could not hand on the upper while the lower was still at the
    # wire. The routing is therefore doubled into an UPPER chain and a LOWER
    # chain, banded so that walking one arch never steps into the other.
    #
    # A hundred is wider than any routing in this lab (the longest is six
    # steps) and keeps the two chains apart under every ordering that reads
    # (sequence, id). (client, 2026-09-12)
    ARCH_BAND = 100

    @api.depends('name')
    def _compute_qr_image(self):
        for mo in self:
            mo.qr_image = self.env['mrp.workcenter']._qr_png(mo.name) if mo.name else False

    def action_confirm(self):
        """Confirm, then give a two-arch case its second chain.

        AFTER the confirmation, deliberately. Core rebuilds `workorder_ids`
        from the routing only while the job is draft (mrp_production.py, the
        `if production.state != 'draft': continue` at the top of
        `_compute_workorder_ids`), so a chain added here cannot be collapsed
        or deleted by that recompute - which matters, because the two chains
        share their operations and that compute keys work orders by
        `operation_id`. (client, 2026-09-12)
        """
        res = super().action_confirm()
        self._lab_split_arches()
        return res

    def _lab_split_arches(self):
        """Turn one chain of steps into an Upper chain and a Lower chain.

        Only for a case the order says is UL, only once (a job that already
        carries an arch has been through here), and never for a case whose
        routing is empty - a job that is only a quantity has nothing to
        split. Re-runnable by construction, which is what lets the same
        method fix the jobs already on the floor.
        """
        Workorder = self.env['mrp.workorder']
        made = Workorder.browse()
        for production in self:
            if production.ul != 'ul':
                continue
            steps = production.workorder_ids.sorted(lambda w: (w.sequence, w.id))
            if not steps or any(step.arch for step in steps):
                continue
            steps.arch = 'upper'
            for step in steps:
                made |= Workorder.create({
                    'name': step.name,
                    'production_id': production.id,
                    'workcenter_id': step.workcenter_id.id,
                    'product_uom_id': step.product_uom_id.id,
                    'operation_id': step.operation_id.id or False,
                    'duration_expected': step.duration_expected,
                    'sequence': step.sequence + self.ARCH_BAND,
                    'state': step.state,
                    'arch': 'lower',
                })
        return made

    def action_print_job_labels(self):
        return self.env.ref(
            'lab_workcenter_scan.action_report_job_label').report_action(self)

    def _compute_state(self):
        """Keep a job out of "To Close" while any step is still on a bench.

        Core's rule is that a job with work orders is only ready to close once every
        one of them is done or cancelled — but it cannot always see them. Two of core's
        own computes write inside themselves (`mrp.workorder._compute_state` calls
        `write`), and `mrp.production.state` depends on `workorder_ids.state` while
        `mrp.workorder.production_state` is related back to `mrp.production.state`.
        That circle means `_compute_state` sometimes runs against a moment where
        `workorder_ids` reads empty, and the branch meant for jobs that HAVE no work
        orders fires instead:

            elif not production.workorder_ids and ... qty_producing >= product_qty:
                production.state = 'to_close'

        `qty_producing` is already the full quantity by then, because `button_start`
        sets it on the FIRST step (`if wo.qty_producing == 0: wo.qty_producing =
        wo.qty_remaining`). So accepting step one is enough to strand the whole job in
        "To Close" with four steps still to do, and the stored value never corrects
        itself because nothing dirties it again.

        The station reads that state to decide what a bench may pick up, so a job that
        claims to be finished drops out of scanning altogether. Rather than reorder
        core's computes, the invariant is restated here: with work orders present and
        any of them still open, the job is not closeable. (client, 2026-08-29)

        The steps are counted in SQL when the relation reads empty. An earlier version
        of this asked `production.workorder_ids` — the very relation whose blank reading
        causes the misfire — so it went blind at exactly the moment it was needed and
        two jobs still reached the floor stranded. (client, 2026-09-09)

        And no step closes a job either, not even the last one. In this lab a case is
        closed by its DELIVERY - the picking marks its jobs done when it goes out, and
        the backlog cron does the same for what went out before that rule existed. So
        "To Close" was a state nothing here ever moved a job out of: the bench had
        finished, the case had not left the building, and the job sat in a limbo the
        scanning board treats as finished and will not show. A job with steps therefore
        never reads To Close; it stays in progress until the delivery closes it.
        (client, 2026-09-09)
        """
        super()._compute_state()
        stranded = self.filtered(
            lambda p: p.state == 'to_close' and isinstance(p.id, int))
        if not stranded:
            return
        steps = stranded._lab_step_states()
        for production in stranded:
            states = steps.get(production.id)
            # No steps at all is core's other branch and none of our business: a job
            # that is only a quantity is closeable the moment the quantity is made.
            if not states:
                continue
            production.state = production._lab_open_state(states)

    def _lab_step_states(self):
        """The state of every step of these jobs, seen past the empty relation.

        `workorder_ids` is authoritative when it holds anything — it can carry states
        this transaction has not written down yet. It is only when it reads blank that
        the rows are counted in SQL, because a job whose steps have vanished from the
        relation is precisely the case the caller exists to catch.
        """
        by_job, blank = {}, []
        for production in self:
            workorders = production.workorder_ids
            if workorders:
                by_job[production.id] = workorders.mapped('state')
            else:
                blank.append(production.id)
        if blank:
            self.env.cr.execute(
                "SELECT production_id, state FROM mrp_workorder "
                "WHERE production_id IN %s", (tuple(blank),))
            for job_id, state in self.env.cr.fetchall():
                by_job.setdefault(job_id, []).append(state)
        return by_job

    def _lab_open_state(self, states):
        """The state a job with unfinished steps should be reading.

        Core's own test for "work has begun", so a corrected job lands on the state it
        would have had if the branch had never misfired.
        """
        self.ensure_one()
        uom = self.product_uom_id
        if (any(s in ('progress', 'done') for s in states)
                or (uom and not uom.is_zero(self.qty_producing))
                or any(self.move_raw_ids.mapped('picked'))):
            return 'progress'
        return 'confirmed'

    @api.model
    def _lab_repair_premature_to_close(self, job_ids=None):
        """Put back any job stored as "To Close" while a step is still open.

        The compute above closes the hole for jobs it is asked about, but a job already
        stranded is stranded for good: nothing dirties `state` again, and until it is
        rewritten the floor sees a finished job it cannot work on. So the correction is
        also available as a plain pass over the stored rows — run by the scan, and by
        the migration, where everything has been written down and there is no compute
        to be blinded by.

        Returns the jobs it moved.
        """
        Production = self.env['mrp.production'].sudo()
        domain = [('state', '=', 'to_close')]
        if job_ids is not None:
            if not job_ids:
                return Production.browse()
            domain.append(('id', 'in', list(job_ids)))
        stranded = Production.search(domain)
        moved = Production.browse()
        for production in stranded:
            states = production.workorder_ids.mapped('state')
            # Steps all finished counts too: a job with steps is never To Close in
            # this lab, whichever step was the last. (client, 2026-09-09)
            if not states:
                continue
            corrected = production._lab_open_state(states)
            # Written, not assigned: `state` is stored-computed, and the point of this
            # pass is that no recompute is coming to save the row.
            production.write({'state': corrected})
            moved |= production
        return moved


class MrpProductionTracking(models.Model):
    """Everything this job is connected to, reachable from the job.

    A manufacturing order sits in the middle of the chain — the order that sold it, the
    visit that collected it, the clinic it goes back to, the operations that make it —
    and until now none of those were one click away in either direction.
    """
    _inherit = 'mrp.production'

    workorder_open_count = fields.Integer(compute='_compute_tracking')
    # A BOOLEAN, not a Many2one to lab.visit. Declaring the comodel would make this
    # manufacturing module require the field-work app — `Field ... with unknown
    # comodel_name` at load if it is absent. The button resolves the record when it is
    # pressed, so the link exists when the field app does and simply is not shown when
    # it does not.
    has_field_visit = fields.Boolean(compute='_compute_tracking')
    clinic_id = fields.Many2one(
        'res.partner', compute='_compute_tracking', string='Clinic')

    def _compute_tracking(self):
        for mo in self:
            order = mo.sale_id if 'sale_id' in mo._fields else False
            mo.workorder_open_count = len(mo.workorder_ids.filtered(
                lambda w: w.state not in ('done', 'cancel')))
            mo.has_field_visit = bool(
                order and 'visit_id' in order._fields and order.visit_id)
            mo.clinic_id = order.partner_id if order else False

    def _field_visit(self):
        self.ensure_one()
        order = self.sale_id if 'sale_id' in self._fields else False
        if order and 'visit_id' in order._fields:
            return order.visit_id
        return None

    def action_view_open_workorders(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': _('Operations Left'),
            'res_model': 'mrp.workorder', 'view_mode': 'list,form',
            'domain': [('production_id', '=', self.id),
                       ('state', 'not in', ('done', 'cancel'))],
        }

    def action_view_visit(self):
        self.ensure_one()
        visit = self._field_visit()
        if not visit:
            return False
        return {
            'type': 'ir.actions.act_window', 'res_model': visit._name,
            'res_id': visit.id, 'view_mode': 'form',
        }

    def action_view_clinic(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'res_model': 'res.partner',
            'res_id': self.clinic_id.id, 'view_mode': 'form',
        }
