# Part of Advanced Stock Reports. See LICENSE file for full copyright and licensing details.
import json

from odoo import api, fields, models


class ReportPrint(models.TransientModel):
    """A printable snapshot of a report: the columns and rows a wizard produced,
    serialised, rendered by one generic QWeb template."""
    _name = 'asr.report.print'
    _description = 'Printable Stock Report'

    name = fields.Char(required=True)
    subtitle = fields.Char()
    company_id = fields.Many2one('res.company', required=True)
    res_model = fields.Char()
    res_id = fields.Integer()
    columns_json = fields.Text()
    rows_json = fields.Text()
    totals_json = fields.Text()
    row_count = fields.Integer()
    row_cap = fields.Integer()
    truncated = fields.Boolean()

    def _columns(self):
        return json.loads(self.columns_json or '[]')

    def _rows(self):
        return json.loads(self.rows_json or '[]')

    def _totals(self):
        return json.loads(self.totals_json or '{}')


class ReportDocument(models.AbstractModel):
    _name = 'report.ebshel_stock_reports.report_document'
    _description = 'Advanced Stock Report PDF'

    @api.model
    def _get_report_values(self, docids, data=None):
        docs = self.env['asr.report.print'].browse(docids)
        decimals = {doc.id: doc.company_id.currency_id.decimal_places for doc in docs}

        def fmt(value, ctype, doc):
            if value is None or value == '' or value is False:
                return ''
            if ctype == 'monetary':
                return f"{float(value):,.{decimals[doc.id]}f}"
            if ctype in ('float', 'qty'):
                text = f"{float(value):,.4f}".rstrip('0')
                return text[:-1] if text.endswith('.') else text
            if ctype == 'percent':
                return f"{float(value):,.2f} %"
            if ctype == 'int':
                return f"{int(value)}"
            if ctype == 'bool':
                return self.env._("Yes") if value else self.env._("No")
            return str(value)

        return {
            'doc_ids': docids,
            'doc_model': 'asr.report.print',
            'docs': docs,
            'fmt': fmt,
            'numeric_types': ('int', 'float', 'qty', 'monetary', 'percent'),
        }
