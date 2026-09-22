# -*- coding: utf-8 -*-
"""Give the downloaded statement a name that says whose it is.

Odoo names a report download from `print_report_name` ONLY when the download URL
carries the record ids (`web/controllers/report.py`: `if docids:`). This report is
printed from the wizard, which passes its partners inside `data` so the period
options travel with them - and the web client, seeing a non-empty `data`, builds
`/report/pdf/<report>?options=...&context=...` with no ids in the path. So the
`if docids` branch never runs and every statement arrives called "Statement of
Account.pdf", whatever clinic it is for. On a phone, where the file lands in a
Downloads folder next to yesterday's, that name identifies nothing and the second
one is "Statement of Account(1).pdf". (client, 2026-09-01)

The rendering is untouched: super() does all of it, and this only relabels the
Content-Disposition afterwards, from the very options the render already used.
The name mirrors the XLSX export's own convention in statement_wizard, so the two
formats of the same statement sort together.
"""
import json
import logging

from werkzeug.urls import url_parse

from odoo import _, http
from odoo.addons.web.controllers.report import ReportController
from odoo.http import content_disposition, request

_logger = logging.getLogger(__name__)

STATEMENT_REPORT = 'epg_partner_statement.report_statement'


class StatementReportController(ReportController):

    @http.route()
    def report_download(self, data, context=None, token=None, readonly=True):
        response = super().report_download(data, context=context, token=token,
                                           readonly=readonly)
        try:
            filename = self._epg_statement_filename(data)
            if filename:
                # Replace rather than add: Odoo already set a generic one.
                response.headers.set('Content-Disposition',
                                     content_disposition(filename))
        except Exception:                                      # noqa: BLE001
            # A download that arrives with a dull name beats one that does not
            # arrive, so naming never breaks printing.
            _logger.warning("Statement filename could not be built",
                            exc_info=True)
        return response

    def _epg_statement_filename(self, data):
        """`Statement <clinic> <from> to <to>.pdf` for a single clinic.

        Returns None for anything that is not a one-clinic statement download,
        which leaves Odoo's own filename in place. The `.pdf` is not decoration:
        Odoo's own naming appends it, and a phone with no extension to go on
        will not offer a PDF viewer for the file at all.
        """
        url, type_ = json.loads(data)[:2]
        if type_ != 'qweb-pdf' or STATEMENT_REPORT not in url:
            return None
        query = url_parse(url).decode_query(cls=dict)
        options = json.loads(query.get('options') or '{}')
        ids = options.get('ids') or []
        date_from, date_to = options.get('date_from'), options.get('date_to')
        span = (' %s to %s' % (date_from, date_to)) if date_from and date_to else ''
        if len(ids) != 1:
            # A run of many clinics is one document about no one clinic; say how
            # many, so a folder of them is still tellable apart.
            return _('Statements%(span)s (%(count)s clinics).pdf',
                     span=span, count=len(ids)) if ids else None
        partner = request.env['res.partner'].browse(ids[0]).exists()
        if not partner:
            return None
        # sudo for the NAME only: the statement itself was already rendered for
        # this user by super(), so this reveals nothing they did not just print.
        name = partner.sudo().display_name or ''
        # A filename is not a path: a clinic called "DR X / EKM" must not invent
        # a directory, and the header is quoted on one line.
        clean = ' '.join(name.replace('/', '-').replace('\\', '-').split())
        return _('Statement %(name)s%(span)s.pdf', name=clean[:80], span=span)
