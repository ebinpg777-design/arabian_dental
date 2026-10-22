# -*- coding: utf-8 -*-
"""Field Live - the manager's and the CEO's one screen (client, 2026-10-20: "no need
of the Ops Desk, Marketing Desk and Command Center; redesign a dashboard for the
field work manager and the CEO that tracks the real-time location and travelling of
the executives, and all details"; 2026-10-21: "more reports, more innovative
features").

One call, `get_board`, gives the screen everything it draws:

* the live map's own payload (location_live.py) - where everyone is, the trails
  split at recording gaps, the doors, the halts, the over-speed points;
* each executive's day as **facts** (visits, cases, money, km on the trail beside the
  km claimed, the sheet's state), as a **story** - a timeline of what the phone and
  the forms recorded, in order - and as **bands** on the clock (on duty, at a clinic,
  stopped, not recorded, over the limit), with the day's minutes **split** between
  clinics, the road, stops and silence;
* the team's numbers for the strip across the top, with the hourly pulse of check-ins;
* the **alerts** worth a manager's minute, each pointing at the person: no signal
  while on duty, a long stop away from any clinic, a phone about to die, a day not
  started by ten, a visit left open, a claim far from the trail - plus the money
  alerts the Ops Desk used to carry (`lab.desk._ops_alerts`);
* the **phones**: which are on ADL FieldX and which build, which still send from
  the browser, which have gone quiet on duty;
* the reports: the **fortnight** (fourteen days of visits, cases, money and km, team
  and person by person, from the day sheets - today from the live facts), the
  **routes** rolled up, and the **month** so far per executive.

Pushed, not only polled: every batch of positions the field posts is announced on the
bus to the manager group (`_announce_live`), so a marker moves on the screen within a
second of the phone moving; the poll stays as the fallback and for everything the
push does not carry.

The data layer is the one the desks read (`lab.desk._field_board`, `_ops_alerts`,
`_leaderboard`) and the live layer (`get_live_map`): Field Live adds no second
opinion on any number.
"""
import logging
from collections import Counter, defaultdict
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError

from .lab_visit import OUTCOMES
from .local_day import local_midnight_utc, tz_name
from .location_live import epoch

_logger = logging.getLogger(__name__)

LONG_HALT_MINUTES = 20      # a stop this long away from any clinic is worth a question
LOW_BATTERY = 15            # per cent
LATE_START_HOUR = 10        # visits planned and no day started by then
LONG_VISIT_MINUTES = 90     # checked in, not out
KM_VARIANCE_PCT = 30        # claim vs trail
OVERSPEED_TIMES = 3
STORY_MAX = 80
OVERVIEW_POINTS = 300       # trail points per person in the overview; the focus gets the full trail
PULSE_HOURS = range(7, 21)
TREND_DAYS = 14
WAITING_STATES = ('submitted', 'ops_approved', 'ops_checked')
LEVEL_RANK = {'danger': 0, 'warning': 1, 'info': 2}
DOOR_LABELS = {
    'ok': 'at the door', 'far': 'away from the clinic',
    'nofix': 'no fix', 'nopin': 'clinic not pinned',
}


class LabFieldLive(models.AbstractModel):
    _name = 'lab.field.live'
    _description = 'Field Live'

    # ------------------------------------------------------------------ the board
    @api.model
    def get_board(self, day=None, user_ids=None):
        """Everything Field Live draws, for `day` (today when empty)."""
        Ping = self.env['lab.location.ping']
        if not Ping._is_manager():
            raise AccessError(_("Only field managers open Field Live."))
        # A light trail per person for the overview; the person in focus is fetched
        # in full by the screen (get_live_map for one user).
        board = Ping.get_live_map(day=day, user_ids=user_ids, max_points=OVERVIEW_POINTS)
        day = fields.Date.to_date(board['day'])
        is_today = bool(board['is_today'])
        now = fields.Datetime.now()
        Desk = self.env['lab.desk']
        facts = Desk._field_board(day)
        rows = {row['id']: row for row in facts['rows']}
        users = self.env['res.users'].sudo().browse([ex['user_id'] for ex in board['executives']])
        by_id = {user.id: user for user in users}
        sheets = {s.user_id.id: s for s in self.env['lab.daily.update'].sudo().search(
            [('date', '=', day), ('user_id', 'in', users.ids)])}
        trips = {t.user_id.id: t for t in self.env['lab.trip'].sudo().search(
            [('date', '=', day), ('user_id', 'in', users.ids)])}
        offline_s = Ping._offline_minutes() * 60
        limit = Ping._speed_limit()
        records = self._day_records(users, day, is_today)
        # The clock the bands hang on: the manager's local midnight, as an epoch.
        day_start = epoch(local_midnight_utc(self.env, day, self.env.user))
        day_end = epoch(local_midnight_utc(self.env, day + timedelta(days=1), self.env.user))
        alerts, phones = [], []
        for ex in board['executives']:
            user = by_id[ex['user_id']]
            row = rows.get(user.id, {})
            trip = trips.get(user.id)
            sheet = sheets.get(user.id)
            ex['route'] = self._route_of(user)
            ex['avatar'] = '/web/image/res.users/%d/avatar_128' % user.id
            ex['facts'] = {
                'planned': row.get('planned', 0), 'done': row.get('done', 0),
                'cases': row.get('cases', 0), 'value': row.get('value', 0.0),
                'collected': row.get('collected', 0.0),
                'cash_with': row.get('unbanked', 0.0), 'cash_pending': row.get('cash_pending', 0.0),
                'far': row.get('far', 0), 'board_status': row.get('status', 'off'),
                'where': row.get('where', ''),
            }
            ex['trip'] = {
                'id': trip.id, 'state': trip.state, 'vehicle': trip.vehicle,
                'claimed_km': round(trip.distance, 1), 'gps_km': round(trip.gps_km or 0.0, 1),
                'variance_pct': round(trip.km_variance_pct or 0.0, 1), 'amount': trip.amount,
            } if trip else None
            ex['sheet'] = {
                'id': sheet.id, 'state': sheet.state,
                'label': dict(sheet._fields['state'].selection).get(sheet.state, sheet.state),
            } if sheet else None
            phone = self._phone_of(user, ex, offline_s)
            ex['phone_app'] = phone
            phones.append(dict(phone, user_id=user.id, name=user.name, on_duty=ex['on_duty']))
            story, bands, split = self._day_of(user, day, ex, sheet, now, is_today, day_end, records)
            ex['story'], ex['bands'], ex['split'] = story, bands, split
            ex['alerts'] = self._alerts_of(user, ex, row, trip, is_today, now, offline_s, limit)
            alerts.extend(ex['alerts'])
        team = self._team_alerts(day, facts) if is_today else []
        alerts = sorted(team + alerts,
                        key=lambda a: (LEVEL_RANK.get(a['level'], 9), -(a.get('epoch') or 0)))
        kpis = self._kpis(board['executives'], facts, alerts, phones, is_today)
        board.update({
            'day_start_epoch': day_start, 'day_end_epoch': day_end,
            'kpis': kpis,
            'alerts': alerts,
            'phones': sorted(phones, key=lambda p: (not p['silent'], not p['on_duty'], p['name'])),
            'leaders': Desk._leaderboard(5) if is_today else [],
            'routes': self._routes(board['executives']),
            'trend': self._trend(users, day, is_today, board['executives'], kpis),
            'month': self._month(users, day),
            'currency': self.env.company.currency_id.symbol or '',
            # The push carries the positions; the poll fetches the rest less often.
            'config': dict(board['config'], live_ms=60000, push=True),
        })
        return board

    # ------------------------------------------------------------------ per person
    @api.model
    def _route_of(self, user):
        team = user.sale_team_id if 'sale_team_id' in user._fields else None
        return team.name if team else ''

    @api.model
    def _phone_of(self, user, ex, offline_s):
        """The phone behind the dot: on the app or not, heard from when."""
        last = ex.get('last') or {}
        age = last.get('age_s')
        return {
            'on_app': bool(user.fw_native_app),
            'app_version': user.fw_native_app_version or '',
            'traccar': bool(user.fw_gps_app),
            'source': last.get('source') or '',
            'battery': last.get('battery'),
            'last_age_s': age,
            'silent': bool(ex.get('on_duty')) and (age is None or age >= offline_s),
        }

    @api.model
    def _day_records(self, users, day, is_today):
        """The day's attendances, visits and cash for everyone at once - four
        queries for the team instead of four per person (2026-10-21)."""
        start = local_midnight_utc(self.env, day, self.env.user)
        end = local_midnight_utc(self.env, day + timedelta(days=1), self.env.user)
        employees = self.env['hr.employee'].sudo().search([('user_id', 'in', users.ids)])
        employee_of = {}
        for employee in employees:
            employee_of.setdefault(employee.user_id.id, employee)
        attendances = defaultdict(list)
        if employees:
            # Every attendance that touches the day, clipped to it later: a day
            # started before midnight and still running belongs to this morning too.
            for att in self.env['hr.attendance'].sudo().search(
                    [('employee_id', 'in', employees.ids), ('check_in', '<', end),
                     '|', ('check_out', '=', False), ('check_out', '>=', start)], order='check_in'):
                attendances[att.employee_id.user_id.id].append(att)
        visits = defaultdict(list)
        for visit in self.env['lab.visit'].sudo().search(
                [('user_id', 'in', users.ids), ('date', '=', day), ('state', '!=', 'cancel')],
                order='check_in, id'):
            visits[visit.user_id.id].append(visit)
        cash = defaultdict(list)
        for collection in self.env['lab.cash.collection'].sudo().search(
                [('user_id', 'in', users.ids), ('date', '=', day)], order='create_date'):
            cash[collection.user_id.id].append(collection)
        return {'employee': employee_of, 'attendances': attendances, 'visits': visits, 'cash': cash,
                'start': start, 'end': end}

    @api.model
    def _day_of(self, user, day, ex, sheet, now, is_today, day_end, records):
        """The day three ways from the same records: as a story (events, oldest
        first), as bands on the clock, and as minutes split between the clinics,
        the road, the stops and the silence. Every event comes from a record or a
        fix that exists; nothing here is inferred twice."""
        events, bands = [], []
        # An open span runs to now today, and to the end of the day for a day gone.
        open_until = epoch(now) if is_today else day_end
        employee = records['employee'].get(user.id)
        duty_minutes = 0
        if employee:
            start = records['start']
            attendances = records['attendances'].get(user.id, [])
            day_start_epoch = epoch(start)
            for att in attendances:
                began = epoch(att.check_in)
                ended = epoch(att.check_out) if att.check_out else open_until
                if began >= day_start_epoch:
                    events.append({'kind': 'start', 'epoch': began, 'title': _('Started the day')})
                if att.check_out and ended <= day_end:
                    events.append({'kind': 'end', 'epoch': ended, 'title': _('Ended the day')})
                since = max(began, day_start_epoch)
                until = min(ended, day_end)
                if until <= since:
                    continue
                bands.append({'kind': 'duty', 'from_epoch': since, 'to_epoch': until,
                              'label': _('On duty')})
                duty_minutes += (until - since) // 60
        outcomes = dict(OUTCOMES)
        visits = records['visits'].get(user.id, [])
        clinic_minutes = 0
        for visit in visits:
            clinic = visit.partner_id.display_name
            if visit.check_in:
                since = epoch(visit.check_in)
                until = epoch(visit.check_out) if visit.check_out else open_until
                events.append({
                    'kind': 'visit_in', 'epoch': since, 'visit_id': visit.id,
                    'title': clinic, 'state': visit.gps_state or 'nofix',
                    'detail': _(DOOR_LABELS.get(visit.gps_state or 'nofix', 'no fix')),
                })
                bands.append({'kind': 'visit', 'from_epoch': since, 'to_epoch': max(since, until),
                              'label': clinic, 'visit_id': visit.id,
                              'state': visit.gps_state or 'nofix', 'open': not visit.check_out})
                clinic_minutes += max(0, until - since) // 60
            if visit.check_out:
                bits = []
                if visit.minutes:
                    bits.append(_('%s min') % visit.minutes)
                if visit.order_count:
                    bits.append(_('%s case(s)') % visit.order_count)
                if visit.outcome:
                    bits.append(outcomes.get(visit.outcome, visit.outcome))
                events.append({
                    'kind': 'visit_out', 'epoch': epoch(visit.check_out), 'visit_id': visit.id,
                    'title': _('Left %s') % clinic, 'detail': ' · '.join(bits),
                    'amount': visit.collected or 0.0,
                })
        halt_minutes = 0
        for halt in ex.get('halts') or []:
            until = halt['ended_epoch'] or open_until
            events.append({
                'kind': 'halt', 'epoch': halt['started_epoch'], 'halt_id': halt['id'],
                'title': _('Stopped %s min') % halt['minutes'] if halt['ended_epoch']
                else _('Stopped, still there'),
                'detail': halt['place'] or _('away from any clinic'),
                'open': not halt['ended_epoch'],
            })
            bands.append({'kind': 'halt', 'from_epoch': halt['started_epoch'],
                          'to_epoch': max(halt['started_epoch'], until),
                          'label': halt['place'] or _('stopped'), 'halt_id': halt['id'],
                          'open': not halt['ended_epoch']})
            halt_minutes += max(0, until - halt['started_epoch']) // 60
        last_speed_epoch = None
        for point in ex.get('overspeed_points') or []:
            # one event per burst, not one per fix: a run on the highway is one fact
            if last_speed_epoch is not None and point[2] - last_speed_epoch < 300:
                last_speed_epoch = point[2]
                continue
            last_speed_epoch = point[2]
            events.append({'kind': 'speed', 'epoch': point[2],
                           'title': _('%s km/h') % int(round(point[3]))})
            bands.append({'kind': 'speed', 'from_epoch': point[2], 'to_epoch': point[2] + 60,
                          'label': _('%s km/h') % int(round(point[3]))})
        gap_minutes = 0
        for gap in ex.get('gaps') or []:
            events.append({'kind': 'gap', 'epoch': gap['from_epoch'],
                           'title': _('Not recorded for %s min') % gap['minutes']})
            bands.append({'kind': 'gap', 'from_epoch': gap['from_epoch'], 'to_epoch': gap['to_epoch'],
                          'label': _('Not recorded')})
            gap_minutes += gap['minutes']
        for cash in records['cash'].get(user.id, []):
            events.append({'kind': 'cash', 'epoch': epoch(cash.create_date),
                           'title': _('Cash taken'), 'amount': cash.amount,
                           'detail': cash.partner_id.display_name})
        if sheet:
            events.append({'kind': 'sheet', 'epoch': epoch(sheet.write_date), 'sheet_id': sheet.id,
                           'title': _('Day sheet: %s') % dict(
                               sheet._fields['state'].selection).get(sheet.state, sheet.state)})
        events.sort(key=lambda e: (e['epoch'] is None, e['epoch'] or 0))
        bands.sort(key=lambda b: b['from_epoch'])
        split = {
            'duty': duty_minutes,
            'clinic': min(clinic_minutes, duty_minutes) if duty_minutes else clinic_minutes,
            'stopped': halt_minutes,
            'unrecorded': gap_minutes,
        }
        split['road'] = max(0, duty_minutes - split['clinic'] - halt_minutes - gap_minutes)
        return events[:STORY_MAX], bands, split

    @api.model
    def _alerts_of(self, user, ex, row, trip, is_today, now, offline_s, limit):
        """Only what a manager can act on today, each one about one person."""
        out = []
        if not is_today:
            return out

        def add(key, level, title, detail=''):
            out.append({'key': key, 'level': level, 'user_id': user.id, 'name': user.name,
                        'title': title, 'detail': detail, 'epoch': epoch(now)})

        last = ex.get('last')
        age = last['age_s'] if last else None
        phone = ex.get('phone_app') or {}
        if ex.get('on_duty') and (age is None or age >= offline_s):
            detail = _('on duty, no position for %s min') % (age // 60) if age is not None \
                else _('on duty, no position yet today')
            if phone.get('on_app'):
                detail += _(' · ADL FieldX may have been stopped by battery saving')
            add('silent', 'danger', _('No signal'), detail)
        open_halt = next((h for h in ex.get('halts') or [] if not h['ended_epoch']), None)
        if open_halt:
            minutes = max(0, (epoch(now) - open_halt['started_epoch']) // 60)
            if minutes >= LONG_HALT_MINUTES:
                add('halt', 'warning', _('Stopped %s min') % minutes,
                    open_halt['place'] or _('away from any clinic'))
        if (ex.get('overspeed') or 0) >= OVERSPEED_TIMES:
            add('speed', 'warning', _('%(times)s times over %(limit)s km/h',
                                      times=ex['overspeed'], limit=limit))
        battery = last.get('battery') if last else None
        if ex.get('on_duty') and battery is not None and 0 < battery < LOW_BATTERY:
            add('battery', 'warning', _('Battery %s%%') % int(round(battery)),
                _('the phone may die before the day ends'))
        local_hour = fields.Datetime.context_timestamp(
            self.with_context(tz=tz_name(self.env, user)), now).hour
        if row.get('status') == 'idle' and row.get('planned') and local_hour >= LATE_START_HOUR:
            add('late', 'info', _('Not started'),
                _('%(planned)s visit(s) planned, no day started by %(hour)s:00',
                  planned=row['planned'], hour=LATE_START_HOUR))
        here = ex.get('at_clinic')
        if here and here.get('since_epoch'):
            minutes = (epoch(now) - here['since_epoch']) // 60
            if minutes >= LONG_VISIT_MINUTES:
                add('long_visit', 'info', _('At %(clinic)s for %(minutes)s min',
                                            clinic=here['name'], minutes=minutes),
                    _('checked in, not out'))
        if row.get('far'):
            add('far', 'info', _('GPS far from the clinic'),
                _('%s visit(s) today, each with a written reason') % row['far'])
        if trip and trip.state in ('closed', 'approved') and trip.gps_km \
                and abs(trip.km_variance_pct or 0.0) >= KM_VARIANCE_PCT:
            add('variance', 'info',
                _('Claimed %(claimed)s km, trail %(trail)s km',
                  claimed=round(trip.distance, 1), trail=round(trip.gps_km, 1)),
                _('%s%% apart') % int(round(abs(trip.km_variance_pct))))
        return out

    @api.model
    def _team_alerts(self, day, facts):
        """The money and the paperwork, from the one reading the Ops Desk used."""
        out = []
        for alert in self.env['lab.desk']._ops_alerts(day, facts['users'], facts['visits']):
            if alert['key'] == 'idle':
                continue        # Field Live says it per person, as "not started"
            out.append({
                'key': alert['key'], 'level': alert['level'], 'user_id': None, 'name': '',
                'title': alert['title'], 'detail': alert['detail'], 'epoch': None,
                'model': alert.get('model'), 'ids': alert.get('ids') or [],
                'label': alert.get('label', ''),
            })
        return out

    # ------------------------------------------------------------------ the strip
    @api.model
    def _kpis(self, executives, facts, alerts, phones, is_today):
        count = Counter(ex['status'] for ex in executives)
        rows = facts['rows']
        totals = facts['totals']
        pulse = Counter()
        for visit in facts['visits']:
            if visit.check_in:
                local = fields.Datetime.context_timestamp(
                    self.with_context(tz=tz_name(self.env, visit.user_id)), visit.check_in)
                pulse[local.hour] += 1
        waiting = self.env['lab.daily.update'].sudo().search_count(
            [('state', 'in', WAITING_STATES)]) if is_today else 0
        return {
            'roster': len(executives),
            'duty': sum(1 for ex in executives if ex['on_duty']),
            'moving': count['moving'], 'at_clinic': count['at_clinic'],
            'stopped': count['stopped'], 'offline': count['offline'],
            'not_started': sum(1 for r in rows if r['status'] == 'idle'),
            'finished': sum(1 for r in rows if r['status'] == 'finished'),
            'off': sum(1 for r in rows if r['status'] in ('off', 'no_setup')),
            'visits_done': totals['done'], 'visits_planned': totals['planned'],
            'cases': totals['cases'], 'case_value': totals['value'],
            'collected': totals['collected'], 'cash_with_field': totals['unbanked'],
            'km': round(sum(ex['km'] for ex in executives), 1),
            'claimed_km': round(totals['distance'], 1),
            'halt_minutes': sum(ex['halt_minutes'] for ex in executives),
            'overspeed': sum(ex['overspeed'] for ex in executives),
            'clinic_minutes': sum((ex.get('split') or {}).get('clinic', 0) for ex in executives),
            'alerts': len(alerts),
            'danger': sum(1 for a in alerts if a['level'] == 'danger'),
            'sheets_waiting': waiting,
            'on_app': sum(1 for p in phones if p['on_app']),
            'silent': sum(1 for p in phones if p['silent']),
            'pulse': [{'hour': hour, 'count': pulse.get(hour, 0)} for hour in PULSE_HOURS],
        }

    # ------------------------------------------------------------------ the reports
    @api.model
    def _routes(self, executives):
        """The day rolled up by sales route, the people without one together."""
        groups = defaultdict(lambda: {'route': '', 'people': 0, 'duty': 0, 'visits_done': 0,
                                      'visits_planned': 0, 'cases': 0, 'collected': 0.0,
                                      'cash_with': 0.0, 'km': 0.0, 'halt_minutes': 0, 'overspeed': 0})
        for ex in executives:
            facts = ex.get('facts') or {}
            row = groups[ex.get('route') or '']
            row['route'] = ex.get('route') or _('No route')
            row['people'] += 1
            row['duty'] += 1 if ex['on_duty'] else 0
            row['visits_done'] += ex['visits_done']
            row['visits_planned'] += ex['visits_planned']
            row['cases'] += facts.get('cases', 0)
            row['collected'] += facts.get('collected', 0.0)
            row['cash_with'] += facts.get('cash_with', 0.0)
            row['km'] += ex['km']
            row['halt_minutes'] += ex['halt_minutes']
            row['overspeed'] += ex['overspeed']
        rows = sorted(groups.values(), key=lambda r: (-r['visits_done'], r['route']))
        for row in rows:
            row['km'] = round(row['km'], 1)
        return rows

    @api.model
    def _sheet_rows(self, users, since, until):
        return self.env['lab.daily.update'].sudo().search_read(
            [('user_id', 'in', users.ids), ('date', '>=', since), ('date', '<=', until)],
            ['date', 'user_id', 'state', 'is_clean', 'visit_done_count', 'visit_count',
             'case_count', 'collected', 'km', 'gps_km', 'order_value'],
            order='date')

    @api.model
    def _trend(self, users, day, is_today, executives, kpis):
        """Fourteen days ending on `day`, from the day sheets; the day itself from
        the live facts while it is still running (its sheet comes at day close)."""
        since = day - timedelta(days=TREND_DAYS - 1)
        by_day = {since + timedelta(days=i): {
            'date': fields.Date.to_string(since + timedelta(days=i)),
            'visits': 0, 'planned': 0, 'cases': 0, 'collected': 0.0,
            'km': 0.0, 'gps_km': 0.0, 'value': 0.0, 'people': 0,
        } for i in range(TREND_DAYS)}
        people = defaultdict(dict)
        for sheet in self._sheet_rows(users, since, day):
            when = fields.Date.to_date(sheet['date'])
            row = by_day.get(when)
            if row is None:
                continue
            row['visits'] += sheet['visit_done_count'] or 0
            row['planned'] += sheet['visit_count'] or 0
            row['cases'] += sheet['case_count'] or 0
            row['collected'] += sheet['collected'] or 0.0
            row['km'] += sheet['km'] or 0.0
            row['gps_km'] += sheet['gps_km'] or 0.0
            row['value'] += sheet['order_value'] or 0.0
            row['people'] += 1
            people[sheet['user_id'][0]][row['date']] = {
                'visits': sheet['visit_done_count'] or 0,
                'clean': bool(sheet['is_clean']), 'state': sheet['state'],
            }
        if is_today:
            today = by_day[day]
            today.update({
                'visits': kpis['visits_done'], 'planned': kpis['visits_planned'],
                'cases': kpis['cases'], 'collected': kpis['collected'],
                'km': kpis['claimed_km'], 'gps_km': kpis['km'], 'value': kpis['case_value'],
                'people': kpis['duty'], 'live': True,
            })
            for ex in executives:
                if ex['on_duty'] or ex['visits_done']:
                    people[ex['user_id']][today['date']] = {
                        'visits': ex['visits_done'], 'clean': None, 'state': 'live'}
        days = [by_day[since + timedelta(days=i)] for i in range(TREND_DAYS)]
        for row in days:
            row['km'] = round(row['km'], 1)
            row['gps_km'] = round(row['gps_km'], 1)
        return {
            'days': days,
            'people': [{'user_id': ex['user_id'], 'name': ex['name'],
                        'days': [people[ex['user_id']].get(d['date']) for d in days]}
                       for ex in executives],
        }

    @api.model
    def _month(self, users, day):
        """The month so far, per executive, from the day sheets; the leaderboard's
        streak and score beside the sums."""
        first = day.replace(day=1)
        sums = defaultdict(lambda: {'days': 0, 'clean': 0, 'visits': 0, 'cases': 0,
                                    'collected': 0.0, 'km': 0.0, 'gps_km': 0.0, 'value': 0.0})
        for sheet in self._sheet_rows(users, first, day):
            row = sums[sheet['user_id'][0]]
            row['days'] += 1
            row['clean'] += 1 if sheet['is_clean'] else 0
            row['visits'] += sheet['visit_done_count'] or 0
            row['cases'] += sheet['case_count'] or 0
            row['collected'] += sheet['collected'] or 0.0
            row['km'] += sheet['km'] or 0.0
            row['gps_km'] += sheet['gps_km'] or 0.0
            row['value'] += sheet['order_value'] or 0.0
        leaders = {row['user_id']: row for row in self.env['lab.desk']._leaderboard(limit=1000)}
        out = []
        for user in users:
            row = sums.get(user.id)
            if not row:
                continue
            lead = leaders.get(user.id, {})
            out.append(dict(row, user_id=user.id, name=user.name,
                            km=round(row['km'], 1), gps_km=round(row['gps_km'], 1),
                            streak=lead.get('streak', 0), score=lead.get('score', 0.0)))
        out.sort(key=lambda r: (-r['visits'], -r['collected'], r['name']))
        return out


class LabLocationPingPush(models.Model):
    _inherit = 'lab.location.ping'

    @api.model
    def _after_record(self, user, day, values):
        super()._after_record(user, day, values)
        try:
            if day == self._today_for(user):
                self._announce_live(user, values)
        except Exception:                                      # noqa: BLE001
            _logger.exception("field live: the push failed for %s", user.login)

    @api.model
    def _announce_live(self, user, values):
        """Tell every open Field Live where this phone just was. The manager group is
        the channel: the bus subscribes each user to their groups, so nobody outside
        the role can listen in, and one send reaches every manager's screen."""
        group = self.env.ref('lab_fieldwork.group_fieldwork_manager', raise_if_not_found=False)
        if not group or not values:
            return
        latest = max(values, key=lambda v: v['ts'])
        group.sudo()._bus_send('lab_fieldwork.live', {
            'user_id': user.id,
            'lat': latest['latitude'], 'lon': latest['longitude'],
            'epoch': epoch(latest['ts']),
            'speed_kmh': latest.get('speed_kmh') or 0.0,
            'battery': latest.get('battery') or None,
            'source': latest.get('source') or 'browser',
            'kept': len(values),
        })
