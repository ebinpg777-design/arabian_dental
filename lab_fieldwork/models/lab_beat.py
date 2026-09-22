# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError

WEEKDAYS = [
    ('0', 'Monday'), ('1', 'Tuesday'), ('2', 'Wednesday'), ('3', 'Thursday'),
    ('4', 'Friday'), ('5', 'Saturday'), ('6', 'Sunday'),
]


class LabBeat(models.Model):
    """A named round of clinics and the day it is worked.

    This is the piece that keeps the rest of the module simple. Without it, an
    executive has to create a visit record every time they intend to go somewhere —
    which is data entry about an intention, the least valuable typing in the system and
    the first thing that stops happening in the field.

    With a beat, the round is described once by a manager and the week's visits are
    generated. The executive's job becomes reacting to a list, not building one.
    """
    _name = 'lab.beat'
    _description = 'Beat (Clinic Round)'
    _order = 'weekday, name'
    _inherit = ['lab.own.record.mixin', 'mail.thread']

    name = fields.Char(required=True, tracking=True)
    code = fields.Char(help="Short code shown on cards where the full name will not fit.")
    active = fields.Boolean(default=True)
    color = fields.Integer()

    user_id = fields.Many2one(
        'res.users', string='Executive',
        domain="[('fw_is_field_person', '=', True)]", tracking=True, index=True,
        default=lambda self: self.env.user,
        help="Whose round this is. Their planned visits come from here.")
    weekday = fields.Selection(
        WEEKDAYS, string='Worked On', required=True, default='0', tracking=True)
    partner_ids = fields.Many2many(
        'res.partner', 'lab_beat_partner_rel', 'beat_id', 'partner_id',
        string='Clinics', domain="[('lab_on_my_route', '=', True)]")

    clinic_count = fields.Integer(compute='_compute_stats', store=True)
    visit_count = fields.Integer(compute='_compute_visit_count')
    # How many clinics on this round have gone quiet. A beat that looks well-planned
    # can still be quietly failing, and this is the only place that shows it.
    at_risk_count = fields.Integer(compute='_compute_at_risk', string='Slipping')
    company_id = fields.Many2one(
        'res.company', default=lambda self: self.env.company, required=True)

    @api.depends('partner_ids')
    def _compute_stats(self):
        for beat in self:
            beat.clinic_count = len(beat.partner_ids)

    def _compute_visit_count(self):
        counts = dict(self.env['lab.visit']._read_group(
            [('beat_id', 'in', self.ids)], ['beat_id'], ['__count']))
        for beat in self:
            beat.visit_count = counts.get(beat, 0)

    def _compute_at_risk(self):
        """One coverage query for every beat on the page, not one per beat."""
        clinics = self.mapped('partner_ids')
        slipping = set()
        if clinics:
            slipping = set(self.env['lab.coverage'].search([
                ('partner_id', 'in', clinics.ids),
                ('status', 'in', ('overdue', 'at_risk', 'never'))]).mapped('partner_id').ids)
        for beat in self:
            beat.at_risk_count = len(slipping & set(beat.partner_ids.ids))

    def action_view_clinics(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': _('Clinics on this Beat'),
            'res_model': 'res.partner', 'view_mode': 'kanban,list,form',
            'domain': [('id', 'in', self.partner_ids.ids)],
            'context': {'default_is_clinic': True, 'default_is_company': True},
        }

    def action_view_coverage(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': _('Coverage on this Beat'),
            'res_model': 'lab.coverage', 'view_mode': 'list,form',
            'domain': [('partner_id', 'in', self.partner_ids.ids)],
            'context': {'search_default_g_status': 1},
        }

    def action_view_visits(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': _('Visits'),
            'res_model': 'lab.visit', 'view_mode': 'kanban,list,form',
            'domain': [('beat_id', '=', self.id)],
            'context': {'default_beat_id': self.id},
        }

    # ------------------------------------------------------------------ planning
    def action_plan(self, weeks=1):
        """Generate the planned visits for this beat.

        Idempotent by (beat, clinic, date): running it twice for the same week tops up
        anything missing instead of duplicating the round, because a manager who is
        unsure whether they already planned will press it again.
        """
        Visit = self.env['lab.visit']
        today = fields.Date.context_today(self)
        created = 0
        for beat in self:
            if not beat.partner_ids:
                raise UserError(_("Beat '%s' has no clinics to plan.", beat.name))
            if not beat.user_id:
                raise UserError(_("Beat '%s' has no executive.", beat.name))
            target = int(beat.weekday)
            for week in range(max(1, weeks)):
                # The next occurrence of this weekday, this week or later.
                delta = (target - today.weekday()) % 7 + week * 7
                date = fields.Date.add(today, days=delta)
                for clinic in beat.partner_ids:
                    exists = Visit.search_count([
                        ('beat_id', '=', beat.id),
                        ('partner_id', '=', clinic.id),
                        ('date', '=', date)])
                    if exists:
                        continue
                    Visit.create({
                        'beat_id': beat.id, 'partner_id': clinic.id,
                        'user_id': beat.user_id.id, 'date': date,
                    })
                    created += 1
        return self._notify(_("%s visit(s) planned.", created) if created
                            else _("Everything was already planned."))

    def _notify(self, message):
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {'message': message, 'type': 'success', 'sticky': False,
                       'next': {'type': 'ir.actions.act_window_close'}},
        }
