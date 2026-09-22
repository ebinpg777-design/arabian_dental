# -*- coding: utf-8 -*-
from odoo import models


class IrActionsReport(models.Model):
    _inherit = 'ir.actions.report'

    def get_paperformat(self):
        """The sticker report prints on whichever format the wizard chose."""
        fmt_id = self.env.context.get('epg_sticker_paperformat_id')
        if fmt_id and self.report_name == 'epg_sticker_print.report_sticker':
            paper = self.env['report.paperformat'].browse(fmt_id).exists()
            if paper:
                return paper
        return super().get_paperformat()
