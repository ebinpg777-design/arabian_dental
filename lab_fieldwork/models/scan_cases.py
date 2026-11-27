# -*- coding: utf-8 -*-
"""A case that came as a scan, and the people told while its order waits.

The doctor sends an intra-oral scan; the lab prints the model. The executive marks
the case "Scan", and the order it makes is a 3D Model Print order with the
impression marked scanned. Those orders are the ones the office must not leave
sitting - the printer is waiting on them - so the people named in Field Work
settings see a notice on their home screen while any of them is unconfirmed.
(client, 2026-11-27: "Executives can mark the cases as Scan then it will become 3D
Model Print sale orders. Show a notification in the main view to the assigned
users when these scanned orders are not confirmed")
"""
from odoo import _, api, fields, models


class LabCase(models.Model):
    _inherit = 'lab.case'

    is_scan = fields.Boolean(
        'Scan case', tracking=True,
        help="Tap if the case came as a digital scan: its order is a 3D Model Print "
             "order.")

    @api.onchange('is_scan')
    def _onchange_is_scan(self):
        if self.is_scan:
            self.is_3d_model_print = True
            self.scanned_impression = 'Yes'

    def _order_values(self):
        vals = super()._order_values()
        if self.is_scan:
            vals.update({'is_3d_model_print': True, 'scanned_impression': 'Yes'})
        return vals


class ResCompany(models.Model):
    _inherit = 'res.company'

    lab_scan_notify_user_ids = fields.Many2many(
        'res.users', 'lab_scan_notify_user_rel', 'company_id', 'user_id',
        string='Told About Scan Orders',
        help="These people see a notice on their home screen while a 3D Model Print "
             "order is waiting to be confirmed.")


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    lab_scan_notify_user_ids = fields.Many2many(
        related='company_id.lab_scan_notify_user_ids', readonly=False)


class ResUsers(models.Model):
    _inherit = 'res.users'

    @api.model
    def _lab_scan_orders_waiting_domain(self):
        return [('is_3d_model_print', '=', True), ('state', 'in', ('draft', 'sent'))]

    @api.model
    def ebshel_home_notices(self):
        """The home screen's notices (ebshel_backend_theme draws them), and this
        module's one: scan orders waiting to be confirmed. Called whether or not
        the theme is installed - so the parent may not be there."""
        parent = getattr(super(), 'ebshel_home_notices', None)
        notices = list(parent() if parent else [])
        user = self.env.user
        if user not in self.env.company.sudo().lab_scan_notify_user_ids:
            return notices
        domain = self._lab_scan_orders_waiting_domain()
        count = self.env['sale.order'].sudo().search_count(domain)
        if count:
            notices.append({
                'key': 'lab_scan_orders',
                'icon': 'fa-cube',
                'tone': 'warning',
                'title': _("Scan orders not confirmed"),
                'text': _("%s 3D Model Print order(s) are waiting to be confirmed.", count),
                'count': count,
                'action': {
                    'type': 'ir.actions.act_window', 'name': _('Scan Orders Waiting'),
                    'res_model': 'sale.order', 'view_mode': 'list,form',
                    'views': [[False, 'list'], [False, 'form']],
                    'domain': domain,
                },
            })
        return notices
