# Part of ebshel_invoice_analysis_fix. See LICENSE file for full copyright and licensing details.
{
    "name": "Invoices Analysis: Amounts in the Pivot",
    "version": "20.0.1.0.0",
    "category": "Hidden/Tools",
    "summary": "Fixes the empty Invoices Analysis and Bills Analysis pivot: Untaxed Amount, "
    "Total, Margin and Inventory Value show again, also in grouped lists.",
    "description": """
Invoices Analysis Fix
=====================
In Odoo 20 the Invoices Analysis and Bills Analysis pivot shows the rows and columns
but no amounts: Untaxed Amount, Total, Margin and Inventory Value stay blank, as do
their group totals in a grouped list. The graph still shows them.

These four fields are declared with the "sum in company currency" aggregator, and the
web client throws that very figure away in any group with a single currency - every
group, for a company that invoices in one currency.

This module declares them as plain sums, the way every other amount in Odoo is
declared. The client then shows the sum in a group with one currency and converts
it in a group that mixes currencies. Nothing else changes.
    """,
    "author": "Ebshel Technologies",
    "maintainer": "Ebshel Technologies",
    "website": "https://ebshel.com",
    "support": "ebinpg777@gmail.com",
    "license": "OPL-1",
    "price": 0.0,
    "currency": "USD",
    "depends": ["account"],
    "installable": True,
    "application": False,
    "auto_install": True,
}
