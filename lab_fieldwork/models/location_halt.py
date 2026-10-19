# -*- coding: utf-8 -*-
"""A stop that was not a clinic.

The trail shows where the phone went; a halt says where it stayed. Ten minutes still
at a tea shop is a break, forty minutes still by the roadside is a breakdown or a
phone left in a bag, and either is worth a glance on the live map — but neither is a
visit, so halts inside a clinic's radius (or at the lab) are never written.

Detected from the trail itself (see `lab.location.ping._detect_halts`), keyed by
when they began, so re-reading a day never doubles them. The executive may add a
note ("flat tyre"); nobody edits the times.
"""
from odoo import api, fields, models


class LabLocationHalt(models.Model):
    _name = 'lab.location.halt'
    _description = 'Field Halt (stop that was not a clinic)'
    _order = 'started_at desc'
    _rec_name = 'display_name'

    user_id = fields.Many2one('res.users', string='Executive', required=True,
                              index=True, ondelete='cascade')
    date = fields.Date(required=True, index=True)
    started_at = fields.Datetime(required=True)
    ended_at = fields.Datetime(help="Empty while the phone is still there.")
    minutes = fields.Integer(compute='_compute_minutes', store=True)
    latitude = fields.Float(digits=(10, 7), required=True)
    longitude = fields.Float(digits=(10, 7), required=True)
    place = fields.Char(help="The nearest clinic of the day when one is within 500 m.")
    note = fields.Char('Reason', help="What the executive says it was.")
    company_id = fields.Many2one('res.company', required=True, index=True,
                                 default=lambda self: self.env.company)

    _halt_unique = models.Constraint(
        'unique(user_id, started_at)', 'That halt is already recorded.')

    @api.depends('started_at', 'ended_at')
    def _compute_minutes(self):
        now = fields.Datetime.now()
        for halt in self:
            end = halt.ended_at or now
            halt.minutes = int(max(0.0, (end - halt.started_at).total_seconds()) // 60) \
                if halt.started_at else 0

    @api.depends('user_id', 'started_at', 'minutes', 'place')
    def _compute_display_name(self):
        for halt in self:
            when = fields.Datetime.context_timestamp(halt, halt.started_at).strftime('%H:%M') \
                if halt.started_at else ''
            halt.display_name = '%s · %s min at %s%s' % (
                halt.user_id.name, halt.minutes, when,
                ' near %s' % halt.place if halt.place else '')
