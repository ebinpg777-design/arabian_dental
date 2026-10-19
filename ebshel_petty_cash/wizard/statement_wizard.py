# Part of ebshel_petty_cash. See LICENSE file for full copyright and licensing details.
from collections import defaultdict
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError

REPORTS = {
    "cash_book": "ebshel_petty_cash.action_report_petty_cash_statement",
    "spending": "ebshel_petty_cash.action_report_petty_cash_spending",
    "aging": "ebshel_petty_cash.action_report_petty_cash_aging",
}


class PettyCashStatementWizard(models.TransientModel):
    """Printed petty cash reports over one or more funds:

    * Cash Book: opening balance, every posted movement with a running
      balance, closing balance - read from the ledger;
    * Spending Summary: paid vouchers of the period by category, fund,
      requester and payee, against the monthly budgets;
    * Advance Aging: advances still out at the end date, by days overdue.
    """

    _name = "ebshel.petty.cash.statement.wizard"
    _description = "Petty Cash Reports"

    report_type = fields.Selection(
        [
            ("cash_book", "Cash Book"),
            ("spending", "Spending Summary"),
            ("aging", "Advance Aging"),
        ],
        string="Report",
        required=True,
        default="cash_book",
    )
    fund_ids = fields.Many2many(
        "ebshel.petty.cash.fund",
        string="Funds",
        required=True,
        default=lambda self: self.env["ebshel.petty.cash.fund"].search([("state", "in", ("open", "suspended"))]),
    )
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
        return self.env.ref(REPORTS[self.report_type]).report_action(self)

    # ------------------------------------------------------------------
    # Cash book
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # Spending summary (company currency, so funds add up)
    # ------------------------------------------------------------------
    def _spending_data(self):
        self.ensure_one()
        lines = self.env["ebshel.petty.cash.expense.line"].search(
            [
                ("fund_id", "in", self.fund_ids.ids),
                ("state", "=", "posted"),
                ("date", ">=", self.date_from),
                ("date", "<=", self.date_to),
            ]
        )
        currency = (self.fund_ids.company_id[:1] or self.env.company).currency_id
        total = sum(lines.mapped("amount_total_company"))

        def table(key, limit=None):
            amounts = defaultdict(float)
            counts = defaultdict(set)
            for line in lines:
                name = key(line) or self.env._("Not set")
                amounts[name] += line.amount_total_company
                counts[name].add(line.expense_id.id)
            rows = sorted(amounts.items(), key=lambda item: -item[1])
            if limit:
                rows = rows[:limit]
            return [
                {
                    "name": name,
                    "amount": amount,
                    "count": len(counts[name]),
                    "share": amount / total * 100.0 if total else 0.0,
                }
                for name, amount in rows
            ]

        funds = []
        months = max(
            (self.date_to.year - self.date_from.year) * 12 + self.date_to.month - self.date_from.month + 1, 1
        )
        for fund in self.fund_ids:
            spent = sum(lines.filtered(lambda line, f=fund: line.fund_id == f).mapped("amount_total_company"))
            budget = fund._to_company_currency(fund.monthly_budget * months, self.date_to) if fund.monthly_budget else 0.0
            funds.append(
                {
                    "name": fund.name,
                    "spent": spent,
                    "budget": budget,
                    "used": spent / budget * 100.0 if budget else 0.0,
                }
            )
        expenses = lines.expense_id
        return {
            "currency": currency,
            "total": total,
            "tax": sum(lines.mapped("amount_tax")) if len(lines.currency_id) <= 1 else 0.0,
            "voucher_count": len(expenses),
            "average": total / len(expenses) if expenses else 0.0,
            "high_risk": len(expenses.filtered(lambda e: e.risk_level == "high")),
            "categories": table(lambda line: line.category_id.name),
            "funds": funds,
            "requesters": table(lambda line: line.expense_id.requested_by_id.name, limit=10),
            "payees": table(lambda line: line.partner_id.name, limit=10),
        }

    # ------------------------------------------------------------------
    # Advance aging
    # ------------------------------------------------------------------
    BUCKETS = (
        ("not_due", "Not Due"),
        ("1_30", "1-30 Days"),
        ("31_60", "31-60 Days"),
        ("61_90", "61-90 Days"),
        ("90_plus", "Over 90 Days"),
    )

    @api.model
    def _bucket(self, days):
        if days <= 0:
            return "not_due"
        if days <= 30:
            return "1_30"
        if days <= 60:
            return "31_60"
        if days <= 90:
            return "61_90"
        return "90_plus"

    def _aging_data(self):
        self.ensure_one()
        advances = self.env["ebshel.petty.cash.advance"].search(
            [("fund_id", "in", self.fund_ids.ids), ("state", "=", "paid"), ("date", "<=", self.date_to)],
            order="holder_id, due_date",
        )
        currency = (self.fund_ids.company_id[:1] or self.env.company).currency_id
        holders = {}
        totals = dict.fromkeys([key for key, _label in self.BUCKETS], 0.0)
        rows = []
        for advance in advances:
            days = (self.date_to - advance.due_date).days if advance.due_date else 0
            bucket = self._bucket(days)
            amount = advance.fund_id._to_company_currency(advance.amount_due, self.date_to)
            totals[bucket] += amount
            holder = holders.setdefault(
                advance.holder_id.id,
                {"name": advance.holder_id.display_name, "total": 0.0, **dict.fromkeys(totals, 0.0)},
            )
            holder[bucket] += amount
            holder["total"] += amount
            rows.append({"advance": advance, "days": max(days, 0), "bucket": bucket, "amount": amount})
        return {
            "currency": currency,
            "buckets": [(key, self.env._(label)) for key, label in self.BUCKETS],
            "holders": sorted(holders.values(), key=lambda h: -h["total"]),
            "rows": rows,
            "totals": totals,
            "total": sum(totals.values()),
        }
