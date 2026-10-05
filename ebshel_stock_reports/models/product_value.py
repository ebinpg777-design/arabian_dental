# Part of Advanced Stock Reports. See LICENSE file for full copyright and licensing details.
from odoo import api, models


class ProductValue(models.Model):
    _inherit = 'product.value'

    def _asr_keys(self):
        keys = set()
        for value in self:
            company = value.company_id
            if value.move_id:
                move = value.move_id
                keys.add((move.company_id.id, move.product_id.id, move.company_id._asr_local_day(move.date)))
            elif value.product_id and company:
                keys.add((company.id, value.product_id.id, company._asr_local_day(value.date)))
        return keys

    @api.model_create_multi
    def create(self, vals_list):
        values = super().create(vals_list)
        self.env['asr.stock.dirty']._mark(values._asr_keys())
        return values

    def write(self, vals):
        keys = self._asr_keys()
        res = super().write(vals)
        self.env['asr.stock.dirty']._mark(keys | self._asr_keys())
        return res

    def unlink(self):
        keys = self._asr_keys()
        res = super().unlink()
        self.env['asr.stock.dirty']._mark(keys)
        return res
