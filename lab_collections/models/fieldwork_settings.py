# -*- coding: utf-8 -*-
from odoo import api, fields, models


class LabFieldworkSettings(models.TransientModel):
    """The leaderboards switch, on the Field Work settings page.

    Whether the field force sees the room is a field-force rule, so it sits with
    the others there. It is the company's own setting, not a parameter: each
    company decides for its own executives. (client, 2026-10-22)
    """
    _inherit = 'lab.fieldwork.settings'

    fw_executive_boards = fields.Boolean(
        'Executives See the Leaderboards',
        help="Field executives see Top Salespeople, Top Collectors and Best Collection "
             "Rate on the Collections screen and its PDF, and their own rank among the "
             "salespeople. Off, they see their own figures only. The office and "
             "management always see the boards.")
    fw_boards_company_id = fields.Many2one('res.company', 'For Company', readonly=True)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        company = self.env.company
        res['fw_executive_boards'] = company.lab_collection_executive_boards
        res['fw_boards_company_id'] = company.id
        return res

    def action_save(self):
        self.ensure_one()
        # sudo: the Field Work Administrator owns this one setting of the company,
        # not the company record, whose write is the system administrator's.
        self.env.company.sudo().lab_collection_executive_boards = self.fw_executive_boards
        return super().action_save()
