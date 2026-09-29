# -*- coding: utf-8 -*-
from odoo import api, models, _

from .export import PDF_SLICE


class FinReportPdf(models.AbstractModel):
    _name = 'report.ebshel_account_reports.fin_report_pdf'
    _description = 'Financial report, on paper'

    @api.model
    def _column_plan(self, count, landscape, detailed):
        """(width of the name column, width of each figure, font size) in percent and pixels:
        the columns are fixed so that every slice of a long table lines up with the last."""
        count = max(count, 1)
        if landscape:
            name = 46 if count <= 3 else 36 if count <= 6 else 26 if count <= 9 else 20
        else:
            name = 50 if count <= 2 else 40 if count == 3 else 30
        if detailed:
            name = max(name, 100 - count * (11 if landscape else 15))
        each = (100.0 - name) / count
        font = 11 if each >= 9.5 else 9.5 if each >= 7 else 8
        return name, round(each, 2), font

    @api.model
    def _get_report_values(self, docids, data=None):
        self.env.flush_all()
        data = data or {}
        report = self.env['ebshel.fin.report'].browse(int(data.get('report_id') or (docids or [0])[0]))
        given = dict(data.get('options') or {})
        layout = data.get('layout') or None
        if not layout:
            # callers older than the export panel - a statement sent to a partner, a schedule
            given['unfold_all'] = bool(given.get('unfold_all', True)) if data.get('options') else True
            layout = {'scope': 'screen'}
        engine, options, columns, lines, layout = report._export_lines(given, layout)
        cap = report._pdf_cap()
        total = len(lines)
        lines = lines[:cap]
        landscape = report._wants_landscape(layout, columns, lines)
        name_w, col_w, font = self._column_plan(len(columns), landscape, any(l.get('parts') for l in lines))
        company = self.env['res.company'].browse(options['companies'][0])
        notes = self.env['ebshel.fin.report.annotation'].for_report(report) if layout['notes'] else {}
        flat_notes = [dict(n, line_key=key) for key, items in notes.items() for n in items]
        facts = report._filters_in_words(options)
        tax_id = company.vat and '%s %s' % (company.country_id.vat_label or _('Tax ID'), company.vat)
        return {
            'doc_ids': [report.id], 'doc_model': 'ebshel.fin.report', 'docs': report,
            'report': report, 'company': company, 'currency': engine.currency(options),
            'company_line': ' · '.join(p for p in (company.city, company.phone, tax_id) if p),
            'options': options, 'summary': report._options_summary(options), 'unit_label': engine.unit_label(options),
            'period': facts[0][1], 'facts': facts[1:] if layout['filters'] else [], 'stamp': report._export_stamp(),
            'columns': columns, 'slices': [lines[i:i + PDF_SLICE] for i in range(0, len(lines), PDF_SLICE)],
            'line_count': len(lines), 'cut_total': total if total > cap else 0, 'empty': report._is_empty(lines),
            'landscape': landscape, 'name_w': name_w, 'col_w': col_w, 'font': font,
            'notes': flat_notes, 'note_marks': {n['line_key']: i + 1 for i, n in enumerate(flat_notes)},
        }
