# -*- coding: utf-8 -*-
"""Print the statement on the letterhead's own paper format.

It was rendering with the branded layout but the DEFAULT paper format, so its margins did
not match the header/footer the layout draws.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    report = env.ref('epg_partner_statement.action_report_partner_statement', raise_if_not_found=False)
    paperformat = env.ref('sale_custom.paperformat_lab', raise_if_not_found=False)
    if report and paperformat and not report.paperformat_id:
        report.paperformat_id = paperformat
