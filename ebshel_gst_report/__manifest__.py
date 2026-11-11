# Part of ebshel_gst_report. See LICENSE file for full copyright and licensing details.
{
    "name": "GSTR-1 Excel Register",
    "version": "20.0.1.2.0",
    "category": "Accounting/Localizations/Reporting",
    "summary": "The GSTR-1 voucher register of a period as one Excel workbook: B2B, B2CS, "
    "HSN summary, exports, credit notes, document series and nil-rated supplies, with the "
    "SGST / CGST / IGST / cess split exactly as Odoo computes it.",
    "description": """
GSTR-1 Excel Register
=====================
One wizard under Accounting > Reporting > GST Reports downloads the period's GSTR-1
working register as an Excel workbook, one sheet per section, or any single section:

* **B2B** - registered customers, one row per customer: voucher count, taxable value,
  IGST / CGST / SGST / cess, total tax and invoice value.
* **B2B Detailed** - one row per invoice and tax rate, with GSTIN and place of supply.
* **B2CS** and **B2CS Detailed** - unregistered customers by place of supply and rate,
  net of their credit notes, as table 7 wants it.
* **HSN** and **HSN B2C-B2B** - the HSN/SAC summary by UQC and rate, credit notes
  deducted, and the same split by B2B / B2C / Export. It adds up to the other sheets.
* **EXP** - export, SEZ and deemed-export invoices with shipping bill and port.
* **CDNR** / **CDNUR** - credit notes to registered customers, and to unregistered ones
  against exports and large inter-state invoices (the small ones are deducted in B2CS).
* **Docs** - the document series: first and last number, issued, cancelled, net.
* **Nil Rated** - nil-rated, exempt and non-GST supplies, inter / intra, registered /
  unregistered.
* **HSN Wise INV-CR** - invoices and credit notes side by side per HSN.
* **Tax Check** - documents whose tax does not fit their place of supply (IGST inside
  the seller's state, CGST/SGST outside it) and lines worth something that carry no tax
  or no HSN/SAC code, to put right before filing.

The tax split is Odoo's own (the l10n_in tax tags), so the figures are the ones Odoo's
GST filing would carry, in the company currency.
    """,
    "author": "Ebshel Technologies",
    "maintainer": "Ebshel Technologies",
    "website": "https://ebshel.com",
    "support": "ebinpg777@gmail.com",
    "license": "OPL-1",
    "price": 39.0,
    "currency": "USD",
    "depends": ["account", "l10n_in"],
    "external_dependencies": {"python": ["xlsxwriter"]},
    "data": [
        "security/ir.access.csv",
        "wizard/gst_report_views.xml",
    ],
    "installable": True,
    "application": False,
}
