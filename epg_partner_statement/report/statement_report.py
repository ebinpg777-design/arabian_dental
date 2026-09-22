# -*- coding: utf-8 -*-
from odoo import api, fields, models

from ..models.statement_engine import STATEMENT_TYPES


class ReportPartnerStatement(models.AbstractModel):
    _name = 'report.epg_partner_statement.report_statement'
    _description = 'Partner Statement (PDF)'

    @api.model
    def _get_report_values(self, docids, data=None):
        data = data or {}
        # A report action carrying `data` is downloaded without the ids in the URL: Odoo's
        # client passes them in the context instead. Take them from wherever they are.
        if not docids:
            docids = data.get('ids') or (data.get('context') or {}).get('active_ids') or []
        Engine = self.env['epg.partner.statement']
        options = Engine.normalize_options(data)
        partners = self.env['res.partner'].browse(docids)
        statements = Engine.compute(partners, options)
        company = options['company']
        return {
            'doc_ids': partners.ids,
            'doc_model': 'res.partner',
            'docs': partners,
            'statements': statements,
            'company': company,
            'res_company': company,
            'options': options,
            'date_from': options['date_from'],
            'date_to': options['date_to'],
            'statement_type_label': dict(STATEMENT_TYPES)[options['statement_type']],
            'layout': Engine.layout_template(),
            'printed_on': fields.Date.context_today(self),
            'labels': Engine.labels(options['statement_type']),
            'payment_qr': company.payment_qr if 'payment_qr' in company._fields else False,
            'signature': company.invoice_signature if 'invoice_signature' in company._fields else False,
        }
