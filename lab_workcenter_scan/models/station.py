# -*- coding: utf-8 -*-
import re
from collections import defaultdict
from datetime import datetime, time, timedelta

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from .mrp_redo import REDOABLE

# How many cards a column may hold. A bench reads a page, not a queue: the counts beside
# each heading say how deep the real pile is.
COLUMN_LIMIT = 40
# The finished column is a record, not a queue: a bench can pass on more in a day
# than it ever holds at once, and a head looking back at Tuesday wants the day,
# not the first twenty of it. (client, 2026-09-09)
DONE_LIMIT = 60

# The two pieces of a case sold as Upper & Lower, said the short way a bench
# says them on a card. (client, 2026-09-12)
ARCH_SHORT = {'upper': 'U', 'lower': 'L'}

# How the waiting column may be ordered. Oldest first is the lab's own rule -
# first in, first made - and the other two are the questions a lead asks when
# that rule has to bend. (client, 2026-09-10)
QUEUE_SORTS = [
    ('oldest', 'Oldest first'),
    ('urgent', 'Urgent first'),
    ('newest', 'Newest first'),
]
URGENT = ('urgent', 'emergency')
# A job standing this long at one bench is what a lead walks over to look at.
STALE_DAYS = 2
# How many cases one search may return: enough to recognise the one on the
# phone, the same cap Track Order uses. (client, 2026-09-14)
FIND_LIMIT = 12


class LabStation(models.AbstractModel):
    """The board on the bench.

    Written for a tablet propped up at a work centre and read across a workshop, so it
    answers three questions and nothing else: what has arrived for me, what am I working
    on, and what have I passed on today.

    One call for the whole screen. A station terminal is used with gloves on a poor
    wireless connection, and three round trips is three chances to show half a board.
    """
    _name = 'lab.station'
    _description = 'Work Centre Station Board'

    @api.model
    def _default_workcenter(self):
        """The station this user runs, when there is exactly one.

        A shared terminal that asks "which station are you?" every morning is answered
        wrong eventually, and the wrong answer moves somebody else's work.
        """
        Workcenter = self.env['mrp.workcenter']
        # Head first, then technician: sale_custom already records who WORKS at a
        # station in `users`, so a technician opening the board lands on their own bench
        # without being given a second role to maintain.
        mine = Workcenter.search([('head_user_ids', 'in', self.env.uid)], limit=2)
        if not mine:
            mine = Workcenter.search([('users', 'in', self.env.uid)], limit=2)
        return mine[:1] if len(mine) == 1 else Workcenter

    @api.model
    def _my_stations(self):
        """The benches this user is actually posted to.

        Listing every work centre invites the wrong one: a head who taps the picker on a
        shared tablet sees forty stations and can accept somebody else's job into their
        own queue, which is precisely the confusion the board exists to remove.

        A manufacturing MANAGER keeps the full list — covering a shift, chasing a job or
        setting the lab up all require reaching a bench you do not run.
        """
        Workcenter = self.env['mrp.workcenter']
        if self.env.user.has_group('mrp.group_mrp_manager'):
            return Workcenter.search([])
        return Workcenter.search(['|', ('head_user_ids', 'in', self.env.uid),
                                  ('users', 'in', self.env.uid)])

    @api.model
    def get_station(self, workcenter_id=None, person_id=None, done_day=None,
                    query=None, sort=None):
        Workcenter = self.env['mrp.workcenter']
        stations = self._my_stations()
        workcenter = Workcenter.browse(int(workcenter_id)) if workcenter_id \
            else self._default_workcenter()
        # Asked for a bench they are not posted to: refuse rather than silently show a
        # different one, or the board would quietly lie about which station it is.
        if workcenter and workcenter not in stations:
            raise UserError(_(
                "You are not assigned to %s. Ask an administrator to add you to that "
                "work centre.", workcenter.display_name))

        if not workcenter:
            return {
                'workcenter': False,
                'stations': self._station_choices(
                    stations, day=self._lab_today()),
                'incoming': [], 'working': [], 'done_today': [], 'awaiting': [],
                'bench_people': [], 'can_assign': False, 'is_head': False,
                'role': '', 'staffed': True, 'person_id': False,
                'totals': {'incoming': 0, 'working': 0, 'awaiting': 0,
                           'shown': COLUMN_LIMIT},
                'unassigned': not stations,
            }

        Workorder = self.env['mrp.workorder']
        # WHAT IS ACTUALLY AT THIS BENCH.
        #
        # Two corrections, both of which the printed Station Queue Sheet already makes
        # and the screen did not:
        #
        # 1. A case routed Wire Bending -> Acrylisation -> Trimming -> Polishing has all
        #    four operations unfinished from the moment it is created, so "every
        #    unfinished work order at this station" put the same case in four queues at
        #    once and showed Acrylisation 24,000 deep before a single job had reached it.
        #    The queue is the FIRST unfinished step, and only that.
        # 2. Nine of ten open manufacturing orders on this database were delivered
        #    months ago and never closed, so without the live filter the bench is mostly
        #    shown work the doctor already has. (client, 2026-08-27)
        # "First unfinished step" cannot be written as a domain — it compares each row
        # against its own siblings — so it is resolved the same way the printed sheet
        # resolves it, over the live cases only. That set is ~2,500 orders, not 24,000.
        Floor = self.env['report.lab.floor']
        live = self.env['mrp.production'].search(Floor._live_domain())
        # BY PIECE, not by case: a case sold as Upper & Lower is two jobs on
        # this floor, and the bench must be able to take the upper while the
        # lower is still at another station. (client, 2026-09-12)
        first_steps = Floor._open_steps_by_arch(live)
        here_ids = self._open_here_ids(workcenter, first_steps)
        open_here = [('id', 'in', here_ids)]
        # Whose work to show. A lead handing out the morning taps a name and sees only
        # that person's bench; 'none' is the pile nobody has been given yet, which is
        # the one the lead is actually there to empty. (client, 2026-08-26)
        person = self._person_filter(person_id)
        # FINDING A JOB IN A QUEUE OF 953. Each column shows forty cards; the
        # deepest bench on this floor has nine hundred behind them, so a job
        # that is not in the first forty could only be found by scanning it.
        # The bench can now say a name, a doctor or a number instead.
        # (client, 2026-09-10)
        find = self._query_filter(query)

        # HARD LIMIT, not a nicety: one station on this floor has 24,000 open jobs, and a
        # board that renders them all sends a payload no phone can hold and no human can
        # read. The counts below still tell the truth about the size of the queue.
        waiting_domain = open_here + person + find + [('accepted_at', '=', False)]
        incoming = self._queue(waiting_domain, sort)
        incoming_total = Workorder.search_count(waiting_domain)

        # ON THE BENCH IS A FACT, NOT A PREDICTION.
        #
        # Arrived is a prediction and rightly asks `open_here` - the case's first
        # unfinished step - because a bench must not be offered work whose earlier
        # step is still out. Once somebody has ACCEPTED a job, that reasoning is
        # spent: the job is in their hands whatever the routing thinks, and a
        # column that hides it is telling the bench they are holding nothing.
        #
        # Aswathy Sajeevan's chip read 7 while her board showed 6 and "On the
        # bench: 0". The seventh was MO/285447, accepted at 15:42 and still in
        # progress - she had started the Labial Bow while the Adams Clasp before
        # it was open, so it was not a first step and no column would show it.
        # 53 jobs across nine benches were invisible the same way, Polishing
        # worst at 22. (client, 2026-09-12)
        accepted_here = [
            ('workcenter_id', '=', workcenter.id),
            ('state', 'not in', ('done', 'cancel')),
            ('accepted_at', '!=', False),
        ] + person + find
        working = Workorder.search(
            accepted_here, order='accepted_at desc, id', limit=COLUMN_LIMIT)
        working_total = Workorder.search_count(accepted_here)

        # Sent from here and not yet taken on by anyone. Responsibility does not end at
        # the moment of handing over; it ends when somebody accepts.
        awaiting_domain = [
            ('handed_from_workcenter_id', '=', workcenter.id),
            ('accepted_at', '=', False),
            ('state', 'not in', ('done', 'cancel')),
        ] + self._sender_filter(person_id) + find
        awaiting = Workorder.search(awaiting_domain, order='id', limit=COLUMN_LIMIT)
        awaiting_total = Workorder.search_count(awaiting_domain)

        # What left this bench on the day being looked at - today unless the head
        # asked for another. (client, 2026-09-09)
        done_date = fields.Date.to_date(done_day) if done_day \
            else self._lab_today()
        done_today, done_total = self._done_on(workcenter, done_date, person_id, find)

        ul_labels = self._ul_labels()
        # One query for the board, not one per card.
        usual = self.env['lab.mrp.report']._usual_days()
        role = self.env['mrp.workorder']._bench_role(workcenter)
        bench_people = self._bench_load(workcenter, done_date, open_here_ids=here_ids)
        return {
            'workcenter': {'id': workcenter.id, 'name': workcenter.display_name,
                           'code': workcenter.scan_code or ''},
            # The picker's own list. Choosing a station on a shared terminal is a
            # decision, and a list of bare names is not enough to make it with.
            'stations': self._station_choices(stations, first_steps, done_date),
            'is_head': role in ('manager', 'lead'),
            'role': role or '',
            # Giving out work is the lead's call, so the pickers only exist for a lead.
            # A technician still sees WHO has each job — that is the point of naming it —
            # they simply cannot move it to somebody else. (client, 2026-08-26)
            'can_assign': role in ('manager', 'lead'),
            'staffed': bool(workcenter.users or workcenter.head_user_ids),
            # Arrived but nobody here has taken responsibility yet. This is the whole
            # point of the board: a job moved to a station that never noticed is a job
            # that surfaces only when it fails to come back.
            'incoming': [self._card(w, ul_labels, usual) for w in incoming],
            'working': [self._card(w, ul_labels, usual) for w in working],
            'done_today': [self._card(w, ul_labels, usual) for w in done_today],
            # The day that column is showing, and whether it is today - the header
            # says "Passed on today" or the date, and the arrows know where to stop.
            'done_day': fields.Date.to_string(done_date),
            'done_is_today': done_date == self._lab_today(),
            'done_total': done_total,
            'today': fields.Date.to_string(self._lab_today()),
            'awaiting': [dict(self._card(w, ul_labels, usual),
                              at_station=w.workcenter_id.sudo().display_name)
                         for w in awaiting],
            # What the columns would hold if they were not capped, so a lead reading
            # "40" never mistakes the page for the queue.
            'totals': {'incoming': incoming_total, 'working': working_total,
                       'awaiting': awaiting_total, 'shown': COLUMN_LIMIT},
            # Who this lead may hand a job to, and how much each of them is already
            # carrying. Spreading work evenly is impossible without the second half.
            'bench_people': bench_people,
            # The bench's day as one line, with where the shift is - so a head
            # knows at ten whether forty by five is still on. (client, 2026-09-09)
            'bench_day': self._bench_day(workcenter, bench_people, done_date),
            # Whether this reader may set the bench's targets from the board:
            # the manufacturing manager's call, by the targets' own access rule.
            'can_set_targets': self.env['lab.work.target'].has_access('write'),
            'person_id': person_id or False,
            # What the bench asked to find, and how the queue is ordered.
            'query': (query or '').strip(),
            'sort': sort or 'oldest',
            'sorts': [{'key': key, 'label': label} for key, label in QUEUE_SORTS],
            # The lab's own list of what goes wrong on a bench. Sent with the board so
            # the "start again" question opens instantly: a dialog that has to fetch
            # before it can be answered is a dialog people tap twice.
            'redo_reasons': self._redo_reasons(),
        }

    @api.model
    def _station_choices(self, stations, first_steps=None, day=None):
        """The benches this person may open, as something they can recognise.

        A name in a dropdown is the least a shared terminal could show: the
        floor has four Wire Bending benches whose names differ by two words at
        the end, and somebody standing at one of them picked the wrong one
        often enough to ask for this.

        Each choice carries the code printed on the bench and the same three
        numbers the board itself shows for the day: waiting to be taken, on
        the bench, and passed on. The card used to say how many people were
        POSTED there and how deep the queue was - neither of which tells
        anybody where the work is, and the queue counted accepted jobs as
        waiting. (client, 2026-09-10)
        """
        day = day or self._lab_today()
        if first_steps is None:
            Floor = self.env['report.lab.floor']
            live = self.env['mrp.production'].search(Floor._live_domain())
            first_steps = Floor._open_steps_by_arch(live)
        # Waiting and on-the-bench are the live cases standing at each station,
        # counted at the step they are actually at - the board's own rule. A
        # case routed through four benches is at ONE of them, not four.
        here = {}
        for row in first_steps:
            station_id = (row.get('workcenter_id') or [None])[0]
            if station_id:
                here.setdefault(station_id, []).append(row)
        # Counted from the rows themselves. These two facts - has it been taken,
        # has it left - used to be two searches over every live step, and
        # `mrp.workorder`'s own order joins the calendar's leaves and sorts, so
        # answering them cost 65ms of a 430ms board. The step query reads them
        # now. (measured 2026-09-12)
        waiting, bench = {}, {}
        for station_id, rows in here.items():
            waiting[station_id] = sum(1 for row in rows if not row['taken'])
            # Handed on but not yet closed: it has left the bench whatever its
            # state says, so it is neither waiting nor in somebody's hands.
            bench[station_id] = sum(1 for row in rows
                                    if row['taken'] and not row['gone'])
        finished = self._finished_by_station(day, [w.id for w in stations])
        uid = self.env.uid
        return [{
            'id': w.id,
            'name': w.display_name,
            'code': w.scan_code or '',
            'waiting': waiting.get(w.id, 0),
            'bench': bench.get(w.id, 0),
            'finished': finished.get(w.id, 0),
            # What the whole bench is holding, for anybody who wants the pile.
            'queue': waiting.get(w.id, 0) + bench.get(w.id, 0),
            'is_lead': uid in w.head_user_ids.ids,
            'people': len(w.users | w.head_user_ids),
        } for w in stations]

    @api.model
    def _finished_by_station(self, day, workcenter_ids):
        """{workcenter_id: operations passed on there on `day`} - the same
        stamp and the same day window the finished column counts by."""
        if not workcenter_ids:
            return {}
        start, end = self._day_window(day)
        groups = self.env['mrp.workorder']._read_group(
            [('workcenter_id', 'in', list(workcenter_ids)),
             ('handed_over_at', '>=', start), ('handed_over_at', '<', end)],
            ['workcenter_id'], ['__count'])
        return {station.id: count for station, count in groups}

    @api.model
    def _redo_reasons(self):
        return [{'id': r.id, 'name': r.name,
                 'station': r.workcenter_id.sudo().display_name or ''}
                for r in self.env['lab.redo.reason'].sudo().search(
                    [('active', '=', True)], order='sequence, id')]

    @api.model
    def _query_filter(self, query):
        """What the bench typed, as a domain: a case, a patient or a doctor.

        The four things a bench says a job out loud by - the manufacturing
        number, the sales order, the patient and the clinic - are matched
        together, so it does not matter which one is to hand. A number typed
        without its prefix still finds the case. (client, 2026-09-10)
        """
        text = (query or '').strip()
        if not text:
            return []
        return ['|', '|', '|',
                ('production_id.name', 'ilike', text),
                ('production_id.sale_id.name', 'ilike', text),
                ('production_id.sale_id.patient', 'ilike', text),
                ('production_id.sale_id.partner_id.name', 'ilike', text)]

    @api.model
    def _queue(self, domain, sort=None):
        """The waiting column, in the order the bench asked for.

        Urgency cannot be an ORDER BY: it lives on the sales order, two
        relations away. So the urgent ones are read first and the rest fill in
        behind them - which also means "urgent first" reaches the urgent job
        sitting nine hundredth, where sorting the page would not.
        """
        Workorder = self.env['mrp.workorder']
        sort = sort if sort in dict(QUEUE_SORTS) else 'oldest'
        if sort == 'newest':
            return Workorder.search(domain, order='date_start desc, id desc',
                                    limit=COLUMN_LIMIT)
        if sort == 'urgent':
            first = Workorder.search(
                domain + [('production_id.sale_id.priority', 'in', URGENT)],
                order='date_start, id', limit=COLUMN_LIMIT)
            if len(first) >= COLUMN_LIMIT:
                return first
            rest = Workorder.search(
                domain + [('id', 'not in', first.ids)],
                order='date_start, id', limit=COLUMN_LIMIT - len(first))
            return first + rest
        return Workorder.search(domain, order='date_start, id', limit=COLUMN_LIMIT)

    @api.model
    def _person_filter(self, person_id):
        """Turn the people strip's selection into a domain fragment."""
        if person_id == 'none':
            return [('bench_user_id', '=', False)]
        if person_id:
            # Either role: filtering to a person must show the jobs they finished
            # for somebody else, because their count includes them. An open job
            # has no finisher yet, so the queues are unaffected. (client, 2026-09-10)
            return self.env['mrp.workorder']._person_leaf([person_id])
        return []

    @api.model
    def _sender_filter(self, person_id):
        """The people strip, for a column of work that is in NOBODY's hands.

        A job waiting to be taken at the next bench has no technician by
        definition - that is what waiting means - so the ordinary filter on
        `bench_user_id` would leave the column showing everybody's work under
        one person's name, which is what the floor reported. The person a
        job in flight belongs to is the one who SENT it, and that is exactly
        what the card already says: it is still yours. (client, 2026-09-09)
        """
        if person_id == 'none':
            return [('handed_from_user_id', '=', False)]
        if person_id:
            return [('handed_from_user_id', '=', int(person_id))]
        return []

    @api.model
    def _open_here_ids(self, workcenter, first_steps=None):
        """Ids of the live pieces whose first open step is at this station -
        the set the Arrived column is drawn from."""
        if first_steps is None:
            Floor = self.env['report.lab.floor']
            live = self.env['mrp.production'].search(Floor._live_domain())
            first_steps = Floor._open_steps_by_arch(live)
        return [row['id'] for row in first_steps
                if (row.get('workcenter_id') or [None])[0] == workcenter.id]

    @api.model
    def _bench_load(self, workcenter, day=None, open_here_ids=None):
        """Everybody posted to this station: their day, and what they carry.

        Two numbers, because they answer two questions. `day_count` is the work
        this person TOOK OR FINISHED on the day being looked at - what the filter
        chips show, and what a head means by "how did the bench do today".
        `load` is what they are holding right now, which is the number that
        matters when work is being handed out and is what the who-did-it popup
        nudges with. A chip showing the pile said nothing about the day.
        (client, 2026-09-09)

        Grouped counts rather than a query per person: a bench with a dozen
        technicians should cost the board a couple of round trips, not a dozen.
        """
        people = (workcenter.users | workcenter.head_user_ids).sorted('name')
        Workorder = self.env['mrp.workorder']
        # What they are HOLDING is the technician's alone: a finisher is named
        # at the moment a job leaves the bench, so nothing open has one.
        loads = dict(Workorder._read_group(
            [('workcenter_id', '=', workcenter.id),
             ('state', 'not in', ('done', 'cancel')),
             ('bench_user_id', 'in', people.ids)],
            ['bench_user_id'], ['__count']))
        this_day = day or self._lab_today()
        days = self._day_counts(workcenter, people, this_day)
        heads = workcenter.head_user_ids
        # What the day was MEANT to be. A count on its own says nothing: eleven is
        # a good day at one bench and a thin one at another, and only the manager
        # who set the number knows which. (client, 2026-09-09)
        scoped = self.env['lab.work.target'].targets_for(
            this_day, people.ids, workcenter, with_scope=True)
        # A TARGET SET FOR NO PARTICULAR BENCH IS ANSWERED BY EVERY BENCH.
        # Technicians here move between benches during a day, and a target set
        # for the day as a whole was being held up against this bench's count
        # alone - so somebody who did twelve at Trimming and nine at Polishing
        # read as short at both, on a day they had beaten the number. Those
        # people, and only those, are counted across the floor. (client, 2026-09-12)
        floor_ids = [u.id for u in people
                     if scoped.get(u.id, (0, 'bench'))[1] == 'day']
        floor_days = self._day_counts(None, people.browse(floor_ids), this_day) \
            if floor_ids else {}
        rows = []
        for u in people:
            target, scope = scoped.get(u.id, (0, 'bench'))
            whole_floor = scope == 'day'
            done = (floor_days if whole_floor else days).get(u.id, 0)
            rows.append({
                'id': u.id, 'name': u.name, 'load': loads.get(u, 0),
                'day_count': done,
                'target': target,
                # Sent ready-made: every screen showing this must agree about what
                # counts as met, and a board is not the place to re-derive it.
                'target_met': bool(target) and done >= target,
                # Say so on the chip. A number that quietly includes other benches
                # is worse than the wrong number: the lead cannot tell which it is.
                'all_benches': whole_floor,
                'bench_count': days.get(u.id, 0) if whole_floor else None,
                'is_lead': u in heads,
            })
        # Counted from the set the columns under this chip are drawn from: the
        # pieces whose first open step is here (Arrived) and what is accepted
        # here (On the bench). Every open step at the station counted later
        # steps no case had reached and cases already delivered, so the chip
        # said hundreds over a filter showing a handful. (review, 2026-09-15)
        if open_here_ids is None:
            open_here_ids = self._open_here_ids(workcenter)
        rows.append({
            'id': 'none', 'name': _('Not given out'), 'is_lead': False,
            'load': Workorder.search_count([
                ('bench_user_id', '=', False),
                '|', ('id', 'in', open_here_ids),
                '&', '&', ('workcenter_id', '=', workcenter.id),
                ('state', 'not in', ('done', 'cancel')),
                ('accepted_at', '!=', False)]),
        })
        return rows

    @api.model
    def set_targets(self, workcenter_id, day, targets, person_id=None):
        """The bench's targets for a day, set from the board itself.

        A manager standing at the bench at eight sets the day's numbers where
        the day is read, instead of walking to a wizard under a menu. One
        bench-specific row per person: a number creates or updates it, a
        zero takes it away - no row of zeros to make the report read as a
        day everybody missed. Only the bench's own people; only for whoever
        may write targets at all. Returns the board, re-read. (client, 2026-09-10)
        """
        Target = self.env['lab.work.target']
        Target.check_access('write')
        workcenter = self.env['mrp.workcenter'].browse(int(workcenter_id))
        if not workcenter.exists():
            raise UserError(_("That station no longer exists."))
        # A bench you do not run is a bench whose board you cannot read, so
        # setting its numbers is refused here rather than after the write.
        if workcenter not in self._my_stations():
            raise UserError(_(
                "You are not assigned to %s, so you cannot set its targets.",
                workcenter.display_name))
        day = fields.Date.to_date(day) if day else self._lab_today()
        people = workcenter.users | workcenter.head_user_ids
        touched = Target.browse()
        for uid, value in (targets or {}).items():
            uid, value = int(uid), max(0, int(value or 0))
            if uid not in people.ids:
                continue
            row = Target.search([('date', '=', day), ('user_id', '=', uid),
                                 ('workcenter_id', '=', workcenter.id)], limit=1)
            if value:
                if row:
                    row.target = value
                else:
                    row = Target.create({'date': day, 'user_id': uid,
                                         'workcenter_id': workcenter.id,
                                         'target': value})
                touched |= row
            elif row:
                row.unlink()
        touched.action_refresh()
        return self.get_station(workcenter.id, person_id, fields.Date.to_string(day))

    @api.model
    def _bench_day(self, workcenter, rows, day):
        """The whole bench's day in one line, from the chips it is made of.

        Done against the targets set, how many people are on theirs, and -
        the number a head actually manages by - where the shift is: forty by
        five is fine at ten with twelve done and not fine with four, and only
        the clock tells the two apart. `expected` is the target scaled to the
        part of the working day gone; `ahead` is done minus that. People with
        no target count in `done` but not against anything. (client, 2026-09-09)
        """
        people = [r for r in rows if r['id'] != 'none']
        with_target = [r for r in people if r.get('target')]
        done = sum(r['day_count'] for r in people)
        target = sum(r['target'] for r in with_target)
        done_against = sum(r['day_count'] for r in with_target)
        shift = self._shift_progress(workcenter, day)
        expected = round(target * shift) if target and shift is not None else None
        return {
            'done': done, 'target': target, 'done_against': done_against,
            'pct': min(100, round(done_against * 100.0 / target)) if target else None,
            'people': len(people), 'with_target': len(with_target),
            'met': len([r for r in with_target if r['target_met']]),
            # Percent of the working day gone; None when the calendar has no
            # hours that day, because nothing is expected of a Sunday.
            'shift': None if shift is None else round(shift * 100),
            'expected': expected,
            'ahead': (done_against - expected) if expected is not None else None,
        }

    @api.model
    def _shift_progress(self, workcenter, day):
        """How far through the bench's working day it is, 0.0 to 1.0.

        By the work centre's own calendar - hours minus lunch minus leave - so
        a bench that starts at eight is not judged at nine as if the day were a
        third gone. A past day is over (1.0), a day ahead has not begun (0.0),
        and a day with no working hours is None.
        """
        calendar = workcenter.resource_calendar_id
        if not calendar:
            return None
        tz = pytz.timezone(calendar.tz) if calendar.tz else self._lab_tz()
        start = tz.localize(datetime.combine(day, time.min))
        intervals = calendar._work_intervals_batch(
            start, start + timedelta(days=1), tz=tz)[False]
        total = sum((stop - begin).total_seconds() for begin, stop, _r in intervals)
        if not total:
            return None
        now = pytz.utc.localize(fields.Datetime.now()).astimezone(tz)
        gone = sum(max(0.0, (min(stop, now) - begin).total_seconds())
                   for begin, stop, _r in intervals)
        return max(0.0, min(1.0, gone / total))

    @api.model
    def _day_counts(self, workcenter, people, day):
        """Per person, the jobs they did or finished at this bench on `day`.

        Taken OR finished, counted once: a job given out in the morning and
        handed on in the afternoon is one piece of work, not two, and a job
        finished today that was taken yesterday still belongs to today's tally -
        it is the day it left the bench.

        Both people on the job count it. At a polishing or packing bench the
        technician who carried the work out and the technician who finished it
        are different people, and a tally that named only the first left the
        second with a day of nothing. (client, 2026-09-10)
        """
        if not people:
            return {}
        start, end = self._day_window(day)
        # `workcenter=None` counts the person's whole floor, which is what a
        # target set for no particular bench is measured against.
        return self.env['mrp.workorder']._day_counts_by_person(
            start, end, people.ids, workcenter)

    @api.model
    def _ul_labels(self):
        """U/L is a RELATED selection, so `_fields['ul'].selection` is not a plain list
        to hand to dict(). fields_get resolves it the way the client does — and returns
        nothing at all if sale_custom is not installed, which is a legitimate setup."""
        Workorder = self.env['mrp.workorder']
        if 'ul' not in Workorder._fields:
            return {}
        return dict(Workorder.fields_get(['ul'])['ul'].get('selection') or [])

    @api.model
    def _lab_tz(self):
        """The lab's one timezone: the context's, else the user's, else the
        company's, else Kolkata.

        `self.env.user.tz or 'UTC'` put the day on UTC for 129 of 156 users and
        for the cron, so a day-shaped figure changed with whoever asked - and a
        stored target count with whoever refreshed it last. (review, 2026-09-15)
        """
        name = (self.env.context.get('tz') or self.env.user.tz
                or self.env.company.partner_id.tz or 'Asia/Kolkata')
        try:
            return pytz.timezone(name)
        except pytz.UnknownTimeZoneError:
            return pytz.timezone('Asia/Kolkata')

    @api.model
    def _lab_time(self, value):
        """A stored (naive UTC) datetime, on the lab's wall clock."""
        return pytz.utc.localize(value).astimezone(self._lab_tz())

    @api.model
    def _lab_today(self):
        """Today on the lab's calendar - the day `_day_window` is asked for.

        `fields.Date.context_today` is UTC for a user with no timezone, so until
        05:30 it named yesterday while the window beside it counted today.
        """
        return self._lab_time(fields.Datetime.now()).date()

    @api.model
    def _day_window(self, day):
        """The lab's day as a UTC pair, for a datetime column.

        `Datetime.to_datetime(date)` is midnight UTC, which in this lab is 05:30
        in the morning - so "today" quietly included the tail of yesterday's
        evening shift and lost the first hours of its own. (2026-09-09)
        """
        tz = self._lab_tz()
        start = tz.localize(datetime.combine(day, time.min))
        return (start.astimezone(pytz.utc).replace(tzinfo=None),
                (start + timedelta(days=1)).astimezone(pytz.utc).replace(tzinfo=None))

    @api.model
    def _done_on(self, workcenter, day, person_id=None, find=None):
        """What this bench passed on during `day`: the newest first, and how many.

        Scoped to the person the strip has selected, like every other column:
        a lead filtering to one technician was still shown the whole bench's
        finished work. (client, 2026-09-09)

        One exception, and it is the point of the exception: where the selected
        person's target for the day is not tied to a bench, their chip counts
        the whole floor - so the list this filter opens must be the whole floor
        too, or the lead clicks a number and is shown fewer jobs than it said.
        (client, 2026-09-12)
        """
        start, end = self._day_window(day)
        bench = [('workcenter_id', '=', workcenter.id)]
        if person_id and person_id != 'none':
            if self.env['lab.work.target'].whole_day_people(
                    day, [int(person_id)], workcenter):
                bench = []
        domain = bench + [('handed_over_at', '>=', start),
                          ('handed_over_at', '<', end)] \
            + self._person_filter(person_id) + (find or [])
        Workorder = self.env['mrp.workorder']
        return (Workorder.search(domain, order='handed_over_at desc', limit=DONE_LIMIT),
                Workorder.search_count(domain))

    @api.model
    def done_on(self, workcenter_id, day=None, person_id=None, query=None):
        """Just the finished column, for another day.

        Its own call rather than a whole board: stepping through last week's work
        must not re-read the queues the bench is standing in front of.
        """
        workcenter = self.env['mrp.workcenter'].browse(int(workcenter_id))
        if not workcenter.exists():
            raise UserError(_("That station no longer exists."))
        day = fields.Date.to_date(day) if day else self._lab_today()
        rows, total = self._done_on(workcenter, day, person_id,
                                    self._query_filter(query))
        ul_labels = self._ul_labels()
        bench_people = self._bench_load(workcenter, day)
        return {
            'rows': [self._card(w, ul_labels) for w in rows],
            # The chips count the same day: stepping back to Tuesday and reading
            # today's tallies beside it would be worse than not stepping at all.
            'bench_people': bench_people,
            'bench_day': self._bench_day(workcenter, bench_people, day),
            # The picker says what each bench did on the day being read, so it
            # must move with the day too. (client, 2026-09-10)
            'stations': self._station_choices(self._my_stations(), day=day),
            'done_day': fields.Date.to_string(day),
            'done_is_today': day == self._lab_today(),
            'done_total': total,
            'today': fields.Date.to_string(self._lab_today()),
        }

    @api.model
    @api.model
    def _day_label(self, due, state):
        """"Due today", "2d late", "in 3d" - the shortest true thing."""
        today = self._lab_today()
        gap = (due - today).days
        if gap == 0:
            return _("due today")
        if gap < 0:
            return _("%sd late", -gap)
        if gap == 1:
            return _("due tomorrow")
        return _("in %sd", gap)

    @api.model
    def _due_for(self, production, usual, today=None):
        """When this case is DUE OUT, by the lab's own standard for its appliance.

        The same definition the Production Reports board promises by - the day
        the case was raised plus the median the lab actually delivered that
        appliance in - so the tile and the board can never disagree about when
        a case is late.

        Deliberately NOT `mrp.production.date_deadline`: that column is set to
        the creation date itself on this database (measured 2026-09-12, mean
        gap 0.0 days over 1,616 open jobs), so showing it would mark every case
        overdue on the day it was raised. An appliance with no standard yet is
        promised nothing rather than guessed at. (client, 2026-09-12)
        """
        if not production or not usual:
            return None, ''
        # The report's own definition, not a fourth copy of it: the tile and the
        # promise clock must never disagree about when a case is late.
        due, gap = self.env['lab.mrp.report']._due_out(production, usual, today)
        if gap is None:
            return None, ''
        state = ('overdue' if gap < 0 else 'today' if gap == 0
                 else 'soon' if gap <= 2 else 'later')
        return due, state

    @api.model
    def _card(self, wo, ul_labels=None, usual=None):
        production = wo.production_id
        # The case behind the job. A bench recognises work by the patient and the arch,
        # not by a manufacturing reference nobody says out loud.
        order = production.sale_id if production else self.env['sale.order']
        due, gap_state = self._due_for(production, usual)
        return {
            'id': wo.id,
            'name': wo.display_name,
            'operation': wo.name,
            # `name`, not `display_name`: lab_reports appends the sales order to the
            # latter, and the card already carries the order as its own chip.
            'production': production.name if production else '',
            'product': production.product_id.display_name if production else '',
            # What the appliance IS, for the tile. The plain name, not
            # `display_name`: that leads with the internal code ("[OCR-C08]
            # Hawleys Appliances"), which the bench does not say out loud and
            # which crowds a card that has one line to give it. Trimmed, because
            # several names on this database carry stray spaces.
            # (client, 2026-09-14)
            'product_name': (production.product_id.name or '').strip() if production else '',
            'patient': order.patient or '',
            # Whose case it is — the doctor/clinic the job goes back to.
            'clinic': order.partner_id.display_name if order else '',
            # The reference every other desk in the lab uses for this case.
            # (client, 2026-08-26)
            'order': order.name or '',
            # The PIECE where a case has two, the case's own arch otherwise: a
            # card reading "UL" on both halves tells the bench nothing about
            # which one it is holding. (client, 2026-09-12)
            'ul': (ARCH_SHORT.get(wo.arch)
                   or ((ul_labels or {}).get(wo.ul, '') if wo.ul else '')),
            'colour': wo.color_scheme or '',
            # When it is due out, and how that reads today.
            'due': due and fields.Date.to_string(due) or '',
            'due_label': due and self._day_label(due, gap_state) or '',
            'due_state': gap_state,
            'qty': wo.qty_production,
            'state': wo.state,
            'accepted_by': wo.accepted_by_id.name or '',
            'bench_user': wo.bench_user_id.name or '',
            'bench_user_id': wo.bench_user_id.id or False,
            # The second name, where this bench records one.
            'wants_finisher': wo.workcenter_id.is_finisher,
            'finisher': wo.finisher_user_id.name or '',
            'finisher_user_id': wo.finisher_user_id.id or False,
            # A scan there is still something to take back: the hand-over, until
            # the next bench takes the job on, or the acceptance.
            'can_undo': bool(wo.handed_over_at or wo.accepted_at)
                        and not (production and production.state in ('done', 'cancel'))
                        and not (wo._undo_blocked_by() if wo.handed_over_at else False),
            'accepted_at': self._clock(wo.accepted_at),
            'handed_over_at': self._clock(wo.handed_over_at),
            'next_station': wo.next_workcenter_id.sudo().display_name or '',
            # Two things that change what the bench does before it touches the job.
            'call_doctor': bool(order.is_pending_work),
            'urgent': order.priority in URGENT,
            # HOW LONG IT HAS STOOD HERE. A queue of nine hundred is not read
            # by date; it is read by what is going stale. The clock starts when
            # the job reached this bench, or when the case was raised where
            # nobody has scanned it here. (client, 2026-09-10)
            **self._age(wo, production),
            # A case on its second or third attempt: the bench should know before it
            # picks the job up, and the person about to restart it should see that
            # this would not be the first time. (client, 2026-08-29)
            'redo_count': production.lab_redo_count if production else 0,
            'redo_reason': (production.lab_redo_reason_id.name
                            if production and production.lab_redo_reason_id else ''),
            'can_restart': bool(production) and production.state in REDOABLE,
        }

    @api.model
    def _age(self, wo, production):
        """{here_days, case_days, stale}: how long at this bench, and in the lab."""
        now = fields.Datetime.now()
        arrived = None
        if wo.handed_from_workcenter_id and production:
            # The step before this one left at the moment this one arrived.
            # Of its own arch: the lower chain is banded after the upper, so
            # every upper hand-over otherwise read as the lower piece arriving.
            earlier = production.workorder_ids.filtered(
                lambda w, this=wo: (w.arch or '') == (this.arch or '')
                and w.sequence < this.sequence and w.handed_over_at)
            arrived = max(earlier.mapped('handed_over_at'), default=None)
        started = production.create_date if production else None
        # Never below zero: the database stamps create_date at the start of the
        # transaction, which can sit a fraction ahead of Python's clock, and a
        # timedelta of -0.2s reports itself as -1 day.
        here = max(0, (now - arrived).days) if arrived else None
        case = max(0, (now - started).days) if started else None
        return {
            'here_days': here,
            'case_days': case,
            # Standing longer than a working day with nobody having taken it.
            'stale': bool((here if here is not None else case or 0) >= STALE_DAYS),
        }

    @api.model
    def _job_label(self, wo):
        """How a job is named back to the bench: the manufacturing number it was
        scanned by, then the sales order the office quotes for the same case."""
        production = wo.production_id
        name = (production.name if production else '') or wo.display_name
        order = production.sale_id.name if production and production.sale_id else ''
        label = '%s · %s' % (name, order) if order else name
        # WHICH PIECE. Both arches of a two-piece case answer to one number, so
        # a message about a scan has to say which of them it moved or the bench
        # cannot tell the two confirmations apart. (client, 2026-09-12)
        if wo.arch:
            piece = _('Upper') if wo.arch == 'upper' else _('Lower')
            label = '%s (%s)' % (label, piece)
        return label

    @api.model
    def _clock(self, value):
        if not value:
            return ''
        return self._lab_time(value).strftime('%H:%M')

    # ------------------------------------------------------------------ actions
    @api.model
    def accept(self, workorder_id, person_id=None):
        wo = self.env['mrp.workorder'].browse(int(workorder_id))
        wo.action_station_accept()
        return self.get_station(wo.workcenter_id.id, person_id)

    @api.model
    def handover(self, workorder_id, person_id=None):
        wo = self.env['mrp.workorder'].browse(int(workorder_id))
        workcenter_id = wo.workcenter_id.id
        if self._needs_person(wo):
            return self._ask_person(wo, person_id)
        if self._needs_finisher(wo):
            return self._ask_finisher(wo, person_id)
        wo.action_station_handover()
        return self.get_station(workcenter_id, person_id)

    @api.model
    def assign_and_handover(self, workorder_id, user_id, person_id=None):
        """The popup's one tap: name who did it, then hand it on."""
        wo = self.env['mrp.workorder'].browse(int(workorder_id))
        workcenter_id = wo.workcenter_id.id
        wo.action_assign_bench_user(user_id)
        # One question at a time: at a finishing bench the answer to "who did it"
        # is followed by "who finished it", and only then does the job move.
        if self._needs_finisher(wo):
            return self._ask_finisher(wo, person_id)
        wo.action_station_handover()
        return self.get_station(workcenter_id, person_id)

    @api.model
    def assign_finisher_and_handover(self, workorder_id, user_id, person_id=None):
        """The second popup's one tap: name who finished it, then hand it on."""
        wo = self.env['mrp.workorder'].browse(int(workorder_id))
        workcenter_id = wo.workcenter_id.id
        wo.action_assign_finisher(user_id)
        if self._needs_person(wo):
            return self._ask_person(wo, person_id)
        wo.action_station_handover()
        return self.get_station(workcenter_id, person_id)

    @api.model
    def undo_scan(self, workorder_id, person_id=None):
        """Take back the last scan on this job — the card scanned by mistake."""
        wo = self.env['mrp.workorder'].browse(int(workorder_id))
        workcenter_id = wo.workcenter_id.id
        was_handover = bool(wo.handed_over_at)
        wo.action_station_undo_scan()
        return {
            'action': 'undone',
            'message': (_("%s is back on the bench.", self._job_label(wo))
                        if was_handover else
                        _("%s is waiting to be taken again.", self._job_label(wo))),
            'board': self.get_station(workcenter_id, person_id),
        }

    # A job about to leave the bench with nobody's name on it. The server still
    # refuses (see action_station_handover) — but a board that only says NO sends the
    # lead back to the card, down to the dropdown and back to the button. So the
    # refusal is returned as a QUESTION the board can ask in one tap: here is the job,
    # here are the people, who did it? (client, 2026-08-29)
    @api.model
    def _needs_person(self, wo):
        return bool(wo.accepted_at) and not wo.bench_user_id \
            and wo.state not in ('done', 'cancel')

    @api.model
    def _needs_finisher(self, wo):
        """A bench that records who finished the work, on a job with nobody named."""
        return bool(wo.accepted_at) and wo.workcenter_id.is_finisher \
            and not wo.finisher_user_id and wo.state not in ('done', 'cancel')

    @api.model
    def _ask_finisher(self, wo, person_id=None):
        """The second question, asked exactly like the first.

        Anyone posted to the bench may answer it, unlike "who did the work" -
        see action_assign_finisher. So the tiles are live for a technician too.
        """
        workcenter = wo.workcenter_id
        people = [p for p in self._bench_load(workcenter) if p['id'] != 'none']
        job = wo.production_id.name or wo.display_name
        return {
            'action': 'needs_finisher',
            'stage': 'finisher',
            'message': _(
                "%(job)s is finished at %(wc)s, which records who finished it. "
                "Choose the finishing technician and it hands on straight away.",
                job=job, wc=workcenter.display_name),
            'job': self._card(wo, self._ul_labels()),
            'people': people,
            'can_assign': True,
            'board': self.get_station(workcenter.id, person_id),
        }

    @api.model
    def _ask_person(self, wo, person_id=None, stage='handover'):
        """`stage` is what the answer will do: 'accepted' only names who is doing it
        (the job stays on the bench); 'handover' names who did it and hands it on."""
        workcenter = wo.workcenter_id
        role = wo._bench_role(workcenter)
        people = [p for p in self._bench_load(workcenter) if p['id'] != 'none']
        job = wo.production_id.name or wo.display_name
        if stage == 'accepted':
            message = _("%s is accepted, but no technician is named yet. Choose who — it "
                        "stays on the bench under their name.", job)
        else:
            message = _("No technician is named on %s. Choose who did the work and it "
                        "hands on straight away.", job)
        return {
            'action': 'needs_person',
            'stage': stage,
            'message': message,
            'job': self._card(wo, self._ul_labels()),
            'people': people,
            'can_assign': role in ('manager', 'lead'),
            'board': self.get_station(workcenter.id, person_id),
        }

    @api.model
    def _job_status(self, wo):
        """Where a job stands at its bench, in the board's own three words."""
        if wo.handed_over_at:
            return 'passed'
        return 'bench' if wo.accepted_at else 'waiting'

    @api.model
    def scan_to_change(self, code, workcenter_id=None, person_id=None):
        """Find a job so its technician, finisher or status can be corrected.

        READS ONLY, like the restart scan. Every one of these three is already
        settable somewhere on this board, but only in the course of doing
        something else - a name is asked for at the moment a job hands on, and
        a status only moves by scanning the job again. What the floor had no
        way to say was "this is right except for one thing": the wrong name was
        tapped at the hand-over, or a card was scanned twice. So the three are
        gathered into one correction, made deliberately and confirmed.
        (client, 2026-09-12)
        """
        workcenter = self.env['mrp.workcenter'].browse(int(workcenter_id)) \
            if workcenter_id else self._default_workcenter()
        if not workcenter:
            raise UserError(_("Choose a station before scanning."))
        wo = self._workorder_from_code(code, workcenter)
        if wo.workcenter_id != workcenter:
            raise UserError(_(
                "%(job)s is at %(where)s, not here. A job is corrected at the "
                "bench it is standing at.",
                job=self._job_label(wo),
                where=wo.workcenter_id.sudo().display_name))
        wo._check_bench_access(_('correct a job'))
        if wo.state in ('done', 'cancel'):
            raise UserError(_(
                "%s is finished, so there is nothing left to correct here.",
                self._job_label(wo)))
        people = [p for p in self._bench_load(workcenter) if p['id'] != 'none']
        role = wo._bench_role(workcenter)
        return {
            'job': self._card(wo, self._ul_labels()),
            'people': people,
            'status': self._job_status(wo),
            'wants_finisher': workcenter.is_finisher,
            'bench_user_id': wo.bench_user_id.id or False,
            'finisher_user_id': wo.finisher_user_id.id or False,
            # Giving work out stays the lead's call, exactly as on the card.
            'can_assign': role in ('manager', 'lead'),
        }

    @api.model
    def apply_change(self, workorder_id, bench_user_id=None, finisher_user_id=None,
                     status=None, person_id=None):
        """Apply a correction, each part through the rule that already guards it.

        Nothing here is a new way to write these fields: the technician goes
        through `action_assign_bench_user` (leads only), the finisher through
        `action_assign_finisher` (anyone at the bench), and the status moves one
        scan at a time through accept, hand over and undo - so a correction
        cannot reach a state that scanning could not, and every refusal the
        floor already knows still applies. (client, 2026-09-12)
        """
        wo = self.env['mrp.workorder'].browse(int(workorder_id)).exists()
        if not wo:
            raise UserError(_("That job no longer exists."))
        workcenter_id = wo.workcenter_id.id
        changed = []
        if bench_user_id is not None and (bench_user_id or False) != (
                wo.bench_user_id.id or False):
            wo.action_assign_bench_user(bench_user_id or False)
            changed.append(_("technician"))
        if finisher_user_id is not None and (finisher_user_id or False) != (
                wo.finisher_user_id.id or False):
            if not finisher_user_id:
                wo._check_bench_access(_('name the finishing technician'))
                wo.write({'finisher_user_id': False})
            else:
                wo.action_assign_finisher(finisher_user_id)
            changed.append(_("finisher"))
        if status and status != self._job_status(wo):
            self._move_status(wo, status)
            changed.append(_("status"))
        if not changed:
            raise UserError(_("Nothing was changed."))
        return {
            'board': self.get_station(workcenter_id, person_id),
            'message': _("%(job)s — %(what)s corrected.",
                         job=self._job_label(wo), what=', '.join(changed)),
        }

    @api.model
    def _move_status(self, wo, wanted):
        """Walk a job to the state asked for, one scan's worth at a time.

        A job's position is three stamps deep, and jumping straight to a stamp
        would skip the rules that hang off the steps between - a hand-over with
        nobody named, an undo the next bench has already built on. So this
        takes the same steps a person would, and stops the moment one of them
        refuses.
        """
        order = ('waiting', 'bench', 'passed')
        if wanted not in order:
            raise UserError(_("That is not a status a job can be in."))
        for _step in range(3):
            here = self._job_status(wo)
            if here == wanted:
                return True
            if order.index(wanted) > order.index(here):
                if here == 'waiting':
                    wo.action_station_accept()
                else:
                    wo.action_station_handover()
            else:
                wo.action_station_undo_scan()
            wo.invalidate_recordset()
        raise UserError(_("That job could not be moved to %s.", wanted))

    @api.model
    def scan_to_cancel(self, code, workcenter_id=None, person_id=None):
        """Find the step a scanned card refers to, so it can be cancelled here.

        READS ONLY. A job that does not belong at this bench - sent to the wrong
        station, or routed through one it never needed - used to sit at the head
        of the queue for good, because the only ways off the board were to do
        the work or to start the whole case again. Cancelling the step takes it
        off THIS bench and lets the case carry on to its next one; the dialog
        still asks why before anything happens. (client, 2026-09-14)
        """
        workcenter = self.env['mrp.workcenter'].browse(int(workcenter_id)) \
            if workcenter_id else self._default_workcenter()
        if not workcenter:
            raise UserError(_("Choose a station before scanning."))
        wo = self._workorder_from_code(code, workcenter)
        if wo.workcenter_id != workcenter:
            raise UserError(_(
                "%(job)s is at %(where)s, not here. A step is cancelled at the bench "
                "it is standing at.",
                job=self._job_label(wo), where=wo.workcenter_id.sudo().display_name))
        self._check_cancellable(wo)
        return {'job': self._card(wo, self._ul_labels())}

    @api.model
    def _check_cancellable(self, wo):
        """Refuse every cancel that would lose work or strand a case."""
        wo._check_bench_access(_('cancel a step'))
        if wo.state in ('done', 'cancel'):
            raise UserError(_("%s is already finished or cancelled.", self._job_label(wo)))
        production = wo.production_id
        if production and production.state in ('done', 'cancel'):
            raise UserError(_("%s is closed, so none of its steps can be cancelled.",
                              production.name))
        # Started here means somebody has put work into it: taking that back is
        # Correct's job, and cancelling over it would throw the work away.
        if wo.accepted_at or wo.state == 'progress':
            raise UserError(_(
                "%s has already been taken at this bench. Use Correct to set it back to "
                "waiting first, if it really does not belong here.", self._job_label(wo)))
        others = production.workorder_ids.filtered(
            lambda w: w.id != wo.id and w.state not in ('done', 'cancel')) \
            if production else wo.browse()
        if not others:
            raise UserError(_(
                "%s has no other step left to go to - cancelling this one would leave "
                "the case open with nothing to do. Start the case again or close it "
                "instead.", self._job_label(wo)))
        return True

    @api.model
    def cancel_step(self, workorder_id, reason, person_id=None):
        """Cancel one step of a case at this bench, and say why on the case.

        Only the step: the rest of the route stands, and the floor's own rule
        - a case is at its first unfinished step - hands the case to its next
        bench the moment this one is cancelled. The reason is required, because
        a cancelled step with no reason is a question nobody can answer later.
        """
        wo = self.env['mrp.workorder'].browse(int(workorder_id)).exists()
        if not wo:
            raise UserError(_("That step no longer exists."))
        reason = (reason or '').strip()
        if not reason:
            raise UserError(_("Say why this step is being cancelled."))
        self._check_cancellable(wo)
        workcenter_id = wo.workcenter_id.id
        label, step, bench = self._job_label(wo), wo.name, wo.workcenter_id.display_name
        production = wo.production_id
        # Elevated for the write only, after every check above has run as the
        # person asking: core's cancel also removes the step's calendar leave,
        # which a bench user has no right to touch.
        wo.sudo().action_cancel()
        production.sudo().message_post(body=_(
            "%(step)s cancelled at %(bench)s by %(who)s: %(reason)s",
            step=step, bench=bench, who=self.env.user.name, reason=reason))
        return {
            'board': self.get_station(workcenter_id, person_id),
            'message': _("%(job)s — %(step)s cancelled at %(bench)s.",
                         job=label, step=step, bench=bench),
        }

    @api.model
    def scan_to_restart(self, code, workcenter_id=None, person_id=None):
        """Find the job a scanned code refers to, so it can be started again.

        READS ONLY. Starting a case again is the one act on this board that
        cannot be undone, so the scan resolves the job and stops: the operator
        is then shown what they scanned and asked why, and `restart` does the
        work once they have answered. A scan that acted on the spot would make
        a mis-scan unrecoverable. (client, 2026-09-12)

        Refused here rather than after the question, so nobody picks a reason
        for a job that was never going to take it.
        """
        workcenter = self.env['mrp.workcenter'].browse(int(workcenter_id)) \
            if workcenter_id else self._default_workcenter()
        if not workcenter:
            raise UserError(_("Choose a station before scanning."))
        wo = self._workorder_from_code(code, workcenter)
        if wo.workcenter_id != workcenter:
            # sudo on the NAME alone: which bench holds a job is exactly what a
            # bench-bound user may not read, and being told where it is must not
            # be an access error.
            raise UserError(_(
                "%(job)s is at %(where)s, not here. A case is started again from "
                "the bench it is standing at.",
                job=self._job_label(wo),
                where=wo.workcenter_id.sudo().display_name))
        wo._check_bench_access(_('start work again'))
        production = wo.production_id
        if not production:
            raise UserError(_("This job has no manufacturing order to start again."))
        if production.state not in REDOABLE:
            raise UserError(_(
                "%(job)s is %(state)s, so it cannot be started again.",
                job=self._job_label(wo),
                state=dict(production._fields['state'].selection).get(
                    production.state, production.state)))
        return {
            'job': self._card(wo, self._ul_labels()),
            'reasons': self._redo_reasons(),
        }

    @api.model
    def restart(self, workorder_id, reason_id, note=None, person_id=None):
        """Start the whole case again from the first bench.

        The engine is `mrp.production.lab_redo` — this only decides who may ask for
        it and from where. A technician or a lead at the bench the job is standing at
        may; anybody else may not, which is the same rule as accepting or handing on.
        The reason is required by the model, and the board never offers a free-text
        box in its place: a reason list is what makes the redo report worth reading.
        (client, 2026-08-29)
        """
        wo = self.env['mrp.workorder'].browse(int(workorder_id))
        workcenter_id = wo.workcenter_id.id
        wo._check_bench_access(_('start work again'))
        production = wo.production_id
        if not production:
            raise UserError(_("This job has no manufacturing order to start again."))
        reason = self.env['lab.redo.reason'].sudo().browse(int(reason_id)).exists()
        if not reason:
            raise UserError(_("Choose why the case is being done again."))
        redo = production.sudo().lab_redo(reason, (note or '').strip() or False)
        return {
            'board': self.get_station(workcenter_id, person_id),
            'attempt': redo.attempt,
            'reason': reason.name,
            'operations': redo.operations_reset,
            'message': _(
                "%(job)s started again from the first bench — %(reason)s "
                "(attempt %(attempt)s).",
                job=production.name, reason=reason.name, attempt=redo.attempt),
        }

    @api.model
    def scan(self, code, workcenter_id=None, person_id=None, confirmed=False):
        """One scan, and the board works out what it means.

        A head with gloves on does not want to find the job in a list, decide whether it
        is an accept or a hand-over, and then press the right button. They point the
        camera at the job card. The state decides:

          arrived here      -> accept it
          accepted here     -> hand it on
          somewhere else    -> say where, and do nothing

        It used to act on the spot, on the reasoning that every outcome is reversible
        and a dialog on a shop floor is a thing people learn to dismiss. The lab asked
        for the confirmation anyway (2026-09-09), and the honest way to give it is to
        say what the scan MEANS before doing it: the board gets the job and the
        intended act, nothing is written, and the same call comes back with
        `confirmed` once the operator has agreed. A bench that scans a tray at a time
        can turn it off (mrp.workcenter.scan_confirm).
        """
        workcenter = self.env['mrp.workcenter'].browse(int(workcenter_id)) \
            if workcenter_id else self._default_workcenter()
        if not workcenter:
            raise UserError(_("Choose a station before scanning."))

        wo = self._workorder_from_code(code, workcenter)
        if wo.workcenter_id != workcenter:
            return {
                'action': 'elsewhere',
                'message': _(
                    "%(job)s is at %(where)s, not here. Scan the station label to move "
                    "it, or fetch it from there.",
                    # sudo: the OTHER station is exactly what a bench-bound user may not read,
                    # and being told where the job is must not be an access error.
                    job=wo.display_name, where=wo.workcenter_id.sudo().display_name),
                'board': self.get_station(workcenter.id, person_id),
            }
        # Nothing above this line has changed anything: 'elsewhere' is a reading, not
        # an act, so it is never worth confirming.
        if workcenter.scan_confirm and not confirmed:
            return self._confirm_scan(wo, code, person_id)
        if not wo.accepted_at:
            wo.action_station_accept()
            # Accepted with nobody's name on it: ask now, while the card is in the
            # lead's hand, rather than at the hand-over hours later. (client, 2026-08-29)
            if self._needs_person(wo):
                return self._ask_person(wo, person_id, stage='accepted')
            return {'action': 'accepted',
                    # The number on the job card, not the work order's display name:
                    # "MO/280575 - Wire Bending/ Adams Clasp" buries the one thing the
                    # person just scanned. (client, 2026-08-29)
                    'message': _("%s accepted.", self._job_label(wo)),
                    'board': self.get_station(workcenter.id, person_id)}
        if self._needs_person(wo):
            return self._ask_person(wo, person_id)
        if self._needs_finisher(wo):
            return self._ask_finisher(wo, person_id)
        wo.action_station_handover()
        return {'action': 'handed',
                'message': _("%(job)s handed to %(next)s.", job=self._job_label(wo),
                             next=wo.next_workcenter_id.sudo().display_name or _('the last step')),
                'board': self.get_station(workcenter.id, person_id)}

    @api.model
    def _check_floor_reader(self):
        """The elevated look-ups are for the floor, not for every login.

        `find_cases` and `locate` read under sudo so a bench-bound user can be
        told where a case went. As public methods with no check, that also
        answered any internal or portal user every order's patient, doctor and
        bench over RPC. The Station Board itself is a manufacturing-user menu.
        (review, 2026-09-15)
        """
        if not (self.env.su or self.env.user.has_group('mrp.group_mrp_user')):
            raise AccessError(_("Only manufacturing users can look up where a case is."))

    @api.model
    def find_cases(self, term, workcenter_id=None):
        """Search cases the way Track Order does, and say where each one stands.

        Part of the order number, the patient or the doctor - nobody reading an
        order out over the phone says the prefix, and "anything for Dr Susmitha"
        is how the question arrives. Each case comes back with every piece still
        in the lab: the bench it is at, and whether it is waiting, on the bench
        or passed on. A case with nothing open says why - not started, or
        finished - rather than vanishing from the answer.

        Read elevated, for the same reason `locate` is: which bench holds a case
        is exactly what a bench-bound user cannot read, and asking where a case
        went must not be an access error. Only what the board already shows is
        returned. (client, 2026-09-14)
        """
        self._check_floor_reader()
        term = (term or '').strip()
        if len(term) < 2:
            return []
        orders = self.env['sale.order'].sudo().search(
            ['|', '|', ('name', 'ilike', term), ('patient', 'ilike', term),
             ('partner_id', 'ilike', term)],
            order='date_order desc, id desc', limit=FIND_LIMIT)
        if not orders:
            return []
        productions = self.env['mrp.production'].sudo().search(
            [('sale_id', 'in', orders.ids)])
        live = productions.filtered(lambda m: m.state not in ('done', 'cancel'))
        rows = self.env['report.lab.floor']._open_steps_by_arch(live)
        steps = {w.id: w for w in self.env['mrp.workorder'].sudo().browse(
            [row['id'] for row in rows])}
        states = dict(self.env['mrp.workorder']._fields['state']
                      ._description_selection(self.env))
        here_id = int(workcenter_id) if workcenter_id else False
        pieces = defaultdict(list)
        for row in rows:
            wo = steps.get(row['id'])
            if not wo:
                continue
            production = wo.production_id
            pieces[production.sale_id.id].append({
                'workorder_id': wo.id,
                'production': production.name,
                'product_name': (production.product_id.name or '').strip(),
                'arch': ARCH_SHORT.get(wo.arch, ''),
                'operation': wo.name or '',
                'station': wo.workcenter_id.display_name or '',
                'here': bool(here_id) and wo.workcenter_id.id == here_id,
                'standing': ('passed' if row['gone']
                             else 'bench' if row['taken'] else 'waiting'),
                'state_label': states.get(wo.state, ''),
                'technician': wo.bench_user_id.name or '',
            })
        out = []
        for order in orders:
            mine = productions.filtered(lambda m, o=order: m.sale_id == o)
            status = ''
            if not pieces.get(order.id):
                if not mine:
                    status = _("Not in production yet")
                elif all(m.state == 'cancel' for m in mine):
                    status = _("Cancelled")
                else:
                    status = _("Finished in the lab")
            out.append({
                'order_id': order.id,
                'order': order.name,
                'patient': order.patient or '',
                'clinic': order.partner_id.display_name or '',
                'date': (self._lab_time(order.date_order).strftime('%d %b %Y')
                    if order.date_order else ''),
                'pieces': pieces.get(order.id, []),
                'status': status,
            })
        return out

    @api.model
    def locate(self, code, workcenter_id=None):
        """Where is this job right now? Answered without touching it.

        The board could already say "it is at Trimming" - but only as the
        refusal of a scan, which means asking the question by doing something.
        A head with a doctor on the phone wants the answer and nothing else, so
        this reads and returns: the station, the step, who has it, and what
        happens next. Elevated for the READ alone - which bench a job sits at is
        exactly what a bench-bound user cannot see, and being told where their
        own case went must not be an access error. (client, 2026-09-09)
        """
        self._check_floor_reader()
        workcenter = self.env['mrp.workcenter'].browse(int(workcenter_id)) \
            if workcenter_id else self._default_workcenter()
        wo = self._workorder_from_code(code, workcenter)
        # The step the job is AT, not the step this bench would like it to be
        # at. `_workorder_from_code` prefers an operation at the station doing
        # the asking - right for a scan, which acts here - but the question
        # "where is it" has one true answer and it is the same from every
        # bench: the first step still open. (client, 2026-09-09)
        privileged = wo.sudo()
        steps = privileged.production_id.workorder_ids.filtered(
            lambda w: w.state not in ('done', 'cancel')).sorted(
                lambda w: (w.sequence, w.id))
        privileged = steps[:1] or privileged
        here = privileged.workcenter_id == workcenter
        production = privileged.production_id
        order = production.sale_id if production else self.env['sale.order']
        states = dict(self.env['mrp.workorder']._fields['state']
                      ._description_selection(self.env))
        return {
            'found': True,
            'id': privileged.id,
            'job': self._job_label(privileged),
            'operation': privileged.name or '',
            'station': privileged.workcenter_id.display_name or '',
            'here': here,
            'state': privileged.state,
            'state_label': states.get(privileged.state, ''),
            'accepted': bool(privileged.accepted_at),
            'accepted_at': self._clock(privileged.accepted_at),
            'technician': privileged.bench_user_id.name or '',
            'next_station': privileged.next_workcenter_id.display_name or '',
            'patient': order.patient or '',
            'clinic': order.partner_id.display_name if order else '',
            'production': production.name if production else '',
        }

    @api.model
    def _confirm_scan(self, wo, code, person_id=None):
        """What this scan would do, said before it does it.

        The job is named the way the card is - the number the person just scanned -
        and the act is named in the words of the board's own buttons, so the
        question reads as the thing about to happen rather than as a warning.
        """
        workcenter = wo.workcenter_id
        accepting = not wo.accepted_at
        if accepting:
            message = _("Accept %(job)s at %(wc)s?",
                        job=self._job_label(wo), wc=workcenter.display_name)
        else:
            message = _("Finish %(job)s here and hand it to %(next)s?",
                        job=self._job_label(wo),
                        next=wo.next_workcenter_id.sudo().display_name
                        or _('the last step'))
        return {
            'action': 'confirm',
            'intent': 'accept' if accepting else 'handover',
            # Sent back with the answer: the board asks the same question again
            # rather than holding a job id the floor may have moved meanwhile.
            'code': code,
            'message': message,
            'job': self._card(wo, self._ul_labels()),
            'board': self.get_station(workcenter.id, person_id),
        }

    @api.model
    def _workorder_from_number(self, code, open_only):
        """Open steps for a case typed as a bare number, without its prefix.

        "287140" finds SO287140. The DIGITS must match exactly rather than
        merely end the name, so "140" does not pick SO287140 out of hundreds.
        The sales order is tried first - it is the number every sheet and every
        phone call carries - and the manufacturing reference only when no order
        has that number.
        """
        Workorder = self.env['mrp.workorder'].sudo()
        digits = (code or '').strip()
        if not digits.isdigit():
            return Workorder
        pattern = '%' + digits
        orders = self.env['sale.order'].sudo().search(
            [('name', '=ilike', pattern)], limit=50).filtered(
                lambda o: re.sub(r'\D', '', o.name or '') == digits)
        if orders:
            leaf = [('production_id.sale_id', 'in', orders.ids)]
        else:
            jobs = self.env['mrp.production'].sudo().search(
                [('name', '=ilike', pattern)], limit=50).filtered(
                    lambda m: re.sub(r'\D', '', m.name or '') == digits)
            if not jobs:
                return Workorder
            leaf = [('production_id', 'in', jobs.ids)]
        return Workorder.search(leaf + open_only, order='sequence, id')

    @api.model
    def _workorder_from_code(self, code, workcenter):
        """Find the job a scanned code refers to.

        The lab prints several different sheets and they do not all carry the same
        number: the job label prints the manufacturing reference, the Production Order
        sheet prints the SALES order, and Odoo's own work order barcode is a third form
        again. A scanner that only understands one of them is a scanner the floor learns
        to distrust, so all of them are accepted — and the sales order especially,
        because every sheet already in circulation carries it. (client, 2026-08-26)

        Preference always goes to a work order AT THIS STATION: one order has several
        operations, and the lead scanning at Trimming means the trimming step.
        """
        code = (code or '').strip()
        if not code:
            raise UserError(_("Nothing was scanned."))
        Workorder = self.env['mrp.workorder']
        open_only = [('state', 'not in', ('done', 'cancel'))]

        # Each number the lab prints, most specific first. The work order barcode names
        # one operation; the manufacturing reference names the case; the sales order may
        # cover several cases, so it is tried last.
        fields_ = ('barcode', 'production_id.name', 'production_id.sale_id.name')

        # sudo for the LOOKUP only: a bench user scanning a card must be told the job
        # is at another station, which they cannot read. Every action taken afterwards
        # goes through _check_bench_access.
        # The case-insensitive fallback below is a LIKE, so the operator's own text
        # becomes a PATTERN. Typing a bare "%" matched the first open job in the
        # database and accepted — or finished — somebody else's work; "_" is the same
        # hole one character wide. Escaped here rather than rejected, so a code that
        # legitimately contains one still scans. (client, 2026-08-27)
        pattern = code.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')

        # sudo for the LOOKUP only: a bench user scanning a card must be told the job
        # is at another station, which they cannot read. Every action taken afterwards
        # goes through _check_bench_access.
        def lookup(operator):
            value = pattern if 'like' in operator else code
            for field in fields_:
                found = Workorder.sudo().search(
                    [(field, operator, value)] + open_only, order='sequence, id')
                if found:
                    return found
            return Workorder.sudo()

        candidates = lookup('=')
        if not candidates and code.upper().startswith('WO-') and code[3:].isdigit():
            candidates = Workorder.sudo().browse(int(code[3:])).exists()
        if not candidates:
            # Typed, not scanned: the camera needs HTTPS and the fallback is a keyboard,
            # where "so260880" is the same job to everyone except the database.
            candidates = lookup('=ilike')
        if not candidates:
            # Just the number. Nobody reading an order out over the phone says the
            # prefix, and typing it on a tablet is the slow part.
            # (client, 2026-09-14)
            candidates = self._workorder_from_number(code, open_only)
        if not candidates:
            # A station label was scanned instead of a job. Say so plainly — the two
            # labels look alike and the mistake is made constantly.
            if self.env['mrp.workcenter'].sudo().search_count([('scan_code', '=', code)]):
                raise UserError(_(
                    "%s is a STATION label. Scan the job card instead — the station "
                    "label is for moving a job you already have open.", code))
            raise UserError(_(
                "Nothing open matches %s. Check the job card, or the job may already be "
                "finished.", code))

        here = candidates.filtered(lambda w: w.workcenter_id == workcenter)
        # A two-piece case puts BOTH arches under one number, and they can stand
        # at the same bench at once. The piece ALREADY TAKEN at this bench comes
        # first: a scan on a bench holding one open piece means "I have finished
        # this one", and preferring the untaken piece turned that scan into an
        # accept of the other one - the first piece was never handed on and the
        # technician had two jobs open at once. One piece at a time: accept it,
        # finish it, and the next scan starts the next piece.
        # (client, 2026-09-16, reversing 2026-09-12)
        found = (here or candidates).sorted(
            lambda w: (not w.accepted_at, w.sequence, w.id))[:1]
        # A job stored as "To Close" with steps still open cannot be started — core
        # refuses to unplan it — and nothing recomputes the row on its own. The scan is
        # where it matters, so the scan is where it gets put right. (client, 2026-09-09)
        self.env['mrp.production']._lab_repair_premature_to_close(
            found.production_id.ids)
        # Back into the caller's environment: the sudo above was for finding it, and a
        # sudo recordset would sail straight through every check that follows.
        return found.with_env(self.env)

    @api.model
    def assign(self, workorder_id, user_id, person_id=None):
        """Give one job, or a handful picked together, to a technician.

        `workorder_id` takes a list as readily as a single id: handing out the morning
        one job at a time is how a lead stops using the board by Wednesday.
        """
        ids = workorder_id if isinstance(workorder_id, (list, tuple)) \
            else [workorder_id]
        wos = self.env['mrp.workorder'].browse([int(i) for i in ids])
        if not wos:
            raise UserError(_("Pick the jobs to give out first."))
        stations = wos.workcenter_id
        if len(stations) != 1:
            raise UserError(_(
                "Those jobs are at different stations. Give out one station's work at "
                "a time — a technician is posted to a bench, not to the whole floor."))
        wos.action_assign_bench_user(user_id)
        return self.get_station(stations.id, person_id)

    @api.model
    def move_by_scan(self, workorder_id, code, person_id=None):
        """Send a job to another station from the board, by scanning its label."""
        wo = self.env['mrp.workorder'].browse(int(workorder_id))
        origin = wo.workcenter_id.id
        result = wo.move_to_scanned_workcenter(code)
        result['board'] = self.get_station(origin, person_id)
        return result
