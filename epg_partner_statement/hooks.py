# -*- coding: utf-8 -*-
"""Pick the letterhead at install time.

The statement uses Odoo's standard document layout unless a branded letterhead template
is configured. If the lab's `sale_custom.external_layout_lab` exists in this database
it becomes the default, so the statement keeps the same head and foot as the invoice.
"""
from . import models  # noqa: F401  (make sure the config param name is one place)


def _adopt_branded_layout(env):
    """Use the house letterhead and its paper format when this database has one.

    Falls back to Odoo's standard document layout / default paper format elsewhere, so
    the module stays usable outside this lab.
    """
    params = env['ir.config_parameter'].sudo()
    template = 'sale_custom.external_layout_lab'
    if not params.get_param('epg_partner_statement.layout') and env.ref(template, raise_if_not_found=False):
        params.set_param('epg_partner_statement.layout', template)
    report = env.ref('epg_partner_statement.action_report_partner_statement', raise_if_not_found=False)
    # The letterhead is drawn in the statement body, so the page needs no header band:
    # this format starts 8 mm from the top instead of reserving 40 mm for a header.
    paperformat = env.ref('sale_custom.paperformat_lab_invoice', raise_if_not_found=False) \
        or env.ref('sale_custom.paperformat_lab', raise_if_not_found=False)
    if report and paperformat and not report.paperformat_id:
        report.paperformat_id = paperformat


def post_init_hook(env):
    _adopt_branded_layout(env)
