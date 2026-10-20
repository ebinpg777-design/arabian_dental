# Part of ebshel_invoice_round_off. See LICENSE file for full copyright and licensing details.
{
    "name": "Invoice Round Off",
    "version": "20.0.1.0.0",
    "category": "Accounting/Accounting",
    "summary": "A Round Off button on invoices and bills: one click makes the total a "
    "whole amount with a rounding line, and every new customer invoice can be "
    "rounded off automatically.",
    "description": """
Invoice Round Off
=================
Totals like 650.01 or 399.99 come from tax-inclusive prices. One click on
"Round Off" adds Odoo's rounding line so the invoice total becomes 650.00 or
400.00, booked on the rounding profit or loss account; "Remove Round Off" takes
it away again. A company setting rounds off every new customer invoice as it is
created.
    """,
    "author": "Ebshel Technologies",
    "maintainer": "Ebshel Technologies",
    "website": "https://ebshel.com",
    "support": "ebinpg777@gmail.com",
    "license": "OPL-1",
    "price": 19.0,
    "currency": "USD",
    "depends": ["account"],
    "data": [
        "views/account_move_views.xml",
        "views/res_config_settings_views.xml",
    ],
    "images": ["static/description/banner.png"],
    "installable": True,
    "application": False,
}
