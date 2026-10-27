# -*- coding: utf-8 -*-
"""Dates in the reports are written the company's way (client, 2026-10-27: "the date
format should be based on the company's date format") - not each reader's language."""
from datetime import date

from odoo import fields
from odoo.tests import TransactionCase, tagged

from odoo.addons.ebshel_account_reports.models.engine import (
    DATE_FORMATS, excel_date_format, luxon_date_format)

NEW_YEARS_EVE = date(2026, 12, 31)


@tagged('post_install', '-at_install')
class TestCompanyDateFormat(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.engine = cls.env['ebshel.fin.engine'].with_context(lang='en_US')
        cls.Report = cls.env['ebshel.fin.report']
        # a reader whose language writes the month first
        cls.env['res.lang']._activate_lang('en_US').date_format = '%m/%d/%Y'

    def test_every_choice_writes_the_date_its_label_shows(self):
        for pattern, label in DATE_FORMATS:
            self.company.ebshel_fin_date_format = pattern
            self.assertEqual(self.engine.fmt_date(NEW_YEARS_EVE), label.split(' - ')[1], pattern)

    def test_the_company_decides_not_the_readers_language(self):
        self.company.ebshel_fin_date_format = '%d-%m-%Y'
        self.assertEqual(self.engine.fmt_date('2026-12-31'), '31-12-2026')
        self.assertEqual(self.company.ebshel_fin_format_date(NEW_YEARS_EVE), '31-12-2026',
                         "the method PDF templates call")
        self.assertEqual(self.engine.fmt_date(False), '')

    def test_the_screen_the_workbook_and_the_stamp_follow_it(self):
        self.company.ebshel_fin_date_format = '%d %b %Y'
        options = {'date': {'preset': 'custom', 'from': '2026-01-01', 'to': '2026-12-31'}}
        data = self.Report.by_key('day_book').get_report_data(options)
        self.assertEqual(data['report']['date_format'], 'dd MMM yyyy', "the date pickers")
        report = self.Report.by_key('day_book')
        self.assertEqual(report._excel_date_format(self.company), 'dd mmm yyyy')
        today = fields.Date.context_today(self.company)
        self.assertTrue(report._export_stamp(self.company).startswith(self.engine.fmt_date(today)))
        self.assertEqual(self.env['ebshel.fin.handler.day_book'].with_context(lang='en_US')
                         ._day_heading(NEW_YEARS_EVE), 'Thursday, 31 Dec 2026')

    def test_a_reading_uses_its_own_companys_format(self):
        self.company.ebshel_fin_date_format = '%Y-%m-%d'
        engine, _handler, _options, _columns = self.Report.by_key('day_book')._prepare(
            {'date': {'preset': 'custom', 'from': '2026-01-01', 'to': '2026-12-31'}})
        self.assertEqual(engine.fmt_date(NEW_YEARS_EVE), '2026-12-31')
        self.assertEqual(engine.period_label(date(2026, 3, 5), date(2026, 3, 20)), '2026-03-05 – 2026-03-20')

    def test_notes_carry_the_date_written_out(self):
        self.company.ebshel_fin_date_format = '%d.%m.%Y'
        report = self.Report.by_key('day_book')
        self.env['ebshel.fin.report.annotation'].create({
            'report_id': report.id, 'line_key': 'total', 'text': 'Checked', 'date': NEW_YEARS_EVE})
        note = self.env['ebshel.fin.report.annotation'].for_report(report)['total'][0]
        self.assertEqual(note['date'], '2026-12-31', "the raw date stays for the screen's own use")
        self.assertEqual(note['date_text'], '31.12.2026')

    def test_the_patterns_for_the_browser_and_the_spreadsheet(self):
        self.assertEqual(luxon_date_format('%b %d, %Y'), 'MMM dd, yyyy')
        self.assertEqual(luxon_date_format('%d/%m/%Y'), 'dd/MM/yyyy')
        self.assertEqual(excel_date_format('%d-%b-%Y'), 'dd-mmm-yyyy')
        self.assertEqual(excel_date_format('%Y-%m-%d'), 'yyyy-mm-dd')

    def test_a_new_company_starts_from_its_language(self):
        self.assertEqual(self.env['res.company'].with_context(lang='en_US')._default_fin_date_format(), '%m/%d/%Y')
        self.env['res.lang']._activate_lang('en_US').date_format = '%A %d %B'
        self.assertEqual(self.env['res.company'].with_context(lang='en_US')._default_fin_date_format(), '%d/%m/%Y',
                         "a language format that is not among the choices falls back")
