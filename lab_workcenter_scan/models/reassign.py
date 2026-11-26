# -*- coding: utf-8 -*-
"""A job passed from one technician to another, and a job that is waiting.

REASSIGNED. A technician takes a job and cannot finish it properly - away the next
day, the work not up to standard, more in hand than one person can do. The lead
gives it to somebody else. Until now the only way to say so was Correct, which
exists for a wrong name tapped at a scan: it wrote the new name OVER the old one,
and the fact that two people had worked on the job was gone from every screen.
A reassignment keeps both - who worked on it first, who has it now, who decided
and why - and every screen that names a job's technician says so.
(client, 2026-11-26: "if any technician working on work order but he couldn't
finish it properly then lead can reassign to another technician ... show the
technician initially worked and reassigned person")

PENDING. A job that cannot go on for now - the doctor has to answer, a try-in has
not come back, a material is out. The bench says so, with the reason, and the case
wears it until work on it goes on. (client, 2026-11-26: "option to make a work to
Pending status by production team ... add note for reason")
"""
from odoo import _, api, fields, models
from odoo.fields import Command
from odoo.exceptions import AccessError, UserError

from .mrp_workorder import CLOSED

REASSIGN_REASONS = [
    ('quality', 'Work not up to standard'),
    ('unavailable', 'Technician not available'),
    ('load', 'Too much work in hand'),
    ('skill', 'Needs a more experienced hand'),
    ('other', 'Other'),
]


class LabWorkorderReassign(models.Model):
    """One reassignment of one job: from whom, to whom, by whom, when and why."""
    _name = 'lab.workorder.reassign'
    _description = 'Work Order Reassignment'
    _order = 'date desc, id desc'
    _rec_name = 'workorder_id'

    workorder_id = fields.Many2one(
        'mrp.workorder', 'Work Order', required=True, ondelete='cascade', index=True)
    production_id = fields.Many2one(
        related='workorder_id.production_id', store=True, index=True)
    # The bench it happened at: a work order sent elsewhere afterwards takes its
    # `workcenter_id` with it, and this row must still say where.
    workcenter_id = fields.Many2one('mrp.workcenter', 'Work Centre', readonly=True)
    from_user_ids = fields.Many2many(
        'res.users', 'lab_wo_reassign_from_rel', 'reassign_id', 'user_id',
        string='Taken From', readonly=True)
    to_user_ids = fields.Many2many(
        'res.users', 'lab_wo_reassign_to_rel', 'reassign_id', 'user_id',
        string='Given To', readonly=True)
    reason = fields.Selection(REASSIGN_REASONS, required=True, default='other', readonly=True)
    note = fields.Char(readonly=True)
    user_id = fields.Many2one('res.users', 'Reassigned By', readonly=True)
    date = fields.Datetime('Reassigned On', readonly=True, index=True)
    # Since when the people it was taken from had it - what Technician Day draws
    # their part of the job from.
    since = fields.Datetime('Had It Since', readonly=True)
    reason_label = fields.Char('Why', compute='_compute_reason_label')

    @api.depends('reason', 'note')
    def _compute_reason_label(self):
        labels = dict(self._fields['reason']._description_selection(self.env))
        for row in self:
            why = labels.get(row.reason, '') if row.reason != 'other' else ''
            row.reason_label = ' - '.join(filter(None, [why, row.note])) or labels.get('other')


class MrpWorkorderReassign(models.Model):
    _inherit = 'mrp.workorder'

    reassign_ids = fields.One2many(
        'lab.workorder.reassign', 'workorder_id', string='Reassignments', readonly=True)
    reassigned = fields.Boolean(
        'Reassigned', copy=False, readonly=True, index='btree_not_null',
        help="This job was passed from the technician who first worked on it to "
             "somebody else at the bench.")
    initial_user_ids = fields.Many2many(
        'res.users', 'mrp_workorder_initial_user_rel', 'workorder_id', 'user_id',
        string='First Worked By', copy=False, readonly=True,
        help="Who worked on this job before it was reassigned.")
    reassign_note = fields.Char('Reassigned Because', compute='_compute_reassign_note')

    @api.depends('reassigned', 'reassign_ids.reason', 'reassign_ids.note', 'reassign_ids.date')
    def _compute_reassign_note(self):
        for wo in self:
            # the newest, by its own date: the relation is not kept in order
            # while rows are being added to it
            wo.reassign_note = wo._last_reassignment().reason_label if wo.reassigned else False

    def _last_reassignment(self):
        self.ensure_one()
        return self.reassign_ids.sorted(lambda r: (r.date, r.id))[-1:]

    def write(self, vals):
        # "First worked by" is a fact about this attempt at this bench, like the
        # names themselves: a job that loses its technician (sent to another bench,
        # started again) starts clean. The history rows stay - they are the record.
        if 'bench_user_id' in vals and not vals['bench_user_id']:
            vals = dict(vals, reassigned=False, initial_user_ids=[Command.clear()])
        # A job that moves on is no longer waiting: taking it at a bench, or
        # handing it to the next, is the bench saying work has gone on.
        moved = vals.get('accepted_at') or vals.get('handed_over_at')
        result = super().write(vals)
        if moved:
            self.production_id.filtered('lab_pending')._lab_resume(
                _("work went on at %s", ', '.join(
                    self.workcenter_id.sudo().mapped('display_name'))))
        return result

    def _bench_people_label(self):
        """The names on the job as every screen should say them: who has it, and -
        where it was reassigned - who worked on it first."""
        self.ensure_one()
        names = ', '.join(self._bench_people().mapped('name'))
        first = self.reassigned and self.initial_user_ids.sorted('name')
        if first:
            return _("%(now)s (first: %(first)s)", now=names or _('nobody'),
                     first=', '.join(first.mapped('name')))
        return names

    def action_reassign_bench_team(self, user_ids, reason='other', note=''):
        """Pass a job somebody is working on to somebody else at the bench.

        The lead's call, like giving work out. What it adds to that: the people
        who had the job are remembered as having worked on it first, and the
        reassignment - from, to, by, when, why - is kept on the job for good.
        The job counts for whoever has it now.
        """
        ids = []
        for uid in user_ids or []:
            if uid and int(uid) not in ids:
                ids.append(int(uid))
        if not ids:
            raise UserError(_("Choose who takes the job over."))
        if reason not in dict(REASSIGN_REASONS):
            raise UserError(_("Choose why the job is being reassigned."))
        note = (note or '').strip()
        if reason == 'other' and not note:
            raise UserError(_("Say why the job is being reassigned."))
        people = self.env['res.users'].browse(ids)
        lead, helpers = people[:1], people[1:]
        now = fields.Datetime.now()
        why = ' - '.join(filter(None, [
            dict(self.env['lab.workorder.reassign']._fields['reason']
                 ._description_selection(self.env)).get(reason) if reason != 'other' else '',
            note]))
        for wo in self:
            wo._check_bench_access(_('reassign work'), leads_only=True)
            if wo.state in CLOSED:
                raise UserError(_("%s is finished.", wo.display_name))
            before = wo._bench_people()
            if not before:
                raise UserError(_(
                    "Nobody is named on %s yet, so there is nothing to reassign. "
                    "Give it out instead.", wo.display_name))
            workcenter = wo.workcenter_id
            for user in people:
                if workcenter.users and user not in workcenter.users \
                        and user not in workcenter.head_user_ids:
                    raise UserError(_(
                        "%(who)s does not work at %(wc)s. Add them to the work centre "
                        "first.", who=user.name, wc=workcenter.display_name))
            if wo.bench_user_id == lead and wo.bench_helper_ids == helpers:
                raise UserError(_(
                    "%(wo)s is already with %(who)s.", wo=wo.display_name,
                    who=', '.join(people.mapped('name'))))
            last = wo._last_reassignment()
            self.env['lab.workorder.reassign'].sudo().create({
                'workorder_id': wo.id,
                'workcenter_id': workcenter.id,
                'from_user_ids': [Command.set(before.ids)],
                'to_user_ids': [Command.set(people.ids)],
                'reason': reason,
                'note': note or False,
                'user_id': self.env.uid,
                'date': now,
                'since': last.date or wo.accepted_at or wo.bench_assigned_at or wo.date_start,
            })
            vals = {
                'bench_user_id': lead.id,
                'bench_helper_ids': [Command.set(helpers.ids)],
                'bench_assigned_at': now,
                'bench_assigned_by_id': self.env.uid,
                'reassigned': True,
            }
            if not wo.initial_user_ids:
                vals['initial_user_ids'] = [Command.set(before.ids)]
            wo.write(vals)
            if 'user_id' in wo._fields:
                wo.user_id = lead.id
            if wo.production_id:
                wo.production_id.message_post(body=_(
                    "%(wo)s at %(wc)s reassigned from %(old)s to %(new)s by %(by)s: "
                    "%(why)s.", wo=wo.display_name, wc=workcenter.display_name,
                    old=', '.join(before.mapped('name')),
                    new=', '.join(people.mapped('name')),
                    by=self.env.user.name, why=why))
            changed = (before | people) - (before & people)
            if changed and (wo.accepted_at or wo.handed_over_at):
                wo._recount_credit_targets(changed)
        return True


class MrpProductionPending(models.Model):
    _inherit = 'mrp.production'

    lab_pending = fields.Boolean(
        'Pending', copy=False, readonly=True, tracking=True, index='btree_not_null',
        help="Work on this job cannot go on for now. It stays so until somebody "
             "resumes it, or the job is taken or handed on at a bench.")
    lab_pending_reason = fields.Text('Pending Because', copy=False, readonly=True)
    lab_pending_by_id = fields.Many2one(
        'res.users', 'Marked Pending By', copy=False, readonly=True)
    lab_pending_at = fields.Datetime('Pending Since', copy=False, readonly=True)

    def _check_pending_role(self):
        if self.env.su or self.env.user.has_group('mrp.group_mrp_user'):
            return
        raise AccessError(_("Only the production team can mark a job pending."))

    def _lab_order_note(self, body):
        """Said on the order as well: the office follows the order, not the job."""
        for mo in self:
            order = mo.sudo().sale_id if 'sale_id' in mo._fields else False
            if order:
                order.message_post(body=body, subtype_xmlid='mail.mt_note')

    def action_lab_pending(self, reason):
        """Mark the job as waiting, and say what for. Marking one that is already
        pending replaces the reason: what it waits for has changed."""
        self._check_pending_role()
        reason = (reason or '').strip()
        if not reason:
            raise UserError(_("Say why the job is pending."))
        for mo in self.sudo():
            if mo.state in ('done', 'cancel'):
                raise UserError(_("%s is closed.", mo.name))
            mo.write({
                'lab_pending': True, 'lab_pending_reason': reason,
                'lab_pending_by_id': self.env.uid,
                'lab_pending_at': fields.Datetime.now(),
            })
            body = _("%(job)s marked PENDING by %(user)s: %(reason)s",
                     job=mo.name, user=self.env.user.name, reason=reason)
            mo.message_post(body=body)
            mo._lab_order_note(body)
        return True

    def action_lab_resume(self):
        """Work goes on."""
        self._check_pending_role()
        self._lab_resume(_("resumed by %s", self.env.user.name))
        return True

    def _lab_resume(self, how):
        for mo in self.sudo().filtered('lab_pending'):
            was = mo.lab_pending_reason or ''
            mo.write({'lab_pending': False, 'lab_pending_reason': False,
                      'lab_pending_by_id': False, 'lab_pending_at': False})
            body = _("%(job)s no longer pending (%(how)s). It was waiting for: %(reason)s",
                     job=mo.name, how=how, reason=was or _('(not written)'))
            mo.message_post(body=body)
            mo._lab_order_note(body)

    def action_lab_pending_ask(self):
        """From the job's own form: ask what it is waiting for."""
        self.ensure_one()
        self._check_pending_role()
        return {
            'type': 'ir.actions.act_window', 'name': _('Mark Pending'),
            'res_model': 'lab.pending.wizard', 'view_mode': 'form', 'target': 'new',
            'context': {'default_production_id': self.id,
                        'default_reason': self.lab_pending_reason or False},
        }

    def action_cancel(self):
        res = super().action_cancel()
        self.sudo().filtered('lab_pending').write({
            'lab_pending': False, 'lab_pending_reason': False,
            'lab_pending_by_id': False, 'lab_pending_at': False})
        return res

    def button_mark_done(self):
        res = super().button_mark_done()
        self.sudo().filtered(lambda m: m.lab_pending and m.state == 'done').write({
            'lab_pending': False, 'lab_pending_reason': False,
            'lab_pending_by_id': False, 'lab_pending_at': False})
        return res


class LabPendingWizard(models.TransientModel):
    """Marking a job pending asks for the one thing that explains it."""
    _name = 'lab.pending.wizard'
    _description = 'Mark a Job Pending'

    production_id = fields.Many2one('mrp.production', required=True, readonly=True)
    reason = fields.Text('What is it waiting for', required=True)

    def action_confirm(self):
        self.ensure_one()
        self.production_id.action_lab_pending(self.reason)
        return {'type': 'ir.actions.act_window_close'}
