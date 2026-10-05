# Part of Advanced Stock Reports. See LICENSE file for full copyright and licensing details.
from odoo import models


class MrpProduction(models.Model):
    _inherit = 'mrp.production'

    def action_confirm(self):
        res = super().action_confirm()
        self.move_raw_ids._asr_set_planned_qty_unit()
        return res
