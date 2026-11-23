# -*- coding: utf-8 -*-
"""Cheques settled when received, banked when deposited (client, 2026-11-23).

* Every cheque already recorded keeps the old way (`accounting = 'clearing'`): their
  receipts are in the books already - every in-hand cheque brought over from Odoo 17
  carries the receipt Odoo 17 posted - so depositing or clearing them books nothing new.
* Each bank journal gets the "PDC - <bank>" account Odoo 17 kept for it, matched by name
  ("PDC - Federal Bank(Current A/C) - 6762" for "Federal Bank(Current A/C) - 6762",
  "PDC -KGB OD ACC" for "KGB OD Account"). A bank with none is set by hand on the journal.
"""
import logging
import re

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def _key(name):
    key = re.sub(r'[^0-9A-Z]', '', (name or '').upper())
    return key.replace('ACCOUNT', 'AC').replace('ACC', 'AC')


def migrate(cr, version):
    if not version:
        return
    cr.execute("UPDATE lab_cheque SET accounting = 'clearing'")
    _logger.info("lab_finance_ops: %s recorded cheque(s) keep being booked when cleared", cr.rowcount)
    env = api.Environment(cr, SUPERUSER_ID, {})
    pdc_accounts = env['account.account'].search([
        ('account_type', '=', 'asset_current'), ('reconcile', '=', True), ('name', '=ilike', 'PDC%')])
    by_key = {}
    for account in pdc_accounts:
        by_key.setdefault(_key(re.sub(r'^\s*PDC\s*-?\s*', '', account.name, flags=re.I)), account)
    for journal in env['account.journal'].search([('type', '=', 'bank'), ('lab_pdc_account_id', '=', False)]):
        account = by_key.get(_key(journal.default_account_id.name))
        if account and journal.company_id in account.company_ids:
            journal.lab_pdc_account_id = account
            _logger.info("lab_finance_ops: %s holds its cheques in %s", journal.name, account.display_name)
