# Part of ebshel_petty_cash. See LICENSE file for full copyright and licensing details.
from odoo import Command, fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    petty_cash_advance_account_id = fields.Many2one(
        "account.account",
        check_company=True,
        domain="[('account_type', 'in', ('asset_current', 'asset_receivable')), ('reconcile', '=', True)]",
        help="Receivable account holding the cash advances (IOUs) paid out of petty "
        "cash until they are settled. Created on first use when empty.",
    )
    petty_cash_double_validation = fields.Boolean(
        string="Petty Cash Second Approval",
        help="Vouchers and advances from the threshold amount up need a second "
        "approval by a Petty Cash Manager.",
    )
    petty_cash_double_validation_amount = fields.Monetary(
        string="Second Approval From",
        currency_field="currency_id",
        help="In company currency.",
    )
    petty_cash_self_approval = fields.Boolean(
        string="Allow Petty Cash Self-Approval",
        help="Let approvers approve the documents they requested themselves.",
    )
    petty_cash_replenish_approval = fields.Boolean(
        string="Approve Replenishments",
        default=True,
        help="Replenishment requests wait for a Petty Cash Manager's approval.",
    )
    petty_cash_duplicate_days = fields.Integer(
        string="Duplicate Voucher Window (Days)",
        default=30,
        help="Flag vouchers with the same amount and payee or receipt reference "
        "within this many days. 0 disables the check.",
    )
    petty_cash_advance_due_days = fields.Integer(
        string="Advance Settlement Days",
        default=7,
        help="Default number of days an advance holder has to settle.",
    )

    def _petty_cash_advance_account(self):
        """The advance account of the company, created on first use."""
        self.ensure_one()
        if self.petty_cash_advance_account_id:
            return self.petty_cash_advance_account_id
        Account = self.env["account.account"].sudo().with_company(self)
        reference = Account.search(
            [
                *Account._check_company_domain(self),
                ("account_type", "=", "asset_current"),
            ],
            order="code desc",
            limit=1,
        )
        code = Account._search_new_account_code(reference.code or "1100")
        account = Account.create(
            {
                "name": self.env._("Petty Cash Advances"),
                "code": code,
                "account_type": "asset_current",
                "reconcile": True,
                "company_ids": [Command.set(self.ids)],
            }
        )
        self.sudo().petty_cash_advance_account_id = account
        return account
