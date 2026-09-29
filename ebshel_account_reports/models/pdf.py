# -*- coding: utf-8 -*-
from odoo import api, models


class FinReportPdf(models.AbstractModel):
    _name = 'report.ebshel_account_reports.fin_report_pdf'
    _description = 'Financial report, on paper'

    @api.model
    def _get_report_values(self, docids, data=None):
        self.env.flush_all()
        data = data or {}
        report = self.env['ebshel.fin.report'].browse(int(data.get('report_id') or (docids or [0])[0]))
        given = dict(data.get('options') or {})
        given['unfold_all'] = bool(given.get('unfold_all', True)) if data.get('options') else True
        engine, handler, options, columns = report._prepare(given)
        lines = handler.lines(report, options, columns, for_export=True)
        currency = engine.currency(options)
        company = self.env['res.company'].browse(options['companies'][0])
        notes = self.env['ebshel.fin.report.annotation'].for_report(report)
        flat_notes = [dict(n, line_key=key) for key, items in notes.items() for n in items]
        return {
            'doc_ids': [report.id], 'doc_model': 'ebshel.fin.report', 'docs': report,
            'report': report, 'company': company, 'currency': currency,
            'options': options, 'summary': report._options_summary(options), 'unit_label': engine.unit_label(options),
            'columns': columns, 'lines': lines, 'notes': flat_notes,
            'note_marks': {n['line_key']: i + 1 for i, n in enumerate(flat_notes)},
        }
