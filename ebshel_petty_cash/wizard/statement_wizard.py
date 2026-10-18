# Part of ebshel_petty_cash. See LICENSE file for full copyright and licensing details.
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError


class PettyCashStatementWizard(models.TransientModel):
    """Cash book of one or more funds between two dates, read from the ledger:
    opening balance, every posted movement with a running balance, closing."""

    _name = "ebshel.petty.cash.statement.wizard"
    _description = "Petty Cash Fund Statement"

    fund_ids = fields.Many2many("ebshel.petty.cash.fund", string="Funds", required=True)
    date_from = fields.Date(
        required=True, default=lambda self: fields.Date.context_today(self).replace(day=1)
    )
    date_to = fields.Date(required=True, default=fields.Date.context_today)

    @api.constrains("date_from", "date_to")
    def _check_dates(self):
        for wizard in self:
            if wizard.date_from > wizard.date_to:
                raise UserError(self.env._("The start date must precede the end date."))

    def action_print(self):
        self.ensure_one()
        return self.env.ref("ebshel_petty_cash.action_report_petty_cash_statement").report_action(self)

    def _statement_data(self):
        """One dict per fund: opening, lines (with running balance), totals, closing."""
        self.ensure_one()
        result = []
        for fund in self.fund_ids:
            foreign = fund.currency_id != fund.company_id.currency_id
            opening = fund._get_balance(self.date_from - timedelta(days=1))
            lines = (
                self.env["account.move.line"]
                .sudo()
                .search(
                    [
                        ("account_id", "=", fund.account_id.id),
                        ("parent_state", "=", "posted"),
                        ("date", ">=", self.date_from),
                        ("date", "<=", self.date_to),
                    ],
                    order="date, move_id, id",
                )
            )
            running = opening
            rows = []
            total_in = total_out = 0.0
            for line in lines:
                amount = line.amount_currency if foreign else line.balance
                running += amount
                total_in += max(amount, 0.0)
                total_out += max(-amount, 0.0)
                rows.append(
                    {
                        "date": line.date,
                        "number": line.move_id.name,
                        "reference": line.move_id.ref or "",
                        "label": line.name or "",
                        "partner": line.partner_id.display_name or "",
                        "in": max(amount, 0.0),
                        "out": max(-amount, 0.0),
                        "balance": running,
                    }
                )
            result.append(
                {
                    "fund": fund,
                    "currency": fund.currency_id,
                    "opening": opening,
                    "rows": rows,
                    "total_in": total_in,
                    "total_out": total_out,
                    "closing": opening + total_in - total_out,
                }
            )
        return result
