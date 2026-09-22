# -*- coding: utf-8 -*-
"""Customer-invoice tiles were pinned to `account.action_move_out_invoice_type`, an action
no menu opens (Accounting uses `action_move_out_invoice`, Sales `action_invoice_salesteams`)
- so the ribbon never showed on either Invoices screen. Re-pin them to the Accounting menu
and list the others as "Also On"; (re)generate the route tiles the same way."""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['crm.team']._lab_sync_all_route_tiles()
    env['crm.team']._lab_pin_invoice_tiles()
