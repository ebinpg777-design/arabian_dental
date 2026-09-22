# -*- coding: utf-8 -*-
"""Print a long statement run in batches.

wkhtmltopdf renders the whole run in ONE process. A month-end run of 251 clinics -
each its own ledger page plus a pay-to page - is hundreds of pages of tables and QR
images in a single process, and it dies with signal 11 (a segfault, surfaced by Odoo as
"Wkhtmltopdf failed (error code: -11). Memory limit too low or maximum file number of
subprocess reached") (client, 2026-08-21).

The statements are independent documents, so the run is split into batches, each
rendered by its own short-lived wkhtmltopdf, and the results merged into the single PDF
the user asked for. Ordering is preserved. Only this report is affected: everything else
takes the standard path untouched.
"""
import logging

from odoo import api, models
from odoo.tools.pdf import merge_pdf

_logger = logging.getLogger(__name__)

# Partners per wkhtmltopdf process. Small enough that a batch is a few dozen pages even
# for a busy clinic, large enough that a 251-clinic run is ~13 processes, not 251.
BATCH_SIZE = 20
PARAM = 'epg_partner_statement.pdf_batch_size'


class IrActionsReport(models.Model):
    _inherit = 'ir.actions.report'

    def _epg_statement_batch_size(self):
        try:
            size = int(self.env['ir.config_parameter'].sudo().get_param(PARAM, BATCH_SIZE))
        except (TypeError, ValueError):
            size = BATCH_SIZE
        return max(size, 1)

    @api.model
    def _statement_ids(self, res_ids, data):
        """Where the run's partners actually are.

        NOT just `res_ids`. A report action carrying `data` is downloaded with no ids
        in the URL - which is exactly how the wizard prints (`statement_wizard
        .action_print` passes them in `data['ids']`) - so `res_ids` arrives EMPTY on
        the one path that matters. The batching below used to test `res_ids` alone,
        took the "nothing to batch" branch every time, and handed the whole run to a
        single renderer: 251 clinics became one QWeb string and the print died with
        MemoryError on production (2026-08-21). The report's own renderer already
        looks in all three places; this has to look in the same three.
        """
        data = data or {}
        return list(res_ids or data.get('ids')
                    or (data.get('context') or {}).get('active_ids') or [])

    @api.model
    def _render_qweb_pdf(self, report_ref, res_ids=None, data=None):
        report = self._get_report(report_ref)
        if report.report_name != 'epg_partner_statement.report_statement':
            return super()._render_qweb_pdf(report_ref, res_ids=res_ids, data=data)

        batch = self._epg_statement_batch_size()
        ids = self._statement_ids(res_ids, data)
        if len(ids) <= batch:
            return super()._render_qweb_pdf(report_ref, res_ids=res_ids, data=data)

        _logger.info("Statement run: %s partners rendered in batches of %s",
                     len(ids), batch)
        chunks = []
        for start in range(0, len(ids), batch):
            slice_ids = ids[start:start + batch]
            # Narrow EVERY place the renderer looks, or a batch quietly renders the
            # whole run again: `data['ids']`, the context's `active_ids`, and res_ids.
            slice_data = dict(data or {}, ids=slice_ids)
            if (slice_data.get('context') or {}).get('active_ids'):
                slice_data['context'] = dict(slice_data['context'],
                                             active_ids=slice_ids)
            pdf, _ext = super()._render_qweb_pdf(
                report_ref, res_ids=slice_ids, data=slice_data)
            chunks.append(pdf)
        return merge_pdf(chunks), 'pdf'

