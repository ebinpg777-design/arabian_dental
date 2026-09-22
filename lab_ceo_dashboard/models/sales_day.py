# -*- coding: utf-8 -*-
"""Sales & Cases, one day at a time: today as it happens, tomorrow as it will likely go.

A month of bars answers "how are we doing"; the morning question is "what came in
today, and what do we expect tomorrow". (client, 2026-09-17)

Tomorrow cannot be read off the data - no order carries a due date here
(`commitment_date` and `date_deadline` are empty on every order) - so tomorrow is
an OUTLOOK, not a report: the same weekday over the last four weeks, the clinics
that reliably send work on that weekday, the follow-ups falling due and the field
visits already planned. Every figure says what it is built from.
"""
from datetime import timedelta

import pytz

from odoo import _, api, fields, models
from odoo.tools import format_date

from .mgmt_pulse import TOP_ROWS

# How many weeks of the same weekday an outlook is built from.
LOOKBACK_WEEKS = 4
# A clinic is a regular for a weekday when it booked on it this many of those weeks.
REGULAR_WEEKS = 3
# The day rail: this many days back from today, then tomorrow.
RAIL_DAYS = 14
FEED_ROWS = 15
LIST_ROWS = 12


class LabMgmtPulseDay(models.AbstractModel):
    _inherit = 'lab.mgmt.pulse'

    # ------------------------------------------------------------------ frame
    @api.model
    def _day_bounds(self, day):
        return self._utc_start(day), self._utc_start(day + timedelta(days=1))

    @api.model
    def _day_orders_domain(self, day, user_id=False):
        start, end = self._day_bounds(day)
        domain = [('state', '=', 'sale'),
                  ('date_order', '>=', start), ('date_order', '<', end)]
        if user_id:
            domain.append(('user_id', '=', user_id))
        return domain

    @api.model
    def _local(self, value):
        return pytz.utc.localize(value).astimezone(self._lab_tz())

    @api.model
    def _today(self):
        return fields.Datetime.now().replace(tzinfo=pytz.utc).astimezone(
            self._lab_tz()).date()

    @api.model
    def _day_label(self, day, today):
        if day == today:
            return _('Today')
        if day == today + timedelta(days=1):
            return _('Tomorrow')
        if day == today - timedelta(days=1):
            return _('Yesterday')
        return format_date(self.env, day, date_format='EEE d MMM')

    # ------------------------------------------------------------------ entry
    @api.model
    def get_sales_day(self, day=False, user_id=False):
        """The Sales & Cases day view for one local day, in one call.

        A day up to today is what was booked; a day after today is an outlook.
        """
        self._check_pulse_access()
        user_id = int(user_id) if user_id else False
        today = self._today()
        day = fields.Date.to_date(day) if day else today
        start, end = self._day_bounds(day)
        result = {
            'day': fields.Date.to_string(day),
            'today': fields.Date.to_string(today),
            'label': self._day_label(day, today),
            'long_label': format_date(self.env, day, date_format='EEEE, d MMMM y'),
            'weekday': format_date(self.env, day, date_format='EEEE'),
            'is_today': day == today,
            'is_tomorrow': day == today + timedelta(days=1),
            'mode': 'outlook' if day > today else 'booked',
            'rail': self._day_rail(today, day, user_id),
            # The day's edges in UTC, so a drill opens exactly the orders counted:
            # the lab's day starts 5.5 hours before a UTC one.
            'bounds': [['date_order', '>=', fields.Datetime.to_string(start)],
                       ['date_order', '<', fields.Datetime.to_string(end)]],
        }
        if day > today:
            result['outlook'] = self._day_outlook(day, user_id)
        else:
            result['booked'] = self._day_booked(day, today, user_id)
        return result

    # ------------------------------------------------------------------ rail
    @api.model
    def _day_rail(self, today, selected, user_id=False):
        """The last fortnight as a row of days, then tomorrow - the page's navigation.

        Each day carries its own count so the rail is a chart as well as a picker.
        """
        first = today - timedelta(days=RAIL_DAYS - 1)
        counts = {}
        start, _end = self._day_bounds(first)
        domain = [('state', '=', 'sale'), ('date_order', '>=', start),
                  ('date_order', '<', self._day_bounds(today)[1])]
        if user_id:
            domain.append(('user_id', '=', user_id))
        Order = self.env['sale.order'].sudo().with_context(tz=self._lab_tz().zone)
        for when, count, value in Order._read_group(
                domain, ['date_order:day'], ['__count', 'amount_total:sum']):
            counts[fields.Date.to_date(when)] = (count, value or 0.0)
        rail = []
        for i in range(RAIL_DAYS + 1):
            day = first + timedelta(days=i)
            count, value = counts.get(day, (0, 0.0))
            rail.append({
                'day': fields.Date.to_string(day),
                'dow': format_date(self.env, day, date_format='EEE'),
                'num': day.day,
                'orders': count,
                'value': value,
                'is_today': day == today,
                'future': day > today,
                'selected': day == selected,
                'sunday': day.weekday() == 6,
            })
        return rail

    # ------------------------------------------------------------------ booked
    @api.model
    def _day_booked(self, day, today, user_id=False):
        Order = self.env['sale.order'].sudo()
        orders = Order.search(self._day_orders_domain(day, user_id), order='date_order desc')
        value = sum(orders.mapped('amount_total'))

        # Like-for-like: the same weekday last week, and on today only up to this
        # very minute - a morning compared against a whole day reads as a collapse.
        last_week = day - timedelta(days=7)
        ghost_orders = Order.search(self._day_orders_domain(last_week, user_id))
        now_local = self._local(fields.Datetime.now())
        if day == today:
            cutoff = now_local.time()
            ghost_by_now = ghost_orders.filtered(
                lambda o: self._local(o.date_order).time() <= cutoff)
        else:
            ghost_by_now = ghost_orders

        hours = [{'hour': h, 'orders': 0, 'value': 0.0, 'ghost': 0} for h in range(24)]
        for order in orders:
            slot = hours[self._local(order.date_order).hour]
            slot['orders'] += 1
            slot['value'] += order.amount_total
        for order in ghost_orders:
            hours[self._local(order.date_order).hour]['ghost'] += 1
        busy = [h['hour'] for h in hours if h['orders'] or h['ghost']]
        first_hour = min(busy + [8])
        last_hour = max(busy + [20, now_local.hour if day == today else 0])
        for slot in hours:
            slot['label'] = '%02d' % slot['hour']

        return {
            'orders': len(orders),
            'value': value,
            'avg': value / len(orders) if orders else 0.0,
            'last_week': {
                'day': fields.Date.to_string(last_week),
                'label': format_date(self.env, last_week, date_format='EEE d MMM'),
                'orders': len(ghost_by_now),
                'value': sum(ghost_by_now.mapped('amount_total')),
                'whole_orders': len(ghost_orders),
                'by_now': day == today,
            },
            'now_hour': now_local.hour if day == today else None,
            'hours': hours[first_hour:last_hour + 1],
            'feed': [self._feed_row(o) for o in orders[:FEED_ROWS]],
            'more': max(len(orders) - FEED_ROWS, 0),
            'routes': self._ranked_orders(orders, 'team_id'),
            'clinics': self._ranked_orders(orders, 'partner_id'),
            'appliances': self._ranked_lines(orders),
            'first_timers': self._first_timers(orders, day),
            # Only today can still be acted on: a regular who has not booked yet.
            'not_yet': self._regulars(day, user_id, exclude=orders.partner_id)
                       if day == today else [],
        }

    @api.model
    def _feed_row(self, order):
        return {
            'id': order.id,
            'name': order.name,
            'time': self._local(order.date_order).strftime('%H:%M'),
            'clinic': order.partner_id.display_name or '',
            'patient': order.patient or '',
            'products': order.product_names or '',
            'route': order.team_id.name or '',
            'value': order.amount_total,
            'rework': bool(order.is_rework) if 'is_rework' in order._fields else False,
        }

    @api.model
    def _ranked_orders(self, orders, field):
        totals = {}
        for order in orders:
            rec = order[field]
            if not rec:
                continue
            row = totals.setdefault(rec.id, {'id': rec.id, 'name': rec.display_name,
                                             'orders': 0, 'value': 0.0})
            row['orders'] += 1
            row['value'] += order.amount_total
        return sorted(totals.values(), key=lambda r: -r['value'])[:TOP_ROWS]

    @api.model
    def _ranked_lines(self, orders):
        totals = {}
        for line in orders.order_line:
            if line.display_type or not line.product_id:
                continue
            row = totals.setdefault(line.product_id.id, {
                'id': line.product_id.id, 'name': line.product_id.display_name,
                'qty': 0.0, 'value': 0.0})
            row['qty'] += line.product_uom_qty
            row['value'] += line.price_total
        return sorted(totals.values(), key=lambda r: -r['value'])[:TOP_ROWS]

    @api.model
    def _first_timers(self, orders, day):
        """Clinics whose first confirmed case ever is on this day."""
        partners = orders.partner_id
        if not partners:
            return []
        seen = {partner.id for [partner] in self.env['sale.order'].sudo()._read_group(
            [('state', '=', 'sale'), ('partner_id', 'in', partners.ids),
             ('date_order', '<', self._day_bounds(day)[0])], ['partner_id'])}
        return [{'id': p.id, 'name': p.display_name}
                for p in partners if p.id not in seen]

    # ------------------------------------------------------------------ patterns
    @api.model
    def _same_weekdays(self, day):
        return [day - timedelta(days=7 * w) for w in range(1, LOOKBACK_WEEKS + 1)]

    @api.model
    def _regulars(self, day, user_id=False, exclude=None, limit=LIST_ROWS):
        """Clinics that booked on this weekday in most of the last four weeks.

        The one list on this page a salesperson can act on: a clinic that has
        sent work every Thursday for a month and has not sent any today is a
        phone call, not a statistic.
        """
        Order = self.env['sale.order'].sudo()
        stats = {}
        for past in self._same_weekdays(day):
            for partner, count, value in Order._read_group(
                    self._day_orders_domain(past, user_id), ['partner_id'],
                    ['__count', 'amount_total:sum']):
                if not partner:
                    continue
                row = stats.setdefault(partner.id, {
                    'partner': partner, 'weeks': [], 'orders': 0, 'value': 0.0})
                row['weeks'].append(fields.Date.to_string(past))
                row['orders'] += count
                row['value'] += value or 0.0
        exclude_ids = set(exclude.ids) if exclude else set()
        pattern = [fields.Date.to_string(d) for d in reversed(self._same_weekdays(day))]
        # The names and routes of every regular in one read, not one per row: the
        # read_group hands each partner back on its own, with no prefetch between them.
        partners = self.env['res.partner'].sudo().browse(list(stats))
        partners.mapped('display_name')
        if 'team_id' in partners._fields:
            partners.mapped('team_id.name')
        rows = []
        for row in stats.values():
            if len(row['weeks']) < REGULAR_WEEKS or row['partner'].id in exclude_ids:
                continue
            partner = row['partner']
            rows.append({
                'id': partner.id,
                'name': partner.display_name,
                'route': partner.team_id.name if 'team_id' in partner._fields
                         and partner.team_id else '',
                'weeks': len(row['weeks']),
                # Oldest week first, so the dots read left to right like a calendar.
                'dots': [d in row['weeks'] for d in pattern],
                'avg_orders': round(row['orders'] / len(row['weeks']), 1),
                'avg_value': row['value'] / len(row['weeks']),
            })
        rows.sort(key=lambda r: (-r['weeks'], -r['avg_value']))
        return rows[:limit]

    # ------------------------------------------------------------------ outlook
    @api.model
    def _day_outlook(self, day, user_id=False):
        Order = self.env['sale.order'].sudo()
        weeks = []
        appliances = {}
        for past in reversed(self._same_weekdays(day)):
            orders = Order.search(self._day_orders_domain(past, user_id))
            weeks.append({
                'day': fields.Date.to_string(past),
                'label': format_date(self.env, past, date_format='d MMM'),
                'orders': len(orders),
                'value': sum(orders.mapped('amount_total')),
            })
            for line in orders.order_line:
                if line.display_type or not line.product_id:
                    continue
                row = appliances.setdefault(line.product_id.id, {
                    'id': line.product_id.id, 'name': line.product_id.display_name,
                    'qty': 0.0, 'value': 0.0})
                row['qty'] += line.product_uom_qty / LOOKBACK_WEEKS
                row['value'] += line.price_total / LOOKBACK_WEEKS
        counts = [w['orders'] for w in weeks]
        values = [w['value'] for w in weeks]
        regulars = self._regulars(day, user_id, limit=50)
        return {
            'weeks': weeks,
            'expected': round(sum(counts) / len(counts)) if counts else 0,
            'low': min(counts) if counts else 0,
            'high': max(counts) if counts else 0,
            'expected_value': sum(values) / len(values) if values else 0.0,
            'regulars': regulars[:LIST_ROWS],
            'regulars_total': len(regulars),
            'regulars_value': sum(r['avg_value'] for r in regulars),
            'appliances': sorted(appliances.values(), key=lambda r: -r['value'])[:TOP_ROWS],
            'followups': self._followups_due(day, user_id),
            'visits': self._visits_planned(day, user_id),
        }

    @api.model
    def _followups_due(self, day, user_id=False):
        """Orders on hold whose follow-up falls on this day (or was missed before it)."""
        Order = self.env['sale.order'].sudo()
        if 'next_followup_date' not in Order._fields:
            return {'count': 0, 'overdue': 0, 'rows': []}
        base = [('hold_reason', '!=', False), ('state', 'not in', ('cancel',)),
                ('next_followup_date', '!=', False)]
        if user_id:
            base.append(('user_id', '=', user_id))
        due = Order.search(base + [('next_followup_date', '=', day)],
                           order='amount_total desc')
        overdue = Order.search_count(base + [('next_followup_date', '<', day)])
        reasons = dict(Order._fields['hold_reason']._description_selection(self.env))
        return {
            'count': len(due),
            'overdue': overdue,
            'rows': [{'id': o.id, 'name': o.name, 'clinic': o.partner_id.display_name,
                      'reason': reasons.get(o.hold_reason, ''),
                      'value': o.amount_total} for o in due[:LIST_ROWS]],
        }

    @api.model
    def _visits_planned(self, day, user_id=False):
        if 'lab.visit' not in self.env:
            return {'count': 0, 'people': []}
        domain = [('date', '=', day), ('state', 'in', ('planned', 'open'))]
        if user_id:
            domain.append(('user_id', '=', user_id))
        people = [{'id': user.id, 'name': user.name, 'visits': count}
                  for user, count in self.env['lab.visit'].sudo()._read_group(
                      domain, ['user_id'], ['__count'], order='__count desc')]
        return {'count': sum(p['visits'] for p in people), 'people': people}
