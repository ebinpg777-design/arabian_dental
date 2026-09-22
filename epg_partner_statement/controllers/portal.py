# -*- coding: utf-8 -*-
"""My Account > Statement of Account: a customer downloads their own statement.

Scope is the logged-in user's commercial partner only; the data is computed with
sudo (portal users cannot read account.move.line) but the partner is never taken from
the request, so nobody sees anyone else's statement.
"""
from odoo import _, fields, http
from odoo.http import request
from odoo.addons.portal.controllers.portal import CustomerPortal


class PortalStatement(CustomerPortal):

    def _portal_partner(self):
        return request.env.user.partner_id.commercial_partner_id

    @http.route(['/my/statement'], type='http', auth='user', website=True)
    def portal_statement(self, date_from=None, date_to=None, statement_type='receivable', **kw):
        partner = self._portal_partner()
        Engine = request.env['epg.partner.statement'].sudo()
        d1, d2 = Engine.period_dates('this_month', request.env.company)
        options = Engine.normalize_options({
            'statement_type': statement_type if statement_type in ('receivable', 'payable', 'both') else 'receivable',
            'date_from': date_from or d1, 'date_to': date_to or d2,
            'company_id': request.env.company.id, 'show_ageing': True})
        st = Engine.compute(partner, options)[partner.id]
        values = self._prepare_portal_layout_values()
        values.update({
            'page_name': 'statement', 'partner': partner, 'statement': st, 'options': options,
            'company': request.env.company, 'labels': Engine.labels(options['statement_type']),
        })
        return request.render('epg_partner_statement.portal_statement_page', values)

    @http.route(['/my/statement/pdf'], type='http', auth='user', website=True)
    def portal_statement_pdf(self, date_from=None, date_to=None, statement_type='receivable', open_items='0', **kw):
        partner = self._portal_partner()
        Engine = request.env['epg.partner.statement'].sudo()
        d1, d2 = Engine.period_dates('this_month', request.env.company)
        options = Engine.normalize_options({
            'statement_type': statement_type if statement_type in ('receivable', 'payable', 'both') else 'receivable',
            'date_from': date_from or d1, 'date_to': date_to or d2,
            'company_id': request.env.company.id, 'open_items_only': open_items == '1', 'show_ageing': True})
        pdf = Engine.render_pdf(partner, options)
        filename = "Statement %s %s.pdf" % ((partner.name or 'account').replace('/', '-'), options['date_to'])
        return request.make_response(pdf, headers=[
            ('Content-Type', 'application/pdf'), ('Content-Length', str(len(pdf))),
            ('Content-Disposition', 'attachment; filename="%s"' % filename)])
