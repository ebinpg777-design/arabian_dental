# -*- coding: utf-8 -*-
"""Guard against printing an unbounded selection in one go.

Production went down with a MemoryError (2026-08-19, report_download): a list's
"Select all" puts EVERY record id into one print request, and Odoo renders them as a
single HTML document before wkhtmltopdf ever runs — tens of thousands of invoices in one
string. The render dies (or takes the whole worker with it) long before a PDF exists.

A cap converts that into a polite message. It only applies to qweb-pdf downloads of many
documents at once; single prints, statements and wizard reports (docids of the wizard)
are untouched. Raise or lower it with the `sale_custom.max_print_batch` parameter.
"""
import logging

from odoo import _, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

DEFAULT_MAX_PRINT_BATCH = 200


class IrActionsReport(models.Model):
    _inherit = 'ir.actions.report'

    def _pre_render_qweb_pdf(self, report_ref, res_ids=None, data=None):
        if res_ids:
            limit = int(self.env['ir.config_parameter'].sudo().get_param(
                'sale_custom.max_print_batch', DEFAULT_MAX_PRINT_BATCH))
            if limit and len(res_ids) > limit:
                report = self._get_report(report_ref)
                _logger.warning(
                    "print of %s x %s refused (limit %s)",
                    len(res_ids), report.report_name, limit)
                raise UserError(_(
                    'This would print %(count)s documents in one file, which is more '
                    'than the server can render at once (limit: %(limit)s).\n\n'
                    'Narrow the selection, or print in batches.',
                    count=len(res_ids), limit=limit))
        return super()._pre_render_qweb_pdf(report_ref, res_ids=res_ids, data=data)
