# Partner Statement of Account (`epg_partner_statement`)

Replaces `customer_vendor_statement`. One engine (`epg.partner.statement`) computes the
statement; the PDF, the Excel export, the e-mail, the monthly job and the portal all render
that same data.

| Feature | Where |
|---|---|
| Ledger per partner & currency: opening, running balance, closing due | wizard → *Print PDF* |
| Open items only (unpaid, days overdue, amount left) | wizard option |
| Ageing summary (Not due / 1-30 / 31-60 / 61-90 / 90+) | on the statement |
| Excel workbook (sheet per partner) | wizard → *Export Excel* |
| E-mail with PDF attached, logged in the partner chatter | wizard → *Send by E-mail* |
| Every partner with an open balance in one run | wizard checkbox |
| Period presets (this/last month, quarter, FY, last 12 months, custom) | wizard |
| Monthly auto-send to opted-in partners | partner form → *Monthly Statement by E-mail*; day in Settings |
| Portal self-service (`/my/statement`, PDF, open items) | My Account |
| Statutory numbers (Tax ID / GSTIN / PAN / DCI), patient names, bank details, payment QR, signature | printed when the fields exist |

Entry points: Contacts / partner form → *Statement* smart button or Action menu; Accounting →
Customers → *Statements of Account*.

Letterhead: Settings → Accounting → *Statements of Account* → *Letterhead* (default is the
standard document layout; at install the lab letterhead `sale_custom.external_layout_lab`
is picked automatically when present).

Tests: `-u epg_partner_statement --test-enable --test-tags /epg_partner_statement`.
