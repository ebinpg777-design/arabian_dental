# -*- coding: utf-8 -*-
from odoo import fields, models

from .statement_engine import STATEMENT_TYPES


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    epg_statement_layout = fields.Char(
        string='Statement Letterhead Template', config_parameter='epg_partner_statement.layout',
        help="XML id of the QWeb layout wrapping the statement (default: the standard "
             "document layout, web.external_layout). A branded letterhead template can be "
             "named here, e.g. sale_custom.external_layout_lab.")
    epg_statement_default_type = fields.Selection(
        STATEMENT_TYPES, string='Default Statement Type', default='receivable',
        config_parameter='epg_partner_statement.default_type')
    epg_statement_auto_send_day = fields.Integer(
        string='Auto-send Day of Month', default=1,
        config_parameter='epg_partner_statement.auto_send_day',
        help="Day of the month on which opted-in partners receive last month's statement.")
