# Part of ebshel_petty_cash. See LICENSE file for full copyright and licensing details.
"""From the fixed approval rules of 20.0.1 to the approval matrix.

* a company's "second approval from X" becomes an administrators' level from X;
* replenishment approval switched off in every company: its level is archived;
* documents waiting for an approval get their approval chain.
"""
from odoo import SUPERUSER_ID, api


def _column_exists(cr, table, column):
    cr.execute(
        "SELECT 1 FROM information_schema.columns WHERE table_name = %s AND column_name = %s",
        (table, column),
    )
    return bool(cr.fetchone())


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    Level = env["ebshel.petty.cash.approval.level"].with_context(active_test=False)

    if _column_exists(cr, "res_company", "petty_cash_double_validation"):
        cr.execute(
            """SELECT id, petty_cash_double_validation_amount FROM res_company
                WHERE petty_cash_double_validation"""
        )
        for company_id, amount in cr.fetchall():
            Level.create(
                {
                    "name": "Second Approval",
                    "sequence": 20,
                    "company_id": company_id,
                    "min_amount": amount or 0.0,
                    "apply_on_expense": True,
                    "apply_on_advance": True,
                    "approver_type": "managers",
                }
            )

    if _column_exists(cr, "res_company", "petty_cash_replenish_approval"):
        cr.execute("SELECT bool_or(petty_cash_replenish_approval) FROM res_company")
        if cr.fetchone()[0] is False:
            level = env.ref("ebshel_petty_cash.approval_level_replenishment", raise_if_not_found=False)
            if level:
                level.active = False

    for model in (
        "ebshel.petty.cash.expense",
        "ebshel.petty.cash.advance",
        "ebshel.petty.cash.replenishment",
    ):
        documents = env[model].search([("state", "=", "to_approve"), ("approval_step_ids", "=", False)])
        if not documents:
            continue
        no_approval = documents._petty_cash_create_steps()
        for document in no_approval:
            document._petty_cash_set_approved(document.requested_by_id)
        for document in documents - no_approval:
            document.approval_step_ids.sorted("sequence")[:1]._petty_cash_activate()
