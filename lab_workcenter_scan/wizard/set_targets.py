# -*- coding: utf-8 -*-
"""Set a day's targets for a whole bench in one screen.

Typing a number per person per day into a list view is the kind of chore that
gets done for a week and then abandoned, so the targets stop meaning anything.
This opens with the bench's people already listed, last set target filled in, and
one Save. A date range is there because a manager thinks in weeks: "twelve a day
for everyone this week" should be one action, not thirty.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError

# How far back to look for somebody's last target. Long enough to cover a
# holiday or a spell on another bench; short enough that a number nobody has
# revisited in a quarter is not presented as this week's intention.
LOOK_BACK = 90


class SetTargets(models.TransientModel):
    _name = 'lab.work.target.wizard'
    _description = 'Set Daily Targets'

    date_from = fields.Date(required=True, string='From',
                            default=lambda self: self.env['lab.station']._lab_today())
    date_to = fields.Date(required=True, string='To',
                          default=lambda self: self.env['lab.station']._lab_today())
    workcenter_id = fields.Many2one(
        'mrp.workcenter', string='Work Centre',
        help="Leave empty to set a target for the whole day, whichever bench the "
             "technician works at.")
    skip_weekends = fields.Boolean(
        string='Skip Sundays', default=True,
        help="Sundays in the range get no target, so the report does not read as "
             "a day everybody missed.")
    line_ids = fields.One2many('lab.work.target.wizard.line', 'wizard_id',
                               string='Technicians')

    @api.onchange('workcenter_id', 'date_from')
    def _onchange_people(self):
        """The bench's own people, with whatever target they last had."""
        for wizard in self:
            if wizard.workcenter_id:
                people = (wizard.workcenter_id.users
                          | wizard.workcenter_id.head_user_ids)
            else:
                # No bench named: everybody posted to any bench, because a
                # whole-day target is still a target for a person who works.
                benches = self.env['mrp.workcenter'].search([])
                people = benches.users | benches.head_user_ids
            # THE LAST TARGET THEY HAD, not only one set for this exact day.
            # This screen is opened to set tomorrow's numbers, and tomorrow has
            # no targets yet by definition - so looking only at `date_from`
            # meant every line opened at zero and the manager retyped the same
            # figures they set yesterday. The most recent target on or before
            # the day being set is the honest default: a day already set shows
            # its own number (it sorts first), and everybody else shows what
            # they were last asked for. (client, 2026-09-12)
            recent = self.env['lab.work.target'].search(
                [('date', '<=', wizard.date_from),
                 ('date', '>=', fields.Date.subtract(wizard.date_from, days=LOOK_BACK)),
                 ('user_id', 'in', people.ids),
                 ('workcenter_id', '=', wizard.workcenter_id.id or False)],
                order='date desc, id desc')
            already = {}
            for row in recent:
                already.setdefault(row.user_id.id, row.target)
            wizard.line_ids = [(5, 0, 0)] + [
                (0, 0, {'user_id': user.id, 'target': already.get(user.id, 0)})
                for user in people.sorted('name')]

    def action_save(self):
        self.ensure_one()
        if self.date_to < self.date_from:
            raise UserError(_("The last day cannot be before the first."))
        lines = self.line_ids.filtered(lambda l: l.user_id)
        if not lines:
            raise UserError(_(
                "No technician came through with the targets, so there is nothing "
                "to set. Pick a work centre that has technicians posted to it, or "
                "reopen this screen and try again."))
        Target = self.env['lab.work.target']
        written = Target.browse()
        day = self.date_from
        while day <= self.date_to:
            # 6 is Sunday. The lab's own week, and the reason the report does not
            # show a floor that missed every target once a week.
            if not (self.skip_weekends and day.weekday() == 6):
                for line in lines:
                    key = [('date', '=', day), ('user_id', '=', line.user_id.id),
                           ('workcenter_id', '=', self.workcenter_id.id or False)]
                    row = Target.search(key, limit=1)
                    if row:
                        row.target = line.target
                    else:
                        row = Target.create({
                            'date': day, 'user_id': line.user_id.id,
                            'workcenter_id': self.workcenter_id.id or False,
                            'target': line.target})
                    written |= row
            day = fields.Date.add(day, days=1)
        written.action_refresh()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Daily Targets'),
            'res_model': 'lab.work.target',
            'view_mode': 'list,pivot,form',
            'domain': [('id', 'in', written.ids)],
        }


class SetTargetsLine(models.TransientModel):
    _name = 'lab.work.target.wizard.line'
    _description = 'Set Daily Targets Line'

    wizard_id = fields.Many2one('lab.work.target.wizard', required=True,
                                ondelete='cascade')
    # Not `required`: a line the client sends without its technician must be
    # dropped quietly rather than stopping the manager's whole save. The view
    # keeps it filled (force_save), `action_save` ignores any that slip through,
    # and a wizard that ends up with none says so in words. (client, 2026-09-10)
    user_id = fields.Many2one('res.users', string='Technician')
    target = fields.Integer(string='Jobs per day', default=0)
