# -*- coding: utf-8 -*-
"""Payment QR moved from a static file (fetched by wkhtmltopdf over HTTP on every print) to
a company field embedded inline. Seed the existing companies with the same image."""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    Company = env['res.company'].sudo()
    default = Company._default_payment_qr()
    if default:
        for company in Company.search([('payment_qr', '=', False)]):
            company.payment_qr = default
