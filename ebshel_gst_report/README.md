# GSTR-1 Excel Register

Accounting › Reporting › GST Reports › **GSTR-1 Register**: pick the period, a section
or *All in one*, and download. The workbook has one sheet per section of the GSTR-1
working register, each with the register's title, the period, the section's name, the
column headers, the rows and a Grand Total line. Numbers are numbers, so Excel adds them.

| Sheet | What it holds | Grouped by |
|---|---|---|
| B2B | invoices to registered customers (regular, composition, UIN holders) | customer |
| B2B Detailed | the same, one row per invoice and tax rate, with GSTIN and place of supply | invoice, rate |
| B2CS | invoices to unregistered customers, **less their credit notes**: all intra-state, inter-state up to ₹2.5 lakh | place of supply, rate |
| B2CS Detailed | the same, one row per document and rate; a credit note is a row of negative amounts | document, rate |
| HSN | the HSN/SAC summary, credit notes deducted; a line worth something with no HSN has a row of its own (blank HSN) | HSN, UQC, rate |
| HSN B2C-B2B | the HSN summary split by B2B / B2C / Export | HSN, UQC, rate, class |
| EXP | export, SEZ and deemed-export invoices with shipping bill and port | invoice |
| CDNR | credit notes to registered customers | credit note, rate |
| CDNUR | credit notes to overseas customers and against large inter-state (B2CL) invoices; the others are deducted in B2CS | credit note, rate |
| DOCS | the document series per journal: first and last number, issued, cancelled, net | journal |
| Nil Rated | nil-rated, exempt and non-GST supplies, inter / intra × registered / unregistered | the 4 × 3 matrix |
| HSN INV-CR | invoices and credit notes side by side per HSN | HSN, UQC, rate, class |
| Tax Check | documents whose tax does not fit their place of supply (IGST inside the seller's state, CGST/SGST outside it), lines worth something with no tax at all, which the other sheets would count as a supply at 0 %, and lines worth something with no HSN/SAC code | document, kind |

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
* **The sheets reconcile**: HSN taxable = B2B + B2CS + exports − CDNR − CDNUR, and the
  same for the tax. For that, B2CS is net of the credit notes to small unregistered
  customers (table 7 takes a net figure and table 9B has no line for them), and the HSN
  summary carries every line worth something, with or without an HSN.
* **A free line** (price 0) takes its rate from its own taxes, so its quantity counts in
  its HSN's row at that rate instead of standing apart at 0 %. Core names a tax IGST /
  CGST / SGST only once it has an amount, hence the fallback. A line with no value, no
  HSN and no rate (an empty line) is left out.
* A credit note is small or large (B2CS or B2CL) as the invoice it reverses is; one with
  no link to an invoice is judged on its own total.
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
