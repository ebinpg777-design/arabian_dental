# Part of ebshel_einvoice_report. See LICENSE file for full copyright and licensing details.
from odoo import http
from odoo.http import request
from odoo.http.stream import content_disposition


class EinvoiceReportController(http.Controller):

    @http.route('/einvoice_report/xlsx/<int:wizard_id>', type='http', auth='user')
    def xlsx(self, wizard_id, **kwargs):
        # a wizard is its creator's: the ORM's own access rules decide who may read it
        wizard = request.env['einvoice.report.wizard'].browse(wizard_id).exists()
        if not wizard:
            raise request.not_found()
        wizard.check_access('read')
        data = wizard._xlsx()
        return request.make_response(data, headers=[
            ('Content-Type', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
            ('Content-Length', len(data)),
            ('Content-Disposition', content_disposition(wizard._filename())),
        ])
