# -*- coding: utf-8 -*-
{
    'name': 'Bank Reconciliation',
    'version': '19.0.1.4.0',
    'category': 'Accounting/Accounting',
    'summary': 'Tick bank ledger lines against the passbook, post bank charges and '
               'interest, and print the Bank Reconciliation Statement',
    'description': """
Bank Reconciliation, the way these books are kept
==================================================
Odoo Community has no bank reconciliation screen, and the Enterprise one is built on
imported bank statements - of which this database has none. Receipts here are posted
straight into each bank's GL account (CANERA BANK, UNION BANK, DHANALAKSHMI BANK), and
not one of the 11,339 bank-account lines had ever been checked against a bank.

This is the Indian Bank Reconciliation Statement, done on the ledger itself:

* **A reconciliation per bank and statement date.** Pick the bank journal; the bank
  GL account is the one its entries actually post to (detected, and editable). Enter the
  passbook closing balance.
* **Tick the lines the bank has cleared**, deposits and withdrawals side by side, each
  with its cleared date. Posted entries are never edited: a clearance is its own
  record, so who cleared what, and when, stays on file.
* **The statement works out itself**: balance as per books, less deposits not yet
  credited, add payments not yet presented, against the balance as per bank - and the
  difference, live, as lines are ticked.
* **Bank charges and interest** the books do not have yet are posted from the screen
  as journal entries in that bank, already cleared.
* **Mark Reconciled** only when the difference is zero; the cleared lines are then
  locked. **Print the BRS** as PDF or Excel for any date.

Assistants
----------
* **Banks overview** - one card per bank: book balance, outstanding and stale entries,
  when it was last reconciled; one click to reconcile or continue.
* **Find a passbook entry** - type one passbook line; it finds the outstanding entry, or
  the 2-4 entries the bank added up into that one figure (a deposit slip), and ticks
  them on the passbook's date.
* **Why is there a difference?** - the entry to tick or untick, two entries the bank shows
  as one, an entry posted the wrong way round, a date after the statement, swapped digits,
  or a bank-only item to post.
* **Ageing** - outstanding entries by age, stale ones flagged (90 days by default), and an
  age filter.
* **First reconciliation** - offers to tick the opening balance.
* **Bank items** - charges, interest, or any other bank-only debit or credit against a
  chosen account, posted already cleared.
* **Tick up to a date**, keyboard ticking, and **Start next statement** carrying the
  previous statement's settings.
* **Day totals** - outstanding entries added up per day and per batch (a branch's cash, a
  branch's transfers, the parties), the way the passbook shows them, ticked in one go.
* **Where from** - every line classed as cash paid in, bank transfer, party, or other,
  with the branch read off the label; filter by it.
* **Batches in the finder** - a branch's whole day of cash, or a whole day, however many
  entries, matched to one passbook figure.
* **Undo** - the last ticks, unticks and date changes put back as they were.
* **Possible duplicates** - the same party's same amount within three days, outstanding.
* **Queries with the bank** - a note on any line, shown on the screen and on the BRS, and
  a ready-to-sign **query letter** to the branch.
* **Entry details** - click an entry: its journal entry and every line, the payment, the
  invoices it settled, the party's ledger balance, who cleared it, and the other
  outstanding entries of the same amount.
* **Bank entries** - receipts from and payments to parties, transfers, charges and
  interest posted from the screen in the bank's journal, ticked or left outstanding.
* **Progress** of the statement, and **shift-click** to tick a range.
* **Passbook balance check** - type each passbook line's balance too; a skipped line is
  caught at once, with the missing amount to look for, and the last balance becomes the
  statement's bank balance in one click.
* **Short credits** - an entry the bank credited a little short (it kept its charges) is
  offered as a near match; one click ticks it and books the shortfall as bank charges.
* **Outstanding trend** - a 12-week line of outstanding entries on every bank card.
* **Carried from the last statement** - a filter for the entries still outstanding since
  before the last reconciliation, the ones worth chasing.

User guide with screenshots: ``doc/USER_GUIDE.pdf`` (source: ``doc/USER_GUIDE.md``).
""",
    'author': 'Ebin P G',
    'website': 'https://www.arabiandentallab.com',
    'depends': ['account', 'mail'],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'report/report.xml',
        'report/brs_templates.xml',
        'report/query_letter.xml',
        'views/bank_reconciliation_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'lab_bank_reconciliation/static/src/scss/bank_rec.scss',
            'lab_bank_reconciliation/static/src/js/bank_rec_screen.js',
            'lab_bank_reconciliation/static/src/xml/bank_rec_screen.xml',
            'lab_bank_reconciliation/static/src/js/bank_rec_overview.js',
            'lab_bank_reconciliation/static/src/xml/bank_rec_overview.xml',
        ],
    },
    'license': 'LGPL-3',
    'installable': True,
    'application': False,
}
