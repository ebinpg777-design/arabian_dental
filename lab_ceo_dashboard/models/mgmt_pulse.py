# -*- coding: utf-8 -*-
"""The Management menu's three analysis screens, as one widget.

Sales & Cases, Field Force and Money used to open straight into graph/pivot
views. The lab asked for widgets instead ("not pivot views"): the people this
menu serves read a screen, they do not slice one. So each entry now lands on a
page of answers — trends, ranked lists, this-month-against-last — and the pivot
that used to be the landing is one click away behind "Full analysis", for the
reader whose next question the page did not answer. (client, 2026-08-27)
"""
from datetime import date, datetime, time, timedelta

import pytz
from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError
from odoo.tools import format_date

from odoo.addons.lab_collections.models.collection_performance import (
    OVERDUE_DAYS,
)

TOP_ROWS = 6

# The ageing bands are no longer declared here: they come from the Collections
# countback (lab.collection.performance.AGE_BUCKETS), which is the one place
# that decides what "open" and "overdue" mean on this ledger. (2026-08-31)


class LabMgmtPulse(models.AbstractModel):
    _name = 'lab.mgmt.pulse'
    _description = 'Management Business Pulse'

    # ------------------------------------------------------------------ frame
    @api.model
    def _month_starts(self, months):
        first = fields.Date.context_today(self).replace(day=1)
        return [first - relativedelta(months=i) for i in range(months - 1, -1, -1)]

    @api.model
    def _lab_tz(self):
        """Context tz, then the user's, then the company's, then Kolkata: 129 of
        156 users have no tz, so the user's alone fixes nothing."""
        name = (self.env.context.get('tz') or self.env.user.tz
                or self.env.company.partner_id.tz or 'Asia/Kolkata')
        try:
            return pytz.timezone(name)
        except pytz.UnknownTimeZoneError:
            return pytz.timezone('Asia/Kolkata')

    @api.model
    def _utc_start(self, day):
        """Local midnight of `day` as the naive UTC datetime `date_order` is stored in.

        `Datetime.to_datetime(day)` is midnight UTC - 05:30 here - so every month's
        count started five and a half hours late and kept the previous month's
        last evening.
        """
        return self._lab_tz().localize(datetime.combine(day, time.min)).astimezone(
            pytz.utc).replace(tzinfo=None)

    @api.model
    def _check_pulse_access(self):
        """The menus are group-gated but an RPC is not: everything below reads
        with sudo(), so the gate has to be here too."""
        if not (self.env.su or self.env.user.has_group(
                'lab_ceo_dashboard.group_lab_executive')):
            raise AccessError(_("Only management can read the business pulse."))

    @api.model
    def get_pulse(self, months=12, user_id=False, period='month'):
        """Everything on the page, in one call.

        One call rather than one per tab: the three sections are a handful of
        grouped reads each, and switching tabs then costs nothing — which is the
        difference between a comparison that gets made and one that does not.

        `user_id` narrows every tab to one salesperson. The same page, read for one
        person instead of the lab: a manager comparing somebody against their own
        last month was otherwise doing it in their head from a pivot.
        (client, 2026-08-28)

        `period` moves the Money tab's flow window (billed / received / routes /
        how the money arrived); the open receivable is a stock, always as of
        today, whatever the period says. (client, 2026-09-02)
        """
        self._check_pulse_access()
        user_id = int(user_id) if user_id else False
        return {
            'currency_id': self.env.company.currency_id.id,
            'user_id': user_id,
            'users': self._pulse_users(),
            'sales': self._sales(months, user_id),
            'field': self._field(user_id=user_id),
            'money': self._money(months, user_id, period),
        }

    @api.model
    def _pulse_users(self):
        """Salespeople who actually have work, most first.

        Not every login: this lab has 111 of them and a dropdown of 111 names is a
        dropdown nobody uses. Only those carrying orders in the window the page
        shows.
        """
        horizon = self._month_starts(12)[0]
        rows = self.env['sale.order'].sudo()._read_group(
            [('state', '=', 'sale'), ('user_id', '!=', False),
             ('date_order', '>=', self._utc_start(horizon))],
            ['user_id'], ['__count'], order='__count desc')
        return [{'id': user.id, 'name': user.name, 'orders': count}
                for user, count in rows]

    # ------------------------------------------------------------------ sales
    @api.model
    def _sales(self, months, user_id=False):
        starts = self._month_starts(months)
        horizon = starts[0]
        # The month buckets follow the same clock as the window edges.
        Order = self.env['sale.order'].sudo().with_context(tz=self._lab_tz().zone)
        # 'done' is not a sale.order state in Odoo 19 (draft/sent/sale/cancel); the
        # leaf is kept harmless but the live one is 'sale'.
        base = [('state', '=', 'sale'),
                ('date_order', '>=', self._utc_start(horizon))]
        if user_id:
            base.append(('user_id', '=', user_id))

        by_month = {}
        for when, count, value in Order._read_group(
                base, ['date_order:month'], ['__count', 'amount_total:sum']):
            by_month[fields.Date.to_date(when).replace(day=1)] = (count, value or 0.0)

        trend = [{
            # The month itself, so a bar can open exactly the orders it counts.
            'month': fields.Date.to_string(m),
            'label': format_date(self.env, m, date_format='MMM'),
            'orders': by_month.get(m, (0, 0.0))[0],
            'value': by_month.get(m, (0, 0.0))[1],
        } for m in starts]

        this_start = starts[-1]
        month_domain = base + [
            ('date_order', '>=', self._utc_start(this_start))]

        def ranked(groupby, label):
            rows = []
            for rec, count, value in Order._read_group(
                    month_domain + [(groupby, '!=', False)], [groupby],
                    ['__count', 'amount_total:sum'],
                    order='amount_total:sum desc', limit=TOP_ROWS):
                rows.append({'id': rec.id, 'name': rec.display_name,
                             'orders': count, 'value': value or 0.0})
            return rows

        # Appliances rank on the line, not the order: an order mixes products.
        appliances = []
        line_domain = [('order_id.state', '=', 'sale'),
                       ('order_id.date_order', '>=',
                        self._utc_start(this_start)),
                       ('display_type', '=', False), ('product_id', '!=', False)]
        if user_id:
            line_domain.append(('order_id.user_id', '=', user_id))
        for product, qty, value in self.env['sale.order.line'].sudo()._read_group(
                line_domain,
                ['product_id'], ['product_uom_qty:sum', 'price_total:sum'],
                order='price_total:sum desc', limit=TOP_ROWS):
            appliances.append({'id': product.id, 'name': product.display_name,
                               'qty': qty or 0.0, 'value': value or 0.0})

        this = trend[-1]
        # Like-for-like: 27 days of August against 27 days of July, not against
        # all of it — a raw full-month comparison reads as a collapse every
        # single mid-month.
        prev_start = this_start - relativedelta(months=1)
        elapsed = fields.Date.context_today(self) - this_start
        prev_domain = [
            ('state', '=', 'sale'),
            ('date_order', '>=', self._utc_start(prev_start)),
            ('date_order', '<', self._utc_start(
                prev_start + elapsed + relativedelta(days=1)))]
        if user_id:
            prev_domain.append(('user_id', '=', user_id))
        [[prev_orders, prev_value]] = Order._read_group(
            prev_domain, [], ['__count', 'amount_total:sum'])
        prev_value = prev_value or 0.0
        return {
            'trend': trend,
            'this_month': this,
            'prev_month': {'orders': prev_orders, 'value': prev_value},
            # The month the ranked panels are about, so their drills open the same
            # slice the heading claims. They used to open all time.
            'month_from': fields.Date.to_string(this_start),
            'routes': ranked('team_id', 'route'),
            'clinics': ranked('partner_id', 'clinic'),
            'appliances': appliances,
        }

    # ------------------------------------------------------------------ field
    @api.model
    def _field(self, weeks=8, user_id=False):
        today = fields.Date.context_today(self)
        # Odoo's :week groups anchor on Sunday; this lab's week starts Monday,
        # so bucket by day and fold into Mondays ourselves.
        monday = today - timedelta(days=today.weekday())
        start = monday - timedelta(weeks=weeks - 1)
        Visit = self.env['lab.visit'].sudo()

        visit_base = [('date', '>=', start), ('state', '=', 'done')]
        if user_id:
            visit_base.append(('user_id', '=', user_id))
        weekly = {}
        for when, count, collected in Visit._read_group(
                visit_base,
                ['date:day'], ['__count', 'collected:sum']):
            day = fields.Date.to_date(when)
            key = day - timedelta(days=day.weekday())
            visits, money = weekly.get(key, (0, 0.0))
            weekly[key] = (visits + count, money + (collected or 0.0))

        week_rows = [{
            'label': format_date(self.env, start + timedelta(weeks=i),
                                 date_format='d MMM'),
            'visits': weekly.get(start + timedelta(weeks=i), (0, 0.0))[0],
            'collected': weekly.get(start + timedelta(weeks=i), (0, 0.0))[1],
        } for i in range(weeks)]

        month_start = today.replace(day=1)
        people = []
        people_domain = [('date', '>=', month_start), ('state', '=', 'done')]
        if user_id:
            people_domain.append(('user_id', '=', user_id))
        for user, count, value, collected in Visit._read_group(
                people_domain,
                ['user_id'], ['__count', 'order_value:sum', 'collected:sum'],
                order='__count desc'):
            people.append({'id': user.id, 'name': user.name, 'visits': count,
                           'value': value or 0.0, 'collected': collected or 0.0})

        return {
            'weeks': week_rows,
            'people': people,
            'new_clinics': self.env['lab.new.clinics'].count_new_clinics(),
            'has_data': bool(weekly or people),
        }

    # ------------------------------------------------------------------ money
    # The flow windows the Money tab can read. April is both the Indian FY
    # start and the day this ledger opens, so "FY so far" is also "everything".
    MONEY_PERIODS = [
        ('month', 'This month'),
        ('last_month', 'Last month'),
        ('quarter', 'Last 3 months'),
        ('fy', 'FY so far'),
    ]

    @api.model
    def _money_window(self, period):
        """(from, to, prev_from, prev_to, delta_label) for one period key.

        Comparisons are like-for-like: a month in progress compares against the
        same elapsed days of the last one, a finished month against the finished
        month before it. FY-so-far has nothing to compare against — the ledger
        opens with this FY — so its delta is None, not a fake zero.
        """
        today = fields.Date.context_today(self)
        month_start = today.replace(day=1)
        if period == 'last_month':
            start = month_start - relativedelta(months=1)
            return (start, month_start - relativedelta(days=1),
                    start - relativedelta(months=1),
                    start - relativedelta(days=1),
                    _('vs the month before'))
        if period == 'quarter':
            start = month_start - relativedelta(months=2)
            return (start, today,
                    start - relativedelta(months=3),
                    start - relativedelta(days=1),
                    _('vs the previous 3 months'))
        if period == 'fy':
            fy_year = today.year if today.month >= 4 else today.year - 1
            return date(fy_year, 4, 1), today, None, None, None
        # this month, against the same elapsed days of the last one
        elapsed = today - month_start
        prev_start = month_start - relativedelta(months=1)
        return (month_start, today, prev_start, prev_start + elapsed,
                _('vs last month, same days'))

    @api.model
    def _billed_window(self, date_from, date_to, user_id=False):
        """{amount, invoices, refunds, refund_amount} of posted customer paper."""
        domain = [('move_type', 'in', ('out_invoice', 'out_refund')),
                  ('state', '=', 'posted'),
                  ('invoice_date', '>=', date_from),
                  ('invoice_date', '<=', date_to)]
        if user_id:
            # The ORDER's salesperson, through the Collections join - the same
            # person received money follows. invoice_user_id is the field a bulk
            # recompute stamped with the route leader on 324 July invoices, so
            # billed and received read two different people.
            domain.append(('id', 'in', self._invoice_ids_of(user_id, date_from, date_to)))
        out = {'amount': 0.0, 'invoices': 0, 'refunds': 0, 'refund_amount': 0.0}
        for move_type, count, value in self.env['account.move'].sudo()._read_group(
                domain, ['move_type'], ['__count', 'amount_total_signed:sum']):
            out['amount'] += value or 0.0
            if move_type == 'out_refund':
                out['refunds'] = count
                out['refund_amount'] = value or 0.0
            else:
                out['invoices'] = count
        out['amount'] = round(out['amount'], 2)
        return out

    @api.model
    def _invoice_ids_of(self, user_id, date_from, date_to):
        """Posted customer paper in the window credited to one salesperson."""
        # Raw SQL underneath: flush what this transaction has posted or linked.
        for model in ('account.move', 'account.move.line', 'sale.order', 'sale.order.line'):
            self.env[model].flush_model()
        return self.env['lab.collection.performance'].sudo()._drill_invoice_ids(
            'user_id', user_id, {'sales_from': date_from, 'sales_to': date_to},
            self.env.company)

    @api.model
    def _money(self, months, user_id=False, period='month'):
        """The Money tab: flows for the chosen window, the stock as of today.

        Every receivable figure is the Collections countback — one definition
        of "open" for the whole system (see outstanding_summary's docstring for
        the day two boards disagreed in front of the client). The countback runs
        ONCE here and every panel below is arithmetic on its rows.
        """
        company = self.env.company
        Perf = self.env['lab.collection.performance']
        today = fields.Date.context_today(self)
        date_from, date_to, prev_from, prev_to, delta_label = \
            self._money_window(period)

        # ------------------------------------------------ flows, this window
        billed = self._billed_window(date_from, date_to, user_id)
        if user_id:
            # A person's receipts follow the clinics credited to them — the
            # same modal-salesperson read the Collections screen uses.
            dates = {'sales_from': date_from, 'sales_to': date_to,
                     'pay_from': date_from, 'pay_to': date_to}
            row = Perf.sudo()._payments_by('user_id', dates, company) \
                .get(user_id, {})
            received = {'amount': round(row.get('amount', 0.0), 2),
                        'count': row.get('count', 0)}
        else:
            amount, count = Perf.sudo()._received_total(
                company, date_from, date_to)
            received = {'amount': round(amount, 2), 'count': count}
        prev = {'billed': None, 'received': None}
        if prev_from:
            prev['billed'] = self._billed_window(
                prev_from, prev_to, user_id)['amount']
            if user_id:
                dates = {'sales_from': prev_from, 'sales_to': prev_to,
                         'pay_from': prev_from, 'pay_to': prev_to}
                prev['received'] = round(Perf.sudo()._payments_by(
                    'user_id', dates, company).get(user_id, {})
                    .get('amount', 0.0), 2)
            else:
                prev['received'] = round(Perf.sudo()._received_total(
                    company, prev_from, prev_to)[0], 2)
        ratio = round(received['amount'] / billed['amount'] * 100, 1) \
            if billed['amount'] > 0 else None

        # ------------------------------------- the stock: one countback, once
        debits_all, debits = self._scoped_debits(Perf, company, user_id)
        ar = self._ar_from_debits(Perf, debits)
        debtors = self._debtors_from(debits, ar['total'])

        # ------------------------------------------------- monthly two-series
        # The chart starts where the ledger does (2026-04): six bars of
        # pre-migration nothing taught nobody anything. April's receipts are
        # opening entries landing in a lump (they net NEGATIVE), so that month
        # is flagged and its received bar is drawn as a footnote, not a value.
        starts = [m for m in self._month_starts(months)]
        first_posted = self.env['account.move'].sudo()._read_group(
            [('move_type', 'in', ('out_invoice', 'out_refund')),
             ('state', '=', 'posted')], [], ['invoice_date:min'])[0][0]
        if first_posted:
            opening = fields.Date.to_date(first_posted).replace(day=1)
            starts = [m for m in starts if m >= opening] or starts[-1:]
        base = [('move_type', 'in', ('out_invoice', 'out_refund')),
                ('state', '=', 'posted'), ('invoice_date', '>=', starts[0])]
        if user_id:
            base.append(('id', 'in', self._invoice_ids_of(user_id, starts[0], today)))
        by_month = {}
        for when, value in self.env['account.move'].sudo()._read_group(
                base, ['invoice_date:month'],
                # Signed, so a credit note pulls the month down instead of
                # inflating it the way a plain total would.
                ['amount_total_signed:sum']):
            by_month[fields.Date.to_date(when).replace(day=1)] = value or 0.0
        received_by_month = Perf.sudo()._received_series(
            company, starts[0], today) if not user_id else {}
        trend = [{
            'month': fields.Date.to_string(m),
            'label': format_date(self.env, m, date_format='MMM'),
            'billed': round(by_month.get(m, 0.0), 2),
            'received': round(received_by_month.get(m, 0.0), 2),
            'opening': bool(first_posted) and m == starts[0],
        } for m in starts]

        # ------------------------------------------------ weekly cash rhythm
        monday = today - timedelta(days=today.weekday())
        week0 = monday - timedelta(weeks=7)
        weekly = Perf.sudo()._received_series(
            company, week0, today, granularity='week')
        weekly_cash = [{
            'label': format_date(self.env, week0 + timedelta(weeks=i),
                                 date_format='d MMM'),
            'amount': round(weekly.get(week0 + timedelta(weeks=i), 0.0), 2),
        } for i in range(8)]

        return {
            'period': {
                'key': period,
                'from': fields.Date.to_string(date_from),
                'to': fields.Date.to_string(date_to),
                'delta_label': delta_label,
            },
            'periods': [[key, _(label)] for key, label in self.MONEY_PERIODS],
            'billed': billed,
            'received': received,
            'prev': prev,
            'ratio': ratio,
            'trend': trend,
            'first_month_label': format_date(
                self.env, starts[0], date_format='MMM yyyy'),
            'ar': ar,
            'debtors': debtors,
            # The panels below read the whole lab even when a salesperson is
            # picked — a route table sliced to one person answers nothing —
            # and the UI says so rather than letting the filter silently lie.
            'routes': Perf.sudo()._money_by_route(
                company, date_from, date_to, debits=debits_all),
            'mix': Perf.sudo()._receipt_mix(company, date_from, date_to),
            'quiet': Perf.sudo()._quiet_debtors(company, debits=debits_all),
            'weekly_cash': weekly_cash,
            'scoped': bool(user_id),
        }

    @api.model
    def _scoped_debits(self, Perf, company, user_id=False):
        """(every open debit, the ones scoped to one salesperson) — the
        countback runs once and both views come off the same rows."""
        debits_all = Perf.sudo()._open_debits(company)
        if not user_id:
            return debits_all, debits_all
        # The clinic's salesperson, as the Collections person rows lay open money,
        # not invoice_user_id (see _billed_window).
        users = Perf.sudo()._partner_user_map(
            company, {d['partner_id'] for d in debits_all})
        return debits_all, [d for d in debits_all
                            if users.get(d['partner_id'], 0) == user_id]

    @api.model
    def _ageing(self, user_id=False):
        """The stock alone, as of today — kept as its own method: the
        boards-agreement suite reads it to prove every screen answers with the
        countback's numbers."""
        Perf = self.env['lab.collection.performance']
        _all, debits = self._scoped_debits(Perf, self.env.company, user_id)
        return self._ar_from_debits(Perf, debits)

    @api.model
    def _ar_from_debits(self, Perf, debits):
        """Ageing bands plus the three judgment numbers a band cannot say:
        how old the average open rupee is, how old the oldest is, and how
        concentrated the debt is in its top five clinics."""
        ageing = Perf._ageing_from(debits)
        total = ageing['total']
        bands = [{
            'key': bucket['key'], 'label': bucket['label'],
            'amount': bucket['amount'], 'count': bucket['count'],
            'pct': round(bucket['amount'] / total * 100, 1) if total else 0.0,
        } for bucket in ageing['buckets']]
        stale = round(sum(d['open'] for d in debits
                          if d['days'] > OVERDUE_DAYS), 2)
        by_partner = {}
        for debit in debits:
            by_partner[debit['partner_id']] = \
                by_partner.get(debit['partner_id'], 0.0) + debit['open']
        top5 = sum(sorted(by_partner.values(), reverse=True)[:5])
        return {
            'total': total,
            'bands': bands,
            'stale': stale,
            'stale_pct': round(stale / total * 100, 1) if total else 0.0,
            'count': len(debits),
            'avg_age': round(sum(d['open'] * d['days'] for d in debits)
                             / total) if total else 0,
            'oldest_days': max((d['days'] for d in debits), default=0),
            'top5_pct': round(top5 / total * 100, 1) if total else 0.0,
            'clinics': len(by_partner),
        }

    @api.model
    def _debtors_from(self, debits, total):
        """Top debtors off the rows already fetched — no second countback."""
        by_partner = {}
        for debit in debits:
            by_partner[debit['partner_id']] = \
                by_partner.get(debit['partner_id'], 0.0) + debit['open']
        ranked = sorted(by_partner.items(), key=lambda kv: kv[1],
                        reverse=True)[:TOP_ROWS]
        names = {p.id: p.display_name for p in self.env['res.partner']
                 .sudo().browse([pid for pid, _v in ranked])}
        return [{'id': pid, 'name': names.get(pid, ''),
                 'open': round(value, 2),
                 'share': round(value / total * 100, 1) if total else 0.0}
                for pid, value in ranked]
