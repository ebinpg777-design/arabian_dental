# -*- coding: utf-8 -*-
from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models

from .local_day import local_now


class LabTarget(models.Model):
    """A month's number for one executive.

    Achievement is measured, never typed: the moment a person can key in what they
    achieved, the target stops being a measurement and becomes a negotiation.
    """
    _name = 'lab.target'
    _description = 'Monthly Target'
    _order = 'month desc, user_id'
    _inherit = ['lab.lock.mixin', 'lab.own.record.mixin', 'mail.thread']
    _rec_name = 'display_name'

    _lock_states = ('closed',)
    _lock_exempt_fields = ('note',)
    _lock_bypass_groups = ('lab_fieldwork.group_fieldwork_manager',)

    user_id = fields.Many2one('res.users', string='Executive',
        domain="[('fw_is_field_person', '=', True)]", required=True,
                              index=True, tracking=True)
    month = fields.Date('Month', required=True, tracking=True,
                        default=lambda s: fields.Date.context_today(s).replace(day=1))
    state = fields.Selection(
        [('draft', 'Draft'), ('open', 'Running'), ('closed', 'Closed')],
        default='draft', required=True, tracking=True)

    goal_visits = fields.Integer('Visits', tracking=True)
    goal_orders = fields.Integer('Cases', tracking=True)
    goal_value = fields.Monetary('Case Value', currency_field='currency_id', tracking=True)
    goal_collect = fields.Monetary('Collection', currency_field='currency_id', tracking=True)

    done_visits = fields.Integer('Visits Done', compute='_compute_done', store=True)
    done_orders = fields.Integer('Cases Done', compute='_compute_done', store=True)
    done_value = fields.Monetary('Value Done', compute='_compute_done', store=True,
                                 currency_field='currency_id')
    done_collect = fields.Monetary('Collected', compute='_compute_done', store=True,
                                   currency_field='currency_id')
    progress = fields.Float('Achieved %', compute='_compute_done', store=True,
                            aggregator='avg')
    note = fields.Text()

    company_id = fields.Many2one('res.company', default=lambda s: s.env.company,
                                 required=True)
    currency_id = fields.Many2one(related='company_id.currency_id', readonly=True)

    _target_uniq = models.Constraint(
        'unique(user_id, month, company_id)',
        'This person already has a target for that month.')

    @api.depends('user_id', 'month', 'goal_value')
    def _compute_done(self):
        Visit = self.env['lab.visit']
        for t in self:
            start = t.month and t.month.replace(day=1)
            if not start or not t.user_id:
                t.done_visits = t.done_orders = 0
                t.done_value = t.done_collect = t.progress = 0.0
                continue
            end = start + relativedelta(months=1, days=-1)
            visits = Visit.search([
                ('user_id', '=', t.user_id.id), ('state', '=', 'done'),
                ('date', '>=', start), ('date', '<=', end)])
            t.done_visits = len(visits)
            t.done_orders = sum(visits.mapped('order_count'))
            t.done_value = sum(visits.mapped('order_value'))
            # Collections made without a visit count towards the same target:
            # the money reached the lab either way. (client, 2026-09-12)
            t.done_collect = sum(visits.mapped('collected')) + self.env[
                'lab.cash.collection']._collected_for(
                    [t.user_id.id], start, end).get(t.user_id.id, 0.0)
            # Value is the headline the lab is judged on, so it is the one that drives
            # the progress ring. Showing an average of four ratios would let a good
            # visit count hide a bad revenue month.
            t.progress = round(t.done_value / t.goal_value * 100, 1) if t.goal_value else 0.0

    DONE_FIELDS = ('done_visits', 'done_orders', 'done_value', 'done_collect',
                   'progress')

    def _refresh_done(self):
        """Recompute the measured figures of the targets still running.

        Stored, because the desks ORDER by progress - but a stored compute that
        depends on the executive and the month never hears about a visit
        closing or money coming in, so the ring read whatever it said the day
        the target was set. The running ones are recomputed before they are
        read and hourly by cron; a closed month keeps what it closed on.
        (2026-09-15)
        """
        live = self.sudo().filtered(lambda t: t.state in ('draft', 'open'))
        if live:
            for fname in self.DONE_FIELDS:
                live.env.add_to_compute(live._fields[fname], live)
            live._recompute_recordset(self.DONE_FIELDS)
        return live

    @api.model
    def _refresh_month(self, day=None):
        """Every running target of the month `day` falls in (default: now)."""
        start = (day or local_now(self.env).date()).replace(day=1)
        return self.sudo().search([
            ('month', '>=', start), ('month', '<', start + relativedelta(months=1)),
            ('state', 'in', ('draft', 'open'))])._refresh_done()

    @api.model
    def _cron_refresh_done(self):
        return len(self._refresh_month())

    @api.depends('user_id', 'month')
    def _compute_display_name(self):
        for t in self:
            t.display_name = "%s — %s" % (
                t.user_id.name or '', t.month and t.month.strftime('%b %Y') or '')

    def action_open(self):
        self.write({'state': 'open'})

    def action_close(self):
        self.write({'state': 'closed'})

    def action_draft(self):
        self.write({'state': 'draft'})

    def _period(self):
        self.ensure_one()
        start = self.month and self.month.replace(day=1)
        return start, (start + relativedelta(months=1)) if start else None

    def action_view_cases(self):
        """The cases behind the achieved number, so a target can be audited rather
        than argued about."""
        self.ensure_one()
        start, end = self._period()
        visits = self.env['lab.visit'].search([
            ('user_id', '=', self.user_id.id), ('state', '=', 'done'),
            ('date', '>=', start), ('date', '<', end)])
        return {
            'type': 'ir.actions.act_window', 'name': _('Cases this Month'),
            'res_model': 'sale.order', 'view_mode': 'list,form',
            'domain': [('id', 'in', visits.mapped('order_ids').ids)],
        }

    def action_view_performance(self):
        self.ensure_one()
        start, _end = self._period()
        return {
            'type': 'ir.actions.act_window', 'name': _('Performance'),
            'res_model': 'lab.performance', 'view_mode': 'list,form',
            'domain': [('user_id', '=', self.user_id.id), ('month', '=', start)],
        }

    def action_view_visits(self):
        self.ensure_one()
        start = self.month.replace(day=1)
        end = start + relativedelta(months=1, days=-1)
        return {
            'type': 'ir.actions.act_window', 'name': _('Visits'),
            'res_model': 'lab.visit', 'view_mode': 'list,form',
            'domain': [('user_id', '=', self.user_id.id),
                       ('date', '>=', start), ('date', '<=', end)],
        }
