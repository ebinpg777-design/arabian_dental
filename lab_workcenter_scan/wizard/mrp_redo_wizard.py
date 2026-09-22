# -*- coding: utf-8 -*-
"""Why this case is being done again.

A dialog rather than a button, because the reason is the whole value of the feature: a
redo with no reason is a case that took twice as long for no recorded cause, and the
list of them tells the lab nothing.
"""
from odoo import _, api, fields, models


class LabMrpRedoWizard(models.TransientModel):
    _name = 'lab.mrp.redo.wizard'
    _description = 'Start This Case Again'

    production_id = fields.Many2one('mrp.production', string='Manufacturing Order',
                                    required=True, readonly=True)
    reason_id = fields.Many2one('lab.redo.reason', string='Why', required=True)
    note = fields.Char('What happened',
                       help="Anything the reason alone does not say — the bench reads "
                            "this before starting again.")
    operations = fields.Integer(compute='_compute_preview', string='Operations to reset')
    stage = fields.Char(compute='_compute_preview', string='Currently at')
    attempt = fields.Integer(compute='_compute_preview', string='This will be attempt')

    @api.depends('production_id')
    def _compute_preview(self):
        """Say what the button is about to do before it does it."""
        for wizard in self:
            production = wizard.production_id
            workorders = production.workorder_ids.filtered(
                lambda w: w.state != 'cancel')
            done = workorders.filtered(lambda w: w.state == 'done')
            wizard.operations = len(workorders)
            wizard.stage = (done[-1].workcenter_id.display_name if done
                            else (workorders[:1].workcenter_id.display_name or ''))
            wizard.attempt = production.lab_redo_count + 2

    def action_redo(self):
        self.ensure_one()
        redo = self.production_id.lab_redo(self.reason_id, self.note)
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {
                'title': _("Started again"),
                'message': _(
                    "%(name)s goes back to the first bench — %(reason)s. "
                    "%(count)s operation(s) reset.",
                    name=self.production_id.name, reason=redo.reason_id.name,
                    count=redo.operations_reset),
                'type': 'success',
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }
