# Part of ebshel_analytic_batch_fix. See LICENSE file for full copyright and licensing details.
{
    "name": "Analytic Widget: No 'Component is destroyed'",
    "version": "20.0.1.0.0",
    "category": "Hidden/Tools",
    "summary": "Fixes the 'Uncaught Promise > Component is destroyed' error when invoices, bills "
    "or journal items with analytic distributions are opened and left quickly.",
    "description": """
Analytic Widget Fix
===================
Odoo's analytic distribution widget reads its analytic accounts through a batching
service that gathers the reads of every line into one request - but sends that
request through whichever line asked first. When that line's widget is closed before
the request leaves (open an invoice and move on, reload a list, change page), Odoo
refuses the request with "Component is destroyed", and every other line waiting on
it shows the error.

This module sends the shared request on the service itself, so one closed widget can
no longer fail the others. Nothing else changes.
    """,
    "author": "Ebshel Technologies",
    "maintainer": "Ebshel Technologies",
    "website": "https://ebshel.com",
    "support": "ebinpg777@gmail.com",
    "license": "OPL-1",
    "price": 0.0,
    "currency": "USD",
    "depends": ["analytic"],
    "assets": {
        "web.assets_backend": [
            "ebshel_analytic_batch_fix/static/src/batched_orm.js",
        ],
    },
    "installable": True,
    "application": False,
    "auto_install": True,
}
