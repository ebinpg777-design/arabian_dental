# Part of Advanced Stock Reports. See LICENSE file for full copyright and licensing details.
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    asr_report_tz = fields.Selection(related='company_id.asr_report_tz', readonly=False)
    asr_aging_buckets = fields.Char(related='company_id.asr_aging_buckets', readonly=False)
    asr_slow_days = fields.Integer(related='company_id.asr_slow_days', readonly=False)
    asr_abc_thresholds = fields.Char(related='company_id.asr_abc_thresholds', readonly=False)
    asr_xyz_thresholds = fields.Char(related='company_id.asr_xyz_thresholds', readonly=False)
    asr_otif_basis = fields.Selection(related='company_id.asr_otif_basis', readonly=False)
    asr_otif_tolerance = fields.Float(related='company_id.asr_otif_tolerance', readonly=False)
    asr_cover_days = fields.Integer(related='company_id.asr_cover_days', readonly=False)
    asr_pdf_row_cap = fields.Integer(related='company_id.asr_pdf_row_cap', readonly=False)
    asr_india_gst = fields.Boolean(related='company_id.asr_india_gst', readonly=False)
    asr_wip_component_price = fields.Selection(related='company_id.asr_wip_component_price', readonly=False)
    asr_engine_state = fields.Selection(related='company_id.asr_engine_state')
    asr_engine_last_run = fields.Datetime(related='company_id.asr_engine_last_run')
    asr_dirty_count = fields.Integer(compute='_compute_asr_dirty_count')

    def _compute_asr_dirty_count(self):
        for settings in self:
            settings.asr_dirty_count = self.env['asr.stock.dirty'].search_count(
                [('company_id', '=', settings.company_id.id)])

    def set_values(self):
        res = super().set_values()
        self._asr_sync_gst_group()
        return res

    def _asr_sync_gst_group(self):
        """The GST menu is carried by a group: implied by the User group while any
        company has the India GST reports switched on."""
        gst_group = self.env.ref('ebshel_stock_reports.group_india_gst').sudo()
        user_group = self.env.ref('ebshel_stock_reports.group_user').sudo()
        enabled = bool(self.env['res.company'].sudo().search_count([('asr_india_gst', '=', True)]))
        if enabled and gst_group not in user_group.implied_ids:
            user_group.write({'implied_ids': [(4, gst_group.id)]})
        elif not enabled and gst_group in user_group.implied_ids:
            user_group.write({'implied_ids': [(3, gst_group.id)]})
            gst_group.write({'user_ids': [(3, uid) for uid in user_group.all_user_ids.ids]})

    def action_asr_rebuild(self):
        self.ensure_one()
        self.env['asr.stock.dirty']._mark_full_rebuild(self.company_id)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'info',
                'message': self.env._("The stock summary of %s will be rebuilt by the next cron run.",
                                      self.company_id.name),
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }

    def action_asr_run_now(self):
        self.ensure_one()
        self.env['asr.stock.dirty'].with_company(self.company_id)._process_queue(time_budget=120)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'success',
                'message': self.env._("Stock summary recomputed."),
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }

    def action_asr_health_check(self):
        self.ensure_one()
        return self.env['asr.stock.dirty'].with_company(self.company_id).action_health_check()
