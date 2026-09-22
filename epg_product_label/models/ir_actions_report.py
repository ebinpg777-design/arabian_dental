# -*- coding: utf-8 -*-
from odoo import models


class IrActionsReport(models.Model):
    _inherit = 'ir.actions.report'

    def get_paperformat(self):
        """Let a label template impose its own paper format on the shared report.

        Every label template needs a different page size, but they all render through
        one report action.  ``report_action`` copies the environment context into the
        action, the web client puts it on the report URL, and the controller restores
        it - so a context key set by the print wizard survives all the way here.
        """
        paperformat_id = self.env.context.get('epg_label_paperformat_id')
        if paperformat_id:
            paperformat = self.env['report.paperformat'].browse(paperformat_id).exists()
            if paperformat:
                return paperformat
        return super().get_paperformat()
