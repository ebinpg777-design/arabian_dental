# -*- coding: utf-8 -*-
from odoo import fields, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    scan_count = fields.Integer(compute='_compute_scan_count')

    def _compute_scan_count(self):
        counts = dict(self.env['lab.scan']._read_group(
            [('partner_id', 'in', self.ids)], ['partner_id'], ['__count']))
        for partner in self:
            partner.scan_count = counts.get(partner, 0)

    def action_view_scans(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('lab_scan_register.action_scan')
        action['domain'] = [('partner_id', '=', self.id)]
        action['context'] = {'default_partner_id': self.id}
        return action
