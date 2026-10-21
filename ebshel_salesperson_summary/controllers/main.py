# Part of ebshel_salesperson_summary. See LICENSE file for full copyright and licensing details.
from odoo import http
from odoo.http import request
from odoo.http.stream import content_disposition


class SalespersonSummaryController(http.Controller):

    @http.route('/salesperson_summary/xlsx/<int:wizard_id>', type='http', auth='user')
    def xlsx(self, wizard_id, **kwargs):
        """The workbook, built on request: nothing is stored as an attachment."""
        wizard = request.env['salesperson.summary.wizard'].browse(wizard_id).exists()
        if not wizard:
            raise request.not_found()
        wizard.check_access('read')
        data = wizard._xlsx()
        return request.make_response(data, headers=[
            ('Content-Type', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
            ('Content-Length', len(data)),
            ('Content-Disposition', content_disposition(wizard._filename('xlsx'))),
        ])
