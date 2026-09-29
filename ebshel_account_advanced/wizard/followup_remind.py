# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class FollowupRemind(models.TransientModel):
    """Send a reminder to the selected customers, from their list or form."""
    _name = 'ebshel.followup.remind'
    _description = 'Send payment reminders'

    partner_ids = fields.Many2many('res.partner', string='Customers', required=True)
    level_id = fields.Many2one('ebshel.followup.level', string='Level',
                               help="Leave empty to send each customer the level they have reached.")
    note = fields.Text('Add a line', help="Added to the email, under the invoice table.")
    print_letter = fields.Boolean('Print letters instead of emailing')

    @api.model
    def default_get(self, fields_list):
        vals = super().default_get(fields_list)
        if self.env.context.get('active_model') == 'res.partner' and self.env.context.get('active_ids'):
            vals['partner_ids'] = [(6, 0, self.env.context['active_ids'])]
        return vals

    def action_send(self):
        self.ensure_one()
        if self.print_letter:
            return self.partner_ids.action_ebshel_print_letter(level_id=self.level_id.id)
        result = self.partner_ids.action_ebshel_send_reminder(level_id=self.level_id.id or None, note=self.note)
        if not result['sent'] and result['skipped']:
            raise UserError(_("Nothing was sent: %s", '; '.join('%s (%s)' % (s['name'], s['why']) for s in result['skipped'])))
        message = _('%(n)d reminder(s) sent', n=len(result['sent']))
        if result['skipped']:
            message += _('; skipped: %s', ', '.join('%s (%s)' % (s['name'], s['why']) for s in result['skipped']))
        return {'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'title': _('Reminders'), 'message': message, 'type': 'success' if result['sent'] else 'warning',
                           'next': {'type': 'ir.actions.act_window_close'}}}
