# -*- coding: utf-8 -*-
"""A Bank Reconciliation Statement, built on the ledger this database actually keeps.

Receipts and payments here post straight into each bank's GL account; there are no
imported bank statements to match against. So a reconciliation is the accountant ticking
the bank-account lines that appear in the bank's passbook, each with the date the bank
cleared it, and the statement working out the rest:

    Balance as per Books
  - Deposits not yet credited by the bank
  + Payments not yet presented to the bank
  = Balance per Books, adjusted          (should equal)
    Balance as per Bank                  -> Difference

A line is outstanding ON A DATE if it was booked by then and the bank had not cleared it
by then - so a line cleared the day after the statement date is still outstanding on
that statement, and an old BRS reprints exactly as it stood.

Around that core sit the assistants that make a long passbook quick to get through:
the passbook finder (one passbook figure -> the entry, the group of entries, or the
whole day's / branch's batch the bank added up into it), the difference hints, ageing
with stale flags, sources and day totals, undo, possible duplicates, queries with the
bank and the letter that asks them, entry details, bank entries posted from the screen,
the banks overview, and carrying a statement's settings into the next one.
"""
import re
from collections import defaultdict
from datetime import timedelta

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import SQL, format_date, format_datetime, formatLang, html2plaintext

# How many lines one side of the matching screen shows. The first reconciliation of a
# busy bank has thousands outstanding; the counts beside each heading tell the truth and
# search narrows it, rather than a browser trying to draw 7,800 rows.
SCREEN_LIMIT = 400
# The passbook finder combines the MATCH_POOL outstanding lines nearest the passbook date
# into groups of two to four (a deposit slip rarely carries more), keeps at most
# MATCH_COMBOS raw combinations, and shows MATCH_RESULTS answers.
MATCH_POOL = 150
MATCH_COMBOS = 3000
MATCH_RESULTS = 6
# A bank not reconciled for longer than this is flagged on the overview.
OVERDUE_DAYS = 35
# Receipts from the same party of the same amount this close together look doubled.
DUPLICATE_DAYS = 3
# Undo keeps this many steps per statement.
STEP_KEEP = 50
FIGURES = ['book_balance', 'deposits_not_credited', 'payments_not_presented',
           'adjusted_balance', 'difference']
SIDES = ('deposits', 'withdrawals')
BUCKETS = ('a', 'b', 'c', 'd')
# Where a bank line came from, read off the ledger: half of CANERA BANK's lines are a
# branch's cash paid in ("Transfer from cash ( KYLM )"), most of the rest are customer
# receipts. The branch is what the label carries in brackets - closed or not: some
# labels were cut short ("Transfer from BANK(TCR").
SOURCES = ('cash', 'bank', 'party', 'other')
FILTERS = SOURCES + ('query', 'carried')
SOURCE_CASE = """CASE WHEN l.name ~* '^\\s*(transfer (from|to) )?cash' THEN 'cash'
                      WHEN l.name ~* '^\\s*transfer (from|to) bank' THEN 'bank'
                      WHEN l.partner_id IS NOT NULL THEN 'party'
                      ELSE 'other' END"""
# Bank entries posted from the screen: kind -> (money into the bank, needs a party)
ENTRY_KINDS = {
    'receipt': (True, True),
    'payment': (False, True),
    'transfer_in': (True, False),
    'transfer_out': (False, False),
    'interest': (True, False),
    'charge': (False, False),
}
LEGACY_KINDS = {'other_in': 'transfer_in', 'other_out': 'transfer_out'}
_UNSET = object()


def _cents(value):
    return int(round(abs(value or 0.0) * 100))


def _group_key(row):
    """A day's batch: one branch's cash, one branch's transfers, parties, or the rest.

    The branch is compared without case or spaces: the same branch is keyed in as
    "TVM 2" on one line and "TVM2" on the next."""
    branch = row['branch'] if row['source'] in ('cash', 'bank') else ''
    return '%s|%s' % (row['source'], re.sub(r'\s+', '', branch.upper()))


class BankReconciliation(models.Model):
    _name = 'bank.reconciliation'
    _description = 'Bank Reconciliation'
    _inherit = ['mail.thread']
    _order = 'date desc, id desc'

    name = fields.Char(compute='_compute_name', store=True)
    company_id = fields.Many2one(
        'res.company', required=True, index=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id', string='Currency')
    journal_id = fields.Many2one(
        'account.journal', string='Bank', required=True, tracking=True,
        domain="[('type', '=', 'bank'), ('company_id', '=', company_id)]")
    account_id = fields.Many2one(
        'account.account', string='Bank Account (GL)', required=True, tracking=True,
        compute='_compute_account_id', store=True, readonly=False, precompute=True,
        domain="[('account_type', '=', 'asset_cash'), ('company_ids', 'in', company_id)]",
        help="The GL account whose lines are reconciled. Detected as the bank account "
             "this journal's entries actually post to - which on this database is not "
             "always the journal's own default account - and editable.")
    date = fields.Date(
        'Statement Date', required=True, tracking=True,
        default=fields.Date.context_today)
    bank_balance = fields.Monetary(
        'Balance as per Bank', currency_field='currency_id', tracking=True,
        help="The closing balance printed in the passbook or bank statement on the "
             "statement date.")
    state = fields.Selection(
        [('draft', 'In Progress'), ('done', 'Reconciled')],
        default='draft', required=True, tracking=True, copy=False)
    charge_account_id = fields.Many2one(
        'account.account', string='Bank Charges Account',
        domain="[('account_type', 'in', ('expense', 'expense_direct_cost')), "
               "('company_ids', 'in', company_id)]",
        help="Where bank charges posted from the matching screen are expensed. "
             "Carried forward from this bank's previous statement.")
    interest_account_id = fields.Many2one(
        'account.account', string='Interest Account',
        domain="[('account_type', 'in', ('income', 'income_other')), "
               "('company_ids', 'in', company_id)]",
        help="Where interest credited by the bank is booked. "
             "Carried forward from this bank's previous statement.")
    stale_days = fields.Integer(
        'Stale After (days)', default=90, tracking=True,
        help="An outstanding entry older than this is flagged as stale. A cheque in "
             "India is valid for three months, hence 90 days.")
    charge_tolerance = fields.Monetary(
        'Short Credits up to', currency_field='currency_id', default=500.0, tracking=True,
        help="A deposit is often credited a little short, the bank keeping its charges. "
             "The passbook finder offers an entry short by up to this much as a near "
             "match, and books the shortfall as bank charges in the same click.")
    note = fields.Text()
    clearance_ids = fields.One2many('bank.clearance', 'reconciliation_id',
                                    string='Cleared Lines')
    step_ids = fields.One2many('bank.reconciliation.step', 'reconciliation_id',
                               string='Undo Steps')

    book_balance = fields.Monetary(
        'Balance as per Books', compute='_compute_figures', currency_field='currency_id')
    deposits_not_credited = fields.Monetary(
        'Deposits not yet Credited', compute='_compute_figures',
        currency_field='currency_id')
    payments_not_presented = fields.Monetary(
        'Payments not yet Presented', compute='_compute_figures',
        currency_field='currency_id')
    adjusted_balance = fields.Monetary(
        'Balance per Books, Adjusted', compute='_compute_figures',
        currency_field='currency_id')
    difference = fields.Monetary(
        compute='_compute_figures', currency_field='currency_id')

    # ------------------------------------------------------------------ computes
    @api.depends('journal_id', 'date')
    def _compute_name(self):
        for rec in self:
            rec.name = '%s — %s' % (rec.journal_id.name or _('Bank'),
                                    format_date(self.env, rec.date) if rec.date else '')

    @api.depends('journal_id')
    def _compute_account_id(self):
        for rec in self:
            rec.account_id = rec._detect_bank_account() if rec.journal_id else False

    @api.constrains('stale_days')
    def _check_stale_days(self):
        for rec in self:
            if rec.stale_days < 31:
                raise ValidationError(_("Stale After must be at least 31 days."))

    def _detect_bank_account(self):
        return self._accounts_for_journals(self.journal_id)[self.journal_id.id]

    @api.model
    def _accounts_for_journals(self, journals):
        """Each journal's bank account: the cash-type account its posted entries use most.

        The journal's `default_account_id` is not enough: CANERA BANK's journal points
        at one account while every one of its 7,788 entries posted to another.
        """
        if not journals:
            return {}
        self.env['account.move.line'].flush_model(['account_id', 'journal_id', 'parent_state'])
        self.env.cr.execute(SQL(
            """
            SELECT DISTINCT ON (l.journal_id) l.journal_id, l.account_id
              FROM account_move_line l
              JOIN account_account a ON a.id = l.account_id
             WHERE l.journal_id = ANY(%s) AND l.parent_state = 'posted'
               AND a.account_type = 'asset_cash'
             GROUP BY l.journal_id, l.account_id
             ORDER BY l.journal_id, count(*) DESC, l.account_id
            """, list(journals.ids)))
        used = dict(self.env.cr.fetchall())
        Account = self.env['account.account']
        return {journal.id: Account.browse(used[journal.id]) if journal.id in used
                else journal.default_account_id for journal in journals}

    def _line_filter(self):
        """The bank-account lines this statement covers: every posted line on the
        account up to the statement date, from ANY journal - the opening balance
        is posted from the general journal, and it is money in the bank too."""
        return SQL(
            "l.account_id = %s AND l.company_id = %s AND l.parent_state = 'posted' "
            "AND l.date <= %s",
            self.account_id.id, self.company_id.id, self.date)

    @api.model
    def _side_sql(self, side):
        return SQL("l.balance > 0") if side == 'deposits' else SQL("l.balance < 0")

    def _outstanding_sql(self):
        return SQL("(c.id IS NULL OR c.cleared_date > %s)", self.date)

    def _age_sql(self, bucket):
        age = SQL("(%s::date - l.date)", self.date)
        return {
            'a': SQL("%s <= 7", age),
            'b': SQL("%s BETWEEN 8 AND 30", age),
            'c': SQL("%s BETWEEN 31 AND %s", age, self.stale_days),
            'd': SQL("%s > %s", age, self.stale_days),
        }[bucket]

    @api.model
    def _source_sql(self, source, since=False):
        if source == 'query':
            return SQL("q.id IS NOT NULL")
        if source == 'carried':
            # outstanding since before the last reconciled statement: the ones to chase
            return SQL("l.date <= %s", since or fields.Date.to_date('0001-01-01'))
        if source in SOURCES:
            return SQL("(%s) = %s", SQL(SOURCE_CASE), source)
        return None

    def _age_labels(self):
        return {'a': _("0–7 days"), 'b': _("8–30 days"),
                'c': _("31–%s days", self.stale_days), 'd': _("Over %s days", self.stale_days)}

    @api.model
    def _source_labels(self):
        return {'cash': _("Cash paid in"), 'bank': _("Bank transfers"),
                'party': _("Parties"), 'other': _("Other"), 'query': _("Under query"),
                'carried': _("From before the last statement")}

    @api.model
    def _flush_lines(self):
        self.env['account.move.line'].flush_model()
        for model in ('bank.clearance', 'bank.line.query'):
            self.env[model].flush_model()

    @api.depends('account_id', 'company_id', 'date', 'bank_balance',
                 'clearance_ids.cleared_date')
    def _compute_figures(self):
        self._flush_lines()
        for rec in self:
            if not (rec.account_id and rec.date and rec.company_id):
                rec.book_balance = rec.deposits_not_credited = 0.0
                rec.payments_not_presented = rec.adjusted_balance = 0.0
                rec.difference = rec.bank_balance
                continue
            self.env.cr.execute(SQL(
                """
                SELECT COALESCE(SUM(l.balance), 0),
                       COALESCE(SUM(l.balance) FILTER (
                           WHERE l.balance > 0 AND (c.id IS NULL OR c.cleared_date > %(d)s)), 0),
                       COALESCE(-SUM(l.balance) FILTER (
                           WHERE l.balance < 0 AND (c.id IS NULL OR c.cleared_date > %(d)s)), 0)
                  FROM account_move_line l
                  LEFT JOIN bank_clearance c ON c.move_line_id = l.id
                 WHERE %(where)s
                """, d=rec.date, where=rec._line_filter()))
            book, deposits, payments = self.env.cr.fetchone()
            currency = rec.currency_id
            rec.book_balance = currency.round(book)
            rec.deposits_not_credited = currency.round(deposits)
            rec.payments_not_presented = currency.round(payments)
            rec.adjusted_balance = currency.round(book - deposits + payments)
            rec.difference = currency.round(rec.bank_balance - rec.adjusted_balance)

    # ------------------------------------------------------------------ carrying forward
    @api.model_create_multi
    def create(self, vals_list):
        """A new statement takes its settings from this bank's previous one."""
        for vals in vals_list:
            if not vals.get('journal_id'):
                continue
            last = self.search([('journal_id', '=', vals['journal_id'])],
                               order='date desc, id desc', limit=1)
            if not last:
                continue
            for field in ('charge_account_id', 'interest_account_id'):
                if not vals.get(field) and last[field]:
                    vals[field] = last[field].id
            for field in ('stale_days', 'charge_tolerance'):
                if field not in vals:
                    vals[field] = last[field]
        return super().create(vals_list)

    @api.onchange('journal_id')
    def _onchange_journal_carry_forward(self):
        if not self.journal_id:
            return
        last = self.search([('journal_id', '=', self.journal_id.id)],
                           order='date desc, id desc', limit=1)
        if last:
            self.charge_account_id = self.charge_account_id or last.charge_account_id
            self.interest_account_id = self.interest_account_id or last.interest_account_id
            self.stale_days = last.stale_days

    # ------------------------------------------------------------------ guards
    def _check_editable(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_(
                    "%s is already reconciled. Reset it to In Progress to change it.",
                    rec.name))

    def _previous_done_date(self):
        previous = self.search([
            ('account_id', '=', self.account_id.id), ('company_id', '=', self.company_id.id),
            ('state', '=', 'done'), ('date', '<', self.date), ('id', '!=', self.id)],
            order='date desc', limit=1)
        return previous.date

    def _own_lines(self, line_ids):
        """The given journal items, if every one is a posted line of this statement's
        bank account up to its date. Checked in one query: a bulk tick on a busy bank
        passes thousands of ids, and reading each through the ORM took a second."""
        ids = sorted({int(i) for i in line_ids or []})
        Line = self.env['account.move.line']
        if not ids:
            return Line
        Line.flush_model(['account_id', 'company_id', 'parent_state', 'date'])
        self.env.cr.execute(SQL("SELECT l.id, (%s) FROM account_move_line l WHERE l.id = ANY(%s)",
                                self._line_filter(), ids))
        rows = self.env.cr.fetchall()
        if not all(ok for _id, ok in rows):
            raise UserError(_(
                "Those lines are not on %(account)s up to %(date)s, so they cannot be "
                "cleared on this reconciliation.",
                account=self.account_id.display_name, date=format_date(self.env, self.date)))
        return Line.browse([line_id for line_id, _ok in rows])

    def _missing_bank_balance(self):
        """No passbook balance typed yet, on books that are not empty. A statement with
        nothing ticked and a zero bank balance 'balances' - and means nothing."""
        return self.currency_id.is_zero(self.bank_balance) and \
            not self.currency_id.is_zero(self.book_balance)

    # ------------------------------------------------------------------ reading lines
    @api.model
    def _search_sql(self, search):
        text = (search or '').strip()
        if not text:
            return SQL("")
        like = '%' + text.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        parts = [SQL("l.name ILIKE %s", like), SQL("m.name ILIKE %s", like),
                 SQL("m.ref ILIKE %s", like), SQL("p.name ILIKE %s", like)]
        try:
            parts.append(SQL("ABS(l.balance) = %s", float(text.replace(',', ''))))
        except ValueError:
            pass
        return SQL("AND (%s)", SQL(" OR ").join(parts))

    _LINE_SELECT = f"""
        SELECT l.id, l.date, COALESCE(l.name, '') AS label, m.name AS move,
               COALESCE(m.ref, '') AS ref, COALESCE(p.name, '') AS partner, l.partner_id,
               l.balance, c.cleared_date, COALESCE(r.state = 'done', FALSE) AS locked,
               j.type AS journal_type, q.note AS query_note,
               {SOURCE_CASE} AS source,
               COALESCE(TRIM(SUBSTRING(l.name FROM '\\(([^)]*)')), '') AS branch
          FROM account_move_line l
          JOIN account_move m ON m.id = l.move_id
          JOIN account_journal j ON j.id = l.journal_id
          LEFT JOIN res_partner p ON p.id = l.partner_id
          LEFT JOIN bank_clearance c ON c.move_line_id = l.id
          LEFT JOIN bank_reconciliation r ON r.id = c.reconciliation_id
          LEFT JOIN bank_line_query q ON q.move_line_id = l.id
    """

    def _screen_lines(self, side, search=None, limit=SCREEN_LIMIT, uncleared_only=False,
                      age=None, upto=None, source=None, since=_UNSET, ids=None):
        """One side of the screen (or, given `ids`, just those lines): lines outstanding
        on the statement date, plus the lines cleared since the last reconciled statement
        of this account (so a tick does not make its line vanish)."""
        self._flush_lines()
        if uncleared_only:
            shown = self._outstanding_sql()
        else:
            if since is _UNSET:
                since = self._previous_done_date()
            shown = SQL("(c.id IS NULL OR c.cleared_date > %s)",
                        since or fields.Date.to_date('0001-01-01'))
        conditions = [self._line_filter(), shown]
        if side:
            conditions.append(self._side_sql(side))
        if ids is not None:
            conditions.append(SQL("l.id = ANY(%s)", list(ids)))
        if age in BUCKETS:
            conditions.append(self._age_sql(age))
        if upto:
            conditions.append(SQL("l.date <= %s", fields.Date.to_date(upto)))
        if source in FILTERS:
            if source == 'carried' and since is _UNSET:
                since = self._previous_done_date()
            conditions.append(self._source_sql(source, since))
        self.env.cr.execute(SQL(
            "%s WHERE %s %s ORDER BY l.date, l.id %s",
            SQL(self._LINE_SELECT), SQL(" AND ").join(conditions), self._search_sql(search),
            SQL("LIMIT %s", limit) if limit else SQL("")))
        return self.env.cr.dictfetchall()

    def _line_payload(self, row):
        day = row['date']
        cleared_date = row.get('cleared_date')
        cleared = bool(cleared_date) and cleared_date <= self.date
        age = (self.date - day).days
        label = row['label'] if row['label'] not in ('/', '') else ''
        return {
            'id': row['id'],
            'date': fields.Date.to_string(day),
            'date_label': format_date(self.env, day),
            'label': label,
            'move': row['move'] or '',
            'ref': row['ref'],
            'partner': row['partner'],
            'amount': abs(row['balance']),
            'side': 'deposits' if row['balance'] > 0 else 'withdrawals',
            'cleared': cleared,
            'cleared_date': fields.Date.to_string(cleared_date) if cleared_date else '',
            'locked': bool(row.get('locked')),
            'age': age,
            'stale': not cleared and age > self.stale_days,
            'general': row.get('journal_type') == 'general',
            'source': row.get('source') or 'other',
            'branch': row.get('branch') or '',
            'query': row.get('query_note') or '',
        }

    def _outstanding_stats(self, search=None, since=_UNSET):
        """Per side: how many lines are outstanding, split by age and by source, and how
        many of them were already outstanding at the last reconciled statement."""
        if since is _UNSET:
            since = self._previous_done_date()
        self._flush_lines()
        self.env.cr.execute(SQL(
            """
            SELECT CASE WHEN l.balance > 0 THEN 'deposits' ELSE 'withdrawals' END,
                   CASE WHEN %(d)s::date - l.date <= 7 THEN 'a'
                        WHEN %(d)s::date - l.date <= 30 THEN 'b'
                        WHEN %(d)s::date - l.date <= %(stale)s THEN 'c'
                        ELSE 'd' END,
                   %(source)s,
                   count(*), COALESCE(SUM(ABS(l.balance)), 0), count(q.id),
                   count(*) FILTER (WHERE l.date <= %(since)s)
              FROM account_move_line l
              JOIN account_move m ON m.id = l.move_id
              LEFT JOIN res_partner p ON p.id = l.partner_id
              LEFT JOIN bank_clearance c ON c.move_line_id = l.id
              LEFT JOIN bank_line_query q ON q.move_line_id = l.id
             WHERE %(where)s AND l.balance != 0 AND %(open)s %(search)s
             GROUP BY 1, 2, 3
            """, d=self.date, stale=self.stale_days, where=self._line_filter(),
            source=SQL(SOURCE_CASE), open=self._outstanding_sql(),
            search=self._search_sql(search), since=since or fields.Date.to_date('0001-01-01')))
        stats = {side: {'count': 0, 'amount': 0.0, 'queries': 0, 'carried': 0,
                        'ageing': {b: {'count': 0, 'amount': 0.0} for b in BUCKETS},
                        'sources': {s: {'count': 0, 'amount': 0.0} for s in SOURCES}}
                 for side in SIDES}
        for side, bucket, source, count, amount, queries, carried in self.env.cr.fetchall():
            entry = stats[side]
            entry['count'] += count
            entry['amount'] += amount
            entry['queries'] += queries
            entry['carried'] += carried
            entry['ageing'][bucket]['count'] += count
            entry['ageing'][bucket]['amount'] += amount
            entry['sources'][source]['count'] += count
            entry['sources'][source]['amount'] += amount
        return stats

    def _progress(self, since):
        """How far this statement has got: of the entries it covers (everything not
        cleared by the last reconciled statement), how many are ticked by its date."""
        self.env.cr.execute(SQL(
            """
            SELECT count(*) FILTER (WHERE c.id IS NOT NULL AND c.cleared_date <= %(d)s),
                   count(*),
                   COALESCE(SUM(ABS(l.balance)) FILTER (
                       WHERE c.id IS NOT NULL AND c.cleared_date <= %(d)s), 0),
                   COALESCE(SUM(ABS(l.balance)), 0)
              FROM account_move_line l
              LEFT JOIN bank_clearance c ON c.move_line_id = l.id
             WHERE %(where)s AND l.balance != 0
               AND (c.id IS NULL OR c.cleared_date > %(since)s)
            """, d=self.date, where=self._line_filter(),
            since=since or fields.Date.to_date('0001-01-01')))
        done, total, done_amount, total_amount = self.env.cr.fetchone()
        return {'done': done, 'total': total, 'done_amount': done_amount,
                'total_amount': total_amount,
                'pct': int(done * 100 / total) if total else 100}

    # ------------------------------------------------------------------ the screen
    def get_screen(self, search=None, age=None, source=None):
        """Everything the matching screen shows, in one call."""
        self.ensure_one()
        self.check_access('read')
        payload, since, age, source = self._screen_common(search, age, source)
        payload['deposits'] = [self._line_payload(r) for r in self._screen_lines(
            'deposits', search, age=age, source=source, since=since)]
        payload['withdrawals'] = [self._line_payload(r) for r in self._screen_lines(
            'withdrawals', search, age=age, source=source, since=since)]
        return payload

    def _screen_delta(self, line_ids, search=None, age=None, source=None):
        """The screen after a change to a few lines: the figures, filters, hints and
        undo as usual, but only the changed lines - a tick on a busy bank used to send
        back and redraw 800 of them."""
        payload, since, _age, _source = self._screen_common(search, age, source)
        payload['delta'] = True
        payload['lines'] = [self._line_payload(r) for r in self._screen_lines(
            None, limit=None, since=since, ids=line_ids)]
        return payload

    def _screen_common(self, search, age, source):
        self.invalidate_recordset(FIGURES)
        age = age if age in BUCKETS else None
        source = source if source in FILTERS else None
        editable = self.state == 'draft' and self.has_access('write')
        balanced = self.currency_id.is_zero(self.difference)
        since = self._previous_done_date()
        ready = balanced and not self._missing_bank_balance()
        payload = {
            'id': self.id,
            'name': self.name,
            'journal': self.journal_id.display_name,
            'account': self.account_id.display_name,
            'account_id': self.account_id.id,
            'company': self.company_id.name,
            'company_id': self.company_id.id,
            'date': fields.Date.to_string(self.date),
            'date_label': format_date(self.env, self.date),
            'previous_label': format_date(self.env, since) if since else '',
            'state': self.state,
            'editable': editable,
            'currency_id': self.currency_id.id,
            'bank_balance': self.bank_balance,
            'stale_days': self.stale_days,
            'figures': {
                'book': self.book_balance,
                'deposits_not_credited': self.deposits_not_credited,
                'payments_not_presented': self.payments_not_presented,
                'adjusted': self.adjusted_balance,
                'difference': self.difference,
                'balanced': balanced,
                'ready': ready,
            },
            'progress': self._progress(since),
            'stats': self._outstanding_stats(search, since),
            'age': age or '',
            'age_labels': self._age_labels(),
            'source': source or '',
            'source_labels': self._source_labels(),
            'limit': SCREEN_LIMIT,
            'hints': self._difference_hints() if editable and not balanced else [],
            'opening': self._opening_items(since) if editable else None,
            'undo': self._undo_info() if editable else False,
            'charge_account': self.charge_account_id.display_name or '',
            'interest_account': self.interest_account_id.display_name or '',
            'posted': False,
        }
        return payload, since, age, source

    def _opening_items(self, since=_UNSET):
        """On an account never reconciled: the outstanding lines posted from the general
        journal - the opening balance above all. They are money already in the bank, but
        until ticked they count as deposits the bank has not credited, and the first
        statement of CANERA BANK opens 211.7M out."""
        if since is _UNSET:
            since = self._previous_done_date()
        if since:
            return None
        self._flush_lines()
        self.env.cr.execute(SQL(
            "%s WHERE %s AND j.type = 'general' AND %s ORDER BY ABS(l.balance) DESC, l.date",
            SQL(self._LINE_SELECT), self._line_filter(), self._outstanding_sql()))
        rows = self.env.cr.dictfetchall()
        if not rows:
            return None
        return {
            'count': len(rows),
            'ids': [r['id'] for r in rows],
            'amount': sum(r['balance'] for r in rows),
            'lines': [dict(self._line_payload(r), label=r['ref'] or r['label'] or r['move'])
                      for r in rows[:5]],
        }

    # ------------------------------------------------------------------ entry details
    def get_line_details(self, line_id):
        """Everything behind one bank line: its journal entry and all the entry's lines,
        the payment it belongs to, the invoices that payment settled, the party and its
        ledger balance, when and by whom the bank line was cleared, the query on it, and
        the other outstanding entries of the same amount."""
        self.ensure_one()
        self.check_access('read')
        line = self.env['account.move.line'].browse(int(line_id or 0)).exists()
        if not line or line.account_id != self.account_id or line.company_id != self.company_id:
            raise UserError(_("That entry is not on %s.", self.account_id.display_name))
        move = line.move_id
        currency = self.currency_id
        clearance = self.env['bank.clearance'].search([('move_line_id', '=', line.id)], limit=1)
        query = self.env['bank.line.query'].search([('move_line_id', '=', line.id)], limit=1)
        cleared = bool(clearance) and clearance.cleared_date <= self.date
        states = dict(move._fields['state']._description_selection(self.env))

        counterparts = move.line_ids.filtered(
            lambda l: l.account_id.account_type in ('asset_receivable', 'liability_payable'))
        settled = (counterparts.matched_debit_ids.debit_move_id
                   | counterparts.matched_credit_ids.credit_move_id).move_id - move
        payment = move.origin_payment_id
        partner = (line.partner_id or move.partner_id or counterparts.partner_id[:1]).commercial_partner_id

        similar = []
        if self.date >= line.date:
            self._flush_lines()
            self.env.cr.execute(SQL(
                "%s WHERE %s AND %s AND %s AND ROUND(ABS(l.balance) * 100) = %s AND l.id != %s "
                "ORDER BY ABS(l.date - %s::date), l.id LIMIT 6",
                SQL(self._LINE_SELECT), self._line_filter(), self._outstanding_sql(),
                self._side_sql('deposits' if line.balance > 0 else 'withdrawals'),
                _cents(line.balance), line.id, line.date))
            similar = [self._line_payload(r) for r in self.env.cr.dictfetchall()]

        return {
            'line_id': line.id,
            'amount': abs(line.balance),
            'side': 'deposits' if line.balance > 0 else 'withdrawals',
            'label': line.name or '',
            'date_label': format_date(self.env, line.date),
            'age': (self.date - line.date).days,
            'in_statement': line.date <= self.date,
            'cleared': cleared,
            'locked': clearance.reconciliation_id.state == 'done',
            'editable': self.state == 'draft' and self.has_access('write')
            and line.date <= self.date,
            'clearance': clearance and {
                'date_label': format_date(self.env, clearance.cleared_date),
                'by': clearance.cleared_by_id.name or '',
                'statement': clearance.reconciliation_id.name or '',
                'after_statement': clearance.cleared_date > self.date,
            } or False,
            'query': query and {
                'note': query.note, 'by': query.user_id.name or '',
                'when': format_datetime(self.env, query.write_date, dt_format='short'),
            } or False,
            'move': {
                'id': move.id,
                'name': move.name,
                'date_label': format_date(self.env, move.date),
                'journal': move.journal_id.display_name,
                'ref': move.ref or '',
                'state': states.get(move.state, move.state),
                'narration': html2plaintext(move.narration or '').strip(),
                'created_by': move.create_uid.name or '',
                'created_on': format_datetime(self.env, move.create_date, dt_format='short'),
                'total': move.amount_total_signed if move.is_invoice() else sum(move.line_ids.mapped('debit')),
            },
            'lines': [{
                'account': l.account_id.display_name,
                'partner': l.partner_id.display_name or '',
                'label': l.name or '',
                'debit': l.debit,
                'credit': l.credit,
                'bank': l == line,
                'matching': l.matching_number or '',
            } for l in move.line_ids],
            'payment': payment and {
                'id': payment.id,
                'name': payment.name,
                'kind': _("Receipt") if payment.payment_type == 'inbound' else _("Payment"),
                'method': payment.payment_method_line_id.name or '',
                'memo': payment.memo or '',
                'partner': payment.partner_id.display_name or '',
                'amount': payment.amount,
                'date_label': format_date(self.env, payment.date),
            } or False,
            'invoices': [{
                'id': invoice.id,
                'name': invoice.name,
                'date_label': format_date(self.env, invoice.invoice_date or invoice.date),
                'total': abs(invoice.amount_total_signed),
                'residual': abs(invoice.amount_residual_signed),
            } for invoice in settled[:10]],
            'party': partner and {
                'id': partner.id,
                'name': partner.display_name,
                'balance': self._party_balance(partner),
                'phone': partner.phone or '',
            } or False,
            'similar': similar,
        }

    def _party_balance(self, partner):
        """What the party owes (positive) or is owed, from its receivable and payable
        ledger lines - not invoice residuals, which this ledger never clears."""
        self.env['account.move.line'].flush_model(['balance', 'partner_id', 'parent_state'])
        self.env.cr.execute(SQL(
            """
            SELECT COALESCE(SUM(l.balance), 0)
              FROM account_move_line l
              JOIN account_account a ON a.id = l.account_id
              JOIN res_partner p ON p.id = l.partner_id
             WHERE p.commercial_partner_id = %s AND l.company_id = %s
               AND l.parent_state = 'posted'
               AND a.account_type IN ('asset_receivable', 'liability_payable')
            """, partner.id, self.company_id.id))
        return self.currency_id.round(self.env.cr.fetchone()[0])

    # ------------------------------------------------------------------ day totals
    def get_day_totals(self, side='deposits', search=None, age=None, source=None):
        """Outstanding entries added up per day, and within the day per batch - one
        branch's cash, one branch's transfers, the parties, the rest. The passbook
        often shows exactly these as one line each."""
        self.ensure_one()
        self.check_access('read')
        side = side if side in SIDES else 'deposits'
        labels = self._source_labels()
        days = {}
        for row in self._screen_lines(side, search, limit=None, uncleared_only=True,
                                      age=age if age in BUCKETS else None,
                                      source=source if source in FILTERS else None):
            amount = abs(row['balance'])
            day = days.setdefault(row['date'], {
                'day': fields.Date.to_string(row['date']),
                'label': format_date(self.env, row['date']),
                'count': 0, 'amount': 0.0, 'groups': {}})
            day['count'] += 1
            day['amount'] += amount
            key = _group_key(row)
            # the label keeps the branch as the first line of the batch spells it
            branch = row['branch'].strip() if key.split('|', 1)[1] else ''
            group = day['groups'].setdefault(key, {
                'key': key, 'source': row['source'],
                'label': '%s · %s' % (labels[row['source']], branch) if branch
                else labels[row['source']],
                'count': 0, 'amount': 0.0})
            group['count'] += 1
            group['amount'] += amount
        out = []
        for date in sorted(days):
            day = days[date]
            day['groups'] = sorted(day['groups'].values(), key=lambda g: -g['amount'])
            out.append(day)
        return {'side': side, 'days': out}

    def clear_day(self, side, day, group=False, search=None, age=None, source=None):
        """Tick a whole day's outstanding entries on one side, or one batch of that day."""
        self.ensure_one()
        self._check_editable()
        day = fields.Date.to_date(day)
        rows = [row for row in self._screen_lines(
            side, search, limit=None, uncleared_only=True,
            age=age if age in BUCKETS else None, source=source if source in FILTERS else None)
            if row['date'] == day and (not group or _group_key(row) == group)]
        if not rows:
            return self.get_screen(search=search, age=age, source=source)
        if group:
            source_key, branch = group.split('|', 1)
            what = branch or self._source_labels()[source_key]
        else:
            what = _("everything")
        label = _("Ticked %(what)s on %(day)s (%(count)s)", what=what,
                  day=format_date(self.env, day), count=len(rows))
        return self.with_context(bank_rec_step=label).set_cleared(
            [r['id'] for r in rows], True, False, search=search, age=age, source=source)

    # ------------------------------------------------------------------ the assistants
    def _difference_hints(self):
        """What most likely explains the difference, from the ledger itself.

        `want` is what a correction has to add to the adjusted balance. Ticking an
        outstanding deposit adds its amount, ticking a payment takes its amount off,
        and unticking does the reverse - so any single line, or pair of lines, whose
        effect is exactly `want` is a candidate worth showing first.
        """
        currency = self.currency_id
        if currency.is_zero(self.bank_balance):
            return [{'kind': 'info', 'icon': 'fa-pencil', 'lines': [], 'text': _(
                "Type the Balance as per Bank from the passbook, and this panel will "
                "look for what explains the difference.")}]
        want = int(round(self.difference * 100))
        need = abs(want)
        money = lambda cents: formatLang(self.env, cents / 100.0, currency_obj=currency)
        self._flush_lines()
        self.env.cr.execute(SQL(
            """
            SELECT l.id, l.date, l.balance, COALESCE(NULLIF(l.name, ''), m.ref, m.name) AS label,
                   m.name AS move, COALESCE(p.name, '') AS partner,
                   (c.id IS NOT NULL AND c.cleared_date <= %(d)s) AS cleared,
                   COALESCE(r.state = 'done', FALSE) AS locked
              FROM account_move_line l
              JOIN account_move m ON m.id = l.move_id
              LEFT JOIN res_partner p ON p.id = l.partner_id
              LEFT JOIN bank_clearance c ON c.move_line_id = l.id
              LEFT JOIN bank_reconciliation r ON r.id = c.reconciliation_id
             WHERE l.account_id = %(acc)s AND l.company_id = %(co)s
               AND l.parent_state = 'posted' AND l.balance != 0
               AND l.date <= %(until)s AND ABS(l.balance) <= %(cap)s
             ORDER BY l.date DESC, l.id DESC
             LIMIT 20000
            """, d=self.date, acc=self.account_id.id, co=self.company_id.id,
            until=self.date + timedelta(days=7), cap=need / 100.0 + 0.005))
        rows = self.env.cr.dictfetchall()

        def effect(row):
            if row['locked'] or row['date'] > self.date:
                return None
            cents = int(round(row['balance'] * 100))
            return -cents if row['cleared'] else cents

        def line(row):
            return {'id': row['id'], 'label': row['label'] or row['move'],
                    'partner': row['partner'], 'amount': abs(row['balance']),
                    'date_label': format_date(self.env, row['date'])}

        hints = []
        for row in [r for r in rows if effect(r) == want][:2]:
            deposit = row['balance'] > 0
            if not row['cleared']:
                text = (_("A deposit of %s is still outstanding. If the passbook shows it "
                          "credited, tick it.") if deposit else
                        _("A payment of %s is still unpresented. If the passbook shows it "
                          "debited, tick it.")) % money(need)
            else:
                text = (_("A deposit of %s is ticked, but if the passbook does not show it "
                          "yet, untick it.") if deposit else
                        _("A payment of %s is ticked, but if the passbook does not show it "
                          "yet, untick it.")) % money(need)
            hints.append({'kind': 'untick' if row['cleared'] else 'tick', 'text': text,
                          'line_ids': [row['id']], 'lines': [line(row)]})

        if len(hints) < 2:
            seen = {}
            for row in rows:
                e = effect(row)
                if e is None or row['cleared'] or (e > 0) != (want > 0):
                    continue
                other = seen.get(want - e)
                if other:
                    hints.append({'kind': 'pair', 'line_ids': [other['id'], row['id']],
                                  'lines': [line(other), line(row)], 'text': _(
                        "These two outstanding entries add up to exactly %s. The bank may "
                        "show them as one.") % money(need)})
                    break
                seen.setdefault(e, row)

        if need % 2 == 0:
            for row in [r for r in rows if r['date'] <= self.date
                        and _cents(r['balance']) * 2 == need][:1]:
                hints.append({'kind': 'search', 'search': '%.2f' % abs(row['balance']),
                              'lines': [line(row)], 'text': _(
                    "An entry of %s is exactly half the difference. It may be posted the "
                    "wrong way round: a receipt booked as a payment, or the reverse.")
                    % money(need // 2)})

        for row in [r for r in rows if r['date'] > self.date
                    and int(round(r['balance'] * 100)) == want][:1]:
            hints.append({'kind': 'info', 'icon': 'fa-calendar', 'lines': [line(row)], 'text': _(
                "An entry of %(amount)s is booked on %(day)s, after the statement date. If the "
                "bank shows it by %(date)s, its accounting date may be wrong.",
                amount=money(need), day=format_date(self.env, row['date']),
                date=format_date(self.env, self.date))})

        if need >= 900 and need % 900 == 0:
            hints.append({'kind': 'info', 'icon': 'fa-exchange', 'lines': [], 'text': _(
                "The difference divides exactly by 9, the classic sign of two swapped "
                "digits: in the bank balance typed here, or in an entry (5,410 keyed as "
                "4,510).")})

        hints.append({
            'kind': 'post', 'lines': [], 'amount': need / 100.0,
            'item_kind': 'interest' if want > 0 else 'charge',
            'text': (_("Or the bank has credited %s the books do not have yet: interest, or "
                       "a receipt never entered. Post it here.") if want > 0 else
                     _("Or the bank has debited %s the books do not have yet: charges, a "
                       "transfer, a payment. Post it here.")) % money(need)})
        return hints[:6]

    def find_matches(self, amount, day=False, side='deposits', window=7):
        """The passbook finder: one line of the passbook -> the outstanding entry it is,
        a day's or a branch's whole batch the bank added up (any number of entries),
        or a group of two to four entries (a deposit slip of several cheques)."""
        self.ensure_one()
        self.check_access('read')
        target = _cents(float(amount or 0.0))
        if not target:
            raise UserError(_("Type the amount the passbook shows."))
        day = fields.Date.to_date(day) if day else self.date
        window = max(0, min(int(window or 7), 60))
        side = side if side in SIDES else 'deposits'
        where = SQL(" AND ").join([
            self._line_filter(), self._side_sql(side), self._outstanding_sql(),
            SQL("l.date BETWEEN %s AND %s",
                day - timedelta(days=window), day + timedelta(days=window))])
        nearest = SQL("ABS(l.date - %s::date), l.id", day)
        self._flush_lines()
        self.env.cr.execute(SQL("%s WHERE %s ORDER BY %s",
                                SQL(self._LINE_SELECT), where, nearest))
        window_rows = self.env.cr.dictfetchall()
        # exact entries first, however many other lines sit nearer the date
        exact = [r for r in window_rows if _cents(r['balance']) == target][:MATCH_RESULTS]
        smaller = [r for r in window_rows if _cents(r['balance']) < target]
        # near matches: the bank credited a little less than the entry (it kept its
        # charges), or debited a little more - short by no more than the tolerance
        near = []
        tolerance = _cents(self.charge_tolerance)
        if tolerance and not exact:
            for row in window_rows:
                book = _cents(row['balance'])
                gap = book - target if side == 'deposits' else target - book
                if 0 < gap <= tolerance:
                    near.append({'kind': 'near', 'rows': [row], 'difference': gap / 100.0})
            near.sort(key=lambda n: (n['difference'], abs((n['rows'][0]['date'] - day).days)))

        singles = [{'kind': 'single', 'rows': [row]} for row in exact]
        # a batch is every outstanding entry of a day, or of a branch's day - built from
        # the whole window, or "everything booked on" a day would leave its big entries out
        batches = self._batches(window_rows, target, day)
        taken = {frozenset(r['id'] for r in b['rows']) for b in batches}
        groups = [g for g in self._combine(smaller[:MATCH_POOL], target, day)
                  if frozenset(r['id'] for r in g['rows']) not in taken]
        # A round figure (5,000) matches many single receipts; keep room for batches and
        # groups, which are what the finder is for, and fill any space left with singles.
        results = singles[:3] + batches[:2] + near[:2]
        results += groups[:MATCH_RESULTS - len(results)]
        results += singles[3:3 + MATCH_RESULTS - len(results)]
        payload = []
        for result in results:
            rows = result['rows']
            payload.append({
                'kind': result['kind'],
                'label': result.get('label', ''),
                'line_ids': [r['id'] for r in rows],
                'lines': [self._line_payload(r) for r in rows],
                'total': sum(abs(r['balance']) for r in rows),
                'same_day': len({r['date'] for r in rows}) == 1,
                'alternatives': result.get('alternatives', 0),
                'difference': result.get('difference', 0.0),
            })
        return {
            'amount': target / 100.0,
            'side': side,
            'day': fields.Date.to_string(day),
            'day_label': format_date(self.env, day),
            'window': window,
            'searched': len(window_rows),
            'results': payload,
        }

    def _batches(self, rows, target, day):
        """Whole days, and one branch's batch within a day, adding up to `target` - a
        branch's day of cash is one credit in the passbook and a dozen lines here."""
        labels = self._source_labels()
        buckets = defaultdict(list)
        for row in rows:
            buckets[(row['date'], _group_key(row))].append(row)
            buckets[(row['date'], '*')].append(row)
        found, seen = [], set()
        for (date, key), members in buckets.items():
            if len(members) < 2 or sum(_cents(r['balance']) for r in members) != target:
                continue
            ids = frozenset(r['id'] for r in members)
            if ids in seen:
                continue
            seen.add(ids)
            if key == '*':
                label = _("Everything booked on %s", format_date(self.env, date))
            else:
                source, branch = key.split('|', 1)
                label = _("%(what)s on %(day)s", what='%s · %s' % (labels[source], branch)
                          if branch else labels[source], day=format_date(self.env, date))
            found.append({'kind': 'batch', 'rows': members, 'label': label,
                          'score': (abs((date - day).days), key == '*', len(members))})
        return sorted(found, key=lambda b: b['score'])

    @api.model
    def _combine(self, pool, target, day):
        """Groups of 2 to 4 lines whose amounts add up to `target` exactly, best first:
        fewer lines, then closer together, then closer to the passbook date.

        Pairs, triples and fours are all found through lookups on amounts, so none is
        missed within the pool (a bounded walk used to give up on a busy day before it
        reached the pair that was the answer). Groups of the same amounts are one
        answer: on a busy day 1,000 + 450 + 6,350 can be made from several different
        receipts, and six copies of it helped nobody. The best-placed one is shown,
        with a count of the alternatives.
        """
        items = sorted(pool, key=lambda r: (abs((r['date'] - day).days), r['date'], r['id']))
        cents = [_cents(r['balance']) for r in items]
        count = len(items)
        by_amount = defaultdict(list)
        for i, amount in enumerate(cents):
            by_amount[amount].append(i)
        found = []

        def full():
            return len(found) >= MATCH_COMBOS

        for i in range(count):
            found.extend((i, k) for k in by_amount.get(target - cents[i], ()) if k > i)
        pair_sums = defaultdict(list)
        for i in range(count):
            for j in range(i + 1, count):
                total = cents[i] + cents[j]
                if total >= target:
                    continue
                for k in by_amount.get(target - total, ()):
                    if k > j:
                        found.append((i, j, k))
                if len(pair_sums[total]) < 25:
                    pair_sums[total].append((i, j))
            if full():
                break
        if not full():
            for total, pairs in list(pair_sums.items()):
                for i, j in pairs:
                    for k, m in pair_sums.get(target - total, ()):
                        if k > j:
                            found.append((i, j, k, m))
                if full():
                    break

        best = {}
        for combo in found[:MATCH_COMBOS]:
            rows = sorted((items[i] for i in combo), key=lambda r: (r['date'], r['id']))
            dates = [r['date'] for r in rows]
            score = (len(rows), (max(dates) - min(dates)).days,
                     sum(abs((d - day).days) for d in dates))
            amounts = tuple(sorted(cents[i] for i in combo))
            known = best.get(amounts)
            if known is None:
                best[amounts] = {'kind': 'group', 'rows': rows, 'score': score, 'alternatives': 0}
            elif score < known['score']:
                best[amounts] = {'kind': 'group', 'rows': rows, 'score': score,
                                 'alternatives': known['alternatives'] + 1}
            else:
                known['alternatives'] += 1
        return sorted(best.values(), key=lambda g: g['score'])

    def get_duplicates(self):
        """Outstanding receipts (or payments) from the same party, of the same amount,
        within a few days of each other: the usual shape of an entry booked twice, and
        one of the reasons an entry sits outstanding for months."""
        self.ensure_one()
        self.check_access('read')
        self._flush_lines()
        self.env.cr.execute(SQL(
            "%s WHERE %s AND %s AND l.partner_id IS NOT NULL "
            "ORDER BY l.partner_id, SIGN(l.balance), ABS(l.balance), l.date, l.id",
            SQL(self._LINE_SELECT), self._line_filter(), self._outstanding_sql()))
        groups, current = [], []
        for row in self.env.cr.dictfetchall():
            if current and row['partner_id'] == current[-1]['partner_id'] \
                    and int(round(row['balance'] * 100)) == int(round(current[-1]['balance'] * 100)) \
                    and (row['date'] - current[-1]['date']).days <= DUPLICATE_DAYS:
                current.append(row)
                continue
            if len(current) > 1:
                groups.append(current)
            current = [row]
        if len(current) > 1:
            groups.append(current)
        groups.sort(key=lambda g: (-abs(g[0]['balance']), g[0]['date']))
        return {
            'count': len(groups),
            'days': DUPLICATE_DAYS,
            'groups': [{
                'partner': g[0]['partner'],
                'amount': abs(g[0]['balance']),
                'side': 'deposits' if g[0]['balance'] > 0 else 'withdrawals',
                'lines': [self._line_payload(r) for r in g],
            } for g in groups[:40]],
        }

    # ------------------------------------------------------------------ acting
    def set_bank_balance(self, amount, search=None, age=None, source=None):
        self.ensure_one()
        self._check_editable()
        self.bank_balance = float(amount or 0.0)
        return self.get_screen(search=search, age=age, source=source)

    def set_cleared(self, line_ids, cleared=True, cleared_date=False, search=None, age=None,
                    source=None, delta=False):
        """Tick (or untick) lines as cleared by the bank.

        The cleared date defaults to the line's own date - the common case for a
        receipt banked the same day - and is editable per line. Every change is kept
        as an undo step. With `delta`, only the changed lines come back.
        """
        self.ensure_one()
        self._check_editable()
        lines = self._own_lines(line_ids)
        Clearance = self.env['bank.clearance']
        existing = Clearance.search([('move_line_id', 'in', lines.ids)])
        locked = existing.filtered(lambda c: c.reconciliation_id.state == 'done')
        if locked:
            raise UserError(_(
                "%s line(s) belong to a statement that is already reconciled and cannot "
                "be changed here.", len(locked)))
        step = {'created': [], 'removed': [], 'redated': []}
        if cleared:
            by_line = {c.move_line_id.id: c for c in existing}
            to_create = []
            for line in lines:
                day = fields.Date.to_date(cleared_date) if cleared_date else line.date
                clearance = by_line.get(line.id)
                if clearance:
                    if clearance.cleared_date != day or clearance.reconciliation_id != self:
                        step['redated'].append({
                            'id': clearance.id,
                            'date': fields.Date.to_string(clearance.cleared_date),
                            'rec': clearance.reconciliation_id.id})
                        clearance.write({'cleared_date': day, 'reconciliation_id': self.id})
                else:
                    to_create.append({'move_line_id': line.id, 'cleared_date': day,
                                      'reconciliation_id': self.id})
            if to_create:
                step['created'] = Clearance.create(to_create).ids
        else:
            step['removed'] = [{
                'move_line_id': c.move_line_id.id,
                'cleared_date': fields.Date.to_string(c.cleared_date),
                'reconciliation_id': c.reconciliation_id.id} for c in existing]
            existing.unlink()
        self._record_step(step)
        if delta:
            return self._screen_delta(lines.ids, search=search, age=age, source=source)
        return self.get_screen(search=search, age=age, source=source)

    def _record_step(self, step):
        created, removed, redated = len(step['created']), len(step['removed']), len(step['redated'])
        if not (created or removed or redated):
            return
        label = self.env.context.get('bank_rec_step')
        if not label:
            if created:
                label = _("Ticked 1 entry") if created == 1 else _("Ticked %s entries", created)
            elif removed:
                label = _("Unticked 1 entry") if removed == 1 else _("Unticked %s entries", removed)
            else:
                label = _("Changed a cleared date") if redated == 1 \
                    else _("Changed %s cleared dates", redated)
        Step = self.env['bank.reconciliation.step']
        Step.create({'reconciliation_id': self.id, 'label': label, 'payload': step})
        Step.search([('reconciliation_id', '=', self.id)], offset=STEP_KEEP).unlink()

    def _undo_info(self):
        step = self.env['bank.reconciliation.step'].search(
            [('reconciliation_id', '=', self.id)], limit=1)
        if not step:
            return False
        return {'label': step.label, 'user': step.user_id.name,
                'when': format_datetime(self.env, step.create_date, dt_format='short')}

    def undo_last(self, search=None, age=None, source=None):
        """Put the ticks back as they were before the last step on this statement."""
        self.ensure_one()
        self._check_editable()
        step = self.env['bank.reconciliation.step'].search(
            [('reconciliation_id', '=', self.id)], limit=1)
        if not step:
            raise UserError(_("There is nothing to undo on this statement."))
        data = step.payload or {}
        Clearance = self.env['bank.clearance']
        Clearance.browse(data.get('created') or []).exists().unlink()
        for old in data.get('redated') or []:
            clearance = Clearance.browse(old['id']).exists()
            if clearance:
                clearance.write({'cleared_date': old['date'],
                                 'reconciliation_id': old.get('rec') or False})
        removed = data.get('removed') or []
        if removed:
            lines = self.env['account.move.line'].browse(
                [r['move_line_id'] for r in removed]).exists()
            back = set(lines.ids) - set(Clearance.search(
                [('move_line_id', 'in', lines.ids)]).move_line_id.ids)
            Clearance.create([{'move_line_id': r['move_line_id'], 'cleared_date': r['cleared_date'],
                               'reconciliation_id': r.get('reconciliation_id') or self.id}
                              for r in removed if r['move_line_id'] in back])
        self.message_post(body=_("Undone: %s.", step.label))
        step.unlink()
        return self.get_screen(search=search, age=age, source=source)

    def clear_matching(self, side, search=None, age=None, upto=False, source=None):
        """Tick every outstanding line on one side that matches the filters (and, given
        `upto`, was booked on or before that date), each on its own date."""
        self.ensure_one()
        self._check_editable()
        ids = [row['id'] for row in self._screen_lines(
            side, search, limit=None, uncleared_only=True, age=age, upto=upto,
            source=source if source in FILTERS else None)]
        if not ids:
            return self.get_screen(search=search, age=age, source=source)
        label = _("Ticked everything up to %(day)s (%(count)s)",
                  day=format_date(self.env, fields.Date.to_date(upto)), count=len(ids)) \
            if upto else _("Ticked all shown (%s)", len(ids))
        return self.with_context(bank_rec_step=label).set_cleared(
            ids, True, False, search=search, age=age, source=source)

    def clear_opening(self, search=None, age=None, source=None):
        self.ensure_one()
        self._check_editable()
        opening = self._opening_items()
        if not opening:
            return self.get_screen(search=search, age=age, source=source)
        return self.with_context(bank_rec_step=_("Ticked the opening balance")).set_cleared(
            opening['ids'], True, False, search=search, age=age, source=source)

    def set_query(self, line_id, note=False, search=None, age=None, source=None, delta=False):
        """Flag a line as under query with the bank, with a note - or clear the flag when
        the note is empty. Queries are listed on the printed statement and the letter."""
        self.ensure_one()
        self.check_access('write')
        line = self._own_lines([line_id])
        Query = self.env['bank.line.query']
        query = Query.search([('move_line_id', '=', line.id)])
        note = (note or '').strip()
        if note:
            if query:
                query.write({'note': note, 'user_id': self.env.uid})
            else:
                Query.create({'move_line_id': line.id, 'note': note})
        else:
            query.unlink()
        if delta:
            return self._screen_delta(line.ids, search=search, age=age, source=source)
        return self.get_screen(search=search, age=age, source=source)

    def tick_with_difference(self, line_ids, cleared_date, difference, search=None, age=None,
                             source=None):
        """Tick entries the bank cleared short, and book the shortfall as bank charges:
        a receipt of 10,000 credited as 9,982 is one click, not a tick and an entry."""
        self.ensure_one()
        self._check_editable()
        lines = self._own_lines(line_ids)
        difference = self.currency_id.round(float(difference or 0.0))
        if difference <= 0 or _cents(difference) > _cents(self.charge_tolerance):
            raise UserError(_(
                "A shortfall booked this way must be more than zero and at most %s "
                "(Short Credits up to, on the statement form).",
                formatLang(self.env, self.charge_tolerance, currency_obj=self.currency_id)))
        if not self.charge_account_id:
            raise UserError(_("Set the Bank Charges Account on this reconciliation first."))
        day = fields.Date.to_date(cleared_date) if cleared_date else self.date
        names = ', '.join(lines.move_id.mapped('name'))
        self.create_bank_entry('charge', difference, day, label=_("Bank charges on %s", names)[:250])
        label = _("Ticked %(entries)s, booked %(amount)s short as charges", entries=names[:80],
                  amount=formatLang(self.env, difference, currency_obj=self.currency_id))
        return self.with_context(bank_rec_step=label).set_cleared(
            lines.ids, True, day, search=search, age=age, source=source)

    def create_bank_entry(self, kind, amount, entry_date=False, partner_id=False,
                          account_id=False, label=False, reference=False, cleared=True,
                          search=None, age=None, source=None):
        """Post an entry in this bank's journal from the matching screen.

        A receipt or payment goes against the party's receivable or payable account (or
        the account picked); a transfer against the account picked; charges and interest
        against the statement's accounts. The bank side is this statement's bank
        account - so the entry is on the screen at once - and, when the passbook already
        shows it, ticked as cleared on its date. A journal entry rather than a payment:
        a payment would post to the journal's outstanding-receipts account and never
        reach the bank account this statement reconciles.
        """
        self.ensure_one()
        self._check_editable()
        kind = LEGACY_KINDS.get(kind, kind)
        if kind not in ENTRY_KINDS:
            raise UserError(_("Unknown kind of bank entry."))
        into_bank, needs_partner = ENTRY_KINDS[kind]
        amount = self.currency_id.round(float(amount or 0.0))
        if amount <= 0:
            raise UserError(_("Enter the amount the bank shows."))
        day = fields.Date.to_date(entry_date) if entry_date else self.date
        if day > self.date:
            raise UserError(_("That date is after the statement date."))
        partner = self.env['res.partner'].browse(int(partner_id or 0)).exists()
        if needs_partner and not partner:
            raise UserError(_("Pick the party."))
        other = self.env['account.account'].browse(int(account_id or 0)).exists()
        if not other:
            company_partner = partner.with_company(self.company_id)
            other = {
                'receipt': company_partner.property_account_receivable_id,
                'payment': company_partner.property_account_payable_id,
                'charge': self.charge_account_id,
                'interest': self.interest_account_id,
            }.get(kind) or self.env['account.account']
        if not other:
            raise UserError({
                'charge': _("Set the Bank Charges Account on this reconciliation first."),
                'interest': _("Set the Interest Account on this reconciliation first."),
            }.get(kind, _("Pick the account on the other side of this entry.")))
        if other == self.account_id:
            raise UserError(_("The other side cannot be the bank account itself."))
        if self.company_id not in other.company_ids:
            raise UserError(_("%(account)s is not an account of %(company)s.",
                              account=other.display_name, company=self.company_id.name))
        if not (label or '').strip():
            label = {
                'receipt': _("Received from %s", partner.name),
                'payment': _("Paid to %s", partner.name),
                'transfer_in': _("Received in the bank"),
                'transfer_out': _("Paid from the bank"),
                'interest': _("Interest credited by bank"),
                'charge': _("Bank charges"),
            }[kind]
        label = label.strip()
        party = {'partner_id': partner.id} if partner else {}
        move = self.env['account.move'].create({
            'move_type': 'entry',
            'journal_id': self.journal_id.id,
            'company_id': self.company_id.id,
            'date': day,
            'ref': (reference or '').strip() or label,
            'line_ids': [
                Command.create(dict(party, account_id=self.account_id.id, name=label,
                                    debit=amount if into_bank else 0.0,
                                    credit=0.0 if into_bank else amount)),
                Command.create(dict(party, account_id=other.id, name=label,
                                    debit=0.0 if into_bank else amount,
                                    credit=amount if into_bank else 0.0)),
            ],
        })
        move.action_post()
        bank_line = move.line_ids.filtered(lambda l: l.account_id == self.account_id)
        if cleared:
            self.env['bank.clearance'].create({
                'move_line_id': bank_line.id, 'cleared_date': day, 'reconciliation_id': self.id})
        self.message_post(body=_(
            "%(label)s of %(amount)s posted as %(move)s%(cleared)s.", label=label,
            amount=formatLang(self.env, amount, currency_obj=self.currency_id), move=move.name,
            cleared=_(" and ticked as cleared") if cleared else ''))
        screen = self.get_screen(search=search, age=age, source=source)
        screen['posted'] = {'id': move.id, 'name': move.name, 'line_id': bank_line.id}
        return screen

    def post_bank_item(self, kind, amount, item_date=False, label=False, account_id=False,
                       search=None, age=None, source=None):
        """The first version's bank item: a charge, interest or transfer, already cleared."""
        return self.create_bank_entry(kind, amount, item_date, account_id=account_id,
                                      label=label, search=search, age=age, source=source)

    def action_validate(self):
        for rec in self:
            rec._check_editable()
            if rec._missing_bank_balance():
                raise UserError(_("Enter the Balance as per Bank from the passbook first."))
            if not rec.currency_id.is_zero(rec.difference):
                raise UserError(_(
                    "The difference is %s. A statement is reconciled only when the books, "
                    "adjusted for what the bank has not seen yet, agree with the bank.",
                    rec.difference))
            later = self.search([
                ('account_id', '=', rec.account_id.id), ('company_id', '=', rec.company_id.id),
                ('state', '=', 'done'), ('date', '>', rec.date)], limit=1)
            if later:
                raise UserError(_(
                    "%s is already reconciled for a later date. Statements are reconciled "
                    "in date order.", later.name))
            self.env['bank.clearance'].search([
                ('move_line_id.account_id', '=', rec.account_id.id),
                ('company_id', '=', rec.company_id.id),
                ('cleared_date', '<=', rec.date),
                '|', ('reconciliation_id', '=', False),
                ('reconciliation_id.state', '=', 'draft'),
            ]).write({'reconciliation_id': rec.id})
            rec.step_ids.unlink()
            rec.state = 'done'
            rec.message_post(body=_("Reconciled: balance as per bank %s.", rec.bank_balance))
        return True

    def action_reset_draft(self):
        if not self.env.user.has_group('account.group_account_manager'):
            raise UserError(_("Only an accounting administrator can reopen a reconciled statement."))
        for rec in self:
            later = self.search([
                ('account_id', '=', rec.account_id.id), ('company_id', '=', rec.company_id.id),
                ('state', '=', 'done'), ('date', '>', rec.date)], limit=1)
            if later:
                raise UserError(_("Reopen %s first: it is reconciled for a later date.",
                                  later.name))
            rec.state = 'draft'
            rec.message_post(body=_("Reopened."))
        return True

    def action_start_next(self):
        """The next statement of this bank: its draft if one is open, else a new one
        carrying this statement's accounts and stale threshold."""
        self.ensure_one()
        if self.state != 'done':
            raise UserError(_("Reconcile this statement before starting the next one."))
        draft = self.search([('journal_id', '=', self.journal_id.id),
                             ('company_id', '=', self.company_id.id),
                             ('state', '=', 'draft')], order='date desc, id desc', limit=1)
        if not draft:
            today = fields.Date.context_today(self)
            draft = self.create({
                'journal_id': self.journal_id.id,
                'company_id': self.company_id.id,
                'account_id': self.account_id.id,
                'date': max(today, self.date + timedelta(days=1)),
                'charge_account_id': self.charge_account_id.id,
                'interest_account_id': self.interest_account_id.id,
                'stale_days': self.stale_days,
                'charge_tolerance': self.charge_tolerance,
            })
        return draft.action_open_screen()

    def action_open_screen(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.client',
            'tag': 'bank_reconciliation_screen',
            'name': self.name,
            'params': {'reconciliation_id': self.id},
            'context': {'active_id': self.id},
        }

    def action_print_pdf(self):
        self.ensure_one()
        return self.env.ref('lab_bank_reconciliation.action_report_brs').report_action(self)

    def action_print_query_letter(self):
        self.ensure_one()
        if not self._query_rows():
            raise UserError(_("No entry on this statement is under query with the bank."))
        return self.env.ref('lab_bank_reconciliation.action_report_query_letter').report_action(self)

    def action_download_xlsx(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_url',
                'url': '/bank_reconciliation/%s/xlsx' % self.id, 'target': 'self'}

    # ------------------------------------------------------------------ the overview
    @api.model
    def get_overview(self):
        """One card per bank: its book balance today, what is outstanding and how old,
        and where its reconciliation stands."""
        self.check_access('read')
        today = fields.Date.context_today(self)
        journals = self.env['account.journal'].search(
            [('type', '=', 'bank'), ('company_id', 'in', self.env.companies.ids)],
            order='company_id, sequence, id')
        accounts = self._accounts_for_journals(journals)
        self._flush_lines()
        cards = []
        for journal in journals:
            account, company = accounts[journal.id], journal.company_id
            statements = self.search([('journal_id', '=', journal.id),
                                      ('company_id', '=', company.id)],
                                     order='date desc, id desc')
            last_done = statements.filtered(lambda r: r.state == 'done')[:1]
            draft = statements.filtered(lambda r: r.state == 'draft')[:1]
            stale_days = statements[:1].stale_days or 90
            stats = self._account_stats(account, company, today, stale_days) if account else None
            if draft:
                status, label = 'progress', _("In progress")
            elif not stats or not stats['lines']:
                status, label = 'empty', _("No entries yet")
            elif not last_done:
                status, label = 'never', _("Never reconciled")
            else:
                days = (today - last_done.date).days
                status = 'overdue' if days > OVERDUE_DAYS else 'current'
                label = _("Reconciled today") if days <= 0 else _("Reconciled %s days ago", days)
            stats = stats or {'book': 0.0, 'lines': 0, 'deposits': {'count': 0, 'amount': 0.0},
                              'payments': {'count': 0, 'amount': 0.0}, 'stale': 0,
                              'oldest': False, 'last_entry': False}
            cards.append({
                'journal_id': journal.id,
                'journal': journal.name,
                'company': company.name,
                'company_id': company.id,
                'currency_id': company.currency_id.id,
                'account': account.display_name if account else '',
                'book': stats['book'],
                'deposits': stats['deposits'],
                'payments': stats['payments'],
                'stale': stats['stale'],
                'trend': self._outstanding_trend(account, company, today) if account else [],
                'stale_days': stale_days,
                'oldest_label': format_date(self.env, stats['oldest']) if stats['oldest'] else '',
                'last_entry_label': (format_date(self.env, stats['last_entry'])
                                     if stats['last_entry'] else ''),
                'status': status,
                'status_label': label,
                'history_count': len(statements),
                'last_done': last_done and {
                    'id': last_done.id, 'date_label': format_date(self.env, last_done.date),
                    'bank_balance': last_done.bank_balance} or False,
                'draft': draft and {
                    'id': draft.id, 'date_label': format_date(self.env, draft.date),
                    'difference': draft.difference,
                    'balanced': draft.currency_id.is_zero(draft.difference)} or False,
            })
        return {
            'cards': cards,
            'totals': {
                'banks': len(cards),
                'attention': sum(1 for c in cards if c['status'] in ('never', 'overdue')),
                'progress': sum(1 for c in cards if c['status'] == 'progress'),
                'stale': sum(c['stale'] for c in cards),
            },
        }

    @api.model
    def _account_stats(self, account, company, day, stale_days):
        self.env.cr.execute(SQL(
            """
            WITH x AS (
                SELECT l.balance, l.date, (c.id IS NULL OR c.cleared_date > %(day)s) AS open
                  FROM account_move_line l
                  LEFT JOIN bank_clearance c ON c.move_line_id = l.id
                 WHERE l.account_id = %(acc)s AND l.company_id = %(co)s
                   AND l.parent_state = 'posted' AND l.date <= %(day)s)
            SELECT COALESCE(SUM(balance), 0), count(*),
                   count(*) FILTER (WHERE open AND balance > 0),
                   COALESCE(SUM(balance) FILTER (WHERE open AND balance > 0), 0),
                   count(*) FILTER (WHERE open AND balance < 0),
                   COALESCE(-SUM(balance) FILTER (WHERE open AND balance < 0), 0),
                   count(*) FILTER (WHERE open AND balance != 0 AND date < %(stale)s),
                   MIN(date) FILTER (WHERE open AND balance != 0),
                   MAX(date)
              FROM x
            """, day=day, acc=account.id, co=company.id,
            stale=day - timedelta(days=stale_days)))
        book, lines, dep_n, dep_amt, pay_n, pay_amt, stale, oldest, last_entry = \
            self.env.cr.fetchone()
        return {'book': book, 'lines': lines,
                'deposits': {'count': dep_n, 'amount': dep_amt},
                'payments': {'count': pay_n, 'amount': pay_amt},
                'stale': stale, 'oldest': oldest, 'last_entry': last_entry}

    @api.model
    def _outstanding_trend(self, account, company, day, weeks=12):
        """How many entries were outstanding at the end of each of the last `weeks`
        weeks: whether the bank is being kept up, or falling behind."""
        self.env['account.move.line'].flush_model()
        self.env['bank.clearance'].flush_model()
        self.env.cr.execute(SQL(
            """
            SELECT d::date, count(l.id)
              FROM generate_series(%(start)s::date, %(day)s::date, interval '7 days') d
              LEFT JOIN account_move_line l
                ON l.account_id = %(acc)s AND l.company_id = %(co)s
               AND l.parent_state = 'posted' AND l.balance != 0 AND l.date <= d
               AND NOT EXISTS (SELECT 1 FROM bank_clearance c
                                WHERE c.move_line_id = l.id AND c.cleared_date <= d)
             GROUP BY d
             ORDER BY d
            """, start=day - timedelta(days=7 * weeks), day=day, acc=account.id, co=company.id))
        return [{'date': fields.Date.to_string(d), 'count': n} for d, n in self.env.cr.fetchall()]

    @api.model
    def open_bank(self, journal_id):
        """Reconcile a bank from the overview: its open statement, or a new one for today."""
        journal = self.env['account.journal'].browse(int(journal_id)).exists()
        if not journal or journal.type != 'bank':
            raise UserError(_("That is not a bank journal."))
        draft = self.search([('journal_id', '=', journal.id), ('state', '=', 'draft')],
                            order='date desc, id desc', limit=1)
        if not draft:
            draft = self.with_company(journal.company_id).create({
                'journal_id': journal.id,
                'company_id': journal.company_id.id,
                'date': fields.Date.context_today(self),
            })
        return draft.action_open_screen()

    # ------------------------------------------------------------------ the reports
    def _query_rows(self):
        """Every line of this account up to the statement date that is under query."""
        self.ensure_one()
        self._flush_lines()
        self.env.cr.execute(SQL("%s WHERE %s AND q.id IS NOT NULL ORDER BY l.date, l.id",
                                SQL(self._LINE_SELECT), self._line_filter()))
        return [dict(self._line_payload(r), date=r['date']) for r in self.env.cr.dictfetchall()]

    def _bank_name(self):
        return self.journal_id.bank_id.name or self.journal_id.name

    def _brs_data(self):
        """The statement as printed: the figures, the ageing, every outstanding line,
        and the entries under query with the bank."""
        self.ensure_one()
        self.invalidate_recordset(FIGURES)

        def outstanding(side):
            return [dict(self._line_payload(r), date=r['date'])
                    for r in self._screen_lines(side, limit=None, uncleared_only=True)]

        stats = self._outstanding_stats()
        labels = self._age_labels()
        return {
            'book': self.book_balance,
            'deposits_not_credited': self.deposits_not_credited,
            'payments_not_presented': self.payments_not_presented,
            'adjusted': self.adjusted_balance,
            'bank': self.bank_balance,
            'difference': self.difference,
            'deposits': outstanding('deposits'),
            'payments': outstanding('withdrawals'),
            'queries': self._query_rows(),
            'ageing': [{
                'label': labels[b],
                'deposits': stats['deposits']['ageing'][b],
                'payments': stats['withdrawals']['ageing'][b],
            } for b in BUCKETS],
        }


class BankClearance(models.Model):
    """One bank-account line, cleared by the bank on a date.

    Its own record, never a field written onto the posted journal item: posted entries
    stay untouched, and who cleared a line, and when, stays on file.
    """
    _name = 'bank.clearance'
    _description = 'Bank Clearance'
    _order = 'cleared_date desc, id desc'

    move_line_id = fields.Many2one(
        'account.move.line', string='Journal Item', required=True, index=True,
        ondelete='cascade')
    cleared_date = fields.Date(required=True, index=True)
    reconciliation_id = fields.Many2one(
        'bank.reconciliation', index=True, ondelete='set null')
    cleared_by_id = fields.Many2one(
        'res.users', string='Cleared By', default=lambda self: self.env.user, readonly=True)
    company_id = fields.Many2one(
        related='move_line_id.company_id', store=True, index=True)

    _move_line_unique = models.Constraint(
        'UNIQUE(move_line_id)', "A journal item can be cleared only once.")

    def _check_not_locked(self):
        if self.filtered(lambda c: c.reconciliation_id.state == 'done'):
            raise UserError(_(
                "That line belongs to a reconciled statement. Reopen the statement first."))

    def write(self, vals):
        self._check_not_locked()
        return super().write(vals)

    def unlink(self):
        self._check_not_locked()
        return super().unlink()


class BankReconciliationStep(models.Model):
    """One change to a statement's ticks, kept so it can be undone."""
    _name = 'bank.reconciliation.step'
    _description = 'Bank Reconciliation Undo Step'
    _order = 'id desc'

    reconciliation_id = fields.Many2one(
        'bank.reconciliation', required=True, index=True, ondelete='cascade')
    company_id = fields.Many2one(
        related='reconciliation_id.company_id', store=True, index=True)
    user_id = fields.Many2one(
        'res.users', default=lambda self: self.env.user, readonly=True)
    label = fields.Char(required=True)
    payload = fields.Json()


class BankLineQuery(models.Model):
    """A bank-account line under query with the bank, with what is being asked."""
    _name = 'bank.line.query'
    _description = 'Bank Line Query'
    _order = 'id desc'

    move_line_id = fields.Many2one(
        'account.move.line', string='Journal Item', required=True, index=True,
        ondelete='cascade')
    note = fields.Char(required=True)
    user_id = fields.Many2one(
        'res.users', string='Raised By', default=lambda self: self.env.user)
    company_id = fields.Many2one(
        related='move_line_id.company_id', store=True, index=True)

    _move_line_unique = models.Constraint(
        'UNIQUE(move_line_id)', "A journal item carries one query at a time.")
