# Part of ebshel_salesperson_summary. See LICENSE file for full copyright and licensing details.
{
    "name": "Salesperson Summary",
    "version": "20.0.1.0.0",
    "category": "Accounting/Accounting",
    "summary": "Receivables by salesperson: opening, billed, received, returns and "
    "outstanding for the period, with the collection rate - a designed PDF, an "
    "on-screen view and an Excel workbook.",
    "description": """
Salesperson Summary
===================
What each salesperson's customers owed, were billed and paid in a period - the
receivable ledger summed by the salesperson on the customer.

* **Summary** - one line per salesperson: customers, opening balance, billed,
  received, returns & adjustments, outstanding, the collection rate and each
  salesperson's share of what is outstanding; a billed-against-received chart.
* **Detailed** - the same, with every customer listed under its salesperson
  (optionally a new page per salesperson).
* **Outputs** - a designed A4 PDF, the same report on screen, and an Excel
  workbook (a summary sheet and a customer sheet with filters).

Figures come from posted receivable journal items, so every column agrees with
the partner ledger; outstanding = opening + billed - received - returns.
    """,
    "author": "Ebshel Technologies",
    "maintainer": "Ebshel Technologies",
    "website": "https://ebshel.com",
    "support": "ebinpg777@gmail.com",
    "license": "OPL-1",
    "price": 29.0,
    "currency": "USD",
    "depends": ["account"],
    "external_dependencies": {"python": ["xlsxwriter"]},
    "data": [
        "security/ir.access.csv",
        "report/report_actions.xml",
        "report/salesperson_summary_templates.xml",
        "wizard/salesperson_summary_views.xml",
    ],
    "assets": {
        "web.report_assets_common": [
            "ebshel_salesperson_summary/static/src/scss/salesperson_summary.scss",
        ],
    },
    "installable": True,
    "application": False,
}
