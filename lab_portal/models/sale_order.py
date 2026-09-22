# -*- coding: utf-8 -*-
from odoo import _, api, fields, models

# What a doctor is actually asking when they ring: has it started, is it made, has it
# left. Five stages, not the eleven the lab runs internally — a customer-facing stage
# that changes eight times in a morning is noise, and a doctor cannot act on any of it.
STAGES = [
    ('registered', 'Registered'),
    ('in_lab', 'In the lab'),
    ('ready', 'Ready'),
    ('sent', 'On its way'),
    ('delivered', 'Delivered'),
    ('cancel', 'Cancelled'),
]
STAGE_ORDER = ['registered', 'in_lab', 'ready', 'sent', 'delivered']


class SaleOrder(models.Model):
    """The case, as the doctor who sent it sees it."""
    _inherit = 'sale.order'

    portal_stage = fields.Selection(
        STAGES, compute='_compute_portal_stage', string='Case Stage')
    portal_progress = fields.Integer(compute='_compute_portal_stage')
    portal_operation = fields.Char(
        compute='_compute_portal_stage', string='Currently')

    # `mrp_production_ids` is not searchable, so Odoo cannot build a recompute trigger
    # through it and warns at load. This field is NOT stored — it is computed each time
    # the portal page is rendered — so the dependency was never what kept it right.
    # Listing only what Odoo can actually trigger on keeps the declaration honest.
    @api.depends('state', 'picking_ids.state')
    def _compute_portal_stage(self):
        for order in self:
            stage, operation = order._portal_stage()
            order.portal_stage = stage
            order.portal_operation = operation
            order.portal_progress = int(
                (STAGE_ORDER.index(stage) + 1) / len(STAGE_ORDER) * 100
            ) if stage in STAGE_ORDER else 0

    def _portal_stage(self):
        """Derived from what the lab actually did, never typed.

        A stage somebody has to remember to update is a stage that is wrong by Thursday,
        and a doctor who is told the wrong thing twice stops looking and rings instead.

        Read with sudo, and only here. A portal user has no access to `mrp.production`
        or `stock.picking` — rightly — so computing this as themselves raised AccessError
        and the whole page 403'd. The STAGE is information the lab chooses to publish;
        the records it is derived from are not. `self` is already an order this user was
        allowed to open, so nothing is widened by reading its own children.
        """
        self.ensure_one()
        if self.state == 'cancel':
            return 'cancel', ''

        order = self.sudo()
        pickings = order.picking_ids.filtered(lambda p: p.state != 'cancel')
        if pickings and all(p.state == 'done' for p in pickings):
            return 'delivered', ''
        if any(p.state in ('assigned', 'confirmed', 'waiting') for p in pickings) \
                and order._all_made():
            return 'sent', _('Packed and on its way to you')

        productions = order.mrp_production_ids.filtered(lambda m: m.state != 'cancel')
        if productions:
            if order._all_made():
                return 'ready', _('Finished — waiting to be sent')
            running = productions.filtered(lambda m: m.state == 'progress')
            current = running.workorder_ids.filtered(
                lambda w: w.state == 'progress')[:1]
            if current:
                # The station is named on purpose. It is the single most common question
                # a doctor rings about, and "in the lab" alone does not answer it.
                return 'in_lab', _('At %s', current.workcenter_id.display_name)
            return 'in_lab', _('Queued in the lab')

        if self.state in ('sale', 'done'):
            return 'in_lab', _('Accepted — starting shortly')
        return 'registered', _('Received by the lab')

    def _all_made(self):
        self.ensure_one()
        made = self.sudo().mrp_production_ids.filtered(lambda m: m.state != 'cancel')
        return bool(made) and all(m.state == 'done' for m in made)

    # ------------------------------------------------------------------ detail
    def portal_case_lines(self):
        """The appliances, in the lab's own language."""
        self.ensure_one()
        labels = dict(self.env['sale.order.line'].fields_get(
            ['ul'])['ul'].get('selection') or []) if 'ul' in \
            self.env['sale.order.line']._fields else {}
        rows = []
        for line in self.order_line.filtered(lambda l: not l.display_type):
            rows.append({
                'name': line.product_id.display_name or line.name,
                'qty': line.product_uom_qty,
                'ul': labels.get(line.ul, '') if 'ul' in line._fields else '',
                'colour': line.color_scheme.name if 'color_scheme' in line._fields
                and line.color_scheme else '',
            })
        return rows

    # ------------------------------------------------------------------ the lab floor
    def portal_productions(self):
        """Each manufacturing order behind this case, with its own track.

        The flat step list answers "how far along is my case"; this answers "which of
        the three appliances I sent is the one still sitting somewhere", which is the
        question a doctor with a split case actually has. One order can raise several
        manufacturing orders, and collapsing them into a single progress bar hides the
        one that has not moved since Tuesday.

        Read with sudo for the same reason as the stage: a portal user cannot read
        `mrp.production`, and must not be able to. What is published here is the
        appliance, where it is and whether it is finished — never who worked on it, how
        long it took, or what it cost.
        """
        self.ensure_one()
        out = []
        productions = self.sudo().mrp_production_ids.filtered(
            lambda m: m.state != 'cancel').sorted('id')
        for mo in productions:
            workorders = mo.workorder_ids.filtered(
                lambda w: w.state != 'cancel').sorted(lambda w: (w.sequence, w.id))
            steps, done = [], 0
            for wo in workorders:
                state = ('done' if wo.state == 'done'
                         else 'progress' if wo.state == 'progress' else 'todo')
                done += 1 if state == 'done' else 0
                steps.append({
                    'name': wo.workcenter_id.display_name or wo.name,
                    'state': state,
                })
            if mo.state == 'done':
                progress = 100
            elif steps:
                progress = int(done / len(steps) * 100)
            else:
                progress = 0
            current = next((s['name'] for s in steps if s['state'] == 'progress'), '')
            out.append({
                'name': mo.name,
                'product': mo.product_id.display_name or '',
                'qty': mo.product_qty,
                'done': mo.state == 'done',
                'progress': progress,
                'current': current,
                'steps': steps,
                'step_count': len(steps),
                'steps_done': done,
            })
        return out

    def portal_promise(self):
        """When the lab said it would be there, and whether that still holds.

        Deliberately not a live countdown to the hour: a date the doctor can plan an
        appointment around is useful, a ticking clock is pressure without information.
        """
        self.ensure_one()
        # sudo for the same reason as the stage: `expected_date` is computed from the
        # order lines' lead times and reading it walks into `product.product`, which a
        # portal user has no access to — and must not have. The promised DATE is
        # published; the product records it is derived from are not.
        order = self.sudo()
        promised = order.commitment_date or order.expected_date
        if not promised:
            return {}
        promised_date = fields.Datetime.to_datetime(promised).date()
        today = fields.Date.context_today(self)
        delivered = self.portal_stage == 'delivered'
        days = (promised_date - today).days
        return {
            'date': promised_date,
            'days': days,
            'delivered': delivered,
            # "Late" only means something while it is still coming.
            'late': (not delivered) and days < 0,
            'soon': (not delivered) and 0 <= days <= 2,
        }

    def portal_case_steps(self):
        """The operations, as a track the doctor can follow.

        Only whether each step is done, running or still to come — no durations, no
        technician names. A customer view is not a window into how the lab is staffed.
        """
        self.ensure_one()
        steps = []
        # Same reasoning as the stage: the step NAMES are published, the work orders are
        # not readable by a portal user.
        for wo in self.sudo().mrp_production_ids.filtered(
                lambda m: m.state != 'cancel').mapped('workorder_ids').sorted(
                    lambda w: (w.production_id.id, w.sequence, w.id)):
            if wo.state == 'cancel':
                continue
            steps.append({
                'name': wo.workcenter_id.display_name or wo.name,
                'state': 'done' if wo.state == 'done'
                else 'progress' if wo.state == 'progress' else 'todo',
            })
        return steps
