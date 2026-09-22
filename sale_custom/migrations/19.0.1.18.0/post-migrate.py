# -*- coding: utf-8 -*-
"""Give every customer 30-day terms, and make 30 days the default for new ones.

The lab bills a clinic and expects the month's work settled the following month,
but no partner carried a payment term at all: 0 of 6,762. With nothing set, Odoo
dated every invoice due on the day it was raised - which is why `invoice_date_due`
equals `invoice_date` on 99.6% of the invoices on file, and why nothing in the
system could tell a bill that is late from one raised this morning.

`account.account_payment_term_30days` is the term used - the shared "30 Days"
(nb_days=30, days_after). NOT the company's own "30 Net Days", which is configured
with nb_days=0 and would have kept due date = invoice date.

Only partners with NOTHING set are touched, so re-running this changes nothing and
a clinic later given different terms keeps them. Invoices already posted keep the
due dates they were raised with; this governs what is billed from now on.
(client, 2026-08-24)
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    from odoo import api, SUPERUSER_ID
    env = api.Environment(cr, SUPERUSER_ID, {})
    term = env.ref('account.account_payment_term_30days', raise_if_not_found=False)
    if not term:
        _logger.warning("sale_custom: no 30-day payment term found, nothing set")
        return

    Partner = env['res.partner'].with_context(active_test=False)
    for company in env['res.company'].search([]):
        # company_dependent: the value is held per company, so it is written from
        # inside each one. The term itself is shared, so both point at the same rule.
        scoped = Partner.with_company(company)
        todo = scoped.search([('property_payment_term_id', '=', False)])
        if todo:
            todo.write({'property_payment_term_id': term.id})
        # What a partner created from now on starts with.
        env['ir.default'].set('res.partner', 'property_payment_term_id', term.id,
                              company_id=company.id)
        _logger.info("sale_custom: %s - %s partners given 30-day terms, and it is "
                     "now the default for new ones", company.name, len(todo))
