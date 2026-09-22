# -*- coding: utf-8 -*-
"""Three desks, three screens — each role's whole morning in one call.

The old Control Tower and Field Health tried to serve every manager from the same
two screens, which meant every manager scrolled past the other desk's problems to
find their own. Since the role split (2026-08-31) there are three different jobs,
so there are three different screens, and each one leads with the thing its owner
must DO today rather than a wall of numbers:

* **Ops Desk** — the approval queue first (clean days one tap, exceptions with
  their reasons unfolded), then the live field board, travel claims and the
  fortnight strip. The Operational Manager's job is "sign what happened".
* **Marketing Desk** — the activities queue with inline scoring, then the doors:
  new clinics, route momentum, the clinics slipping away, the outcome mix. The
  Marketing Manager's job is "judge what it was worth, and see where to push".
* **Command Center** — whether the MACHINE is running: both desks' backlogs and
  the SLA, who actually holds each desk, the crons' heartbeat, the policy
  calibration and setup gaps carried over from Field Health, and the flow ticker.

Everything here is a read. The writes (approve, send back, score) go through
lab.daily.update's own guarded actions, so a desk can never do what its owner's
role could not do from the form.
"""
from datetime import timedelta

from collections import defaultdict

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import format_date, formatLang

LOOKBACK_DAYS = 60      # radius calibration window, as Field Health had it
TREND_MONTHS = 6
BUCKETS = [(0, 100), (100, 200), (200, 300), (300, 500), (500, 1000), (1000, None)]


class LabDesk(models.AbstractModel):
    _name = 'lab.desk'
    _description = 'Field Work — Role Desks'

    # =================================================================== shared
    @api.model
    def _executives(self, visits=None):
        """Whoever is working the field today — the group, unioned with anyone
        who actually has a visit, so moving somebody's role at lunchtime never
        deletes their morning from the board. (ported from the Control Tower)"""
        group = self.env.ref('lab_fieldwork.group_fieldwork_executive',
                             raise_if_not_found=False)
        roster = group.sudo().all_user_ids if group else self.env['res.users']
        worked = visits.mapped('user_id') if visits is not None else self.env['res.users']
        users = roster.filtered(lambda u: u.active) | worked
        users = users.filtered(lambda u: u.company_ids & self.env.companies)
        return users.sorted('name')

    @api.model
    def _sla_hours(self):
        return self.env['lab.daily.update']._sla_days() * 24

    @api.model
    def _queue(self, state):
        """One desk's waiting sheets, oldest first, each carrying its own urgency.

        The age bar is the design: a queue sorted by age with the SLA drawn on it
        needs no explanation, and the one sheet about to breach is the one at the
        top going red."""
        states = (state,) if isinstance(state, str) else tuple(state)
        stamp_field = 'submitted_at' if states == ('submitted',) else 'ops_approved_at'
        sheets = self.env['lab.daily.update'].search(
            [('state', 'in', states)], order=stamp_field)
        now, sla_h = fields.Datetime.now(), self._sla_hours()
        rows = []
        for sheet in sheets:
            stamp = sheet[stamp_field] or sheet.create_date
            age_h = max(0.0, (now - stamp).total_seconds() / 3600.0)
            rows.append({
                'id': sheet.id,
                'user_id': sheet.user_id.id,
                'user': sheet.user_id.name,
                'date': fields.Date.to_string(sheet.date),
                'date_label': format_date(self.env, sheet.date,
                                          date_format='EEE d MMM'),
                'clean': sheet.is_clean,
                'streak': sheet.streak,
                'visits': sheet.visit_done_count,
                'cases': sheet.case_count,
                'value': sheet.order_value,
                'collected': sheet.collected,
                'km': round(sheet.km, 1),
                'new_clinics': sheet.new_clinic_count,
                'flags': [f for f, on in (
                    ('gps', sheet.flag_gps), ('open', sheet.flag_open_work),
                    ('zero', sheet.flag_zero_visits), ('km', sheet.flag_km_outlier),
                    ('attendance', sheet.flag_attendance)) if on],
                'why': sheet.anomaly_note or '',
                'note': sheet.note or '',
                'score': sheet.marketing_score or '',
                # Operations read it and wrote what it checked instead of
                # approving: the marketing card shows that note.
                'ops_checked': sheet.state == 'ops_checked',
                'ops_note': sheet.ops_check_note or '',
                'ops_by': sheet.ops_approved_by_id.name or '',
                # Asked about already, and nothing changed: the card says so
                # rather than letting the same question be asked a third time.
                'nudges': sheet.nudge_count,
                'age_h': round(age_h, 1),
                # 0..1 of the SLA consumed; the bar colour reads off this.
                'urgency': min(1.0, age_h / sla_h) if sla_h else 0.0,
                'overdue': age_h > sla_h,
            })
        return rows

    @api.model
    def _fortnight(self, today, days=14):
        """The strip: a day means nothing without the fortnight behind it.
        (ported from the Control Tower)"""
        start = today - timedelta(days=days - 1)
        buckets = {}
        for day, count, value, collected in self.env['lab.visit']._read_group(
                [('date', '>=', start), ('date', '<=', today),
                 ('state', '=', 'done')],
                ['date:day'], ['__count', 'order_value:sum', 'collected:sum']):
            buckets[fields.Date.to_date(day)] = (count, value or 0.0, collected or 0.0)
        out = []
        for offset in range(days):
            when = start + timedelta(days=offset)
            count, value, collected = buckets.get(when, (0, 0.0, 0.0))
            out.append({
                'day': fields.Date.to_string(when),
                'label': format_date(self.env, when, date_format='EEE d'),
                'visits': count, 'value': value, 'collected': collected,
                'weekend': when.weekday() == 6,
                'is_day': when == today,
            })
        return out

    @api.model
    def _ticker(self, limit=12):
        """The flow as a feed: who submitted, who signed, what came back.

        Assembled from the sheets' own stamps rather than a log table — the
        stamps already exist, cannot disagree with the record, and cost one
        query."""
        events = []
        recent = self.env['lab.daily.update'].search(
            [], order='write_date desc', limit=80)
        for s in recent:
            if s.approved_at:
                events.append((s.approved_at, 'checked' if s.state == 'checked'
                               else 'approved', s))
            if s.ops_approved_at:
                events.append((s.ops_approved_at,
                               'ops_checked' if s.ops_check_note else 'ops', s))
            if s.state == 'sent_back':
                events.append((s.write_date, 'back', s))
            if s.submitted_at:
                events.append((s.submitted_at, 'submitted', s))
        events.sort(key=lambda e: e[0], reverse=True)
        labels = {
            'submitted': self.env._('sent their day sheet'),
            'ops': self.env._('was approved by Operations'),
            'back': self.env._('was sent back'),
            'approved': self.env._('was fully approved'),
            'ops_checked': self.env._('was checked by Operations, issues noted'),
            'checked': self.env._('was checked by Marketing, issues noted'),
        }
        out = []
        for stamp, kind, sheet in events[:limit]:
            out.append({
                'kind': kind,
                'when': fields.Datetime.to_string(stamp),
                'ago': self._ago(stamp),
                'who': sheet.user_id.name,
                'date_label': format_date(self.env, sheet.date,
                                          date_format='d MMM'),
                'what': labels[kind],
                'sheet_id': sheet.id,
            })
        return out

    @api.model
    def _ago(self, stamp):
        delta = fields.Datetime.now() - stamp
        hours = delta.total_seconds() / 3600.0
        if hours < 1:
            return self.env._('%s min ago', int(delta.total_seconds() // 60))
        if hours < 24:
            return self.env._('%s h ago', int(hours))
        return self.env._('%s d ago', int(hours // 24))

    @api.model
    def _leaderboard(self, limit=5):
        """Consistency made visible: this month's clean days, streaks and the
        marketing desk's own scores, per executive."""
        month = fields.Date.context_today(self).replace(day=1)
        sheets = self.env['lab.daily.update'].search(
            [('date', '>=', month)])
        rows = []
        for user in sheets.mapped('user_id'):
            mine = sheets.filtered(lambda s, u=user: s.user_id == u)
            scored = mine.filtered('marketing_score')
            latest = mine.sorted('date')[-1:]
            streak = latest.streak + (
                1 if latest.state == 'approved' and latest.is_clean else 0)
            rows.append({
                'user_id': user.id, 'user': user.name,
                'clean': len(mine.filtered('is_clean')),
                'approved': len(mine.filtered(lambda s: s.state == 'approved')),
                'total': len(mine),
                'streak': streak,
                'score': round(sum(int(s.marketing_score) for s in scored)
                               / len(scored), 1) if scored else 0.0,
            })
        rows.sort(key=lambda r: (r['streak'], r['score'], r['clean']),
                  reverse=True)
        return rows[:limit]

    # ================================================================= ops desk
    @api.model
    def _pinned_day(self, day):
        """The day a desk is being read for, clamped to the calendar.

        All three desks travel the same way: the WINDOWED figures (the field
        board, momentum, the admin week) move to the pinned day, while the
        queues stay live - what is waiting to be signed is waiting now, and no
        choice of date can change that.
        """
        today = fields.Date.context_today(self)
        picked = fields.Date.to_date(day) if day else today
        return min(picked, today), today

    @api.model
    def _day_keys(self, anchor, today):
        """The three keys every desk's date picker reads."""
        return {
            'date': fields.Date.to_string(anchor),
            'date_label': format_date(self.env, anchor, date_format='EEEE, d MMMM'),
            'today': fields.Date.to_string(today),
            'is_today': anchor == today,
        }

    @api.model
    def get_ops_desk(self, day=None):
        today, _real_today = self._pinned_day(day)
        board = self._field_board(today)
        users, visits = board['users'], board['visits']
        rows, totals = board['rows'], board['totals']
        queue = self._queue('submitted')
        return {
            'currency_id': self.env.company.currency_id.id,
            **self._day_keys(today, _real_today),
            'queue': queue,
            'cover': self.env['lab.desk.cover']._cover_banner('ops'),
            'clean_waiting': len([q for q in queue if q['clean']]),
            'oldest_h': max([q['age_h'] for q in queue], default=0.0),
            'sla_h': self._sla_hours(),
            'trips_pending': self.env['lab.trip'].search_count(
                [('state', '=', 'closed')]),
            'sent_back': self.env['lab.daily.update'].search_count(
                [('state', '=', 'sent_back')]),
            'rows': rows,
            'totals': totals,
            'trend': self._fortnight(today),
            'alerts': self._ops_alerts(today, users, visits),
            'leaderboard': self._leaderboard(),
            'ticker': self._ticker(6),
            # Signable in place: the claims themselves, not just their count.
            'trip_rows': self._trip_rows(),
            'cash_held': self._cash_held(),
            'undelivered': self._undelivered(),
            # A flag that keeps coming back is the rule or the habit, never
            # the day - either way it is the manager's to fix, not to re-read.
            'habits': self._habit_radar(),
            'sla_forecast': self._sla_forecast(queue),
            'heatmap': self._discipline_heatmap(today),
            # A clinic going quiet is not only a marketing problem: it is the
            # route ops runs tomorrow, and the rescue visit is planned from
            # here. Same panel on all three desks. (client, 2026-09-02)
            'rescue': self._rescue_list(),
            'executives': [{'id': u.id, 'name': u.name} for u in users],
        }

    @api.model
    def _field_board(self, today):
        """The team, right now: one row per executive with their status
        ladder, work and money. Extracted so all THREE desks can carry it -
        the client asked for the live board and the fortnight grid on the
        marketing desk and the Command Center too. (client, 2026-09-01)"""
        Visit = self.env['lab.visit']
        visits = Visit.search([('date', '=', today), ('state', '!=', 'cancel')])
        users = self._executives(visits)
        trips = self.env['lab.trip'].search(
            [('date', '=', today), ('user_id', 'in', users.ids)])
        trip_by_user = {t.user_id.id: t for t in trips}
        # Attendance, read once for the whole desk. Without it the board can only see
        # visits, and everybody with nothing planned looks identical - see the status
        # ladder below. sudo: the desk reads the team's attendance without handing the
        # manager the HR application.
        emp_by_user = {
            e.user_id.id: e
            for e in self.env['hr.employee'].sudo().search([('user_id', 'in', users.ids)])
        }

        rows, totals = [], dict(planned=0, done=0, cases=0, value=0.0,
                                collected=0.0, unbanked=0.0, distance=0.0)
        # Every rupee not yet counted back by the office - pocket, float, or an
        # envelope on the desk - from the one reading My Day uses, so the board
        # and the phone never disagree. (client, 2026-09-18)
        positions = self.env['lab.cash.handover'].cash_position(users)
        for user in users:
            mine = visits.filtered(lambda v: v.user_id == user)
            done = mine.filtered(lambda v: v.state == 'done')
            here = mine.filtered(lambda v: v.state == 'open')[:1]
            trip = trip_by_user.get(user.id)
            unbanked = positions[user.id]['with_you']
            # A status nobody types (ported from the Control Tower).
            #
            # The ladder used to end at "planned nothing -> off today", which read the
            # visit list and nothing else. So an executive who had started their day
            # and was standing in the office with no calls booked was reported "off
            # today", as was one who had already finished, as was one HR had never
            # linked to an employee and who therefore CANNOT start. One label, four
            # different mornings, and a manager reading the board could not tell which.
            # Attendance is what separates them. (client, 2026-09-01)
            employee = emp_by_user.get(user.id)
            on_duty = bool(employee) and employee.attendance_state == 'checked_in'
            # Did they clock in TODAY? attendance_state cannot answer it - it reads
            # 'checked_out' both for somebody who put in a full day and for somebody
            # who never arrived - and hours_today cannot either, because it is 0.00
            # until enough time has passed, so a day just started and ended reads as
            # never worked. The check-in stamp is the fact itself.
            last_in = employee.last_check_in if employee else False
            attended_today = bool(last_in) and fields.Datetime.context_timestamp(
                self, last_in).date() == today
            #
            # Attendance gates the WHOLE ladder, not just its tail. Reading the visit
            # list first says "day finished" the moment the last booked call is made -
            # of somebody still clocked in, who may well have more work coming - and
            # "not started" of somebody who started their day an hour ago but has not
            # reached a clinic yet. Finishing your calls is not ending your day, and
            # the board must not claim it is. (client, 2026-09-01)
            moving = bool(done) or bool(trip and trip.state in ('open', 'closed', 'approved'))
            all_done = bool(mine) and bool(done) and len(done) == len(mine)
            if here:
                status = 'at_clinic'
            elif on_duty:
                # Still clocked in: whatever the visits say, the day is not over.
                if all_done:
                    status = 'visits_done'
                elif moving:
                    status = 'moving'
                else:
                    status = 'on_duty'
            elif attended_today:
                # Clocked in and back out again - the day really is finished.
                status = 'finished'
            elif not employee:
                status = 'no_setup'
            elif all_done:
                status = 'finished'
            elif moving:
                status = 'moving'
            elif mine:
                status = 'idle'
            else:
                status = 'off'
            rows.append({
                'id': user.id, 'name': user.name, 'status': status,
                'phone': user.partner_id.phone or '',
                'planned': len(mine), 'done': len(done),
                'at_clinic': here.partner_id.display_name if here else '',
                'at_clinic_id': here.id if here else False,
                'since': fields.Datetime.to_string(here.check_in)
                         if here and here.check_in else '',
                'cases': sum(done.mapped('order_count')),
                'value': sum(done.mapped('order_value')),
                'collected': sum(done.mapped('collected')),
                'unbanked': unbanked,
                'cash_in_hand': unbanked,
                'cash_pending': positions[user.id]['pending'],
                'distance': trip.distance if trip else 0.0,
                'trip_state': trip.state if trip else 'none',
                'far': len(done.filtered(lambda v: v.gps_state == 'far')),
            })
            totals['planned'] += len(mine)
            totals['done'] += len(done)
            totals['cases'] += sum(done.mapped('order_count'))
            totals['value'] += sum(done.mapped('order_value'))
            totals['collected'] += sum(done.mapped('collected'))
            totals['unbanked'] += unbanked
            totals['distance'] += trip.distance if trip else 0.0

        return {'users': users, 'visits': visits,
                'rows': rows, 'totals': totals}

    @api.model
    def _ops_alerts(self, today, users, visits):
        """Only what a person must decide about today. (ported, minus the
        marketing-side rescue list, which now lives on the marketing desk)"""
        out = []
        done = visits.filtered(lambda v: v.state == 'done')
        unbanked = done.filtered(
            lambda v: v.pay_mode == 'cash' and v.collected and not v.cash_banked)
        if unbanked:
            out.append({
                'key': 'unbanked', 'level': 'danger', 'icon': 'fa-money',
                'title': self.env._('Cash not deposited'),
                'detail': self.env._(
                    '%(count)s visit(s) collected cash that is not yet '
                    'deposited.', count=len(unbanked)),
                'model': 'lab.visit', 'ids': unbanked.ids,
                'label': self.env._('Show visits')})
        # Cash on the road past the policy: in a float, so not "undeposited",
        # but with somebody for longer - or more - than the lab allows. The
        # thing to decide today is to ask for it. (client, 2026-09-18)
        Handover = self.env['lab.cash.handover']
        positions = Handover.cash_position(users)
        policy = Handover._cash_policy()
        overdue = [u for u in users if Handover._is_overdue(positions[u.id], policy)]
        if overdue:
            floats = [positions[u.id]['allocation_id'] for u in overdue
                      if positions[u.id]['allocation_id']]
            out.append({
                'key': 'cash_overdue', 'level': 'danger', 'icon': 'fa-money',
                'title': self.env._('Cash on the road past the policy'),
                'detail': self.env._(
                    '%(who)s: %(amount)s of collected cash held longer than '
                    '%(days)s day(s) or above %(ceiling)s. Ask for the envelope.',
                    who=', '.join(u.name for u in overdue),
                    amount=Handover._money(sum(positions[u.id]['available'] for u in overdue)),
                    days=policy[0], ceiling=Handover._money(policy[1])),
                'model': 'petty.cash.allocation', 'ids': floats,
                'label': self.env._('Show the floats')})
        waiting = Handover.sudo().search([('state', '=', 'declared')])
        if waiting:
            out.append({
                'key': 'handovers', 'level': 'warning', 'icon': 'fa-inbox',
                'title': self.env._('Cash waiting to be counted'),
                'detail': self.env._(
                    '%(count)s handover(s), %(amount)s in envelopes on the desk. '
                    'Count each against its code and confirm.',
                    count=len(waiting),
                    amount=self.env['lab.cash.handover']._money(
                        sum(waiting.mapped('amount')))),
                'model': 'lab.cash.handover', 'ids': waiting.ids,
                'label': self.env._('Count them')})
        far = done.filtered(lambda v: v.gps_state == 'far')
        if far:
            out.append({
                'key': 'far', 'level': 'warning', 'icon': 'fa-map-marker',
                'title': self.env._('GPS far from the clinic'),
                'detail': self.env._('%(count)s visit(s) today. Each has a written reason.',
                                     count=len(far)),
                'model': 'lab.visit', 'ids': far.ids,
                'label': self.env._('Review')})
        idle = users.filtered(
            lambda u: not visits.filtered(lambda v: v.user_id == u))
        if idle:
            out.append({
                'key': 'idle', 'level': 'info', 'icon': 'fa-calendar-o',
                'title': self.env._('Nothing planned today'),
                'detail': ', '.join(idle.mapped('name')[:6]),
                'model': 'lab.beat', 'ids': [],
                'label': self.env._('Plan a beat')})
        return out

    # =========================================================== marketing desk
    @api.model
    def get_marketing_desk(self, day=None):
        today, real_today = self._pinned_day(day)
        return {
            'currency_id': self.env.company.currency_id.id,
            **self._day_keys(today, real_today),
            'queue': self._queue(('ops_approved', 'ops_checked')),
            'cover': self.env['lab.desk.cover']._cover_banner('marketing'),
            'sla_h': self._sla_hours(),
            'new_clinics': self.env['lab.new.clinics'].count_new_clinics(),
            'momentum': self._route_momentum(today),
            'rescue': self._rescue_list(),
            'outcomes': self._outcome_mix(today),
            'months': self._month_trend(today),
            'leaderboard': self._leaderboard(),
            'ticker': self._ticker(6),
            'conversion': self._new_door_conversion(today),
            'improving': self._score_momentum(today),
            # For the rescue planner: who a visit can be handed to.
            'executives': [{'id': u.id, 'name': u.name}
                           for u in self._executives()],
            'targets': self._targets_snapshot(today),
            'undelivered': self._undelivered(),
            # The client asked for these on every desk (2026-09-01): the live
            # field board and the fortnight-per-person grid.
            **{k: v for k, v in self._field_board(today).items()
               if k in ('rows', 'totals')},
            'heatmap': self._discipline_heatmap(today),
        }

    @api.model
    def _route_momentum(self, today, days=14, top=6):
        """Each route's last fortnight against the one before it, with the
        daily points for a sparkline. Where to push is a comparison, not a
        total."""
        start_prev = today - timedelta(days=2 * days - 1)
        start_cur = today - timedelta(days=days - 1)
        # Bounded above as well as below. Read for today the bound is inert,
        # but pinned to a past day a visit from AFTER it would land past the
        # end of the sparkline (IndexError) - caught by the day-travel tests.
        visits = self.env['lab.visit'].sudo().search_read(
            [('date', '>=', start_prev), ('date', '<=', today),
             ('state', '=', 'done')],
            ['date', 'order_value', 'partner_id'])
        team_of = {}
        partners = self.env['res.partner'].sudo().browse(
            {v['partner_id'][0] for v in visits if v['partner_id']})
        for p in partners:
            team_of[p.id] = p.team_id or p.commercial_partner_id.team_id
        routes = {}
        for v in visits:
            team = team_of.get(v['partner_id'] and v['partner_id'][0])
            if not team:
                continue
            row = routes.setdefault(team.id, {
                'id': team.id, 'name': team.name, 'current': 0.0,
                'previous': 0.0, 'points': [0.0] * days})
            if v['date'] >= start_cur:
                row['current'] += v['order_value'] or 0.0
                row['points'][(v['date'] - start_cur).days] += v['order_value'] or 0.0
            else:
                row['previous'] += v['order_value'] or 0.0
        out = sorted(routes.values(), key=lambda r: r['current'], reverse=True)[:top]
        for row in out:
            row['delta_pct'] = round(
                (row['current'] - row['previous']) * 100.0 / row['previous'], 1) \
                if row['previous'] else (100.0 if row['current'] else 0.0)
        return out

    @api.model
    def _rescue_list(self, limit=8, debits=None):
        """The clinics slipping away, biggest money first — the marketing
        desk's own rescue radar. (moved here from the tower's alert strip)"""
        rescue = self.env['lab.coverage'].search(
            [('order_state', 'in', ('stopped', 'slipping'))],
            order='value_drop desc', limit=limit)
        return self._with_money_facts([{
            'id': c.id, 'partner_id': c.partner_id.id,
            'name': c.partner_id.display_name,
            'route': c.partner_id.team_id.name or '',
            'state': c.order_state,
            'drop': c.value_drop,
            'drop_label': formatLang(self.env, c.value_drop,
                                     currency_obj=self.env.company.currency_id),
        } for c in rescue], key='partner_id', debits=debits)

    @api.model
    def _outcome_mix(self, today, days=7):
        """What the week's visits actually produced, for the donut."""
        rows = self.env['lab.visit']._read_group(
            [('date', '>', today - timedelta(days=days)), ('date', '<=', today),
             ('state', '=', 'done')],
            ['outcome'], ['__count'])
        labels = dict(self.env['lab.visit']._fields['outcome'].selection)
        total = sum(count for _o, count in rows) or 1
        return [{
            'key': outcome or 'none',
            'label': labels.get(outcome, self.env._('No outcome')),
            'count': count,
            'pct': round(count * 100.0 / total, 1),
        } for outcome, count in sorted(rows, key=lambda r: -r[1])]

    @api.model
    def _month_trend(self, today=None):
        """Six months of doors and value, ending on the pinned day.
        (ported from Field Health)"""
        from dateutil.relativedelta import relativedelta
        today = today or fields.Date.context_today(self)
        first = today.replace(day=1) - relativedelta(months=TREND_MONTHS - 1)
        visits = self.env['lab.visit'].sudo().search(
            [('date', '>=', first), ('date', '<=', today),
             ('state', '=', 'done')])
        rows = []
        for i in range(TREND_MONTHS):
            month = first + relativedelta(months=i)
            nxt = month + relativedelta(months=1)
            mine = visits.filtered(lambda v: month <= v.date < nxt)
            rows.append({'label': month.strftime('%b'),
                         'visits': len(mine),
                         'cases': sum(mine.mapped('order_count')),
                         'value': sum(mine.mapped('order_value'))})
        return rows

    # ============================================ "see all" behind the tiles
    # Every list panel shows a top handful; the reader kept asking what the
    # rest of it was. Each kind resolves HERE rather than in the client so the
    # list is the panel's own set - the countback rows, the live delivery set,
    # the neglected clinics - and not a lookalike domain that drifts from it.
    # (client, 2026-09-02)
    @api.model
    def _see_all_specs(self, today):
        Sheet = self.env['lab.daily.update']
        return {
            'rescue': lambda: (
                'lab.coverage', self.env._('Clinics going quiet'),
                [('order_state', 'in', ('stopped', 'slipping'))]),
            'unvisited': lambda: (
                'res.partner',
                self.env._('Not visited in %s days',
                           self._unvisited_doctors(today)['quiet_days']),
                [('id', 'in', self._unvisited_doctors(today)['ids'])]),
            'undelivered': lambda: (
                'lab.delivery', self.env._('Undelivered dispatches'),
                [('id', 'in', self.env['lab.delivery']._live_ids())]),
            'queue': lambda: (
                'lab.daily.update', self.env._('Day sheets waiting'),
                [('state', 'in', ('submitted', 'ops_approved', 'ops_checked'))]),
            'pending': lambda: (
                'lab.daily.update', self.env._('Unapproved activities'),
                [('state', 'in', ('draft', 'submitted', 'ops_approved',
                                  'ops_checked', 'sent_back'))]),
            'trips': lambda: (
                'lab.trip', self.env._('Travel claims waiting'),
                [('state', '=', 'closed')]),
            'cash': lambda: (
                'lab.visit', self.env._('Cash still held'),
                [('pay_mode', '=', 'cash'), ('collected', '>', 0),
                 ('cash_banked', '=', False), ('state', '=', 'done')]),
            'sent_back': lambda: (
                'lab.daily.update', self.env._('Sent back, being fixed'),
                [('state', '=', 'sent_back')]),
            'visits_today': lambda: (
                'lab.visit', self.env._('Visits today'),
                [('date', '=', today), ('state', '!=', 'cancel')]),
            'new_clinics': lambda: (
                'res.partner', self.env._('New clinics this month'),
                [('is_clinic', '=', True),
                 ('create_date', '>=', fields.Datetime.to_datetime(
                     today.replace(day=1)))]),
            'sheets_month': lambda: (
                'lab.daily.update', self.env._('Day sheets this month'),
                [('date', '>=', today.replace(day=1)), ('date', '<=', today)]),
        }

    @api.model
    def action_see_all(self, kind, day=None):
        """The whole set behind one panel, as a list action.

        Receivables are deliberately NOT here: their rows are the Collections
        countback and only that module can open them without inventing a
        second definition of what is owed."""
        today, _real = self._pinned_day(day)
        spec = self._see_all_specs(today).get(kind)
        if not spec:
            raise UserError(self.env._("There is no full list for %s.", kind))
        model, name, domain = spec()
        if model not in self.env:
            raise UserError(self.env._("%s is not installed here.", model))
        # search_count with the reader's own rules: the button says how many
        # they will actually see, never how many exist.
        count = self.env[model].search_count(domain)
        return {
            'type': 'ir.actions.act_window',
            'name': '%s (%s)' % (name, count),
            'res_model': model,
            # `views` spelled out, not view_mode alone: an action handed
            # straight to doAction() never passes through the loader that
            # builds it, and the client dies on the missing key. Caught by
            # clicking the first See all. (2026-09-02)
            'view_mode': 'list,form',
            'views': [(False, 'list'), (False, 'form')],
            'domain': domain,
            'context': {'create': False},
        }

    @api.model
    def action_see_all_receivables(self):
        """Open receivable, through the countback that owns the definition."""
        if 'lab.collection.performance' not in self.env:
            raise UserError(self.env._("The Collections module is not installed."))
        return self.env['lab.collection.performance'].action_drill('overdue')

    # ==================================================== ops desk: signals
    @api.model
    def _trip_rows(self, limit=8):
        """The travel claims waiting, whole enough to sign without opening.

        The desk showed their COUNT and made the manager leave for a list view
        to do the actual work. Kilometres, vehicle and what the claim is worth
        are the entire decision, so they belong on the desk; the signature
        still goes through lab.trip's own guarded action_approve.
        """
        trips = self.env['lab.trip'].search(
            [('state', '=', 'closed')], order='date, id', limit=limit)
        vehicles = dict(trips._fields['vehicle'].selection)
        return [{
            'id': t.id, 'user': t.user_id.name,
            'date_label': format_date(self.env, t.date, date_format='EEE d MMM'),
            'km': round(t.distance, 1),
            'vehicle': vehicles.get(t.vehicle, t.vehicle),
            'amount': t.amount,
        } for t in trips]

    FLAG_LABELS = [
        ('flag_gps', 'gps', 'GPS far from clinic'),
        ('flag_open_work', 'open', 'work left open'),
        ('flag_zero_visits', 'zero', 'no visits recorded'),
        ('flag_km_outlier', 'km', 'unusual distance'),
        ('flag_attendance', 'attendance', 'attendance not closed'),
    ]

    @api.model
    def _habit_radar(self, lookback=5, threshold=3):
        """The same flag on most of an executive's recent sheets.

        One flagged day is a day; the same flag three times in five is the
        radius, the phone, or the habit - and reading each sheet separately
        hides exactly that. Sent to the desk so the conversation happens once,
        about the pattern, instead of five times about the days.
        """
        Sheet = self.env['lab.daily.update'].sudo()
        out = []
        for user in self._executives():
            last = Sheet.search([('user_id', '=', user.id)],
                                order='date desc', limit=lookback)
            if len(last) < threshold:
                continue
            for field, key, label in self.FLAG_LABELS:
                hits = last.filtered(field)
                if len(hits) >= threshold:
                    out.append({
                        'user_id': user.id, 'user': user.name,
                        'flag': key, 'label': label,
                        'count': len(hits), 'of': len(last),
                        'sheet_ids': hits.ids,
                    })
        return sorted(out, key=lambda r: r['count'], reverse=True)

    @api.model
    def _sla_forecast(self, queue, horizon_h=12):
        """What breaches next if nothing is signed.

        The overdue count says what already went wrong; a manager leaving for
        the evening needs what goes wrong BEFORE morning. Read off the queue
        already computed, so it costs nothing.
        """
        soon = [q for q in queue
                if not q['overdue'] and q['age_h'] + horizon_h > self._sla_hours()]
        return {'soon': len(soon), 'horizon_h': horizon_h,
                'ids': [q['id'] for q in soon]}

    @api.model
    def _discipline_heatmap(self, today, days=14):
        """A fortnight of day sheets as one glance: who is consistently clean,
        who keeps getting flagged, where the holes are. The grid says in one
        row what a month of queue-reading says slowly."""
        start = today - timedelta(days=days - 1)
        sheets = self.env['lab.daily.update'].sudo().search(
            [('date', '>=', start), ('date', '<=', today)])
        by_user_day = {(sh.user_id.id, sh.date): sh for sh in sheets}
        span = [start + timedelta(days=i) for i in range(days)]
        rows = []
        for user in self._executives():
            cells, clean = [], 0
            for when in span:
                sheet = by_user_day.get((user.id, when))
                if sheet is None:
                    code = 'off'
                elif sheet.state == 'sent_back':
                    code = 'back'
                elif sheet.is_clean:
                    code = 'clean'
                    clean += 1
                else:
                    code = 'flag'
                cells.append({'day': fields.Date.to_string(when),
                              'code': code,
                              'weekend': when.weekday() == 6,
                              'sheet_id': sheet.id if sheet else False})
            rows.append({'user_id': user.id, 'user': user.name,
                         'clean': clean, 'cells': cells})
        # The row that needs attention floats up: fewest clean days first.
        rows.sort(key=lambda r: r['clean'])
        return {'days': [format_date(self.env, d, date_format='d')
                         for d in span],
                'rows': rows}

    @api.model
    def _cash_held(self, limit=6):
        """Cash collected and never deposited, per person, across EVERY day.

        The today-only alert misses the real risk: money that has been riding
        in a pocket since Tuesday. Per person with the oldest day counted, so
        "ASLAM holds 12,400 from 3 days ago" is one line, not an audit.
        """
        # From the one reading My Day uses (pocket + float - counted back), so
        # the board says what the phone says. Before, this read visits alone:
        # cash already in a float still showed as riding in a pocket, for ever,
        # because nothing ever took it out. (client, 2026-09-18)
        group = self.env.ref('lab_fieldwork.group_fieldwork_executive',
                             raise_if_not_found=False)
        users = group.sudo().all_user_ids.filtered('active') if group \
            else self.env['res.users']
        Handover = self.env['lab.cash.handover']
        positions = Handover.cash_position(users)
        policy = Handover._cash_policy()
        pocket = defaultdict(list)
        for visit in self.env['lab.visit'].sudo().search(
                [('user_id', 'in', users.ids), ('pay_mode', '=', 'cash'),
                 ('collected', '>', 0), ('cash_banked', '=', False),
                 ('state', '=', 'done')]):
            pocket[visit.user_id.id].append(visit.id)
        out = []
        for user in users:
            row = positions[user.id]
            if row['with_you'] <= 0:
                continue
            out.append({
                'user_id': user.id, 'user': user.name,
                'partner_id': user.partner_id.id,
                'allocation_id': row['allocation_id'],
                'amount': row['with_you'], 'pending': row['pending'],
                'pending_count': row['pending_count'], 'days': row['days'],
                'overdue': Handover._is_overdue(row, policy),
                'ids': pocket.get(user.id, [])})
        return sorted(out, key=lambda r: -r['amount'])[:limit]

    # ============================================= marketing desk: signals
    @api.model
    def _new_door_conversion(self, today, days=60):
        """Did the new doors come back?

        "65 new clinics" is applause; the second order is the business. Of the
        clinics opened in the window: how many ever ordered, and how many
        ordered AGAIN - the number that says whether opening doors is working.
        """
        start = fields.Datetime.to_datetime(today - timedelta(days=days))
        end = fields.Datetime.to_datetime(today + timedelta(days=1))
        partners = self.env['res.partner'].sudo().search(
            [('team_id', '!=', False), ('parent_id', '=', False),
             ('create_date', '>=', start), ('create_date', '<', end)])
        opened = len(partners)
        ordered = repeat = 0
        if partners:
            for _partner, count in self.env['sale.order'].sudo()._read_group(
                    [('partner_id', 'in', partners.ids), ('state', '=', 'sale')],
                    ['partner_id'], ['__count']):
                ordered += 1
                if count >= 2:
                    repeat += 1
        return {
            'days': days, 'opened': opened, 'ordered': ordered, 'repeat': repeat,
            'repeat_pct': round(repeat * 100.0 / opened, 1) if opened else 0.0,
        }

    @api.model
    def _score_momentum(self, today, days=14, limit=8):
        """Whose activity score is moving, and which way.

        The score is given one day at a time and read never. Averaged per
        fortnight and set against the fortnight before, it becomes the one
        thing a score is for: seeing who is improving before the numbers do.
        """
        Sheet = self.env['lab.daily.update'].sudo()
        start_prev = today - timedelta(days=2 * days - 1)
        start_cur = today - timedelta(days=days - 1)
        sheets = Sheet.search([
            ('date', '>=', start_prev), ('date', '<=', today),
            ('marketing_score', '!=', False)])
        cur, prev = {}, {}
        for sheet in sheets:
            bucket = cur if sheet.date >= start_cur else prev
            bucket.setdefault(sheet.user_id, []).append(int(sheet.marketing_score))
        rows = []
        for user, scores in cur.items():
            now = sum(scores) / len(scores)
            before_scores = prev.get(user)
            before = (sum(before_scores) / len(before_scores)
                      if before_scores else None)
            rows.append({
                'user_id': user.id, 'user': user.name,
                'now': round(now, 1),
                'before': round(before, 1) if before is not None else None,
                'delta': round(now - before, 1) if before is not None else None,
            })
        # Movement first, then the unrated-last-fortnight newcomers.
        rows.sort(key=lambda r: (r['delta'] is None, -(r['delta'] or 0)))
        return rows[:limit]

    @api.model
    def _targets_snapshot(self, today, limit=8):
        """This month's running targets, best progress first.

        The goals already exist under Planning; putting the progress on the
        desk is what makes them a conversation in week two instead of a
        surprise in week five.
        """
        month = today.replace(day=1)
        # Brought up to date first: the stored figures do not hear about a
        # visit closing, and this list is ORDERED by them.
        self.env['lab.target']._refresh_month(month)
        targets = self.env['lab.target'].sudo().search(
            [('month', '=', month), ('state', '=', 'open')],
            order='progress desc', limit=limit)
        return [{
            'id': t.id, 'user': t.user_id.name,
            'progress': min(100, round(t.progress)),
            'progress_label': round(t.progress),
            'done_value': t.done_value, 'goal_value': t.goal_value,
        } for t in targets]

    @api.model
    def _pending_matrix(self):
        """Every unapproved activity, per person: the one table the client
        asked for by name. Who has days waiting for Operations, waiting for
        Marketing, or sent back and stuck - with the oldest wait in hours, so
        the worst row reads first and every cell opens its sheets."""
        Sheet = self.env['lab.daily.update'].sudo()
        pending = Sheet.search([('state', 'in',
                                 ('submitted', 'ops_approved', 'ops_checked',
                                  'sent_back'))])
        now = fields.Datetime.now()
        rows = {}
        for sheet in pending:
            row = rows.setdefault(sheet.user_id, {
                'user_id': sheet.user_id.id, 'user': sheet.user_id.name,
                'ops': [], 'mkt': [], 'back': [], 'oldest_h': 0.0})
            key = {'submitted': 'ops', 'ops_approved': 'mkt', 'ops_checked': 'mkt',
                   'sent_back': 'back'}[sheet.state]
            row[key].append(sheet.id)
            stamp = sheet.submitted_at or sheet.create_date
            row['oldest_h'] = max(row['oldest_h'],
                                  (now - stamp).total_seconds() / 3600.0)
        out = []
        for row in rows.values():
            out.append({**row, 'ops': row['ops'], 'mkt': row['mkt'],
                        'back': row['back'],
                        'oldest_h': round(row['oldest_h'], 1)})
        out.sort(key=lambda r: -r['oldest_h'])
        return {
            'rows': out,
            'total': len(pending),
            'sla_h': self._sla_hours(),
        }

    @api.model
    def _week_sales_snapshot(self, today):
        """This week's business in four numbers and its best sellers - the
        Command Center watches the money, not only the paperwork."""
        monday = today - timedelta(days=today.weekday())
        Order = self.env['sale.order'].sudo()
        domain = [('state', '=', 'sale'),
                  ('date_order', '>=', fields.Datetime.to_datetime(monday)),
                  ('date_order', '<', fields.Datetime.to_datetime(
                      today + timedelta(days=1)))]
        [[orders, value]] = Order._read_group(domain, [],
                                              ['__count', 'amount_total:sum'])
        value = value or 0.0
        sheets = self.env['lab.daily.update'].sudo().search(
            [('date', '>=', monday), ('date', '<=', today)])
        collected = sum(sheets.mapped('collected'))
        # The same week from ACCOUNTING: posted credits on the receivable.
        # The field figure is what executives say they took; this is what the
        # ledger actually received. A gap between them is money that has not
        # reached a journal yet, which is the control a lab wants - and it is
        # why every money figure on these desks now names its source.
        # (client, 2026-09-01)
        banked = 0.0
        receivable = self.env['account.account'].sudo().search(
            [('account_type', '=', 'asset_receivable')])
        if receivable:
            self.env.cr.execute("""
                SELECT COALESCE(SUM(l.credit), 0)
                  FROM account_move_line l
                 WHERE l.parent_state = 'posted' AND l.company_id = %s
                   AND l.account_id IN %s
                   AND l.date >= %s AND l.date <= %s
            """, (self.env.company.id, tuple(receivable.ids), monday, today))
            banked = float(self.env.cr.fetchone()[0] or 0.0)
        top = [{'name': user.name, 'orders': count, 'value': amount or 0.0}
               for user, count, amount in Order._read_group(
                   domain + [('user_id', '!=', False)], ['user_id'],
                   ['__count', 'amount_total:sum'],
                   order='amount_total:sum desc', limit=5)]
        return {
            'orders': orders, 'value': value, 'collected': collected,
            'banked': banked,
            'ratio': round(banked * 100.0 / value) if value else 0,
            'top': top,
        }

    @api.model
    def _targets_rollup(self, today):
        """The month's targets as one line plus the people falling behind -
        enough for the administrator to know who needs the conversation."""
        month = today.replace(day=1)
        self.env['lab.target']._refresh_month(month)
        targets = self.env['lab.target'].sudo().search(
            [('month', '=', month), ('state', '=', 'open')])
        if not targets:
            return {'count': 0, 'avg': 0, 'behind': []}
        # Where the month SHOULD be by today, pro rata.
        expected = today.day * 100.0 / 30.0
        behind = targets.filtered(
            lambda t: t.goal_value and t.progress < expected * 0.6)
        return {
            'count': len(targets),
            'avg': round(sum(targets.mapped('progress')) / len(targets)),
            'expected': round(expected),
            'behind': [{'id': t.id, 'user': t.user_id.name,
                        'progress': round(t.progress)}
                       for t in behind.sorted(lambda t: t.progress)][:5],
        }

    @api.model
    def _clinic_money_facts(self, partner_ids, debits=None):
        """Per clinic: what they owe now, when they last ordered, when they
        last paid - the three numbers every clinic row on these desks was
        missing. (client, 2026-08-31)

        Outstanding comes from the countback (its _open_debits narrows to a
        partner set and returns exactly the full run's rows for them - never
        residuals on this ledger). "Last paid" is the last posted CREDIT on a
        receivable account: receipts here are plain journal entries, so
        account.payment would answer almost never.
        """
        ids = [pid for pid in partner_ids if pid]
        facts = {pid: {'outstanding': 0.0, 'last_so': '', 'last_pay': ''}
                 for pid in ids}
        if not ids:
            return facts
        if debits is None and 'lab.collection.performance' in self.env:
            debits = self.env['lab.collection.performance'].sudo(
            )._open_debits(self.env.company, partner_ids=ids)
        for debit in debits or []:
            if debit['partner_id'] in facts:
                facts[debit['partner_id']]['outstanding'] += debit['open']
        for partner, when in self.env['sale.order'].sudo()._read_group(
                [('partner_id', 'in', ids), ('state', '=', 'sale')],
                ['partner_id'], ['date_order:max']):
            facts[partner.id]['last_so'] = format_date(
                self.env, fields.Date.to_date(when), date_format='d MMM yy')
        receivable = self.env['account.account'].sudo().search(
            [('account_type', '=', 'asset_receivable')])
        for partner, when in self.env['account.move.line'].sudo()._read_group(
                [('partner_id', 'in', ids), ('parent_state', '=', 'posted'),
                 ('account_id', 'in', receivable.ids), ('credit', '>', 0),
                 # A credit note credits the receivable too, but nobody calls
                 # it a payment - and on the debtor list "last paid" answers
                 # "when did money last arrive". (2026-09-01)
                 ('move_id.move_type', '!=', 'out_refund')],
                ['partner_id'], ['date:max']):
            facts[partner.id]['last_pay'] = format_date(
                self.env, when, date_format='d MMM yy')
        return facts

    @api.model
    def _undelivered(self, limit=6):
        """Work still riding: dispatches not yet handed over, oldest first.
        On every desk - an undelivered case is operations\' problem, the
        clinic relationship\'s problem, and the administrator\'s problem all
        at once. (client, 2026-08-31)"""
        if 'lab.delivery' not in self.env:
            return {'total': 0, 'rows': []}
        Delivery = self.env['lab.delivery'].sudo()
        # The LIVE set, not every open row: all 17,898 dispatches on this
        # database are draft and 97.9% of them ride on orders that have
        # already shipped, so "still open" counted the lab's whole history.
        # (2026-09-02, same lesson as the flow board)
        live_ids = Delivery._live_ids()
        domain = [('id', 'in', live_ids)]
        today = fields.Date.context_today(self)
        rows = []
        state_labels = dict(Delivery._fields['state']._description_selection(self.env))
        for d in Delivery.search(domain, order='create_date', limit=limit):
            age = (fields.Datetime.now() - d.create_date).days
            rows.append({
                'id': d.id,
                'clinic': d.partner_id.display_name if d.partner_id else '',
                'who': d.executive_id.name or self.env._('unassigned'),
                'state': state_labels.get(d.state),
                'age_d': age,
            })
        return {'total': len(live_ids), 'rows': rows}

    @api.model
    def _route_week_visits(self, today):
        """This week\'s visits, grouped by route and by person - the two ways
        the client counts the walking."""
        monday = today - timedelta(days=today.weekday())
        visits = self.env['lab.visit'].sudo().search(
            [('date', '>=', monday), ('date', '<=', today),
             ('state', '=', 'done')])
        by_route, by_person = {}, {}
        for visit in visits:
            team = visit.partner_id.team_id
            if team:
                by_route[team.name] = by_route.get(team.name, 0) + 1
            name = visit.user_id.name
            by_person[name] = by_person.get(name, 0) + 1
        rank = lambda d: sorted(({'name': k, 'visits': v} for k, v in d.items()),
                                key=lambda r: -r['visits'])[:8]
        return {'routes': rank(by_route), 'people': rank(by_person),
                'total': len(visits)}

    @api.model
    def _unvisited_doctors(self, today, quiet_days=30, active_days=120, limit=8, debits=None):
        """Doctors nobody has stood in front of for a month.

        Scoped to clinics that gave an order in the last %s days - on a list
        of every routed clinic, "not visited" would just be the phone book.
        Ranked by what their recent orders were worth: the most valuable
        neglected door first.""" % 120
        Order = self.env['sale.order'].sudo()
        cutoff_active = fields.Datetime.to_datetime(
            today - timedelta(days=active_days))
        value_by_partner = {
            partner: value or 0.0
            for partner, value in Order._read_group(
                [('state', '=', 'sale'), ('date_order', '>=', cutoff_active),
                 ('partner_id', '!=', False)],
                ['partner_id'], ['amount_total:sum'])}
        if not value_by_partner:
            return {'total': 0, 'rows': [], 'quiet_days': quiet_days}
        visited_ids = set(self.env['lab.visit'].sudo().search(
            [('date', '>=', today - timedelta(days=quiet_days))]
        ).mapped('partner_id').ids)
        neglected = [(partner, value)
                     for partner, value in value_by_partner.items()
                     if partner.id not in visited_ids]
        # Facts for the WHOLE neglected set, not just the shown rows: the
        # panel's headline is the total receivable sitting behind unvisited
        # doors, and a total of eight rows would be a lie with a caption.
        facts = self._clinic_money_facts([p.id for p, _v in neglected],
                                         debits=debits)
        # The doors that owe the most float up - the sale figure is gone from
        # the display (client: "total sale not needed"), so it no longer
        # decides the order either.
        neglected.sort(key=lambda pv: (-facts[pv[0].id]['outstanding'], -pv[1]))
        rows = []
        for partner, _value in neglected[:limit]:
            rows.append({'id': partner.id, 'name': partner.display_name,
                         'route': partner.team_id.name or '',
                         **facts[partner.id]})
        return {
            'total': len(neglected),
            'quiet_days': quiet_days,
            'ids': [partner.id for partner, _v in neglected],
            'outstanding_total': round(sum(
                f['outstanding'] for f in facts.values()), 2),
            'rows': rows,
        }

    @api.model
    def _with_money_facts(self, rows, key='id', debits=None):
        facts = self._clinic_money_facts([r[key] for r in rows],
                                         debits=debits)
        for row in rows:
            row.update(facts.get(row[key],
                                 {'outstanding': 0.0, 'last_so': '',
                                  'last_pay': ''}))
        return rows

    @api.model
    def _open_receivables(self, overdue_days=30, top=6, debits=None):
        """Who still owes, 30 days and older - THROUGH THE COUNTBACK.

        This ledger reconciles nothing, so residuals lie (they once put 10.3M
        and 6.3M on two boards at the same time). lab.collection.performance
        is the one published answer; if that module is absent the panel simply
        does not render."""
        if 'lab.collection.performance' not in self.env:
            return False
        Perf = self.env['lab.collection.performance']
        # The debtors are ranked and AMOUNTED on the 30+ day slice alone.
        # outstanding_summary's own top_debtors sums a clinic's ENTIRE open,
        # so a clinic that paid its old bills two weeks ago and merely has
        # fresh invoices was shown under a "30+ days" heading with the fresh
        # money in its figure - the client caught the smell ("last payment
        # was on 13th Aug, why is it in this list"). The membership was right
        # (their OLD balance survived the payment); the number was not.
        # (2026-09-01)
        cutoff = getattr(Perf, 'OVERDUE_DAYS', overdue_days)
        if debits is None:
            debits = Perf.sudo()._open_debits(self.env.company)
        # Totals from the same rows the debtor list is built from - one
        # ledger scan answers everything this panel says. The load ran the
        # countback FIVE times before this (3.3 s a load); it runs once now.
        # (2026-09-01)
        total_open = sum(d['open'] for d in debits)
        overdue = round(sum(d['open'] for d in debits
                            if d['days'] > cutoff), 2)
        per_partner = {}
        for debit in debits:
            if debit['days'] > cutoff:
                per_partner[debit['partner_id']] = \
                    per_partner.get(debit['partner_id'], 0.0) + debit['open']
        ranked = sorted(per_partner.items(), key=lambda kv: -kv[1])[:top]
        names = {p.id: p.display_name for p in self.env['res.partner'].sudo()
                 .browse([pid for pid, _v in ranked])}
        debtors = [{'id': pid, 'name': names.get(pid, ''),
                    'open': round(value, 2)} for pid, value in ranked]
        return {
            'overdue': overdue,
            'overdue_pct': round(overdue / total_open * 100, 1)
                           if total_open else 0.0,
            'count': len(debits),
            'overdue_days': cutoff,
            'debtors': self._with_money_facts(debtors, debits=debits),
        }

    # ================================================= admin desk: signals
    @api.model
    def _flag_board(self, today, window_days=90):
        """Days the desks raised to the administrator, and what they are about.

        The list answers "what is waiting on me". The two breakdowns answer the
        question a list cannot: whether the field force has one bad day or one
        bad habit. A flag kind counted across ninety days is the difference
        between answering flags and removing what causes them.
        (client, 2026-09-05)
        """
        Sheet = self.env['lab.daily.update']
        # Off the FIELD, not off a module constant reached through the model:
        # FLAG_KINDS is module-level in daily_update.py, so `Sheet.FLAG_KINDS`
        # silently resolved to nothing and every row showed a blank Issue while
        # the breakdown printed raw keys. The field always knows its own labels.
        kinds = dict(Sheet._fields['flag_kind']._description_selection(self.env))
        open_sheets = Sheet.search([('state', '=', 'flagged')],
                                   order='flagged_at')
        now = fields.Datetime.now()
        rows = [{
            'id': sheet.id,
            'who': sheet.user_id.name,
            'date': fields.Date.to_string(sheet.date),
            'kind': sheet.flag_kind or '',
            'kind_label': kinds.get(sheet.flag_kind, ''),
            'desk': sheet.flagged_desk or '',
            'by': sheet.flagged_by_id.name or '',
            'reason': (sheet.flag_reason or '')[:160],
            # How long it has been sitting on the administrator's desk. A
            # raised sheet that nobody answers teaches the desks not to raise.
            'hours': int((now - sheet.flagged_at).total_seconds() // 3600)
                     if sheet.flagged_at else 0,
        } for sheet in open_sheets]

        window = today - timedelta(days=window_days)
        by_kind, by_user = {}, {}
        for kind, count in Sheet._read_group(
                [('flagged_at', '!=', False), ('date', '>=', window),
                 ('date', '<=', today)], ['flag_kind'], ['__count']):
            by_kind[kind or 'other'] = count
        for user, count in Sheet._read_group(
                [('flagged_at', '!=', False), ('date', '>=', window),
                 ('date', '<=', today)], ['user_id'], ['__count'],
                order='__count desc', limit=6):
            by_user[user.id] = (user.name, count)
        total = sum(by_kind.values())
        return {
            'rows': rows,
            'open': len(rows),
            'window_days': window_days,
            'total': total,
            'kinds': sorted(
                [{'key': key, 'label': kinds.get(key, key), 'count': count,
                  'share': round(count / total * 100) if total else 0}
                 for key, count in by_kind.items()],
                key=lambda r: r['count'], reverse=True),
            'people': [{'id': uid, 'name': name, 'count': count}
                       for uid, (name, count) in sorted(
                           by_user.items(), key=lambda kv: kv[1][1],
                           reverse=True)],
            # The oldest one waiting, which is the number that shames a desk
            # into answering.
            'oldest_hours': max([r['hours'] for r in rows], default=0),
        }

    @api.model
    def action_open_flags(self):
        """The raised sheets themselves, from the panel."""
        return {
            'type': 'ir.actions.act_window',
            'name': _('Raised to the administrator'),
            'res_model': 'lab.daily.update',
            'view_mode': 'list,form',
            'domain': [('state', '=', 'flagged')],
            'context': {'create': False},
        }

    def _health(self):
        """The whole system as plain yes/no sentences.

        The Command Center was a wall of panels a reader had to interpret;
        this is the interpretation, done for them. Every line is one check
        with one sentence, green or red, and the red ones open the records
        they are about. The panels below become the detail, not the message.
        (client, 2026-08-31)
        """
        checks = []
        Sheet = self.env['lab.daily.update'].sudo()

        def check(label, count, ok, ok_text, bad_text, model=None, ids=None):
            checks.append({
                'label': label, 'count': count,
                'ok': bool(ok),
                'text': ok_text if ok else bad_text,
                'model': model or '', 'ids': ids or [],
            })

        absent = self.env['lab.desk.cover']._absent_desks()
        check(self.env._('Desks manned'), len(absent), not absent,
              self.env._('Every desk has a manager today.'),
              absent and self.env._(
                  '%(desk)s: %(why)s', desk=absent[0]['label'],
                  why=absent[0]['detail']) or '')

        sla_h = self._sla_hours()
        cutoff = fields.Datetime.now() - timedelta(hours=sla_h)
        late = Sheet.search(
            ['|', '&', ('state', '=', 'submitted'), ('submitted_at', '<', cutoff),
                  '&', ('state', 'in', ('ops_approved', 'ops_checked')),
                       ('ops_approved_at', '<', cutoff)])
        check(self.env._('Late sheets'), len(late), not late,
              self.env._('No day sheet is late.'),
              self.env._('%(count)s day sheet(s) waited longer than %(h)s hours.',
                         count=len(late), h=sla_h),
              'lab.daily.update', late.ids)

        stuck = [c for c in self._cron_heartbeat()
                 if not c['active'] or c['stale']]
        check(self.env._('Automatic jobs'), len(stuck), not stuck,
              self.env._('All automatic jobs are running.'),
              stuck and self.env._('"%(name)s" is not running.',
                                   name=stuck[0]['name']) or '')

        debt = self._nudge_debt(limit=100)
        check(self.env._('Unanswered asks'), len(debt), not debt,
              self.env._('No question is waiting for an answer.'),
              self.env._('%(count)s day sheet(s) were asked about twice with '
                         'no change.', count=len(debt)),
              'lab.daily.update', [d['id'] for d in debt])

        yesterday = fields.Date.context_today(self) - timedelta(days=1)
        worked = self.env['lab.visit'].sudo().search(
            [('date', '=', yesterday)]).mapped('user_id')
        filed = Sheet.search([('date', '=', yesterday)]).mapped('user_id')
        missing = worked - filed
        check(self.env._('Yesterday filed'), len(missing), not missing,
              self.env._("Everyone who worked yesterday has a day sheet."),
              self.env._('No day sheet yesterday for: %(who)s.',
                         who=', '.join(missing.mapped('name')[:4])))

        verdict = self._radius_calibration()['verdict']
        check(self.env._('Visit radius'), 0, verdict in ('good', 'unknown'),
              self.env._('The visit radius fits how people work.'),
              self.env._('The visit radius looks wrong — see the radius panel.'))

        return {'checks': checks,
                'ok': all(c['ok'] for c in checks)}


    @api.model
    def _flow_series(self, today, days=14):
        """Filed against fully approved, per day: is the machine keeping up?

        Two bars a day. When the light one (filed) keeps outrunning the dark
        one (approved), the backlog is growing - visible a week before the
        funnel looks alarming.
        """
        start = today - timedelta(days=days - 1)
        sheets = self.env['lab.daily.update'].sudo().search(
            [('date', '>=', start), ('date', '<=', today)])
        out = []
        for offset in range(days):
            day = start + timedelta(days=offset)
            mine = sheets.filtered(lambda sh: sh.date == day)
            out.append({
                'day': fields.Date.to_string(day),
                'label': format_date(self.env, day, date_format='EEE d'),
                'filed': len(mine),
                # Checked with issues noted is finished too: the chart asks
                # whether the desks keep up, not how many days were clean.
                'approved': len(mine.filtered(
                    lambda sh: sh.state in ('approved', 'checked'))),
                'weekend': day.weekday() == 6,
            })
        return out

    @api.model
    def _velocity_series(self, today, weeks=6):
        """Median approval hours per desk, week by week - the trend the
        single this-week/last-week pair cannot show."""
        Sheet = self.env['lab.daily.update'].sudo()
        out = []
        for i in range(weeks - 1, -1, -1):
            end = today - timedelta(days=7 * i)
            start = end - timedelta(days=6)
            sheets = Sheet.search([('date', '>=', start), ('date', '<=', end)])

            def med(frm, to):
                waits = sorted((to(sh) - frm(sh)).total_seconds() / 3600.0
                               for sh in sheets if frm(sh) and to(sh))
                return round(_percentile(waits, 50), 1) if waits else 0.0

            out.append({
                'label': format_date(self.env, end, date_format='d MMM'),
                'ops': med(lambda sh: sh.submitted_at,
                           lambda sh: sh.ops_approved_at),
                'mkt': med(lambda sh: sh.ops_approved_at,
                           lambda sh: sh.approved_at),
            })
        return out

    @api.model
    def _exception_mix(self, today, days=7):
        """What KIND of issues the week produced, for the donut. Five flags
        become one picture: mostly GPS is a radius problem, mostly attendance
        is a habit problem - the mix says which conversation to have."""
        start = today - timedelta(days=days - 1)
        sheets = self.env['lab.daily.update'].sudo().search(
            [('date', '>=', start), ('date', '<=', today),
             ('is_clean', '=', False)])
        counts = []
        for field, key, label in self.FLAG_LABELS:
            n = len(sheets.filtered(field))
            if n:
                counts.append({'key': key, 'label': label, 'count': n})
        total = sum(c['count'] for c in counts) or 1
        for c in counts:
            c['pct'] = round(c['count'] * 100.0 / total, 1)
        return sorted(counts, key=lambda c: -c['count'])

    @api.model
    def _approval_velocity(self, today):
        """How long a sheet sits on each desk: the median, this week against
        last. The funnel says where sheets ARE; this says whether the desks
        are getting faster or slower at moving them - the only number that
        catches a manager quietly drowning while the backlog still looks
        ordinary."""
        Sheet = self.env['lab.daily.update'].sudo()

        def median_hours(rows, frm, to):
            waits = sorted((to(s) - frm(s)).total_seconds() / 3600.0
                           for s in rows if frm(s) and to(s))
            return round(_percentile(waits, 50), 1) if waits else None

        def window(offset_days):
            end = today - timedelta(days=offset_days)
            start = end - timedelta(days=6)
            return Sheet.search([('date', '>=', start), ('date', '<=', end)])

        this_week, last_week = window(0), window(7)
        return {
            'ops_now': median_hours(this_week, lambda s: s.submitted_at,
                                    lambda s: s.ops_approved_at),
            'ops_prev': median_hours(last_week, lambda s: s.submitted_at,
                                     lambda s: s.ops_approved_at),
            'mkt_now': median_hours(this_week, lambda s: s.ops_approved_at,
                                    lambda s: s.approved_at),
            'mkt_prev': median_hours(last_week, lambda s: s.ops_approved_at,
                                     lambda s: s.approved_at),
        }

    @api.model
    def _nudge_debt(self, limit=8):
        """Asked twice, changed nothing, still on a desk.

        The nudge's own design says it: twice with no movement is a send-back
        that has not happened yet. The administrator sees the debt building
        instead of finding it in next Monday's report.
        """
        sheets = self.env['lab.daily.update'].sudo().search(
            [('state', 'in', ('submitted', 'ops_approved', 'ops_checked')),
             ('nudge_count', '>=', 2)],
            order='nudge_count desc, date', limit=limit)
        return [{
            'id': s.id, 'user': s.user_id.name,
            'date_label': format_date(self.env, s.date, date_format='EEE d MMM'),
            'nudges': s.nudge_count,
            'desk': 'ops' if s.state == 'submitted' else 'marketing',
        } for s in sheets]

    @api.model
    def _checked_board(self, today, days=14, limit=12):
        """Days a desk read and marked checked instead of approving.

        The administrator's answer to "did anyone look at this day?" - which
        an approval could not give once days with issues were being approved
        like clean ones. Newest first, with each desk's note.
        (client, 2026-09-15)
        """
        Sheet = self.env['lab.daily.update'].sudo()
        domain = [('date', '>', today - timedelta(days=days)),
                  ('date', '<=', today),
                  '|', ('state', 'in', ('ops_checked', 'checked')),
                       ('ops_check_note', '!=', False)]
        sheets = Sheet.search(domain, order='date desc, id desc', limit=limit)
        states = dict(Sheet._fields['state']._description_selection(self.env))
        return {
            'total': Sheet.search_count(domain),
            'days': days,
            'rows': [{
                'id': s.id, 'user': s.user_id.name,
                'date_label': format_date(self.env, s.date,
                                          date_format='EEE d MMM'),
                'state': s.state, 'state_label': states.get(s.state, ''),
                'ops_by': s.ops_approved_by_id.name or '',
                'ops_note': s.ops_check_note or '',
                'mkt_by': s.approved_by_id.name if s.state == 'checked' else '',
                'mkt_note': s.check_note or '',
            } for s in sheets],
        }

    # ============================================================== admin desk

    @api.model
    def get_admin_desk(self, day=None):
        Sheet = self.env['lab.daily.update']
        today, real_today = self._pinned_day(day)
        # ONE ledger scan for the whole page: receivables, the unvisited
        # doctors' balances and the rescue rows all read these same rows.
        debits = self.env['lab.collection.performance'].sudo()._open_debits(
            self.env.company) \
            if 'lab.collection.performance' in self.env else []
        week_ago = today - timedelta(days=7)
        # Bounded above as well as below: pinned to a past day, the week is
        # THAT week - sheets from after it would say the machine was running
        # when the question is whether it was running then.
        recent = Sheet.search([('date', '>=', week_ago), ('date', '<=', today)])
        sla_h = self._sla_hours()
        cutoff = fields.Datetime.now() - timedelta(hours=sla_h)
        last_report = self.env['lab.weekly.report'].search(
            [], order='date_from desc', limit=1)
        return {
            'currency_id': self.env.company.currency_id.id,
            **self._day_keys(today, real_today),
            'funnel': {
                'draft': len(recent.filtered(lambda s: s.state == 'draft')),
                'submitted': Sheet.search_count([('state', '=', 'submitted')]),
                'ops_approved': Sheet.search_count(
                    [('state', 'in', ('ops_approved', 'ops_checked'))]),
                'approved_week': len(recent.filtered(
                    lambda s: s.state == 'approved')),
                'checked_week': len(recent.filtered(
                    lambda s: s.state == 'checked')),
                'sent_back': Sheet.search_count([('state', '=', 'sent_back')]),
                'flagged': Sheet.search_count([('state', '=', 'flagged')]),
                'week_total': len(recent),
                'overdue': Sheet.search_count(
                    ['|',
                     '&', ('state', '=', 'submitted'),
                          ('submitted_at', '<', cutoff),
                     '&', ('state', 'in', ('ops_approved', 'ops_checked')),
                          ('ops_approved_at', '<', cutoff)]),
                'sla_h': sla_h,
            },
            'desks': self._role_coverage(),
            # The business, before the machine: what is waiting unapproved,
            # what the week sold, how the targets stand. The system panels
            # (health, crons, radius...) live behind the System drawer now -
            # the client cropped every one of them and said "no use in sales
            # and field work". (2026-08-31)
            'pending_matrix': self._pending_matrix(),
            'week_sales': self._week_sales_snapshot(today),
            'targets_rollup': self._targets_rollup(today),
            'route_week': self._route_week_visits(today),
            **{k: v for k, v in self._field_board(today).items()
               if k in ('rows', 'totals')},
            'heatmap': self._discipline_heatmap(today),
            'unvisited': self._unvisited_doctors(today, debits=debits),
            # The going-quiet clinics, same panel as the marketing desk - the
            # administrator asked for it here too, planner and all.
            'rescue': self._rescue_list(debits=debits),
            'executives': [{'id': u.id, 'name': u.name}
                           for u in self._executives()],
            'outcomes': self._outcome_mix(today),
            'conversion': self._new_door_conversion(today),
            'receivables': self._open_receivables(debits=debits),
            'undelivered': self._undelivered(),
            'flags': self._flag_board(today),
            'checked': self._checked_board(today),
            'health': self._health(),
            'velocity': self._approval_velocity(today),
            # The charts: the machine over time, not only right now.
            'flow': self._flow_series(today),
            'speed_trend': self._velocity_series(today),
            'exceptions': self._exception_mix(today),
            'nudge_debt': self._nudge_debt(),
            # Who is standing in today, and — the one that stops the field —
            # which desk has nobody at it at all.
            'covers': self._cover_board(),
            'crons': self._cron_heartbeat(),
            'radius': self._radius_calibration(),
            'gaps': self._setup_gaps(),
            'roi': self._roi(),
            'last_report': {
                'id': last_report.id, 'name': last_report.name,
                'date_to': fields.Date.to_string(last_report.date_to),
            } if last_report else False,
            'ticker': self._ticker(12),
        }

    @api.model
    def _cover_board(self):
        """Today's stand-ins, and any desk nobody is sitting at.

        The administrator is the only person who can see both desks at once, so
        this is the only screen where "the marketing desk is unmanned and its
        queue has been growing since Tuesday" can be noticed before the weekly
        report says it a week late.
        """
        Cover = self.env['lab.desk.cover']
        today = fields.Date.context_today(self)
        running = Cover.sudo().search(
            [('date_from', '<=', today), ('date_to', '>=', today)])
        upcoming = Cover.sudo().search(
            [('date_from', '>', today), ('date_from', '<=', today + timedelta(days=14))],
            order='date_from', limit=5)

        desk_labels = dict(Cover._fields['desk']._description_selection(self.env))

        def row(cover):
            return {
                'id': cover.id,
                'desk': desk_labels[cover.desk],
                'manager': cover.manager_id.name,
                'delegate': cover.delegate_id.name,
                'from': format_date(self.env, cover.date_from, date_format='d MMM'),
                'to': format_date(self.env, cover.date_to, date_format='d MMM'),
                'reason': cover.reason or '',
                'signed': cover.signed_count,
            }

        return {
            'running': [row(c) for c in running],
            'upcoming': [row(c) for c in upcoming],
            'absent': self.env['lab.desk.cover']._absent_desks(),
        }

    @api.model
    def _role_coverage(self):
        """Who actually holds each desk. A desk with no dedicated person is the
        first thing the administrator should know — approvals then depend on an
        administrator remembering to do somebody else's job."""
        out = []
        for key, xmlid, label in (
                ('executive', 'lab_fieldwork.group_fieldwork_executive',
                 self.env._('Executives')),
                ('ops', 'lab_fieldwork.group_fieldwork_ops_manager',
                 self.env._('Operational Manager')),
                ('marketing', 'lab_fieldwork.group_fieldwork_marketing_manager',
                 self.env._('Marketing Manager')),
                ('admin', 'lab_fieldwork.group_fieldwork_admin',
                 self.env._('Administrator'))):
            group = self.env.ref(xmlid, raise_if_not_found=False)
            users = group.sudo().user_ids.filtered('active') if group \
                else self.env['res.users']
            out.append({
                'key': key, 'label': label, 'count': len(users),
                'names': users.mapped('name')[:6],
                # Executives and admins must exist; the two desks warn when
                # nobody holds them BY NAME (implication is a fallback, not
                # an owner).
                'warn': key in ('ops', 'marketing', 'executive', 'admin')
                        and not users,
            })
        return out

    @api.model
    def _cron_heartbeat(self):
        """Is the machine actually running? The flow lives on five crons; a dead
        one fails silently for weeks unless something shows its pulse."""
        Data = self.env['ir.model.data'].sudo()
        rows = []
        now = fields.Datetime.now()
        for name in ('cron_close_field_day', 'cron_generate_day_sheets',
                     'cron_day_sheet_digest', 'cron_day_sheet_escalate',
                     'cron_weekly_field_report'):
            rec = Data.search([('module', '=', 'lab_fieldwork'),
                               ('name', '=', name), ('model', '=', 'ir.cron')],
                              limit=1)
            cron = self.env['ir.cron'].sudo().browse(rec.res_id).exists() \
                if rec else self.env['ir.cron'].sudo()
            if not cron:
                continue
            stale = bool(cron.active and cron.nextcall
                         and cron.nextcall < now - timedelta(hours=2))
            rows.append({
                'name': cron.cron_name or cron.name,
                'active': cron.active,
                'nextcall': fields.Datetime.to_string(cron.nextcall),
                'stale': stale,
            })
        return rows

    @api.model
    def _roi(self):
        """Travel cost against case value. (ported from Field Health)"""
        from dateutil.relativedelta import relativedelta
        start = fields.Date.context_today(self).replace(day=1) \
            - relativedelta(months=TREND_MONTHS - 1)
        trips = self.env['lab.trip'].sudo().search(
            [('date', '>=', start), ('state', '=', 'approved')])
        visits = self.env['lab.visit'].sudo().search(
            [('date', '>=', start), ('state', '=', 'done')])
        cost = sum(trips.mapped('amount'))
        value = sum(visits.mapped('order_value'))
        return {
            'cost': cost, 'value': value,
            'collected': sum(visits.mapped('collected')),
            'distance': sum(trips.mapped('distance')),
            'visits': len(visits),
            'ratio': round(value / cost, 1) if cost else 0.0,
            'cost_per_visit': round(cost / len(visits), 2) if visits else 0.0,
            'months': TREND_MONTHS,
        }

    @api.model
    def _radius_calibration(self):
        """Does the radius match where people actually stand? A quarter of
        check-ins 'away' means the RULE is wrong, not the people.
        (ported from Field Health)"""
        from dateutil.relativedelta import relativedelta
        since = fields.Date.context_today(self) - relativedelta(days=LOOKBACK_DAYS)
        visits = self.env['lab.visit'].sudo().search([
            ('date', '>=', since), ('check_in', '!=', False),
            ('gps_state', 'in', ('ok', 'far'))])
        radius = float(self.env['ir.config_parameter'].sudo().get_param(
            'lab_fieldwork.visit_radius_m', 300))
        distances = sorted(v.distance_m for v in visits)
        total = len(distances)
        buckets = []
        for low, high in BUCKETS:
            n = len([d for d in distances
                     if d >= low and (high is None or d < high)])
            buckets.append({
                'label': ('%d m+' % low) if high is None
                         else ('%d–%d m' % (low, high)),
                'low': low, 'count': n,
                'pct': round(n * 100.0 / total, 1) if total else 0.0,
                'inside': high is not None and high <= radius})
        far = len([d for d in distances if d > radius])
        far_pct = round(far * 100.0 / total, 1) if total else 0.0
        p50 = _percentile(distances, 50)
        p90 = _percentile(distances, 90)
        if total < 20:
            verdict, advice, suggested = 'unknown', self.env._(
                "Not enough check-ins yet to judge the radius."), 0
        elif far_pct >= 25:
            verdict, suggested = 'tight', _round50(p90)
            advice = self.env._(
                "%(pct)s%% of check-ins are outside the radius. The radius may "
                "be too small: 90%% of people stand within %(p90)s m.",
                pct=far_pct, p90=int(p90))
        elif far_pct <= 2 and p90 * 2 < radius:
            verdict, suggested = 'loose', _round50(max(p90, 50))
            advice = self.env._(
                "Almost every check-in is inside, and 90%% are within %(p90)s m. "
                "At %(radius)s m the radius may be too big to catch anything.", p90=int(p90), radius=int(radius))
        else:
            verdict, suggested = 'good', 0
            advice = self.env._(
                "%(pct)s%% of check-ins fall outside the radius. That is a "
                "normal level.", pct=far_pct)
        return {'radius': radius, 'total': total, 'far': far, 'far_pct': far_pct,
                'p50': round(p50), 'p90': round(p90), 'buckets': buckets,
                'verdict': verdict, 'advice': advice, 'suggested': suggested}

    @api.model
    def _setup_gaps(self):
        """The things quietly switched off, each with the action that closes it.
        (ported from Field Health)"""
        Partner = self.env['res.partner'].sudo()
        Beat = self.env['lab.beat'].sudo()
        group = self.env.ref('lab_fieldwork.group_fieldwork_executive',
                             raise_if_not_found=False)
        execs = group.sudo().all_user_ids.filtered('active') if group \
            else self.env['res.users']
        month = fields.Date.context_today(self).replace(day=1)
        on_a_beat = Beat.search([]).partner_ids
        gaps = []
        unpinned_domain = [('is_clinic', '=', True), ('is_geolocated', '=', False)]
        unpinned = Partner.search_count(unpinned_domain)
        if unpinned:
            gaps.append(_gap('unpinned', 'danger', 'fa-map-o', unpinned,
                        self.env._('Clinics not pinned on the map'),
                        self.env._("Their visits can never be location-checked."),
                        'res.partner', [], self.env._('Pin them'),
                        domain=unpinned_domain))
        orphan_domain = [('is_clinic', '=', True),
                         ('id', 'not in', on_a_beat.ids)]
        orphans = Partner.search_count(orphan_domain)
        if orphans:
            gaps.append(_gap('orphan_clinic', 'warning', 'fa-unlink', orphans,
                        self.env._("Clinics on nobody's round"),
                        self.env._("Nobody is scheduled to visit them."),
                        'res.partner', [], self.env._('Assign a beat'),
                        domain=orphan_domain))
        empty = Beat.search([('partner_ids', '=', False)])
        if empty:
            gaps.append(_gap('empty_beat', 'warning', 'fa-list', len(empty),
                        self.env._('Beats with no clinics'),
                        self.env._('Planning one generates nothing at all.'),
                        'lab.beat', empty.ids, self.env._('Add clinics')))
        beatless = execs.filtered(
            lambda u: not Beat.search_count([('user_id', '=', u.id)]))
        if beatless:
            gaps.append(_gap('no_beat', 'warning', 'fa-user-times', len(beatless),
                        self.env._('Executives with no beat'),
                        self.env._('%(who)s. Their My Day is empty every morning.',
                                   who=', '.join(beatless.mapped('name')[:5])),
                        'lab.beat', [], self.env._('Plan a beat')))
        Target = self.env['lab.target'].sudo()
        untargeted = execs.filtered(lambda u: not Target.search_count(
            [('user_id', '=', u.id), ('month', '=', month)]))
        if untargeted:
            gaps.append(_gap('no_target', 'info', 'fa-bullseye', len(untargeted),
                        self.env._('No target set this month'),
                        self.env._('%(who)s.',
                                   who=', '.join(untargeted.mapped('name')[:5])),
                        'lab.target', [], self.env._('Set targets')))
        floatless = execs.filtered(lambda u: not u.petty_cash_allocation_id)
        if floatless:
            gaps.append(_gap('no_float', 'info', 'fa-money', len(floatless),
                        self.env._('Executives with no petty cash float'),
                        self.env._('%(who)s. Collected cash has nowhere to go.',
                                   who=', '.join(floatless.mapped('name')[:5])),
                        'petty.cash.allocation', [],
                        self.env._('Allocate a float')))
        return gaps


# ------------------------------------------------------------------- helpers
def _gap(key, level, icon, count, title, detail, model, ids, label, domain=None):
    return {'key': key, 'level': level, 'icon': icon, 'count': count,
            'title': title, 'detail': detail, 'model': model, 'ids': ids,
            'label': label, 'domain': domain or []}


def _percentile(sorted_values, pct):
    if not sorted_values:
        return 0.0
    k = (len(sorted_values) - 1) * pct / 100.0
    lo, hi = int(k), min(int(k) + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (k - lo)


def _round50(value):
    return int(round(value / 50.0) * 50) if value else 0
