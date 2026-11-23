# -*- coding: utf-8 -*-
from odoo import _, fields, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    technician_work_count = fields.Integer(
        string='Works', compute='_compute_technician_work_count',
        help="Order lines of the cases this technician is named on.")

    def _compute_technician_work_count(self):
        technicians = self.filtered('is_technician')
        counts = {}
        if technicians:
            counts = dict(self.env['sale.technician.work']._read_group(
                [('technician_id', 'in', technicians.ids)], ['technician_id'], ['__count']))
        for partner in self:
            partner.technician_work_count = counts.get(partner._origin, 0)

    def action_view_technician_work(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('lab_technician_work.action_technician_work')
        action['name'] = _('Work of %s', self.display_name)
        action['domain'] = [('technician_id', '=', self.id)]
        action['context'] = {'search_default_this_year': 1, 'pivot_row_groupby': ['date:month']}
        return action
