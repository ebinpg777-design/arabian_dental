# -*- coding: utf-8 -*-
"""Collection performance: what was invoiced, what came back, and how much of it.

The lab's question at the route meeting is always the same shape: *last month we
billed this much on the KLM route — how much of it has actually come in?* So the
figures pair two different windows:

* **Invoiced** — POSTED customer invoices dated in the sales period, net of credit
  notes. Invoices, not confirmed orders: an order is a promise, an invoice is a
  claim, and only a claim can be collected against. (client, 2026-08-21: "the sales
  amount should be calculated based on the confirmed invoices". On July 2026 the
  switch moves the headline from 56,01,600 by order value to 54,83,785 by invoice
  value — the 1,17,815 gap is orders delivered but not yet billed.)
* **Received** — customer receipts posted in the collection period.
* **Collection %** — received ÷ invoiced.

On top of the pair, the OPEN side: how much receivable is still out, how old it is,
and which clinics are sitting on it.

Attribution:

* by **Sales Route**: the invoice's own ``team_id`` — 100% populated on this database
  and frozen at invoicing time, so a clinic later moved to another route does not
  rewrite history. Receipts and open money take the clinic's current route (a receipt
  or an old debt has no route of its own).
* by **Salesperson**: resolved through the ORIGINATING SALE ORDER, not from
  ``invoice_user_id``. A bulk recompute on 2026-08-18 stamped the route's Team Leader
  over the real salesperson on 324 July invoices (₹3,45,680 of ARUN's TVM bookings
  credited to the leader) — the order's own salesperson is the one signal that never
  suffered that. Receipts follow the clinic's modal salesperson over the sales
  window, then the route leader, then the clinic's salesperson; "Unassigned" catches
  the rest so the columns always add up.

Open money — the FIFO countback
-------------------------------
``amount_residual`` is DEAD on this database: receipts are booked as plain journal
entries and never reconciled against invoices (1 partial reconcile in the whole DB),
so every invoice still claims its full value and the naive residual overstates the
outstanding 2.5× — ₹2.36 Cr claimed against ₹99 lakh real (verified 2026-08-21, and
the distortion is uneven enough to re-rank the routes). What IS true is each
clinic's balance, so the open figures are built the way a shop reads a ledger:
per clinic, receipts pay off invoices oldest-first, and whatever the money has not
reached yet is open, aged by its own invoice date. Due dates are no extra
information here — invoice_date_due equals invoice_date on 99.6% of invoices — so
"overdue" is defined as open money older than 30 days.
"""
import base64
import logging

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import SQL
from odoo.tools.misc import formatLang

_logger = logging.getLogger(__name__)

GROUPINGS = [('team_id', 'Sales Route'), ('user_id', 'Salesperson')]

# Open money older than this many days counts as overdue. Due dates carry no signal
# in this database (they equal the invoice date), so the threshold is the deal the
# lab actually works on: the month's bill is settled within the following month.
OVERDUE_DAYS = 30

AGE_BUCKETS = [
    ('current', '0–30 days', 0, 30),
    ('d30', '31–60 days', 31, 60),
    ('d60', '61–90 days', 61, 90),
    ('d90', 'Over 90 days', 91, None),
]

TREND_MONTHS = 6

# Who the collection figures are for: the accounts desk, the field force (confined
# to their own round by _viewer_route_ids) and management. Everyone else is refused
# at the method, because the menus are gated and an RPC is not.
ACCESS_GROUPS = (
    'account.group_account_readonly',
    'account.group_account_invoice',
    'lab_fieldwork.group_fieldwork_executive',
    'lab_fieldwork.group_fieldwork_manager',
    'lab_ceo_dashboard.group_lab_executive',
)


class CollectionPerformance(models.AbstractModel):
    _name = 'lab.collection.performance'
    _description = 'Collection Performance'

    # ------------------------------------------------------------------ periods
    @api.model
    def default_periods(self, today=None):
        """Invoiced = last month, collections = this month (client's own framing)."""
        today = today or fields.Date.context_today(self)
        this_start = today.replace(day=1)
        last_start = this_start - relativedelta(months=1)
        return {
            'sales_from': last_start,
            'sales_to': this_start - relativedelta(days=1),
            'pay_from': this_start,
            'pay_to': (this_start + relativedelta(months=1)) - relativedelta(days=1),
        }

    @api.model
    def _dates(self, options):
        base = self.default_periods()
        out = {}
        for key in ('sales_from', 'sales_to', 'pay_from', 'pay_to'):
            value = (options or {}).get(key)
            out[key] = fields.Date.to_date(value) if value else base[key]
        return out

    # ------------------------------------------------------------------ who sees what
    # A field-work Executive works one round and sees that round's money - their
    # cards, their ageing, their table rows, and only their own documents behind a
    # click. Anyone with an accounting role, and a field-work Manager, sees the
    # company. The LEADERBOARDS stay whole for everyone: a ranking you cannot
    # compare yourself against is not a ranking. (client, 2026-08-24)
    @api.model
    def _group_safe(self, xmlid):
        """has_group() that tolerates the module being absent.

        lab_collections does not depend on lab_fieldwork - an accounting module
        should not require the field force to be installed - so the group may not
        exist at all. has_group() would raise on a missing xmlid.
        """
        group = self.env.ref(xmlid, raise_if_not_found=False)
        return bool(group) and group in self.env.user.all_group_ids

    @api.model
    def _check_collections_access(self):
        """Refuse a caller who holds none of the roles the screen is opened to.

        Every figure below is raw SQL, which no ACL or record rule ever sees, and
        _viewer_route_ids answers "the whole company" for anyone outside field work
        - so without this any internal login could read the whole receivable book
        over RPC. Internal readers that need the figures use sudo().
        """
        if self.env.su or any(self._group_safe(xmlid) for xmlid in ACCESS_GROUPS):
            return
        raise AccessError(_("The collection figures are for the accounts desk, the "
                            "field force and management."))

    @api.model
    def _viewer_route_ids(self):
        """The routes this viewer is confined to, or None for the whole company."""
        # The Executive group is tested FIRST and wins. 78 of the 82 salespeople on
        # this database also hold Accounting Readonly, so deferring to the accounting
        # role - as this did - meant the scope silently never applied to almost every
        # real salesperson, and the widget would have shown them the whole company.
        # A field-work Manager still sees everything; that is the way out.
        # (client, 2026-08-24)
        if self._group_safe('lab_fieldwork.group_fieldwork_manager'):
            return None
        if self._group_safe('lab_fieldwork.group_fieldwork_executive'):
            return self.env['crm.team'].sudo().search(
                ['|', ('user_id', '=', self.env.user.id),
                      ('member_ids', 'in', self.env.user.id)]).ids
        return None

    @api.model
    def _can_drill_documents(self):
        """May this viewer open the invoices and receipts themselves?

        Read access to account.move is the honest test - not a group name: it is
        exactly what the list view will need a moment later.
        """
        try:
            self.env['account.move'].check_access('read')
            self.env['account.move.line'].check_access('read')
        except AccessError:
            return False
        return True

    def _check_scope(self, group, key):
        """Refuse a row that is not the viewer's to open.

        The screen only offers a restricted viewer their own rows, but the drill and
        the breakdown are RPCs: without this, a crafted call would hand back another
        route's invoices to someone who cannot see that route on screen.
        """
        self._check_collections_access()
        scope = self._viewer_route_ids()
        if scope is None or key is None:
            return scope
        allowed = key in scope if group == 'team_id' else key == self.env.uid
        if not allowed:
            raise AccessError(_("That sales route is not yours to open."))
        return scope

    @api.model
    def _company(self, options):
        company_id = (options or {}).get('company_id')
        if not company_id:
            return self.env.company
        company = self.env['res.company'].browse(int(company_id))
        # The SQL reads whichever company it is handed, so a company the viewer
        # does not belong to has to be refused here, not trusted from the options.
        if not self.env.su and company not in self.env.user.company_ids:
            raise AccessError(_("You do not have access to that company's figures."))
        return company

    # ------------------------------------------------------------------ accounts
    def _receivable_ids(self, company):
        """The receivable accounts, resolved once per query.

        Naming the six account ids instead of joining account_account on
        account_type lets every line scan use the (account_id, date) index —
        with the join, the planner filtered 1,90,000 receivable lines FIRST and
        then probed account_move once per line (5,70,000 buffer reads per query,
        measured 2026-08-21).
        """
        self.env.cr.execute(SQL(
            "SELECT id FROM account_account WHERE account_type = 'asset_receivable'"))
        return tuple(r[0] for r in self.env.cr.fetchall()) or (0,)

    # ------------------------------------------------------------------ invoiced
    def _invoice_where(self, dates, company):
        """The moves that count as sales: posted customer paper in the window."""
        return SQL(
            """m.state = 'posted'
               AND m.company_id = %s
               AND m.move_type IN ('out_invoice', 'out_refund')
               AND m.invoice_date >= %s AND m.invoice_date <= %s""",
            company.id, dates['sales_from'], dates['sales_to'])

    def _order_user_join(self, dates, company):
        """Each invoice's salesperson, read off its originating order.

        The modal ``sale_order.user_id`` across the invoice's lines; an invoice not
        made from an order (there are none today, but hand-typed ones will come)
        falls back to its own ``invoice_user_id``.
        """
        return SQL("""
            LEFT JOIN (
                SELECT DISTINCT ON (l.move_id) l.move_id, s.user_id
                  FROM account_move_line l
                  JOIN account_move im ON im.id = l.move_id
                  JOIN sale_order_line_invoice_rel r ON r.invoice_line_id = l.id
                  JOIN sale_order_line sol ON sol.id = r.order_line_id
                  JOIN sale_order s ON s.id = sol.order_id
                 WHERE s.user_id IS NOT NULL
                   AND im.company_id = %s
                   AND im.invoice_date >= %s AND im.invoice_date <= %s
                 GROUP BY l.move_id, s.user_id
                 ORDER BY l.move_id, COUNT(*) DESC, s.user_id
            ) ou ON ou.move_id = m.id""",
            company.id, dates['sales_from'], dates['sales_to'])

    @api.model
    def _sales_by(self, group, dates, company):
        """{key: {amount, count}} of posted customer invoices in the sales window.

        Credit notes subtract; their count is reported separately so the KPI card
        can say "n credit notes netted off" the day the first one is raised.
        """
        if group == 'team_id':
            key_sql, join_sql = SQL("COALESCE(m.team_id, 0)"), SQL("")
        else:
            key_sql = SQL("COALESCE(ou.user_id, m.invoice_user_id, 0)")
            join_sql = self._order_user_join(dates, company)
        self.env.cr.execute(SQL("""
            SELECT %s AS key,
                   SUM(CASE WHEN m.move_type = 'out_invoice'
                            THEN m.amount_total ELSE -m.amount_total END) AS amount,
                   COUNT(*) FILTER (WHERE m.move_type = 'out_invoice') AS invoices,
                   COUNT(*) FILTER (WHERE m.move_type = 'out_refund') AS refunds
              FROM account_move m
              %s
             WHERE %s
             GROUP BY 1
        """, key_sql, join_sql, self._invoice_where(dates, company)))
        return {row[0] or 0: {'amount': row[1] or 0.0, 'count': row[2],
                              'refunds': row[3]}
                for row in self.env.cr.fetchall()}

    # ------------------------------------------------------------------ received
    def _receipt_agg(self, dates, company):
        """CTEs producing ``agg``: receipts summed per (clinic, move).

        A receipt is a posted credit on a clinic's receivable account. This
        database records collections as journal entries (bank/cash) and holds no
        account.payment rows at all — a payment-based figure would read zero for
        ever (verified on the live data, 2026-08-21). What money-in means here is
        whatever clears an invoice, however the entry was made; refunds (debits)
        net off.

        Aggregate-first is the performance contract of this module: the partner /
        team / salesperson joins that attribute the money run on the few hundred
        ``agg`` rows, never on the raw lines. Before this shape the planner probed
        res_partner twice per line — a million index lookups per dashboard load.
        ``l.date``, ``l.company_id`` and ``l.parent_state`` are the line's stored
        copies of the move's values, so only the move_type test still needs
        account_move — joined as a pre-filtered set (``mv``), small enough to hash.
        """
        return SQL("""
            WITH mv AS (
                SELECT id FROM account_move
                 WHERE state = 'posted' AND company_id = %s
                   AND move_type NOT IN ('out_invoice', 'out_refund')
                   AND date >= %s AND date <= %s
            ), agg AS (
                SELECT l.partner_id, l.move_id, SUM(l.credit - l.debit) AS amount
                  FROM account_move_line l
                  JOIN mv ON mv.id = l.move_id
                 WHERE l.account_id IN %s
                   AND l.date >= %s AND l.date <= %s
                   AND l.partner_id IS NOT NULL
                 GROUP BY 1, 2
            )""",
            company.id, dates['pay_from'], dates['pay_to'],
            self._receivable_ids(company), dates['pay_from'], dates['pay_to'])

    def _receipt_key(self, group, dates, company):
        """(key expression, extra join) attributing a receipt to a route / person."""
        if group == 'team_id':
            return SQL("COALESCE(rp.team_id, cp.team_id, 0)"), SQL("")
        # Credit a receipt to whoever actually booked that clinic's work in the
        # sales window (the modal salesperson of its orders), then the route leader,
        # then the clinic's own salesperson. Deriving it from the same orders that
        # stand behind the invoiced figure is what keeps the two halves of the
        # percentage comparable — and it survives the duplicate user accounts this
        # database has (the KLM leader is user 107 "Nithin Sha" while its orders are
        # booked by 111 "Nithin sha": the same person, two logins). (2026-08-21)
        return (
            SQL("COALESCE(booked.user_id, t.user_id, rp.user_id, cp.user_id, 0)"),
            SQL("""
              LEFT JOIN (
                    SELECT s.partner_id,
                           mode() WITHIN GROUP (ORDER BY s.user_id) AS user_id
                      FROM sale_order s
                     WHERE s.state IN ('sale', 'done')
                       AND s.company_id = %s
                       AND s.date_order >= %s AND s.date_order < (%s::date + 1)
                       AND s.user_id IS NOT NULL
                     GROUP BY s.partner_id
              ) booked ON booked.partner_id = rp.id""",
                company.id, dates['sales_from'], dates['sales_to']))

    @api.model
    def _payments_by(self, group, dates, company):
        """{key: {amount, count}} of money received in the collection window."""
        key_sql, booked_join = self._receipt_key(group, dates, company)
        self.env.cr.execute(SQL("""
            %s
            SELECT %s AS key,
                   SUM(agg.amount) AS amount,
                   COUNT(DISTINCT agg.move_id) AS cnt
              FROM agg
              JOIN res_partner rp ON rp.id = agg.partner_id
              LEFT JOIN res_partner cp ON cp.id = rp.commercial_partner_id
              LEFT JOIN crm_team t ON t.id = COALESCE(rp.team_id, cp.team_id)
              %s
             GROUP BY 1
        """, self._receipt_agg(dates, company), key_sql, booked_join))
        return {row[0] or 0: {'amount': row[1] or 0.0, 'count': row[2]}
                for row in self.env.cr.fetchall()}

    # ------------------------------------------------------------------ open money
    @api.model
    def _open_debits(self, company, as_of=None, partner_ids=None):
        """Every open rupee of receivable, FIFO-counted, one row per open debit.

        Per clinic: total receipts pay off the receivable debits oldest-first, and
        each debit's uncovered remainder is returned with its age in days and the
        clinic's current route. See the module docstring for why this — and not
        ``amount_residual`` — is the truth on this database.
        """
        as_of = as_of or fields.Date.context_today(self)
        # The line's own parent_state / company_id / account_id stand in for the
        # account_move and account_account joins the first version made: they are
        # stored copies of the same values, and dropping the joins turns 1,90,000
        # per-line index probes into one scan. `partner_ids` narrows the countback
        # to one row's clinics — per clinic the FIFO is self-contained, so a
        # restricted run returns exactly the full run's rows for those clinics.
        if partner_ids is not None and not partner_ids:
            return []
        partner_sql = SQL("AND l.partner_id IN %s", tuple(partner_ids)) \
            if partner_ids is not None else SQL("")
        receivable = self._receivable_ids(company)
        # Two passes over the index, not one over everything. `bal` settles each
        # clinic's account in a single grouped index-only scan and keeps ONLY the
        # clinics that still owe something — 2,719 of 4,901 here. The countback
        # window then runs over those clinics' debit lines alone (1,21,771 rows
        # instead of 1,88,462), which is where the time actually went: the window
        # aggregate is pure CPU per row, so the cheapest row is the one never fed
        # to it. Identical output, 38% faster. (2026-08-21)
        #
        # The line's own parent_state / company_id / account_id stand in for the
        # account_move and account_account joins the first version made: they are
        # stored copies of the same values, and dropping the joins turns 1,90,000
        # per-line index probes into one scan. Both halves come off
        # account_move_line_lab_countback_idx as index-only scans, and the
        # window's ORDER BY is satisfied by the index rather than an external sort.
        # Raw SQL sees only what is written: an invoice or a receipt posted earlier
        # in the same transaction (a cheque allocated right after an entry, the EOD
        # page after a posting) was missing from the countback.
        self.env['account.move.line'].flush_model()
        self.env['account.move'].flush_model()
        self.env.cr.execute(self._countback_sql(company, receivable, partner_sql, as_of))
        return [
            {'partner_id': r[0], 'team_key': r[1], 'move_id': r[2], 'date': r[3],
             'open': float(r[4]), 'days': r[5], 'billed': float(r[6])}
            for r in self.env.cr.fetchall() if r[4]
        ]

    def _countback_sql(self, company, receivable, partner_sql, as_of):
        """The FIFO countback, as one SQL statement.

        The single definition of what "open" means: `_open_debits` reads it for the
        cards, and `_populate_open_items` inserts from it for the list behind them.
        One statement, so the figure and the list it opens cannot drift apart.
        """
        return SQL("""
            WITH bal AS (
                SELECT l.partner_id, SUM(l.credit) AS paid
                  FROM account_move_line l
                 WHERE l.parent_state = 'posted'
                   AND l.company_id = %s
                   AND l.account_id IN %s
                   AND l.partner_id IS NOT NULL
                   %s
                 GROUP BY 1
                HAVING SUM(l.debit) > SUM(l.credit)
            ), d AS (
                SELECT l.partner_id, l.move_id, l.debit, l.date AS ldate,
                       l.name AS lname,
                       SUM(l.debit) OVER (PARTITION BY l.partner_id
                                          ORDER BY l.date, l.id) AS cum
                  FROM account_move_line l
                  JOIN bal b ON b.partner_id = l.partner_id
                 WHERE l.parent_state = 'posted'
                   AND l.company_id = %s
                   AND l.account_id IN %s
                   AND l.debit > 0
                   %s
            )
            SELECT d.partner_id,
                   COALESCE(rp.team_id, cp.team_id, 0) AS team_key,
                   d.move_id, d.ldate,
                   GREATEST(0, LEAST(d.debit, d.cum - b.paid)) AS open_amt,
                   (%s::date - d.ldate) AS days,
                   d.debit AS billed,
                   -- What the item IS. The line's own label, unless it merely
                   -- repeats the document number (every invoice line does), in
                   -- which case the order it came from says more. On the migrated
                   -- entries the label is the ORIGINAL Odoo 10 invoice number,
                   -- which is the only thing distinguishing thousands of rows all
                   -- called "Opening Balance". (client, 2026-08-22)
                   CASE WHEN COALESCE(d.lname, '') <> ''
                             AND d.lname IS DISTINCT FROM m.name
                        THEN d.lname
                        ELSE COALESCE(NULLIF(m.invoice_origin, ''),
                                      NULLIF(m.ref, ''), '')
                   END AS description,
                   -- Already joined for the description above, so the patient and
                   -- the products cost nothing extra to carry along: both are
                   -- sale_custom's own stored computes on account.move, built for
                   -- exactly this - "the statement reads it in the same query as
                   -- the rest of the line rather than walking into the invoice per
                   -- row." A journal entry (1,485 of these items) has no invoice
                   -- lines behind it, so both are simply NULL for those.
                   -- (client, 2026-08-29)
                   m.move_type, m.patient_names, m.product_names
              FROM d
              JOIN bal b ON b.partner_id = d.partner_id
              JOIN res_partner rp ON rp.id = d.partner_id
              LEFT JOIN res_partner cp ON cp.id = rp.commercial_partner_id
              -- joined AFTER the countback has narrowed to the open items, so it
              -- costs a few thousand probes rather than a few hundred thousand
              LEFT JOIN account_move m ON m.id = d.move_id
             WHERE d.cum > b.paid
        """, company.id, receivable, partner_sql,
             company.id, receivable, partner_sql, as_of)

    @api.model
    def _partner_user_map(self, company, partner_ids):
        """Clinic → salesperson, for laying open money at someone's door.

        The modal salesperson of the clinic's confirmed orders (all time — old debt
        belongs to whoever ran the account), then the route leader, then the
        clinic's own salesperson.
        """
        if not partner_ids:
            return {}
        self.env.cr.execute(SQL("""
            SELECT rp.id,
                   COALESCE(booked.user_id, t.user_id, rp.user_id, cp.user_id, 0)
              FROM res_partner rp
              LEFT JOIN res_partner cp ON cp.id = rp.commercial_partner_id
              LEFT JOIN crm_team t ON t.id = COALESCE(rp.team_id, cp.team_id)
              LEFT JOIN (
                    SELECT s.partner_id,
                           mode() WITHIN GROUP (ORDER BY s.user_id) AS user_id
                      FROM sale_order s
                     WHERE s.state IN ('sale', 'done') AND s.company_id = %s
                       AND s.user_id IS NOT NULL
                     GROUP BY s.partner_id
              ) booked ON booked.partner_id = rp.id
             WHERE rp.id IN %s
        """, company.id, tuple(partner_ids)))
        return dict(self.env.cr.fetchall())

    @api.model
    def _bucket(self, days):
        for key, _label, lo, hi in AGE_BUCKETS:
            if days >= lo and (hi is None or days <= hi):
                return key
        return 'd90'

    def _ageing_from(self, debits):
        """The bucket split of a set of open debits, with shares for the strip.

        Each bucket also carries how many open items it is made of: a band worth
        ten lakh is a different problem when it is one clinic than when it is
        two hundred, and the Money pulse shows that count beside the amount.
        """
        sums = {key: 0.0 for key, *_rest in AGE_BUCKETS}
        counts = {key: 0 for key, *_rest in AGE_BUCKETS}
        for debit in debits:
            bucket = self._bucket(debit['days'])
            sums[bucket] += debit['open']
            counts[bucket] += 1
        total = sum(sums.values())
        return {
            'total': round(total, 2),
            'buckets': [
                {'key': key, 'label': label, 'amount': round(sums[key], 2),
                 'count': counts[key],
                 'share': round(sums[key] / total * 100) if total else 0}
                for key, label, _lo, _hi in AGE_BUCKETS
            ],
        }

    # ------------------------------------------------------ the shared answer
    @api.model
    def outstanding_summary(self, company=None, user_id=False, top_debtors=0):
        """What is owed, counted back — for any screen that needs the figure.

        This exists because two boards disagreed with this one in front of the
        client (2026-08-31). The CEO hub and the Money pulse both read
        ``lab.outstanding.report.amount_residual`` and reported 10.31 M
        overdue where the ledger says 6.26 M, because that view sees only
        ``out_invoice``/``out_refund`` rows and this ledger's receipts are plain
        journal entries that are never reconciled — so a fully paid invoice
        still claims its whole face value, and anything past its date reads as
        overdue.

        So the countback is published here rather than copied there: one
        definition of "open", one of "overdue", and any dashboard that asks gets
        the same number as the Collections screen and as the ledger itself.
        """
        self._check_collections_access()
        # An RPC hands a company id, an internal caller a record; both are checked.
        company = self._company({'company_id': company.id if isinstance(
            company, models.BaseModel) else company})
        debits = self._open_debits(company)
        if user_id:
            # Laid at the clinic's salesperson's door - the attribution the
            # Collections person rows use - not invoice_user_id, which a bulk
            # recompute stamped with the route leader on 324 July invoices.
            users = self._partner_user_map(company, {d['partner_id'] for d in debits})
            debits = [d for d in debits if users.get(d['partner_id'], 0) == user_id]
        ageing = self._ageing_from(debits)
        summary = {
            'total': ageing['total'],
            'overdue': round(sum(d['open'] for d in debits
                                 if d['days'] > OVERDUE_DAYS), 2),
            'count': len(debits),
            'ageing': ageing,
            'overdue_days': OVERDUE_DAYS,
        }
        summary['overdue_pct'] = round(
            summary['overdue'] / summary['total'] * 100, 1) \
            if summary['total'] else 0.0
        if top_debtors:
            by_partner = {}
            for debit in debits:
                by_partner[debit['partner_id']] = \
                    by_partner.get(debit['partner_id'], 0.0) + debit['open']
            ranked = sorted(by_partner.items(), key=lambda kv: kv[1],
                            reverse=True)[:top_debtors]
            names = {p.id: p.display_name for p in self.env['res.partner']
                     .sudo().browse([pid for pid, _v in ranked])}
            summary['debtors'] = [
                {'id': pid, 'name': names.get(pid, ''), 'open': round(value, 2)}
                for pid, value in ranked]
        return summary

    @api.model
    def action_open_items_for_partner(self, partner_id):
        """One clinic's open items, straight from the countback.

        The Money pulse's debtor list used to open the residual report, which
        held different money than the figure clicked. This opens the rows the
        figure is actually made of, and applies the viewer's own route scope so
        an RPC cannot fetch a round they may not see. (2026-08-31)
        """
        self._check_collections_access()
        company = self.env.company
        partner = self.env['res.partner'].sudo().browse(int(partner_id))
        # One clinic the viewer may see, checked outright rather than by filtering
        # the rows to their route: a clinic visited off the route - covering a
        # colleague, carrying a box there - came back as an empty list from the
        # visit form. (client, 2026-09-17)
        if not self._partner_in_viewer_scope(partner):
            raise AccessError(_("That clinic is not on your round."))
        items = self._populate_open_items(company, [int(partner_id)])
        domain = [('id', 'in', items.ids)]
        list_view = self.env.ref(
            'lab_collections.view_collection_open_item_list',
            raise_if_not_found=False)
        return {
            'type': 'ir.actions.act_window',
            'name': _('Open receivable — %s', partner.display_name),
            'res_model': 'lab.collection.open.item',
            # Spelled out, not view_mode alone: a hand-built action handed
            # straight to doAction() skips the loader that builds `views`, and
            # the client crashes on the missing key — the same trap
            # action_drill's comment records. Caught live from the pulse's
            # quiet-debtors panel. (2026-09-02)
            'view_mode': 'list,form',
            'views': [(list_view.id if list_view else False, 'list'),
                      (False, 'form')],
            'domain': domain,
            'context': {'create': False},
        }

    @api.model
    def _partner_in_viewer_scope(self, partner):
        """May this viewer see this clinic's money?

        The accounts desk and management see every clinic. A field executive
        sees the clinics on their own routes, and any clinic they have visited.
        """
        scope = self._viewer_route_ids()
        if scope is None:
            return True
        partner = partner.sudo().exists()
        if not partner:
            return False
        team = partner.team_id or partner.commercial_partner_id.team_id
        if team.id in scope:
            return True
        return bool(self.env['lab.visit'].sudo().search_count([
            ('user_id', '=', self.env.uid),
            ('partner_id', 'child_of', partner.commercial_partner_id.id)], limit=1))

    @api.model
    def _is_accounting_user(self):
        """Someone who may read invoices and the ledger as themselves."""
        user = self.env.user
        return user.has_group('account.group_account_invoice') \
            or user.has_group('account.group_account_readonly')

    @api.model
    def _pdf_download(self, pdf, name):
        """A rendered PDF handed back as a download only the asker can open.

        The attachment belongs to no record, so the core rule lets only the
        person who created it read it.
        """
        attachment = self.env['ir.attachment'].sudo().create({
            'name': name, 'type': 'binary', 'mimetype': 'application/pdf',
            'datas': base64.b64encode(pdf),
        })
        return {
            'type': 'ir.actions.act_url',
            'url': '/web/content/%s?download=true' % attachment.id,
            'target': 'self',
        }

    @api.model
    def _file_download(self, content, name, mimetype):
        """Any rendered file handed back the same private way as _pdf_download."""
        attachment = self.env['ir.attachment'].sudo().create({
            'name': name, 'type': 'binary', 'mimetype': mimetype,
            'datas': base64.b64encode(content),
        })
        return {
            'type': 'ir.actions.act_url',
            'url': '/web/content/%s?download=true' % attachment.id,
            'target': 'self',
        }

    @api.model
    def action_statement_for_partner(self, partner_id):
        """The clinic's statement of account, for whoever may see its money.

        Someone with an accounting role gets the full statement dialog, as on the
        contact form. A field executive has none, and the statement reads ledger
        lines as the person asking - so for them it is rendered elevated, for this
        one clinic only, after the scope check, and handed back as a download
        only they can open. (client, 2026-09-17)
        """
        self._check_collections_access()
        partner = self.env['res.partner'].sudo().browse(int(partner_id)).exists()
        if not partner:
            raise UserError(_("That clinic no longer exists."))
        partner = partner.commercial_partner_id
        if not self._partner_in_viewer_scope(partner):
            raise AccessError(_("That clinic is not on your round."))
        if self._is_accounting_user():
            action = partner.with_env(self.env).action_open_statement_wizard()
            # The card hands this straight to doAction(), which skips the loader
            # that fills in `views` - without it the client crashed reading
            # `views.map` the moment the button was pressed. (2026-09-17)
            action.setdefault('views', [(False, 'form')])
            return action
        # Everybody gets the dialog now; for somebody without an accounting role
        # it is fixed to this clinic and rendered elevated. (client, 2026-09-18)
        return self.env['epg.partner.statement.wizard'].open_for_partner(partner)

    # ------------------------------------------ shared money reads (the pulse)
    # The Money pulse asks its received/route/quiet questions HERE so that
    # "received" keeps a single definition — the same posted receivable credits
    # `_receipt_agg` counts — instead of a second one growing in another module.
    @api.model
    def _received_series(self, company, date_from, date_to, granularity='month'):
        """{period_start_date: amount} of money received, bucketed by month/week.

        Weeks are Monday-anchored (PostgreSQL's date_trunc), matching every
        other week on these boards.
        """
        grain = 'week' if granularity == 'week' else 'month'
        self.env.cr.execute(SQL("""
            SELECT date_trunc(%s, l.date)::date AS bucket,
                   SUM(l.credit - l.debit)
              FROM account_move_line l
              JOIN account_move m ON m.id = l.move_id
             WHERE m.state = 'posted' AND m.company_id = %s
               AND m.move_type NOT IN ('out_invoice', 'out_refund')
               AND l.account_id IN %s
               AND l.partner_id IS NOT NULL
               AND l.date >= %s AND l.date <= %s
             GROUP BY 1
        """, grain, company.id, self._receivable_ids(company),
            date_from, date_to))
        return {row[0]: float(row[1] or 0.0) for row in self.env.cr.fetchall()}

    @api.model
    def _received_total(self, company, date_from, date_to):
        """(amount, receipt count) for a window — the received card's pair."""
        self.env.cr.execute(SQL("""
            SELECT COALESCE(SUM(l.credit - l.debit), 0),
                   COUNT(DISTINCT l.move_id)
              FROM account_move_line l
              JOIN account_move m ON m.id = l.move_id
             WHERE m.state = 'posted' AND m.company_id = %s
               AND m.move_type NOT IN ('out_invoice', 'out_refund')
               AND l.account_id IN %s
               AND l.partner_id IS NOT NULL
               AND l.date >= %s AND l.date <= %s
        """, company.id, self._receivable_ids(company), date_from, date_to))
        amount, count = self.env.cr.fetchone()
        return float(amount or 0.0), count

    @api.model
    def _receipt_mix(self, company, date_from, date_to):
        """How the period's money arrived, by journal type, biggest first.

        Bank vs cash is a real management question in this lab — cash rides in
        executives' pockets before it is banked — so the split earns a bar.
        """
        self.env.cr.execute(SQL("""
            SELECT j.type, SUM(l.credit - l.debit) AS amount
              FROM account_move_line l
              JOIN account_move m ON m.id = l.move_id
              JOIN account_journal j ON j.id = m.journal_id
             WHERE m.state = 'posted' AND m.company_id = %s
               AND m.move_type NOT IN ('out_invoice', 'out_refund')
               AND l.account_id IN %s
               AND l.partner_id IS NOT NULL
               AND l.date >= %s AND l.date <= %s
             GROUP BY 1 ORDER BY 2 DESC
        """, company.id, self._receivable_ids(company), date_from, date_to))
        labels = {'bank': _('Bank'), 'cash': _('Cash')}
        # Positive flows only, folded by label: this bar answers "how did the
        # money arrive", and a negative general-journal lump (April's opening
        # entries) is not an arrival channel — with it in, Bank read 104.7% and
        # an "Other" segment read -37.8%. The received CARD keeps the full net,
        # countback-consistent; the mix describes the receipts. (2026-09-02)
        folded = {}
        for jtype, amount in self.env.cr.fetchall():
            if not amount or amount <= 0:
                continue
            label = labels.get(jtype, _('Other'))
            key = jtype if jtype in labels else 'other'
            row = folded.setdefault(
                key, {'type': key, 'label': label, 'amount': 0.0})
            row['amount'] += float(amount)
        rows = sorted(folded.values(), key=lambda r: r['amount'], reverse=True)
        total = sum(r['amount'] for r in rows)
        for row in rows:
            row['amount'] = round(row['amount'], 2)
            row['share'] = round(row['amount'] / total * 100, 1) if total > 0 else 0.0
        return rows

    @api.model
    def _quiet_debtors(self, company, debits=None, silence_days=60, limit=8):
        """Clinics owing the most that have not paid anything in a long time.

        Owed-and-silent is a different list from owed-the-most: a big balance
        that pays every week is a customer, a big balance that went quiet is a
        risk. `last_pay` is the clinic's last posted receipt of any size.
        """
        debits = self._open_debits(company) if debits is None else debits
        owed = {}
        for debit in debits:
            owed[debit['partner_id']] = owed.get(debit['partner_id'], 0.0) \
                + debit['open']
        if not owed:
            return []
        self.env.cr.execute(SQL("""
            SELECT l.partner_id, MAX(l.date)
              FROM account_move_line l
              JOIN account_move m ON m.id = l.move_id
             WHERE m.state = 'posted' AND m.company_id = %s
               AND m.move_type NOT IN ('out_invoice', 'out_refund')
               AND l.account_id IN %s
               AND l.credit > 0
               AND l.partner_id IN %s
             GROUP BY 1
        """, company.id, self._receivable_ids(company), tuple(owed)))
        last_pay = dict(self.env.cr.fetchall())
        today = fields.Date.context_today(self)
        cutoff = today - relativedelta(days=silence_days)
        quiet = [(pid, value) for pid, value in owed.items()
                 if last_pay.get(pid) is None or last_pay[pid] <= cutoff]
        quiet.sort(key=lambda kv: kv[1], reverse=True)
        quiet = quiet[:limit]
        names = {p.id: p.display_name for p in
                 self.env['res.partner'].sudo().browse([p for p, _v in quiet])}
        return [{
            'id': pid,
            'name': names.get(pid, ''),
            'open': round(value, 2),
            'last_pay': fields.Date.to_string(last_pay[pid])
                        if last_pay.get(pid) else False,
            'days_silent': (today - last_pay[pid]).days
                           if last_pay.get(pid) else None,
        } for pid, value in quiet]

    @api.model
    def _money_by_route(self, company, date_from, date_to, debits=None):
        """One row per route: billed and received in the window, open and
        overdue as of today. The route meeting's whole agenda as one table.

        Billed rides the invoice's own frozen ``team_id``; received and open
        money ride the clinic's current route — the same attribution the
        Collections screen has always used, restated not redefined.
        """
        dates = {'sales_from': date_from, 'sales_to': date_to,
                 'pay_from': date_from, 'pay_to': date_to}
        billed = self._sales_by('team_id', dates, company)
        received = self._payments_by('team_id', dates, company)
        debits = self._open_debits(company) if debits is None else debits
        open_by, overdue_by = {}, {}
        for debit in debits:
            key = debit['team_key']
            open_by[key] = open_by.get(key, 0.0) + debit['open']
            if debit['days'] > OVERDUE_DAYS:
                overdue_by[key] = overdue_by.get(key, 0.0) + debit['open']
        keys = set(billed) | set(received) | set(open_by)
        teams = {t.id: t.name for t in
                 self.env['crm.team'].sudo().browse([k for k in keys if k])
                 .exists()}
        rows = []
        for key in keys:
            if key and key not in teams:      # a deleted team's orphan key
                continue
            total_open = round(open_by.get(key, 0.0), 2)
            overdue = round(overdue_by.get(key, 0.0), 2)
            rows.append({
                'id': key,
                'name': teams.get(key, _('No route')),
                'billed': round(billed.get(key, {}).get('amount', 0.0), 2),
                'received': round(received.get(key, {}).get('amount', 0.0), 2),
                'open': total_open,
                'overdue': overdue,
                'overdue_pct': round(overdue / total_open * 100)
                               if total_open > 0 else 0,
            })
        rows.sort(key=lambda r: r['open'], reverse=True)
        return rows

    # ------------------------------------------------------------------ trend
    @api.model
    def _trend_by_route(self, dates, company):
        """{team_key: [{label, percent}]} — collection %% per month, last 6 months.

        Received-in-month ÷ invoiced-in-month per route. Only three to four months
        of clean history exist on this database (it opens 2026-04, and April's
        migrated receipts land in a lump), so the bars are a shape, not a series —
        the display caps at 150%% for exactly that reason.
        """
        end = dates['pay_to'].replace(day=1)
        start = end - relativedelta(months=TREND_MONTHS - 1)
        months = [start + relativedelta(months=i) for i in range(TREND_MONTHS)]
        self.env.cr.execute(SQL("""
            SELECT COALESCE(m.team_id, 0),
                   date_trunc('month', m.invoice_date)::date,
                   SUM(CASE WHEN m.move_type = 'out_invoice'
                            THEN m.amount_total ELSE -m.amount_total END)
              FROM account_move m
             WHERE m.state = 'posted' AND m.company_id = %s
               AND m.move_type IN ('out_invoice', 'out_refund')
               AND m.invoice_date >= %s
             GROUP BY 1, 2
        """, company.id, start))
        invoiced = {(r[0], r[1]): float(r[2] or 0) for r in self.env.cr.fetchall()}
        # Same aggregate-first shape as _receipt_agg: months come off l.date (the
        # line's stored copy of the move date), partners attach to the per-clinic
        # monthly sums, never to the raw lines.
        self.env.cr.execute(SQL("""
            WITH mv AS (
                SELECT id FROM account_move
                 WHERE state = 'posted' AND company_id = %s
                   AND move_type NOT IN ('out_invoice', 'out_refund')
                   AND date >= %s
            ), agg AS (
                SELECT l.partner_id,
                       date_trunc('month', l.date)::date AS month,
                       SUM(l.credit - l.debit) AS amount
                  FROM account_move_line l
                  JOIN mv ON mv.id = l.move_id
                 WHERE l.account_id IN %s AND l.date >= %s
                   AND l.partner_id IS NOT NULL
                 GROUP BY 1, 2
            )
            SELECT COALESCE(rp.team_id, cp.team_id, 0), agg.month, SUM(agg.amount)
              FROM agg
              JOIN res_partner rp ON rp.id = agg.partner_id
              LEFT JOIN res_partner cp ON cp.id = rp.commercial_partner_id
             GROUP BY 1, 2
        """, company.id, start, self._receivable_ids(company), start))
        received = {(r[0], r[1]): float(r[2] or 0) for r in self.env.cr.fetchall()}
        teams = {t for t, _m in list(invoiced) + list(received)}
        trend = {}
        for team in teams:
            points = []
            for month in months:
                sold = invoiced.get((team, month), 0.0)
                got = received.get((team, month), 0.0)
                # A month can read NEGATIVE here - April 2026 does, because the
                # migrated opening balances were booked as receivable debits on
                # non-invoice moves. A negative collection month is not a thing
                # the sparkline should draw, so it floors at zero.
                points.append({
                    'label': month.strftime('%b %Y'),
                    'percent': max(round(got / sold * 100, 1), 0.0) if sold > 0 else 0.0,
                })
            trend[team] = points
        return trend

    # ------------------------------------------------------------------ rows
    @api.model
    def _targets(self, company):
        """{team_id: target %%} plus the company fallback under key 0.

        A route with no target of its own follows the company, so changing the
        company figure moves every route that has not been given its own.
        """
        default = company.lab_collection_target or 0.0
        teams = self.env['crm.team'].sudo().with_context(active_test=False).search(
            [('company_id', 'in', (False, company.id))])
        targets = {t.id: (t.lab_collection_target or default) for t in teams}
        targets[0] = default
        return targets

    @api.model
    def _sales_targets(self, group, company):
        """{key: monthly sales target} for routes or salespeople.

        Held per route on crm.team and per person on res.users: a salesperson works
        several routes, so the number that means anything to them is their own.
        """
        if group == 'team_id':
            teams = self.env['crm.team'].sudo().with_context(active_test=False).search(
                [('company_id', 'in', (False, company.id))])
            return {t.id: t.lab_sales_target or 0.0 for t in teams}
        users = self.env['res.users'].sudo().with_context(active_test=False).search(
            [('lab_sales_target', '>', 0)])
        return {u.id: u.lab_sales_target for u in users}

    @api.model
    def _excluded_users(self):
        """Logins that are not ranked: the accounts desk, OdooBot, duplicate logins.

        `active_test=False` deliberately, the same way `_labels` and `_sales_targets`
        read: an archived login still carries last month's figures, and a
        salesperson who has left must not walk back onto the board because the
        exclusion silently dropped out of the set. OdooBot is archived and on the
        board today, so this is a live case, not a hypothetical.
        """
        return set(self.env['res.users'].sudo().with_context(active_test=False)
                   .search([('lab_exclude_from_ranking', '=', True)]).ids)

    @api.model
    def _labels(self, group, keys):
        model = 'crm.team' if group == 'team_id' else 'res.users'
        recs = self.env[model].sudo().with_context(active_test=False).browse(
            [k for k in keys if k]).exists()
        labels = {rec.id: rec.display_name for rec in recs}
        labels[0] = 'Unassigned'
        return labels

    def _overdue_by(self, group, debits, company, users=None):
        """{key: {open, overdue}} laying the open debits at each row's door."""
        if group == 'team_id':
            key_of = lambda d: d['team_key']            # noqa: E731
        else:
            if users is None:
                users = self._partner_user_map(
                    company, {d['partner_id'] for d in debits})
            key_of = lambda d: users.get(d['partner_id'], 0)  # noqa: E731
        out = {}
        for debit in debits:
            entry = out.setdefault(key_of(debit), {'open': 0.0, 'overdue': 0.0})
            entry['open'] += debit['open']
            if debit['days'] > OVERDUE_DAYS:
                entry['overdue'] += debit['open']
        return out

    @api.model
    def report_rows(self, group='team_id', options=None, _debits=None, _trend=None,
                    _users=None):
        """One row per route (or salesperson), sorted by what is still uncollected."""
        self._check_collections_access()
        options = options or {}
        company = self._company(options)
        dates = self._dates(options)
        sales = self._sales_by(group, dates, company)
        pays = self._payments_by(group, dates, company)
        debits = self._open_debits(company) if _debits is None else _debits
        overdue = self._overdue_by(group, debits, company, users=_users)
        if _trend is not None:
            trend = _trend
        else:
            trend = self._trend_by_route(dates, company) if group == 'team_id' else {}
        labels = self._labels(group, set(sales) | set(pays) | set(overdue))
        targets = self._targets(company)
        # A salesperson works several routes, so no single route's target is theirs:
        # that column follows the company figure rather than inventing a number.
        default_target = targets[0]
        sales_targets = self._sales_targets(group, company)
        excluded = self._excluded_users() if group == 'user_id' else set()
        rows = []
        for key in set(sales) | set(pays) | set(overdue):
            sold = sales.get(key, {}).get('amount', 0.0)
            got = pays.get(key, {}).get('amount', 0.0)
            rows.append({
                'key': key,
                'label': labels.get(key, 'Unassigned'),
                'sales': round(sold, 2),
                'invoices': sales.get(key, {}).get('count', 0),
                'refunds': sales.get(key, {}).get('refunds', 0),
                'collected': round(got, 2),
                'receipts': pays.get(key, {}).get('count', 0),
                'pending': round(sold - got, 2),
                'percent': round(got / sold * 100, 1) if sold else 0.0,
                'open': round(overdue.get(key, {}).get('open', 0.0), 2),
                'overdue': round(overdue.get(key, {}).get('overdue', 0.0), 2),
                'trend': trend.get(key, []),
                'target': round(targets.get(key, default_target)
                                if group == 'team_id' else default_target, 1),
                'own_target': group == 'team_id',
                'sales_target': round(sales_targets.get(key, 0.0), 2),
                # Achievement against the book they were asked to write. Blank
                # (None) rather than 0 when no target is agreed, so the screen can
                # say "not set" instead of implying total failure.
                'sales_pct': (round(sold / sales_targets[key] * 100, 1)
                              if sales_targets.get(key) else None),
                # Marked, never dropped: the money is real and has to keep an owner
                # on the person view. Only the leaderboards act on this.
                'excluded': key in excluded,
            })
        rows.sort(key=lambda r: (-r['pending'], -r['sales'], r['label']))
        return rows

    @api.model
    def trend_data(self, options=None):
        """{team_key: [{label, percent}]} — the sparklines, on their own.

        Split out of ``dashboard_data`` because it is the single most expensive
        thing on the screen (263 ms of a 718 ms load) and the least urgent: the
        figures are readable without it. The client paints the table first and
        fills the sparkline column in when this returns.
        """
        self._check_collections_access()
        options = options or {}
        return self._trend_by_route(self._dates(options), self._company(options))

    @api.model
    def dashboard_data(self, options=None, with_trend=True, with_extras=False,
                       limit=5):
        """Everything the Collections screen shows, in one call.

        ``with_trend=False`` leaves the sparklines out so the first paint does not
        wait on them; the screen then asks for ``trend_data`` separately. The PDF
        renderer keeps the default, since it has nothing to paint early.

        ``with_extras=True`` adds the leaderboards and the per-person analysis. They
        are built from the salesperson rows and the countback THIS METHOD HAS
        ALREADY COMPUTED, so they cost almost nothing here - where fetching them
        separately made the screen run the 1,88,000-line countback and the whole
        salesperson pass a second time. (2026-08-21)
        """
        self._check_collections_access()
        options = options or {}
        dates = self._dates(options)
        company = self._company(options)
        as_of = fields.Date.context_today(self)
        debits = self._open_debits(company, as_of)
        trend = None if with_trend else {}
        # Resolved once and shared: report_rows() needs it for the salesperson
        # overdue column and performance() for the clinic counts.
        users = self._partner_user_map(company, {d['partner_id'] for d in debits})
        by_route = self.report_rows('team_id', options, _debits=debits, _trend=trend)
        by_user = self.report_rows('user_id', options, _debits=debits, _trend={},
                                   _users=users)
        # The boards are built from EVERY salesperson, whatever the viewer is
        # allowed to see below - kept before the scope narrows the rows.
        all_user_rows = by_user
        scope = self._viewer_route_ids()
        if scope is not None:
            by_route = [r for r in by_route if r['key'] in scope]
            by_user = [r for r in by_user if r['key'] == self.env.uid]
            # Narrowing the countback narrows open, overdue and the ageing strip
            # with it, because all three are derived from these rows further down.
            debits = [d for d in debits if d['team_key'] in scope]
        sales = round(sum(r['sales'] for r in by_route), 2)
        collected = round(sum(r['collected'] for r in by_route), 2)
        ageing = self._ageing_from(debits)
        # The company target is one number, but each route may carry its own, so the
        # figure the whole table should be judged against is the blend: every route's
        # target weighted by what that route actually invoiced. A route that billed
        # nothing this period cannot pull the blend around.
        targets = self._targets(company)
        weighted = sum(r['sales'] * r['target'] for r in by_route if r['sales'] > 0)
        weight = sum(r['sales'] for r in by_route if r['sales'] > 0)
        target = round(weighted / weight, 1) if weight else targets[0]
        data = {
            'currency': company.currency_id.symbol or '',
            # The toggle is HIDDEN, not offered and then failing, for a viewer who
            # cannot write: the Collections screen is open to read-only accountants.
            'can_rank': self.env.user.has_group('account.group_account_manager'),
            # Collect % and Sales target: the same room that agrees them
            # (set_user_setting's own gate, for the user-tab target) may move
            # them, and nobody else - the figures a salesperson is measured
            # AGAINST are not the salesperson's own to set. Without this the
            # cell's pencil icon and click-to-edit box were offered to every
            # viewer regardless of rights, and a sales executive who tried it
            # got a raw AccessError - crm.team write was already denied to
            # them, but the UI never said so until they tried.
            # (client, 2026-08-29)
            'can_edit_targets': self.env.user.has_group('account.group_account_manager'),
            'can_drill_documents': self._can_drill_documents(),
            # The screen says whose figures these are, so a smaller number than the
            # office quotes is never read as the report being wrong.
            'scope_label': (', '.join(self.env['crm.team'].sudo().browse(scope)
                                      .mapped('name')) or 'No sales route')
                           if scope is not None else '',
            'dates': {k: fields.Date.to_string(v) for k, v in dates.items()},
            'as_of': as_of.strftime('%d/%m/%Y'),
            'totals': {
                'sales': sales,
                'collected': collected,
                'pending': round(sales - collected, 2),
                'percent': round(collected / sales * 100, 1) if sales else 0.0,
                'target': target,
                'invoices': sum(r['invoices'] for r in by_route),
                'credit_notes': sum(r['refunds'] for r in by_route),
                'receipts': sum(r['receipts'] for r in by_route),
                'open': ageing['total'],
                'overdue': round(sum(
                    d['open'] for d in debits if d['days'] > OVERDUE_DAYS), 2),
            },
            'ageing': ageing,
            'by_route': by_route,
            'by_user': by_user,
        }
        if with_extras:
            # Boards from everyone (a ranking must include the room); the review
            # strip only from the rows this viewer is entitled to.
            data['boards'] = self.leaderboards(options, limit=limit,
                                               _rows=all_user_rows)
            data['people'] = self.performance(options, _rows=by_user,
                                              _debits=debits, _users=users)
        return data

    # ------------------------------------------------------------------ leaderboards
    @api.model
    def extras(self, options=None, limit=5):
        """Leaderboards and the per-person analysis, from ONE pass over the rows.

        Both are built from the salesperson rows and the same open-money countback;
        asked separately they each recomputed the lot (461 ms + 823 ms). Together
        they cost one of those. The screen fetches this after its first paint.
        """
        self._check_collections_access()
        options = options or {}
        company = self._company(options)
        # The countback is the most expensive figure on the screen, so it is computed
        # HERE and handed to both halves. report_rows() would otherwise run it once
        # for the overdue column and performance() a second time for the clinic
        # counts — the same 1,88,000-line scan twice per screen. Same for the
        # clinic→salesperson map, which both halves need. (2026-08-21)
        debits = self._open_debits(company)
        users = self._partner_user_map(company, {d['partner_id'] for d in debits})
        rows = self.report_rows('user_id', options, _debits=debits, _users=users)
        return {
            'boards': self.leaderboards(options, limit=limit, _rows=rows),
            'people': self.performance(options, _rows=rows, _debits=debits,
                                       _users=users),
        }

    @api.model
    def leaderboards(self, options=None, limit=5, _rows=None):
        """Who wrote the most business, and who brought the most money in.

        Deliberately two separate tables rather than one ranking: the person who
        books the most work is often not the person who collects best, and the lab
        rewards both. Ranked on the same figures the table shows, so a name at the
        top here can always be found in the row below.
        """
        self._check_collections_access()
        options = options or {}
        rows = self.report_rows('user_id', options) if _rows is None else _rows
        # "Unassigned" is not a person, and an excluded login is not a competitor.
        named = [r for r in rows if r['key'] and not r.get('excluded')]
        # Hoisted out of the comprehension below, where it re-sorted the whole row
        # set once per row. The floor is measured over RANKED rows only: a house
        # account must not get to define what a real book is.
        floor = self._material_floor(named)
        # A board entry has to be a figure someone can click into, so a zero never
        # takes a place: with no guard, a company whose only book is excluded shows
        # five people at 0.00 that each open an empty list.
        top_sales = sorted([r for r in named if r['sales'] > 0],
                           key=lambda r: -r['sales'])[:limit]
        top_coll = sorted([r for r in named if r['collected'] > 0],
                          key=lambda r: -r['collected'])[:limit]
        # Best collection RATE, but only for people carrying a real book: a person
        # with one small invoice fully paid would otherwise top the table for ever.
        material = [r for r in named if r['sales'] > 0 and r['sales'] >= floor]
        top_rate = sorted(material, key=lambda r: -r['percent'])[:limit]
        pick = lambda r, f: {'key': r['key'], 'label': r['label'], 'value': r[f],
                             'sales': r['sales'], 'collected': r['collected'],
                             'percent': r['percent']}                  # noqa: E731
        return {
            'top_sales': [pick(r, 'sales') for r in top_sales],
            'top_collectors': [pick(r, 'collected') for r in top_coll],
            'top_rate': [pick(r, 'percent') for r in top_rate],
            # Named, not merely counted: a board with a name missing has to say
            # whose. Only people who actually have a row this period - an excluded
            # login with no activity is not on screen to be looked for.
            'excluded': [{'key': r['key'], 'label': r['label']}
                         for r in sorted(rows, key=lambda r: -r['sales'])
                         if r['key'] and r.get('excluded')],
        }

    @api.model
    def _material_floor(self, rows):
        """The book size below which a collection rate is not worth ranking."""
        books = sorted((r['sales'] for r in rows if r['sales'] > 0), reverse=True)
        if not books:
            return 0.0
        return books[len(books) // 2] * 0.25       # a quarter of the median book

    @api.model
    def performance(self, options=None, _rows=None, _debits=None, _users=None):
        """One line per salesperson: book, money in, target, and what is stuck.

        Everything a review conversation needs in one row - what they were asked to
        write, what they wrote, how much of it came back, and how old the worst of
        what has not.
        """
        self._check_collections_access()
        options = options or {}
        company = self._company(options)
        rows = self.report_rows('user_id', options) if _rows is None else _rows
        debits = self._open_debits(company) if _debits is None else _debits
        users = self._partner_user_map(
            company, {d['partner_id'] for d in debits}) if _users is None else _users
        stats = {}
        for debit in debits:
            key = users.get(debit['partner_id'], 0)
            entry = stats.setdefault(key, {'clinics': set(), 'oldest': 0})
            entry['clinics'].add(debit['partner_id'])
            entry['oldest'] = max(entry['oldest'], debit['days'])
        out = []
        for row in rows:
            if not row['key']:
                continue
            extra = stats.get(row['key'], {'clinics': set(), 'oldest': 0})
            out.append(dict(
                row,
                clinics=len(extra['clinics']),
                oldest_days=extra['oldest'],
                # Against target, against the room: both readings matter in a review.
                rank_sales=0, rank_collected=0,
            ))
        # Ranks must agree with the boards above them, so they are numbered over the
        # same set. An excluded person keeps rank 0 and the strip prints a dash.
        ranked = [r for r in out if not r.get('excluded')]
        for field, rank_key in (('sales', 'rank_sales'), ('collected', 'rank_collected')):
            for position, row in enumerate(
                    sorted(ranked, key=lambda r: -r[field]), start=1):
                row[rank_key] = position
        out.sort(key=lambda r: -r['sales'])
        return out

    # ------------------------------------------------------------------ settings
    @api.model
    def set_user_setting(self, user_id, values):
        """Write one salesperson's dashboard settings from the board.

        NOT an `orm.write` from the client: `res.users` is writable only by
        base.group_erp_manager, while the room that runs the route meeting is the
        accounts desk - and every one of them is denied on res.users. This is the
        narrow grant: two named fields on one internal user, rather than write
        access to every user record.

        It does widen who can move a monthly sales target beyond the ERP managers,
        deliberately - that is the room that agrees them. It is also why the inline
        target edit worked only for an administrator until now.
        """
        if not self.env.user.has_group('account.group_account_manager'):
            raise AccessError(_("Only an Accounts Manager can change a "
                                "salesperson's leaderboard settings."))
        allowed = ('lab_exclude_from_ranking', 'lab_sales_target')
        vals = {k: v for k, v in (values or {}).items() if k in allowed}
        if not vals:
            return False
        user = self.env['res.users'].sudo().with_context(active_test=False).browse(
            int(user_id)).exists()
        if not user or user.share:
            raise AccessError(_("That is not a salesperson account."))
        user.write(vals)
        # res.users is not a mail.thread on this build, so there is no chatter to
        # track into: this log line is the audit trail behind the on-screen note.
        _logger.info("collections: %s set %s on user %s (%s)",
                     self.env.user.login, vals, user.id, user.login)
        return True

    @api.model
    def set_team_setting(self, team_id, values):
        """Write one route's Collect % / Sales target from the board.

        The other half of `set_user_setting`'s fix, and for the same reason: not a
        plain `orm.write` from the client. `crm.team`'s own ACL denies write to
        everyone except Sales/Administrator, which is not the accounts desk -
        the room that actually agrees these two numbers - so a manual crm.team
        write from an Accounts Manager was ALREADY denied before this method
        existed, quietly, the same way res.users was. This is the matching
        narrow grant: two named fields on one named route, gated on the same
        group as the person-level setting, not on being a Sales Administrator.
        (client, 2026-08-29)
        """
        if not self.env.user.has_group('account.group_account_manager'):
            raise AccessError(_("Only an Accounts Manager can change a "
                                "Sales Route's Collect % or Sales target."))
        allowed = ('lab_collection_target', 'lab_sales_target')
        vals = {k: v for k, v in (values or {}).items() if k in allowed}
        if not vals:
            return False
        team = self.env['crm.team'].sudo().browse(int(team_id)).exists()
        if not team:
            raise AccessError(_("That is not a Sales Route."))
        team.write(vals)
        _logger.info("collections: %s set %s on route %s (%s)",
                     self.env.user.login, vals, team.id, team.name)
        return True

    # ------------------------------------------------------------------ breakdown
    @api.model
    def row_detail(self, group='team_id', key=0, options=None):
        """One route's (or person's) open money: age split and the chase list."""
        self._check_scope(group, key)
        options = options or {}
        company = self._company(options)
        debits = self._row_debits(group, key or 0, company)
        by_partner = {}
        for debit in debits:
            entry = by_partner.setdefault(
                debit['partner_id'], {'due': 0.0, 'days': 0})
            entry['due'] += debit['open']
            entry['days'] = max(entry['days'], debit['days'])
        top = sorted(by_partner.items(), key=lambda kv: -kv[1]['due'])[:10]
        partners = {p.id: p for p in self.env['res.partner'].browse(
            [pid for pid, _v in top])}
        return {
            'ageing': self._ageing_from(debits),
            'debtors': [
                {'id': pid, 'name': partners[pid].display_name,
                 'phone': partners[pid].phone or '',
                 'due': round(vals['due'], 2), 'days': vals['days']}
                for pid, vals in top if pid in partners
            ],
        }

    def _row_debits(self, group, key, company):
        """The open debits belonging to one row of the table.

        Resolves the row to its clinics first and counts back only their ledgers —
        expanding one route used to recompute the whole company's countback and
        throw 95%% of it away (600 ms per click, measured 2026-08-21).
        """
        if group == 'team_id':
            self.env.cr.execute(SQL("""
                SELECT rp.id
                  FROM res_partner rp
                  LEFT JOIN res_partner cp ON cp.id = rp.commercial_partner_id
                 WHERE COALESCE(rp.team_id, cp.team_id, 0) = %s""", key))
            partner_ids = [r[0] for r in self.env.cr.fetchall()]
        else:
            self.env.cr.execute(SQL("""
                SELECT DISTINCT l.partner_id
                  FROM account_move_line l
                 WHERE l.parent_state = 'posted' AND l.company_id = %s
                   AND l.account_id IN %s AND l.partner_id IS NOT NULL""",
                company.id, self._receivable_ids(company)))
            candidates = [r[0] for r in self.env.cr.fetchall()]
            users = self._partner_user_map(company, candidates)
            partner_ids = [pid for pid in candidates if users.get(pid, 0) == key]
        return self._open_debits(company, partner_ids=partner_ids)

    # ------------------------------------------------------------------ drill-through
    @api.model
    def action_drill(self, kind, group='team_id', key=None, options=None):
        """The documents behind a figure, as a list the user can walk into.

        The figures come from SQL the ORM cannot express as a search domain (FIFO
        countback, modal salespeople), so each drill resolves to the underlying
        move ids and opens exactly those — the list always foots to the number
        that was clicked.
        """
        options = options or {}
        scope = self._check_scope(group, key)
        # Invoices and receipts ARE account.move / account.move.line. A field
        # executive has no accounting access by design, so offering them the
        # documents only produced Odoo's raw "not allowed to access Journal Entry"
        # dialog on top of the dashboard. The open-money drills stay: those open
        # lab.collection.open.item, which they own. (client, 2026-08-25)
        if kind in ('invoices', 'receipts') and not self._can_drill_documents():
            raise AccessError(_(
                "Your Collections screen shows the figures for your route, but the "
                "invoices and receipts behind them are accounts-office documents. "
                "Open receivable and Overdue still open, item by item."))
        company = self._company(options)
        dates = self._dates(options)
        if kind == 'receipts':
            # Journal items, NOT the entries: a receipt is booked as a plain journal
            # entry whose partner sits on the RECEIVABLE LINE, and the move itself
            # often has none - the entry list showed blank customers. The lines are
            # also what the figure is made of, so the column foots to the card.
            # (client, 2026-08-21)
            return self._receipt_line_action(group, key, dates, company, scope)
        if kind == 'invoices':
            ids = self._drill_invoice_ids(group, key, dates, company, scope)
            name = 'Invoices'
        else:
            # Open money opens the OPEN ITEMS, not the documents. A document list
            # cannot state this figure: the item's own value is what it was billed
            # at, while the card counts only the part the clinic's receipts have
            # not yet reached. lab.collection.open.item carries both, so the
            # Open column foots to the card exactly. (client, 2026-08-22)
            return self._open_item_action(kind, group, key, company, scope)
        # Invoices and receipts answer different questions, so each opens on its own
        # list: an invoice needs its date and the clinic that owes it, a receipt the
        # day the money landed and who paid. The stock list showed neither.
        # (client, 2026-08-21)
        list_view = self.env.ref('lab_collections.view_collection_invoice_list',
                                 raise_if_not_found=False)
        return {
            'type': 'ir.actions.act_window',
            'name': name,
            'res_model': 'account.move',
            # A hand-built action handed straight to doAction() never passes through
            # the server-side loader that turns view_mode into `views`, and the web
            # client crashes on the missing key ("Cannot read properties of
            # undefined (reading 'map')"). Spell the views out. (2026-08-21)
            'view_mode': 'list,form',
            'views': [(list_view.id if list_view else False, 'list'), (False, 'form')],
            'domain': [('id', 'in', ids)],
            'context': {'create': False},
        }

    def _populate_open_items(self, company, partner_ids=None):
        """Fill the open-item rows for this drill, straight from the countback.

        INSERT ... SELECT rather than ORM create(): the rows are a read-only
        snapshot of a query that has just run, and 9,455 of them through the ORM
        would cost seconds for nothing. They are transient - Odoo vacuums them.
        """
        Item = self.env['lab.collection.open.item'].sudo()
        receivable = self._receivable_ids(company)
        if partner_ids is not None and not partner_ids:
            return Item.browse()
        partner_sql = SQL("AND l.partner_id IN %s", tuple(partner_ids)) \
            if partner_ids is not None else SQL("")
        as_of = fields.Date.context_today(self)
        # The same stale-ledger trap _open_debits guards against: raw SQL sees only
        # what is written, so an invoice posted earlier in this transaction was
        # missing from the drill.
        self.env['account.move.line'].flush_model()
        self.env['account.move'].flush_model()
        self.env.cr.execute(SQL(
            """
            INSERT INTO lab_collection_open_item
                (create_uid, create_date, write_uid, write_date, company_id,
                 currency_id, partner_id, team_id, move_id, date, days,
                 open_amount, billed_amount, description,
                 move_type, patient, product_names)
            SELECT %s, NOW() AT TIME ZONE 'UTC', %s, NOW() AT TIME ZONE 'UTC', %s,
                   %s, c.partner_id, NULLIF(c.team_key, 0), c.move_id, c.ldate,
                   c.days, c.open_amt, c.billed, c.description,
                   c.move_type, c.patient_names, c.product_names
              FROM (%s) c
             WHERE c.open_amt > 0
         RETURNING id
            """,
            self.env.uid, self.env.uid, company.id,
            company.currency_id.id,
            self._countback_sql(company, receivable, partner_sql, as_of)))
        return Item.browse([r[0] for r in self.env.cr.fetchall()])

    def _open_item_action(self, kind, group, key, company, scope=None):
        """The open items behind the Open receivable / Overdue / ageing figures."""
        partner_ids = None
        if key is not None and group == 'user_id':
            # A salesperson owns open money through the clinics credited to them,
            # which is a modal-order calculation the ORM cannot express.
            users = self._partner_user_map(
                company, self._receivable_partner_ids(company))
            partner_ids = [p for p, u in users.items() if u == key]
        items = self._populate_open_items(company, partner_ids)
        domain = [('id', 'in', items.ids)]
        labels = dict((b[0], b[1]) for b in AGE_BUCKETS)
        if kind == 'overdue':
            domain.append(('days', '>', OVERDUE_DAYS))
            name = 'Overdue — open for more than %s days' % OVERDUE_DAYS
        elif kind in labels:
            _key, _label, low, high = next(b for b in AGE_BUCKETS if b[0] == kind)
            domain.append(('days', '>=', low))
            if high is not None:
                domain.append(('days', '<=', high))
            name = 'Open receivable — %s' % labels[kind]
        else:
            name = 'Open receivable'
        if key is not None and group == 'team_id':
            # key 0 is the "Unassigned" row: clinics on no route at all.
            domain.append(('team_id', '=', key or False))
        elif scope is not None:
            # A card, not a row: a restricted viewer still gets only their round.
            domain.append(('team_id', 'in', scope))
        # An ungrouped Odoo list totals the PAGE, not the search - 80 rows of 9,455 -
        # so the figure that was clicked is put in the breadcrumb, where it sits
        # beside the detail instead of contradicting it. Group by Sales Route or
        # Customer from the search panel and Odoo totals the whole set per group.
        listed = self.env['lab.collection.open.item'].search(domain)
        name = '%s — %s (%s items)' % (
            name,
            formatLang(self.env, sum(listed.mapped('open_amount')),
                       currency_obj=company.currency_id),
            len(listed))
        return {
            'type': 'ir.actions.act_window',
            'name': name,
            'res_model': 'lab.collection.open.item',
            'view_mode': 'list',
            'views': [(self.env.ref(
                'lab_collections.view_collection_open_item_list').id, 'list')],
            # No `search_view_id`: a hand-built action must pass it as [id, name]
            # and a bare [id] stops the client opening the action at all. Odoo
            # finds the model's own search view without being told. (2026-08-22)
            'domain': domain,
            # Grouped by customer from the moment it opens. Ungrouped, an Odoo list
            # totals the PAGE rather than the search, so 80 rows of 9,455 disagreed
            # with the card that was clicked; grouped, every clinic carries its own
            # true total and the list foots to the figure in the breadcrumb.
            # (client, 2026-08-27)
            'context': {'create': False, 'search_default_group_partner': 1},
        }

    def _receivable_partner_ids(self, company):
        """Every clinic with a posted receivable line, for attribution lookups."""
        self.env.cr.execute(SQL("""
            SELECT DISTINCT l.partner_id
              FROM account_move_line l
             WHERE l.parent_state = 'posted' AND l.company_id = %s
               AND l.account_id IN %s AND l.partner_id IS NOT NULL
        """, company.id, self._receivable_ids(company)))
        return [r[0] for r in self.env.cr.fetchall()]

    def _receipt_line_action(self, group, key, dates, company, scope=None):
        """The receivable credits that make up the Received figure."""
        key_sql, booked_join = self._receipt_key(group, dates, company)
        group_sql = SQL("TRUE") if key is None else SQL("%s = %s", key_sql, key)
        if key is None and scope is not None:
            group_sql = SQL("COALESCE(rp.team_id, cp.team_id, 0) IN %s",
                            tuple(scope) or (0,))
        self.env.cr.execute(SQL("""
            SELECT l.id
              FROM account_move_line l
              JOIN account_move m ON m.id = l.move_id
              JOIN res_partner rp ON rp.id = l.partner_id
              LEFT JOIN res_partner cp ON cp.id = rp.commercial_partner_id
              LEFT JOIN crm_team t ON t.id = COALESCE(rp.team_id, cp.team_id)
              %s
             WHERE m.state = 'posted'
               AND m.company_id = %s
               AND m.move_type NOT IN ('out_invoice', 'out_refund')
               AND l.account_id IN %s
               AND l.date >= %s AND l.date <= %s
               AND l.partner_id IS NOT NULL
               AND %s
        """, booked_join, company.id, self._receivable_ids(company),
             dates['pay_from'], dates['pay_to'], group_sql))
        ids = [r[0] for r in self.env.cr.fetchall()]
        view = self.env.ref('lab_collections.view_collection_receipt_list',
                            raise_if_not_found=False)
        return {
            'type': 'ir.actions.act_window',
            'name': 'Receipts',
            'res_model': 'account.move.line',
            'view_mode': 'list,form',
            'views': [(view.id if view else False, 'list'), (False, 'form')],
            'domain': [('id', 'in', ids)],
            'context': {'create': False},
        }

    def _drill_invoice_ids(self, group, key, dates, company, scope=None):
        if key is None and scope is not None:
            group_sql = SQL("COALESCE(m.team_id, 0) IN %s", tuple(scope) or (0,))
        elif key is None:
            group_sql = SQL("TRUE")
        elif group == 'team_id':
            group_sql = SQL("COALESCE(m.team_id, 0) = %s", key)
        else:
            group_sql = SQL("COALESCE(ou.user_id, m.invoice_user_id, 0) = %s", key)
        join_sql = self._order_user_join(dates, company) \
            if group == 'user_id' and key is not None else SQL("")
        self.env.cr.execute(SQL(
            "SELECT m.id FROM account_move m %s WHERE %s AND %s",
            join_sql, self._invoice_where(dates, company), group_sql))
        return [r[0] for r in self.env.cr.fetchall()]

    def _drill_receipt_ids(self, group, key, dates, company):
        key_sql, booked_join = self._receipt_key(group, dates, company)
        group_sql = SQL("TRUE") if key is None else SQL("%s = %s", key_sql, key)
        self.env.cr.execute(SQL("""
            %s
            SELECT DISTINCT agg.move_id
              FROM agg
              JOIN res_partner rp ON rp.id = agg.partner_id
              LEFT JOIN res_partner cp ON cp.id = rp.commercial_partner_id
              LEFT JOIN crm_team t ON t.id = COALESCE(rp.team_id, cp.team_id)
              %s
             WHERE %s
        """, self._receipt_agg(dates, company), booked_join, group_sql))
        return [r[0] for r in self.env.cr.fetchall()]
