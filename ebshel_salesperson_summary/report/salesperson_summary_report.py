# Part of ebshel_salesperson_summary. See LICENSE file for full copyright and licensing details.
from odoo import api, fields, models


class ReportSalespersonSummary(models.AbstractModel):
    _name = 'report.ebshel_salesperson_summary.report_salesperson_summary'
    _description = 'Salesperson Summary (PDF)'

    @api.model
    def _get_report_values(self, docids, data=None):
        wizard = self.env['salesperson.summary.wizard'].browse(docids).exists()[:1]
        company = wizard.company_id or self.env.company
        return {
            'doc_ids': wizard.ids,
            'doc_model': wizard._name,
            'docs': wizard,
            'wizard': wizard,
            'company': company,
            'res_company': company,
            'currency': company.currency_id,
            'layout': wizard._layout() if wizard else 'web.external_layout',
            'data': wizard._collect() if wizard else {'groups': [], 'totals': {}},
            'people': wizard._people_label() if wizard else '',
            'rate_class': wizard.rate_class,
            'printed_on': fields.Date.context_today(self),
        }
