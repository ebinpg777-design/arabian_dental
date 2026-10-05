# Part of Advanced Stock Reports. See LICENSE file for full copyright and licensing details.
from odoo import fields, models

GST_CATEGORIES = [
    ('lost', 'Lost'),
    ('stolen', 'Stolen'),
    ('destroyed', 'Destroyed'),
    ('written_off', 'Written Off'),
    ('gift', 'Gift'),
    ('free_sample', 'Free Sample'),
    ('other', 'Other'),
]


class StockScrapReasonTag(models.Model):
    """Odoo 19 already has reason tags on scraps; the module reuses them as the
    single reason master for scraps, returns and count adjustments."""
    _inherit = 'stock.scrap.reason.tag'

    asr_gst_category = fields.Selection(
        GST_CATEGORIES, string='GST Category',
        help="How the GST stock register reports goods leaving stock for this reason "
             "(section 17(5)(h) categories: lost, stolen, destroyed, written off, gift, free sample).")
    asr_usage = fields.Selection([
        ('scrap', 'Scraps'),
        ('return', 'Returns'),
        ('count', 'Count Adjustments'),
        ('any', 'Any'),
    ], string='Used For', default='any', required=True,
        help="Where this reason is offered: on scraps only, on returns only, on count adjustments only, or everywhere.")
    asr_description = fields.Char(string='Description')


class StockScrap(models.Model):
    _inherit = 'stock.scrap'

    def _prepare_move_values(self):
        vals = super()._prepare_move_values()
        if self.scrap_reason_tag_ids:
            vals['asr_reason_id'] = self.scrap_reason_tag_ids[0].id
        return vals
