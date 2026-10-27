# -*- coding: utf-8 -*-
from odoo import api, models, _

from .export import PDF_SLICE

# How wide wkhtmltopdf lays the table out, in CSS pixels: measured on 0.12.6.1 with
# the module's paper format, a landscape A4 table printed at 1,220px (2026-10-08);
# upright is the same page turned, its printable width in proportion. A little under
# what was measured, so that a figure this says fits does.
TABLE_PX = {True: 1200.0, False: 1200.0 * (210 - 16) / (297 - 16)}
CELL_PADDING_PX = 12
# advance widths of the report's font (Lato, regular and bold alike) in ems
GLYPH_EM = dict({d: 0.58 for d in '0123456789'}, **{
    ',': 0.22, '.': 0.22, '-': 0.36, '(': 0.3, ')': 0.3, ' ': 0.19, '\N{NO-BREAK SPACE}': 0.19, '%': 0.85})



class FinReportPdf(models.AbstractModel):
    _name = 'report.ebshel_account_reports.fin_report_pdf'
    _description = 'Financial report, on paper'

    @api.model
    def _column_plan(self, count, landscape, detailed, first_column=1.0):
        """(width of the name column, width of each figure, font size) in percent and pixels:
        the columns are fixed so that every slice of a long table lines up with the last."""
        count = max(count, 1)
        if landscape:
            name = 46 if count <= 3 else 36 if count <= 6 else 26 if count <= 9 else 20
        else:
            name = 50 if count <= 2 else 40 if count == 3 else 30
        if detailed:
            name = max(name, 100 - count * (11 if landscape else 15))
        name = round(min(name * first_column, 80.0), 2)
        each = (100.0 - name) / count
        font = 11 if each >= 9.5 else 9.5 if each >= 7 else 8
        return name, round(each, 2), font

    @api.model
    def _fit_font(self, asked, usual, col_w, landscape, lines):
        """The text size asked for, as far as the widest figure still fits its column.

        A figure wider than its column is cut - the cells do not wrap - so a larger
        print stops where the widest figure on this page would be. Never below the
        size the columns give anyway: this only caps an increase.
        """
        if asked <= usual:
            return asked
        widest = max((sum(GLYPH_EM.get(ch, 0.6) for ch in cell.get('text') or '')
                      for line in lines for cell in line['columns']), default=0.0)
        if not widest:
            return asked
        room = col_w / 100.0 * TABLE_PX[bool(landscape)] - CELL_PADDING_PX
        fits = room / (widest * 1.08)   # bold rows and the engine's rounding of sizes
        return round(max(usual, min(asked, fits)), 1)

    @api.model
    def _without_currency(self, engine, lines, currency):
        """The lines with their amounts written without the currency sign, in the same
        style for negatives. Copies: the lines may be the screen's own."""
        plain = engine._writer(currency.decimal_places)
        out = []
        for line in lines:
            cells = []
            for cell in line['columns']:
                if cell.get('display') == 'amount' and cell.get('value') is not None and cell.get('text'):
                    cell = dict(cell, text=plain(0.0 if abs(cell['value']) < 0.005 else cell['value']))
                cells.append(cell)
            out.append(dict(line, columns=cells))
        return out

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
        name_w, col_w, font = self._column_plan(len(columns), landscape, any(l.get('parts') for l in lines),
                                                report._first_column())
        company = self.env['ebshel.fin.engine'].lead_company(options)
        currency = engine.currency(options)
        unit_label = engine.unit_label(options)
        if report.print_currency_once and not unit_label and currency:
            # the sign once, in the heading - as when the figures are shown in lakhs -
            # and not in front of every figure, where it takes the room the text needs
            lines = self._without_currency(engine, lines, currency)
            unit_label = currency.symbol
        # larger print where the report asks for it, as far as its widest figure fits
        font = self._fit_font(round(font * report._print_font(), 1), font, col_w, landscape, lines)
        notes = self.env['ebshel.fin.report.annotation'].for_report(report) if layout['notes'] else {}
        flat_notes = [dict(n, line_key=key) for key, items in notes.items() for n in items]
        facts = report._filters_in_words(options)
        tax_id = company.vat and '%s %s' % (company.country_id.vat_label or _('Tax ID'), company.vat)
        return {
            'doc_ids': [report.id], 'doc_model': 'ebshel.fin.report', 'docs': report,
            'report': report, 'company': company, 'currency': currency,
            'company_line': ' · '.join(p for p in (company.city, company.phone, tax_id) if p),
            'options': options, 'summary': report._options_summary(options), 'unit_label': unit_label,
            'period': facts[0][1], 'facts': facts[1:] if layout['filters'] else [], 'stamp': report._export_stamp(company),
            'columns': columns, 'slices': [lines[i:i + PDF_SLICE] for i in range(0, len(lines), PDF_SLICE)],
            'line_count': len(lines), 'cut_total': total if total > cap else 0, 'empty': report._is_empty(lines),
            'landscape': landscape, 'name_w': name_w, 'col_w': col_w, 'font': font,
            'notes': flat_notes, 'note_marks': {n['line_key']: i + 1 for i, n in enumerate(flat_notes)},
        }
