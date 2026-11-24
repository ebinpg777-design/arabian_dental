# -*- coding: utf-8 -*-
from odoo import api, models


class ProductionSheetMixin(models.AbstractModel):
    """Both sheets print from the same dictionary: `mrp.production._lab_sheet`."""
    _name = 'report.lab_production_sheet.mixin'
    _description = 'Lab production sheet (shared)'

    @api.model
    def _get_report_values(self, docids, data=None):
        productions = self.env['mrp.production'].browse(docids)
        return {
            'doc_ids': docids,
            'doc_model': 'mrp.production',
            'docs': productions,
            'sheets': {production.id: production._lab_sheet() for production in productions},
            'stamp': self.env['report.lab.floor']._printed_stamp(),
        }


class ReportProductionReport(models.AbstractModel):
    _name = 'report.lab_production_sheet.report_production_report'
    _inherit = 'report.lab_production_sheet.mixin'
    _description = 'Daily Production Report'


class ReportJobSheet(models.AbstractModel):
    _name = 'report.lab_production_sheet.report_job_sheet'
    _inherit = 'report.lab_production_sheet.mixin'
    _description = 'Technician Job Sheet'
