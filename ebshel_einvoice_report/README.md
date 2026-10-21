# GST E-Invoice Report

An Excel workbook of a period's GST e-invoices, in the column layout of the e-invoice
bulk upload. **Accounting ▸ Reporting ▸ E-Invoice Report**: pick the period (and the
companies of one state), optionally include credit notes, **Download Excel**.

| Sheet | One row per | Columns |
|---|---|---|
| Buyer Details | document | Supply Type Code (B2B, SEZWP/SEZWOP, EXPWP/EXPWOP, DEXP), Igst On Intra (Y/N), Document Type (INV, CRN, DBN), number, date, buyer GSTIN, name, POS and state code (GST codes), address, PIN, phone, e-mail, taxable value, SGST, CGST, IGST, cess, state cess, discount, other charges, round off, invoice value |
| E-Invoice Items Sheet | invoice line | document, Sl. No., description, Is_Service (Y/N from the SAC), HSN, quantity, unit (GST UQC from the unit of measure), unit price, gross, pre-tax discount, taxable value, GST rate, SGST, CGST, IGST, cess rate and amount, state cess, item total |

* Customers covered: registered (regular, composition, UIN holders), SEZ, deemed export
  and export (`l10n_in_gst_treatment`); posted documents only.
* The tax split is Odoo's own (`l10n_in`'s `_l10n_in_prepare_tax_details`, by the GST
  tax tags), the figures Odoo's e-invoicing would send; each document's taxable value
  + taxes + round off equals its invoice total.
* Line discounts appear per item (Pre Tax Discount) and are already out of the taxable
  value, so the invoice-level discount column stays 0.
* The buyer's name is the commercial partner's name; `_buyer_name(move)` is a hook for
  databases that keep a separate billing name.

Depends on `account` and `l10n_in`; needs the `xlsxwriter` Python package (shipped with
Odoo).
