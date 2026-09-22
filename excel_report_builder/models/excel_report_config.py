# -*- coding: utf-8 -*-
# Copyright (C) 2025-2026 Ebin P G
# License OPL-1. See LICENSE file for full copyright and licensing details.

from odoo import models, fields, api


class ExcelReportBuilderConfig(models.Model):
    _name = 'excel.report.builder.config'
    _description = 'Excel Report Builder Global Configuration'

    name = fields.Char(default='Excel Report Builder Config', readonly=True)
    allowed_model_ids = fields.Many2many(
        'ir.model',
        'excel_config_allowed_model_rel',
        'config_id',
        'model_id',
        string='Allowed Models',
        domain="[('transient', '=', False)]",
        help="Leave empty to show the Excel export button on all list views. "
             "If models are selected, the button only appears on those models' list views.",
    )

    @api.model
    def _get_singleton(self):
        config = self.search([], limit=1)
        if not config:
            config = self.sudo().create({'name': 'Excel Report Builder Config'})
        return config

    @api.model
    def get_allowed_models(self):
        """Return list of allowed model technical names. Empty list means all models allowed."""
        config = self.sudo()._get_singleton()
        return config.allowed_model_ids.mapped('model')


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    excel_allowed_model_ids = fields.Many2many(
        'ir.model',
        'excel_res_config_model_rel',
        'settings_id',
        'model_id',
        string='Restrict Excel Button to Models',
        domain="[('transient', '=', False)]",
    )

    def get_values(self):
        res = super().get_values()
        config = self.env['excel.report.builder.config']._get_singleton()
        res['excel_allowed_model_ids'] = [(6, 0, config.allowed_model_ids.ids)]
        return res

    def set_values(self):
        super().set_values()
        config = self.env['excel.report.builder.config']._get_singleton()
        config.sudo().allowed_model_ids = self.excel_allowed_model_ids
