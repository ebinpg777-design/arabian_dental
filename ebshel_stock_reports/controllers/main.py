# Part of Advanced Stock Reports. See LICENSE file for full copyright and licensing details.
from werkzeug.exceptions import Forbidden, NotFound

from odoo import http
from odoo.http import content_disposition, request


class AdvancedStockReportsController(http.Controller):

    @http.route('/ebshel_stock_reports/xlsx/<string:model>/<int:res_id>', type='http', auth='user')
    def download_xlsx(self, model, res_id, **kwargs):
        if model not in request.env or not model.startswith('asr.report.'):
            raise NotFound()
        wizard = request.env[model].browse(res_id).exists()
        if not wizard or not hasattr(wizard, '_asr_xlsx'):
            raise NotFound()
        if not request.env.user.has_group('ebshel_stock_reports.group_user'):
            raise Forbidden()
        wizard.check_access('read')
        wizard = wizard.with_company(wizard.company_id)
        content = wizard._asr_xlsx()
        return request.make_response(content, headers=[
            ('Content-Type', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
            ('Content-Disposition', content_disposition(wizard._asr_xlsx_filename())),
        ])
