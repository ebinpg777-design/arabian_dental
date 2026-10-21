# Part of ebshel_einvoice_report. See LICENSE file for full copyright and licensing details.
{
    "name": "GST E-Invoice Report",
    "version": "20.0.1.0.0",
    "category": "Accounting/Localizations/Reporting",
    "summary": "The period's GST e-invoices as an Excel workbook in the layout of the "
    "e-invoice bulk upload: buyer details per invoice, items per line, SGST / CGST / "
    "IGST / cess split exactly as Odoo computes them.",
    "description": """
GST E-Invoice Report
====================
One Excel workbook for the e-invoices of a period (B2B, SEZ, deemed export and
export customers; credit notes on request), in the column layout of the GST
e-invoice bulk upload:

* **Buyer Details** - one row per document: supply type, IGST on intra-state,
  document type and number, the buyer's GSTIN, legal name, place of supply and
  address with GST state codes, taxable value, SGST / CGST / IGST / cess, round
  off and invoice value.
* **E-Invoice Items** - one row per invoice line: description, service flag,
  HSN, quantity and GST unit code (UQC), unit price, gross, discount, taxable
  value, GST rate and the tax split.

The tax split is Odoo's own (the l10n_in tax tags), so the figures are the ones
Odoo's e-invoicing would send, and every document adds up to its invoice total.
    """,
    "author": "Ebshel Technologies",
    "maintainer": "Ebshel Technologies",
    "website": "https://ebshel.com",
    "support": "ebinpg777@gmail.com",
    "license": "OPL-1",
    "price": 29.0,
    "currency": "USD",
    "depends": ["account", "l10n_in"],
    "external_dependencies": {"python": ["xlsxwriter"]},
    "data": [
        "security/ir.access.csv",
        "wizard/einvoice_report_views.xml",
    ],
    "installable": True,
    "application": False,
}
