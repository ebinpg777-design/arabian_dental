# Invoices Analysis: Amounts in the Pivot

Fixes the empty **Invoices Analysis** / **Bills Analysis** pivot in Odoo 20: the rows
and columns are there, but Untaxed Amount, Total, Margin and Inventory Value show no
value - in the pivot and in the group totals of a grouped list. The graph shows them.

**Cause.** `account/report/account_invoice_report.py` declares these fields with
`aggregator='sum_currency'`. For every monetary field the web client asks for
`<field>:sum_currency` beside the field's own aggregate, as the converted figure for
groups that mix currencies, and discards it in groups with one currency
(`web/static/src/views/pivot/pivot_model.js` `_getMeasurements`,
`web/static/src/model/relational_model/utils.js` `getAggregatesFromGroupData`). When
the field's own aggregate *is* `sum_currency`, its own figure is discarded too.
Odoo fixed the client in odoo/odoo@714bc0538121 (2026-10-08).

**Fix.** The four fields are declared with `aggregator='sum'`
(`models/account_invoice_report.py`), the client's ordinary path for amounts: the sum
in a single-currency group, the converted sum in a mixed one. Average Price
(`avg_currency`) was never blank and is left alone. Still correct once the Odoo core
includes the upstream fix. Auto-installs with `account`; no configuration.
