# GSTR-1 Excel Register

Accounting › Reporting › GST Reports › **GSTR-1 Register**: pick the period, a section
or *All in one*, and download. The workbook has one sheet per section of the GSTR-1
working register, each with the register's title, the period, the section's name, the
column headers, the rows and a Grand Total line. Numbers are numbers, so Excel adds them.

| Sheet | What it holds | Grouped by |
|---|---|---|
| B2B | invoices to registered customers (regular, composition, UIN holders) | customer |
| B2B Detailed | the same, one row per invoice and tax rate, with GSTIN and place of supply | invoice, rate |
| B2CS | invoices to unregistered customers: all intra-state, inter-state up to ₹2.5 lakh | place of supply, rate |
| B2CS Detailed | the same, one row per invoice and rate | invoice, rate |
| HSN | the HSN/SAC summary, credit notes deducted | HSN, UQC, rate |
| HSN B2C-B2B | the HSN summary split by B2B / B2C / Export | HSN, UQC, rate, class |
| EXP | export, SEZ and deemed-export invoices with shipping bill and port | invoice |
| CDNR | credit notes to registered customers | credit note, rate |
| CDNUR | credit notes to unregistered and overseas customers | credit note, rate |
| DOCS | the document series per journal: first and last number, issued, cancelled, net | journal |
| Nil Rated | nil-rated, exempt and non-GST supplies, inter / intra × registered / unregistered | the 4 × 3 matrix |
| HSN INV-CR | invoices and credit notes side by side per HSN | HSN, UQC, rate, class |
| Tax Check | documents whose tax does not fit their place of supply (IGST inside the seller's state, CGST/SGST outside it), and lines worth something with no tax at all, which the other sheets would count as a supply at 0 % | document, kind |

## Where the figures come from

Every sheet is a different grouping of **one list of invoice lines**: the product lines
of every posted customer invoice and credit note whose invoice date is in the period,
for the companies chosen. A line's taxable value is its company-currency balance (after
discount) and its tax split comes from l10n_in's `_l10n_in_prepare_tax_details`, which
sorts each tax by its GST tax tag into IGST / CGST / SGST / cess. So the figures are the
ones Odoo's own GST filing would carry, and no two sheets can disagree.

* Intra-state versus inter-state is judged against the **seller's** state: the companies
  chosen must share one state. The place of supply is the invoice's GST state (`l10n_in_state_id`,
  which Odoo sets from the customer's state), else the customer's state.
* The **customer's name** comes from `_customer_name(move)`, a hook a database with a
  separate billing name can override.
* **Nil Rated** reads the taxes' *Indian Tax Type* (nil rated / exempt / non-GST).
* **DOCS** counts posted and cancelled documents that carry a number.
* **Tax Check** lists a line with no tax only when it is worth something: free-of-charge
  lines and lines with a nil-rated, exempt or non-GST tax are not mistakes. A charge that
  is no supply at all (a cheque-return charge recovered from a customer, say) belongs on
  a nil-rated or non-GST tax, so that it lands on the Nil Rated sheet instead of B2CS.

## Compared with the register the lab used before (Odoo 17)

Same sections and column order. Three things are deliberately different: the *Total Tax
Amount* column is the sum of the four taxes on every sheet (the old register left it 0.00
on B2B and dropped IGST from it on B2CS); amounts are written as numbers, not text with
trailing spaces; sheet names are the section's name, with the period in the sheet's
header, so they are never cut off at 31 characters.

## Odoo 20 notes

* The download goes through `/gst_report/xlsx/<wizard id>` as the current user; nothing
  is stored as an attachment.
* Tests run on a database with `l10n_in` installed (`L10nInTestInvoicingCommon`):
  `--test-tags /ebshel_gst_report`.
