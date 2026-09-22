# -*- coding: utf-8 -*-
"""Cover: who signs a desk while its manager is away.

The two-stage approval is the point of the role split, and it is also its single
point of failure. There is exactly one Operational Manager and one Marketing
Manager; the day either of them is on leave, every executive's day sheet stops
where it stands, the SLA runs, the escalation cron warns about a queue nobody can
clear, and the weekly report the administrator reads says the field went quiet.

A cover is that manager's own answer to it, arranged in advance: *this person
signs my desk between these dates, and here is why*. It grants nothing permanent
and it is not a role — the stand-in never gains the group, never sees the desk
after the last day, and every signature they make is stamped with their own name,
so the audit trail says who actually signed rather than who was supposed to.

Deliberately NOT record-rule magic: the approval guards ask this model directly
(``_covers(user, desk)``), so a cover can only ever widen the one thing it is
meant to widen — the right to sign — and never quietly widen what somebody can
read. (client, 2026-08-31)
"""
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.fields import Domain
from odoo.tools import format_date

DESKS = [
    ('ops', 'Operational Desk'),
    ('marketing', 'Marketing Desk'),
]

DESK_GROUP = {
    'ops': 'lab_fieldwork.group_fieldwork_ops_manager',
    'marketing': 'lab_fieldwork.group_fieldwork_marketing_manager',
}


class LabDeskCover(models.Model):
    _name = 'lab.desk.cover'
    _description = 'Field Work Desk Cover'
    _order = 'date_from desc, id desc'
    _inherit = ['mail.thread']

    manager_id = fields.Many2one(
        'res.users', string='Manager', required=True, tracking=True,
        default=lambda self: self.env.user,
        help="Whose desk is being covered.")
    delegate_id = fields.Many2one(
        'res.users', string='Covered By', required=True, tracking=True,
        help="Who signs it while they are away. They do not gain the role - only "
             "the right to sign this desk, only between these dates.")
    desk = fields.Selection(DESKS, required=True, default='ops', tracking=True)
    date_from = fields.Date(required=True, tracking=True,
                            default=fields.Date.context_today)
    date_to = fields.Date(required=True, tracking=True,
                          default=fields.Date.context_today)
    reason = fields.Char(tracking=True, help="Leave, travel, training - said once "
                                             "so the weekly report can say it too.")
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        'res.company', required=True, default=lambda self: self.env.company)

    state = fields.Selection(
        [('scheduled', 'Scheduled'), ('running', 'Running'), ('over', 'Finished')],
        compute='_compute_state', search='_search_state',
        help="Computed from the dates - a cover is never switched on by hand, "
             "which is how one gets left on.")
    signed_count = fields.Integer(
        compute='_compute_signed_count',
        help="Sheets this stand-in actually signed on the covered desk.")

    _dates_ordered = models.Constraint(
        'CHECK(date_to >= date_from)',
        'A cover cannot end before it starts.')

    # Not stored. It compares the dates with TODAY, and nothing on the record
    # changes when a day arrives, so a stored value stayed 'Scheduled' for ever
    # and the "Running today" filter never found a cover. (2026-09-15)
    @api.depends('date_from', 'date_to')
    def _compute_state(self):
        today = fields.Date.context_today(self)
        for cover in self:
            if not cover.date_from or not cover.date_to:
                cover.state = 'scheduled'
            elif cover.date_to < today:
                cover.state = 'over'
            elif cover.date_from > today:
                cover.state = 'scheduled'
            else:
                cover.state = 'running'

    def _search_state(self, operator, value):
        if operator not in ('in', 'not in'):
            return NotImplemented
        today = fields.Date.context_today(self)
        by_state = {
            'scheduled': Domain('date_from', '>', today),
            'running': Domain('date_from', '<=', today) & Domain('date_to', '>=', today),
            'over': Domain('date_to', '<', today),
        }
        domain = Domain.OR(by_state[v] for v in value if v in by_state)
        return domain if operator == 'in' else ~domain

    def _compute_signed_count(self):
        Sheet = self.env['lab.daily.update'].sudo()
        for cover in self:
            if not (cover.delegate_id and cover.date_from and cover.date_to):
                cover.signed_count = 0
                continue
            signer, stamp = (('ops_approved_by_id', 'ops_approved_at')
                             if cover.desk == 'ops'
                             else ('approved_by_id', 'approved_at'))
            cover.signed_count = Sheet.search_count([
                (signer, '=', cover.delegate_id.id),
                (stamp, '>=', fields.Datetime.to_datetime(cover.date_from)),
                (stamp, '<', fields.Datetime.to_datetime(
                    cover.date_to + timedelta(days=1))),
            ])

    @api.constrains('manager_id', 'delegate_id', 'desk')
    def _check_delegate(self):
        for cover in self:
            if cover.manager_id == cover.delegate_id:
                raise ValidationError(_(
                    "A manager cannot cover their own desk - that is just the "
                    "ordinary day."))
            group = self.env.ref(DESK_GROUP[cover.desk], raise_if_not_found=False)
            if group and cover.manager_id not in group.sudo().all_user_ids:
                raise ValidationError(_(
                    "%(name)s does not hold the %(desk)s, so there is nothing "
                    "there to cover.",
                    name=cover.manager_id.name,
                    desk=dict(DESKS)[cover.desk]))

    def _check_arranger(self, vals_list):
        """Who may be named on a cover, checked on create as well as write.

        Only write() used to ask, so a manager could CREATE a cover naming
        another manager's desk with themselves as the stand-in and then sign
        both stages of a day sheet alone. (2026-09-15)
        """
        if self.env.su:
            return
        me = self.env.user
        admin = me.has_group('lab_fieldwork.group_fieldwork_admin')
        for vals in vals_list:
            if not admin and 'manager_id' in vals and vals['manager_id'] != me.id:
                raise UserError(_("You can only arrange cover for your own desk."))
            if vals.get('delegate_id') == me.id:
                raise UserError(_(
                    "You cannot name yourself to cover a desk. Cover is arranged "
                    "by the manager who is going away."))

    @api.model_create_multi
    def create(self, vals_list):
        self._check_arranger(vals_list)
        covers = super().create(vals_list)
        for cover in covers:
            # In their inbox: a cover somebody arranged for you and never
            # mentioned is a cover you do not know you are working.
            cover.message_subscribe(
                partner_ids=(cover.manager_id | cover.delegate_id).partner_id.ids)
            cover.message_notify(
                partner_ids=cover.delegate_id.partner_id.ids,
                subject=_("You are covering a field work desk"),
                body=_("%(who)s asked you to sign the %(desk)s from %(a)s to "
                       "%(b)s.%(why)s",
                       who=cover.manager_id.name, desk=dict(DESKS)[cover.desk],
                       a=cover.date_from, b=cover.date_to,
                       why=(" (%s)" % cover.reason) if cover.reason else ""))
        return covers

    def write(self, vals):
        # Own or administer: a manager arranges cover for their own desk, the
        # administrator can arrange anyone's. Enforced here as well as by the
        # record rule, because a rule that is switched off on this database
        # would otherwise silently let anybody sign anybody's desk.
        if not self.env.su and not self.env.user.has_group(
                'lab_fieldwork.group_fieldwork_admin'):
            foreign = self.filtered(lambda c: c.manager_id != self.env.user)
            if foreign:
                raise UserError(_("You can only arrange cover for your own desk."))
        self._check_arranger([vals])
        return super().write(vals)

    # ------------------------------------------------------------------- the API
    @api.model
    def _covers(self, user, desk):
        """Is `user` signing `desk` today on somebody's behalf?

        Sudo: an ops manager standing in for marketing must be recognised
        without being able to read the marketing desk's covers first.
        """
        today = fields.Date.context_today(self)
        return bool(self.sudo().search_count([
            ('delegate_id', '=', user.id), ('desk', '=', desk),
            ('date_from', '<=', today), ('date_to', '>=', today),
        ]))

    @api.model
    def _cover_banner(self, desk):
        """What to say at the top of a desk somebody is standing in on."""
        today = fields.Date.context_today(self)
        cover = self.sudo().search([
            ('delegate_id', '=', self.env.uid), ('desk', '=', desk),
            ('date_from', '<=', today), ('date_to', '>=', today),
        ], limit=1)
        if not cover:
            return False
        return {
            'manager': cover.manager_id.name,
            # Said the way the rest of the desk says dates, not as an ISO string.
            'until': format_date(self.env, cover.date_to, date_format='d MMM'),
            'reason': cover.reason or '',
        }

    @api.model
    def _absent_desks(self):
        """Desks whose own manager is away today with nobody standing in.

        The administrator's screen leads with this: a desk with no one at it is
        the failure that stops the whole field, and it is invisible from any
        queue - the sheets simply sit there looking patient.
        """
        today = fields.Date.context_today(self)
        out = []
        for key, label in DESKS:
            group = self.env.ref(DESK_GROUP[key], raise_if_not_found=False)
            holders = group.sudo().all_user_ids.filtered('active') if group else \
                self.env['res.users']
            if not holders:
                out.append({'desk': key, 'label': label, 'why': 'nobody',
                            'detail': _("No one has this role.")})
                continue
            away = self.sudo().search([
                ('manager_id', 'in', holders.ids), ('date_from', '<=', today),
                ('date_to', '>=', today)])
            present = holders - away.mapped('manager_id')
            if not present and not away.mapped('delegate_id'):
                out.append({'desk': key, 'label': label, 'why': 'uncovered',
                            'detail': _("The manager is away and no one is "
                                        "covering.")})
        return out
