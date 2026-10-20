# Part of ebshel_payment_allocation. See LICENSE file for full copyright and licensing details.
{
    "name": "Payment Allocation to Invoices & Bills",
    "version": "20.0.1.0.0",
    "category": "Accounting/Accounting",
    "summary": "Pick the open invoices or bills a customer payment or vendor payment "
    "settles - with the open amount of each - on the payment form itself; the payment "
    "is reconciled with them when it is confirmed.",
    "description": """
Payment Allocation to Invoices & Bills
======================================
A customer payment or vendor payment lists the partner's open invoices or bills,
each with its open amount. Tick what the money is for, or type the amount and let it
fill the oldest items; confirming the payment books one journal item per invoice and
reconciles each one. What is not allocated stays as an open credit.
    """,
    "author": "Ebshel Technologies",
    "maintainer": "Ebshel Technologies",
    "website": "https://ebshel.com",
    "support": "ebinpg777@gmail.com",
    "license": "OPL-1",
    "price": 39.0,
    "currency": "USD",
    "depends": ["account"],
    "data": [
        "security/ir.access.csv",
        "views/account_payment_views.xml",
    ],
    "images": ["static/description/banner.png"],
    "installable": True,
    "application": False,
}
