# -*- coding: utf-8 -*-
import re

import pytz

from odoo import fields, models

LAB_TZ = 'Asia/Kolkata'


class LabReportMixin(models.AbstractModel):
    _name = 'lab.report.mixin'
    _description = 'Lab XLSX report helper'

    @staticmethod
    def _safe_sheet_name(name, used):
        """Excel sheet names: no []:*?/\\, max 31 chars, unique. `used` is a set of
        lower-cased names already taken (mutated in place)."""
        name = re.sub(r'[\[\]:*?/\\]', '-', (name or 'Sheet').strip())[:31] or 'Sheet'
        base, i = name, 1
        while name.lower() in used:
            suffix = '-%d' % i
            name = (base[:31 - len(suffix)] + suffix)
            i += 1
        used.add(name.lower())
        return name

    def _local_tz(self):
        """The timezone printed times belong to: the context's, else the user's, else
        the company's, else Kolkata - most users have no tz of their own, and
        `strftime` on a stored datetime printed UTC, 5h30 behind the lab."""
        name = (self.env.context.get('tz') or self.env.user.tz
                or self.env.company.partner_id.tz or LAB_TZ)
        try:
            return pytz.timezone(name)
        except pytz.UnknownTimeZoneError:
            return pytz.timezone(LAB_TZ)

    def _local_str(self, value, fmt='%d-%m-%Y'):
        """A stored (naive UTC) datetime formatted on the lab's wall clock; '' if unset."""
        if not value:
            return ''
        return pytz.utc.localize(value).astimezone(self._local_tz()).strftime(fmt)

    def _doc_date(self):
        """Today's date for a document reference, in the lab's timezone."""
        return self._local_str(fields.Datetime.now())

    def _xlsx_formats(self, workbook, head2_align='center'):
        """The set of cell formats shared by every lab XLSX report."""
        return {
            'main': workbook.add_format({'bold': True, 'border': True, 'font_size': 14, 'align': 'center'}),
            'title': workbook.add_format({'bold': True, 'border': True, 'font_size': 12, 'align': 'center'}),
            'head': workbook.add_format({'bold': True, 'border': True, 'font_size': 10, 'align': head2_align}),
            'common': workbook.add_format({'border': True, 'align': 'left'}),
            'vcenter': workbook.add_format({'border': True, 'valign': 'vcenter', 'align': 'left'}),
        }

    def _xlsx_download(self):
        """Return a download action for the generated file stored in report_data."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_url',
            'url': '/web/content?model=%s&id=%s&field=report_data&filename_field=name&download=true' % (
                self._name, self.id),
            'target': 'self',
        }
