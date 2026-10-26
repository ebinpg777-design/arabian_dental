# -*- coding: utf-8 -*-
"""Where the field is RIGHT NOW - the live layer over the trail.

The trail (location_ping.py) already records where an executive's phone went while
their day was running, and the day sheet draws it afterwards. This file is everything
that reads the same pings while the day is still going on (client, 2026-10-19:
"implement live GPS in field executive visits and travel", with the gas-delivery
project's live map as the pattern):

* **A last position on the user** - six small fields written forward-only with every
  batch, so the live map and the desk board read one row per executive instead of
  the day's trail.
* **A status nobody types**: moving / at a clinic / stopped / offline / off, from the
  age of the last fix, the speed and the open visit.
* **Arrival the trail noticed.** A ping inside a planned clinic's radius stamps the
  visit `auto_arrived_at`; one well outside an open visit's radius stamps
  `auto_left_at`. Neither changes the visit - My Day nudges the executive to check in
  or out, because a check-in is an act and a note, not a geofence.
* **Halts**: stopped ten minutes or more somewhere that is not a clinic, as records
  (`lab.location.halt`), re-read idempotently.
* **The trip's km from the trail**, beside the odometer claim.
* **`get_live_map`**: one call that gives the manager's map (and the executive's own
  mini-map) everything: positions, trails split at recording gaps, doors, halts,
  over-speed points, the next clinic with an ETA.

All of it is derived from pings that only exist while the executive is on duty, so
the privacy boundary stays the one the trail already has.
"""
import json
import logging
import secrets
from datetime import datetime, timedelta

from .field_app import APP_LIVE_SECONDS, IDLE_CHECK_SECONDS
from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from .lab_visit import has_fix, metres_between
from .local_day import local_midnight_utc, tz_name

_logger = logging.getLogger(__name__)

# One colour per executive on the map, by position in the day's roster.
PALETTE = ['#2563eb', '#dc2626', '#059669', '#d97706', '#7c3aed', '#db2777',
           '#0891b2', '#65a30d', '#ea580c', '#4f46e5', '#0d9488', '#be123c']
MOVING_KMH = 4.0            # below this a fix is "standing", whatever the GPS jitter says
JITTER_M = 15.0             # two fixes closer than this are the same place
MAX_KMH = 150.0             # a jump faster than this is a GPS error, not a journey
TRAIL_GAP_SECONDS = 900     # silence longer than this breaks the trail into a new leg
# heard from within the app's idle report-in, with slack: the phone is online
HEARD_RECENT_SECONDS = IDLE_CHECK_SECONDS + 120
MAX_TRAIL_POINTS = 1500     # what the browser is sent per executive per day
TRAIL_ACCURACY_M = 100      # a fix at this many metres or worse is not drawn (see _trail_accuracy)
MAX_OVERSPEED_POINTS = 400
HALT_RADIUS_M = 60.0        # how far a "stopped" phone may drift and still be stopped
HALT_SPEED_KMH = 5.0
NEAR_CLINIC_M = 500.0       # a halt this close to the day's clinic is named after it
LEAVE_MARGIN_M = 150.0      # outside radius + this = left the clinic
ROAD_FACTOR = 1.3           # straight line -> road, for the ETA
ETA_FLOOR_KMH = 20.0        # an executive who just stopped is not stuck there


def split_legs(points, gap_seconds=TRAIL_GAP_SECONDS):
    """[[point, ...], ...] and [(from, to)] - the trail cut wherever the recording
    stopped for longer than `gap_seconds`. A point is (ts, lat, lon, speed)."""
    legs, gaps, leg = [], [], []
    last = None
    for point in points:
        if last is not None and (point[0] - last[0]).total_seconds() > gap_seconds:
            gaps.append((last[0], point[0]))
            if leg:
                legs.append(leg)
            leg = []
        leg.append(point)
        last = point
    if leg:
        legs.append(leg)
    return legs, gaps


# Travel modes (2026-10-23): what each stretch of the trail was - a drive, a walk,
# standing still, or a stretch the phone crossed with nothing reliable recorded.
# A travel claim is about the vehicle: a walk between two doors is not a kilometre
# to reimburse, and a blind stretch is a straight line under the road.
#
# The phone's own motion sensors decide first where it gave them (the Lab app from
# 1.0.4 sends Android's activity recognition with every fix: in a vehicle / on foot /
# still) - they tell a traffic jam at walking pace from a walk, which no speed can.
# Without them, speed decides, read the way a person reads a trail by eye.
VEHICLE_KMH = 10.0        # from this speed the person is in a vehicle; below, on foot
DRIFT_KMH = 1.0           # slower than this even at best: a phone on a desk, wandering
FOOT_MAX_KMH = 20.0       # the sensors' "on foot" is believed up to this pace
BLIND_SECONDS = 120       # two reliable fixes this far apart in time...
BLIND_METRES = 500.0      # ...and in distance: the phone moved with nothing reliable between
FOOT_MAX_M = 1500.0       # a "walk" this long between two fixes is not one
STAY_M = 50.0             # a fix this close to the last one is the phone still there (a heartbeat)
PAUSE_SECONDS = 240       # a stop this long - a heartbeat, a visit - is where a walk begins or ends
CRAWL_SECONDS = 1800      # walking pace between two drives, with no stop in it: traffic
SHORT_VEHICLE_S = 20      # a "drive" this short inside a walk is a GPS flick, not a drive


def _step(a, b):
    """(metres, seconds, implied km/h) from point `a` to point `b`."""
    metres = metres_between(a[1], a[2], b[1], b[2]) or 0.0
    seconds = max(1.0, (b[0] - a[0]).total_seconds())
    return metres, seconds, metres / seconds * 3.6


def _motion(point):
    """What the phone's sensors said at this fix ('vehicle'/'foot'/'still'), or None."""
    return point[8] if len(point) > 8 else None


EXCURSION_FIXES = 3        # a run of at most this many fixes...
EXCURSION_M = 100.0        # ...at least this far from the fixes either side of it...
EXCURSION_BACK_M = 60.0    # ...those two this close together: the phone was back...
EXCURSION_KMH = 25.0       # ...one way of it faster than this...
EXCURSION_SECONDS = 120    # ...in this little time


def drop_excursions(points):
    """The trail without the fixes a phone put somewhere it never went.

    Standing at a clinic, a phone now and then reports a fix - two or three at most -
    hundreds of metres away at a confident "7 m", and is back where it was seconds
    later: drawn, a star of spikes round the clinic; measured, km nobody drove
    (ASHIQ, 2026-10-26: four such runs in fifteen minutes, 250 m to 1.2 km out;
    Abdul Latheef the same morning: seven fixes in eight minutes while his phone's
    own sensors said "still"; a week of the team's fixes held 31 in 14,274, one of
    them 8 km out). A run is dropped when
      * the fixes either side of it are within EXCURSION_BACK_M of each other, and
        each of its fixes is EXCURSION_M or more from both;
      * one way of it - out or back, within EXCURSION_SECONDS - was faster than
        EXCURSION_KMH;
      * the phone did not say it was riding there (_riding), and said it was
        standing at one end of it (_standing).
    A real errand at that pace is a ride: a riding phone reads its speed or its
    sensors say so, and it is moving at both ends. The fixes stay stored; the map,
    the km and the halts read the trail without them.
    """
    if len(points) < 3:
        return list(points)
    out, i, n = [points[0]], 1, len(points)
    while i < n:
        skip = next((k for k in range(1, EXCURSION_FIXES + 1)
                     if i + k < n and _excursion(out[-1], points[i - 1:i + k], points[i + k])), 0)
        if skip:
            i += skip
        else:
            out.append(points[i])
            i += 1
    return out


def _excursion(before, run, after):
    """Whether run[1:] - run[0] is the fix just before it, which a speed the server
    derived was measured from - is fixes the phone never went to, between `before`
    and `after`."""
    prior, run = run[0], run[1:]
    if (metres_between(before[1], before[2], after[1], after[2]) or 0.0) > EXCURSION_BACK_M:
        return False
    for prev, point in zip([prior] + run[:-1], run):
        if _riding(point, prev):
            return False
        if any((metres_between(point[1], point[2], other[1], other[2]) or 0.0) < EXCURSION_M
               for other in (before, after)):
            return False
    if not (_standing(before) or _standing(after)):
        return False
    return any(secs <= EXCURSION_SECONDS and kmh > EXCURSION_KMH
               for _metres, secs, kmh in (_step(before, run[0]), _step(run[-1], after)))


def _riding(point, prev):
    """Whether the phone said it was riding at `point`: by its own speed reading
    where it gave one, else by its motion sensor. A speed the phone did not give is
    the ground covered since the fix before, worked out on arrival (location_ping
    record) - at a fix the phone never went to, that is the jump itself, 60 to 145
    km/h - so a speed equal to that is no reading."""
    speed = point[3] or 0.0
    if abs(speed - round(_step(prev, point)[2], 1)) >= 0.11:
        return speed >= VEHICLE_KMH
    return _motion(point) == 'vehicle'


def _standing(point):
    """Whether the phone stood at `point`: its sensors said still, or it was barely
    moving (a reading or the ground covered since the fix before, alike)."""
    return _motion(point) == 'still' or (point[3] or 0.0) < MOVING_KMH


def _stays(points):
    """For each point, the index of the first point of the stay it belongs to: a
    phone saying again and again, minutes apart, that it is where it was (the
    heartbeats of a parked phone) is one stay, begun when it got there. A short
    step is never a stay - the trail only keeps one under STAY_M after a heartbeat's
    wait, and a crawl in traffic is 50 m a minute."""
    first = [0] * len(points)
    for i in range(1, len(points)):
        metres, secs, _kmh = _step(points[i - 1], points[i])
        stays = metres < STAY_M and (points[i][3] or 0.0) < MOVING_KMH \
            and PAUSE_SECONDS <= secs <= TRAIL_GAP_SECONDS
        first[i] = first[i - 1] if stays else i
    return first


def classify_modes(points):
    """One mode per point - 'vehicle', 'foot', 'blind' or 'still' - for the stretch
    that ENDS at it (the first point is 'still'). A point is (ts, lat, lon, speed,
    ..., motion at [8]); the speed is the phone's own reading on arrival where it gave
    one (Doppler) - the speed at the START of a stretch belongs to the stretch before.

    * The sensors, where the phone gave them: 'vehicle' is a drive at any pace (a
      jam); 'foot' is a walk unless even the slowest reading of the stretch is
      faster than anyone walks.
    * Leaving a stay, the phone may have set off any time after it got there: a
      heartbeat repeats the last position with a fresh time, and timing the stretch
      from the last repeat made a 600 m walk out of a hospital "21 km/h". So the
      stretch is timed from the start of the stay - the slowest it can have been -
      for telling a walk from a drive.
    * Slower than DRIFT_KMH even at the fastest reading is a phone on a desk: still.
    * Far and long with nothing between, and not a walk: blind (travel, but a
      straight line under the road).
    * Then the corrections a reader makes by eye, never across a recording gap and
      never against the sensors: a walking-pace stretch between two drives with no
      stop in it, shorter than CRAWL_SECONDS, is traffic; a "drive" shorter than
      SHORT_VEHICLE_S inside a walk is a GPS flick.
    A jitter step, an impossible jump and a recording gap are 'still': never distance.
    """
    n = len(points)
    modes = ['still'] * n
    seconds = [0.0] * n
    sure = [False] * n          # the sensors said so: never smoothed away
    paused = [False] * n        # a stop of PAUSE_SECONDS or more ends here
    first = _stays(points)
    for i in range(1, n):
        a, b = points[i - 1], points[i]
        metres, secs, fastest = _step(a, b)
        seconds[i] = secs
        if secs > TRAIL_GAP_SECONDS:
            # A long silence after which the phone is somewhere else is a journey the
            # phone did not record: linked, counted, measured along its road (mode_km).
            # One that ends where it began is the phone still there. (client,
            # 2026-10-24: "not recorded time should not needed. It should be linked.")
            # A "journey" faster than anything on the road is a GPS error: not counted.
            modes[i] = 'blind' if metres > BLIND_METRES and fastest <= MAX_KMH else 'gap'
            continue
        if metres < JITTER_M or fastest > MAX_KMH:
            paused[i] = secs >= PAUSE_SECONDS
            continue
        since = (b[0] - points[first[i - 1]][0]).total_seconds()
        slowest = metres / since * 3.6 if since > secs else fastest
        reported = b[3] or 0.0
        if reported > MAX_KMH:
            reported = 0.0          # a jump read as a speed, not a speedometer
        motion = _motion(b)
        if secs > BLIND_SECONDS and metres > BLIND_METRES and \
                (slowest >= VEHICLE_KMH or metres > FOOT_MAX_M):
            modes[i] = 'blind'
        elif motion == 'vehicle':
            modes[i], sure[i] = 'vehicle', True
        elif motion == 'foot' and slowest < FOOT_MAX_KMH:
            modes[i], sure[i] = 'foot', True
        elif max(fastest, reported) < DRIFT_KMH:
            paused[i] = secs >= PAUSE_SECONDS
        elif max(slowest, reported) >= VEHICLE_KMH:
            modes[i] = 'vehicle'
        else:
            modes[i] = 'foot'
        # leaving a stay of a pause or more: a walk can begin here
        paused[i] = paused[i] or since - secs >= PAUSE_SECONDS
    _smooth_modes(modes, seconds, sure, paused, 'foot', 'vehicle', CRAWL_SECONDS, pauses_keep=True)
    _smooth_modes(modes, seconds, sure, paused, 'vehicle', 'foot', SHORT_VEHICLE_S)
    return ['still' if mode == 'gap' else mode for mode in modes]


def _smooth_modes(modes, seconds, sure, paused, inner, outer, max_seconds, pauses_keep=False):
    """A run of `inner` (stills inside it allowed) shorter than `max_seconds`, with
    `outer` on both sides, becomes `outer` - unless the sensors vouched for any of it,
    or (`pauses_keep`) it holds a stop, the step into it or out of it included. A
    recording gap ('gap') ends a run and is never looked across."""
    n, i = len(modes), 0
    while i < n:
        if modes[i] != inner:
            i += 1
            continue
        j = i
        while j < n and modes[j] in (inner, 'still'):
            j += 1
        before = next((modes[k] for k in range(i - 1, -1, -1) if modes[k] != 'still'), None)
        after = modes[j] if j < n else None
        if before == outer and after == outer \
                and not any(sure[k] for k in range(i, j)) \
                and not (pauses_keep and any(paused[k] for k in range(i, min(j + 1, n)))) \
                and sum(seconds[k] for k in range(i, j) if modes[k] == inner) < max_seconds:
            for k in range(i, j):
                if modes[k] == inner:
                    modes[k] = outer
        i = j


DRIVE_ROAD_FACTOR = 1.5        # a drive's road at most this times its step...
DRIVE_ROAD_SLACK_M = 50.0      # ...plus this, or it is a snap to the road beside it


def road_metres(road):
    """The length of a road as lab.location.road holds it: [[lat, lon], ...]."""
    return sum(metres_between(a[0], a[1], b[0], b[1]) or 0.0 for a, b in zip(road, road[1:]))


def mode_km(points, modes=None, steps=None):
    """Distance along the trail by mode: the steps trail_km counts, each laid to the
    stretch it ends. 'travel' (vehicle + blind) is what a travel claim is judged
    against; 'foot' is a walk; 'total' is trail_km.

    Every drive and every blind stretch - two fixes far apart with nothing recorded
    between, however long the silence - counts the road between its two fixes where
    it is known (`steps`: lab.location.road's, filled every five minutes), never less
    than the straight line. Straight lines cut every corner and every gap: 119 km on
    a day the vehicle read 128, 88 on one it read 105. (client, 2026-10-24: "the KM
    showing also wrong... a big problem on reimbursing the KM allowance") 'blind_road'
    says how much of 'blind' is road."""
    modes = modes or classify_modes(points)
    out = {'vehicle': 0.0, 'foot': 0.0, 'blind': 0.0}
    by_road = 0.0
    for i in range(1, len(points)):
        mode = modes[i]
        if mode not in out:
            continue
        metres = _step(points[i - 1], points[i])[0]
        if mode in ('vehicle', 'blind') and steps:
            road = road_step(steps, points[i - 1], points[i])
            if road and len(road) > 1:
                along = road_metres(road)
                # A drive's step is a few hundred metres at most: a road far longer
                # than the line between its two fixes is one snapped to the road
                # beside it (the gaps' own guard is applied when their road is read).
                if mode == 'blind' or along <= DRIVE_ROAD_FACTOR * metres + DRIVE_ROAD_SLACK_M:
                    metres = max(metres, along)
                    if mode == 'blind':
                        by_road += metres
        out[mode] += metres / 1000.0
    out = {mode: round(km, 2) for mode, km in out.items()}
    out['blind_road'] = round(by_road / 1000.0, 2)
    out['travel'] = round(out['vehicle'] + out['blind'], 2)
    out['total'] = round(out['vehicle'] + out['foot'] + out['blind'], 2)
    return out


def trail_km(points):
    """Distance along the trail, skipping jitter and jumps, and never across a gap."""
    km = 0.0
    for leg in split_legs(points)[0]:
        for a, b in zip(leg, leg[1:]):
            metres = metres_between(a[1], a[2], b[1], b[2]) or 0.0
            seconds = (b[0] - a[0]).total_seconds() or 1.0
            if metres < JITTER_M or metres / seconds * 3.6 > MAX_KMH:
                continue
            km += metres / 1000.0
    return round(km, 2)


def thin_runs(points, modes, limit):
    """[(point, mode)] - `points` thinned to about `limit` like downsample(), but never
    across a change of mode: the point where a drive turns into a walk is always
    kept, and each kept point carries the mode of the whole stretch it ends, not of
    its own last step. Thinned the plain way, a walk or a blind stretch between two
    kept points was drawn as road - or a road as a walk. (2026-10-23)"""
    n = len(points)
    step = max(1, -(-n // max(1, limit)))
    kept = []
    carried = None              # the run's mode so far, carried over still steps
    span = 'still'              # the moving mode of the steps since the last kept point
    for i, (point, mode) in enumerate(zip(points, modes)):
        if mode != 'still':
            span = carried = mode
        after = modes[i + 1] if i + 1 < n else None
        turns = after not in (None, 'still') and carried is not None and after != carried
        if i == 0 or i == n - 1 or i % step == 0 or turns:
            kept.append((point, span))
            span = 'still'
    return kept


ROAD_STEP_TOLERANCE_M = 150.0   # a known road must set off this close to its step's start fix


def road_step(steps, start, end):
    """The road from fix `start` to fix `end` as lab.location.road holds it - keyed by
    the end fix's time - or None when it is not known for THIS step. A one-vertex entry
    is known: that step is drawn straight (a fix snapped to the wrong road).

    Fixes that reach the server late - a phone back in coverage sending what it took
    meanwhile (2026-10-24) - land between two fixes whose road was already read. The
    step ending at the later fix now starts at a new fix, and its stored road, set off
    from the earlier one, would be drawn running back there. A road that does not set
    off near the step's start is not known for it; the service is asked again."""
    if not steps:
        return None
    road = steps.get(str(epoch(end[0])))
    if not road:
        return None
    if len(road) > 1 and \
            (metres_between(start[1], start[2], road[0][0], road[0][1]) or 0.0) > ROAD_STEP_TOLERANCE_M:
        return None
    return road


def drawn_leg(leg, index_of, points, steps=None):
    """A thinned leg [(point, mode)] as the map draws it: [lat, lon, epoch, speed, mode]
    a vertex. A blind stretch whose road is known (`steps`: lab.location.road's, keyed
    by the time of the fix each step ends at) is drawn along that road, as the drive it
    was - the phone recorded nothing there, and a dotted straight line across the
    fields read as a fault (client, 2026-10-23). Only blind stretches: everywhere else
    the fixes are close enough that the line between them is the road."""
    out, prev = [], None
    for point, mode in leg:
        pieces = None
        if mode == 'blind' and prev is not None and steps:
            i, j = index_of.get(id(prev)), index_of.get(id(point))
            if i is not None and j is not None and j > i:
                pieces = [(points[k], road_step(steps, points[k - 1], points[k])) for k in range(i + 1, j + 1)]
                if not all(road and len(road) > 1 for _p, road in pieces):
                    pieces = None
        if pieces:
            for end, road in pieces:
                when = epoch(end[0])
                for lat, lon in road:
                    if out and abs(out[-1][0] - lat) < 1e-6 and abs(out[-1][1] - lon) < 1e-6:
                        continue
                    out.append([lat, lon, when, end[3], 'vehicle'])
        else:
            out.append([point[1], point[2], epoch(point[0]), point[3], mode])
        prev = point
    return out


def downsample(points, limit=MAX_TRAIL_POINTS):
    """At most `limit` points, the last one always kept."""
    if len(points) <= limit:
        return points
    step = -(-len(points) // limit)
    kept = points[::step]
    if kept[-1] is not points[-1]:
        kept.append(points[-1])
    return kept


def epoch(when):
    return int(when.replace(tzinfo=None).timestamp() - datetime.utcfromtimestamp(0).timestamp()) \
        if when else None


class LabLocationPingLive(models.Model):
    _inherit = 'lab.location.ping'

    # ---------------------------------------------------------------- settings
    @api.model
    def _param_int(self, key, default):
        try:
            return int(self.env['ir.config_parameter'].sudo().get_str(key, str(default)))
        except (TypeError, ValueError):
            return default

    @api.model
    def _speed_limit(self):
        return max(0, self._param_int('lab_fieldwork.speed_limit', 60))

    @api.model
    def _halt_minutes(self):
        return max(3, self._param_int('lab_fieldwork.halt_minutes', 10))

    @api.model
    def _stale_minutes(self):
        return max(1, self._param_int('lab_fieldwork.ping_stale_minutes', 5))

    @api.model
    def _offline_minutes(self):
        return max(self._stale_minutes() + 1, self._param_int('lab_fieldwork.ping_offline_minutes', 30))

    @api.model
    def _auto_arrive_enabled(self):
        return self.env['ir.config_parameter'].sudo().get_str(
            'lab_fieldwork.auto_arrive', 'True') in ('True', 'true', '1')

    # ---------------------------------------------------------------- reading the day
    @api.model
    def _trail_accuracy(self):
        """A fix the phone itself puts at this many metres or worse is where the
        phone roughly is, never a point on a road. Indoors a phone reports a cell
        tower's fixed position at "100 m" every other fix, between real GPS fixes:
        drawn, the trail was a star of lines radiating from the tower (Abdul
        Latheef, 2026-10-21/22). 0 draws every fix."""
        return max(0, self._param_int('lab_fieldwork.trail_accuracy_m', TRAIL_ACCURACY_M))

    @api.model
    def _day_fixes(self, user, day):
        """Every fix of the day, in order, as [(ts, lat, lon, speed_kmh, overspeed,
        battery, accuracy_m, source, motion), ...] - the latest one is where the phone
        is. Readers unpack with `*_rest`: the tuple has grown before."""
        pings = self.sudo().search_read(
            [('user_id', '=', user.id), ('date', '=', day)],
            ['ts', 'latitude', 'longitude', 'speed_kmh', 'overspeed', 'battery', 'accuracy_m',
             'source', 'motion'],
            order='ts')
        return [(p['ts'], p['latitude'], p['longitude'], p['speed_kmh'] or 0.0,
                 bool(p['overspeed']), p['battery'] or 0.0, p['accuracy_m'] or 0.0,
                 p['source'] or 'browser', p['motion'] or None)
                for p in pings]

    @api.model
    def _trail_points(self, fixes):
        """The fixes worth drawing and measuring: accurate enough to sit on a road,
        and only where the phone went (drop_excursions)."""
        limit = self._trail_accuracy()
        points = [p for p in fixes if not p[6] or p[6] < limit] if limit else list(fixes)
        return drop_excursions(points)

    @api.model
    def _day_points(self, user, day):
        """The day's TRAIL: what the map draws and the distance counts."""
        return self._trail_points(self._day_fixes(user, day))

    @api.model
    def _trail_km_for(self, user, day):
        """The day's TRAVEL km - in a vehicle, or blind - never a walk."""
        return self._trail_split_for(user, day)['travel']

    @api.model
    def _trail_split_for(self, user, day):
        return mode_km(self._day_points(user, day), steps=self._road_steps(user, day))

    @api.model
    def _road_steps(self, user, day):
        """The roads known for this person's day (lab.location.road, filled every five
        minutes by _cron_fill_roads): the km of a stretch the phone did not record is
        measured along them. {} when roads are off or none is known yet."""
        if not self._road_trail_enabled():
            return {}
        road = self.env['lab.location.road'].sudo().search(
            [('user_id', '=', user.id), ('date', '=', day)], limit=1)
        if not road.steps or road.accuracy_key != self._trail_accuracy():
            return {}
        try:
            return json.loads(road.steps)
        except ValueError:
            return {}

    @api.model
    def _refresh_day_km(self, user, day):
        """The day's km read again - after its roads were filled, or fixes a phone sent
        late or again landed in it - on the trip until it is approved and on the day
        sheet until a desk has signed it: a signed figure is not changed under the
        people who signed it. (2026-10-24)"""
        self._refresh_trip_km(user, day)
        sheets = self.env['lab.daily.update'].sudo().search(
            [('user_id', '=', user.id), ('date', '=', day), ('state', 'in', ('draft', 'submitted', 'sent_back'))])
        if sheets:
            summary = self._day_summary(user, day)
            values = {key: summary[key] for key in ('gps_km', 'foot_km', 'blind_km')}
            for sheet in sheets:
                if any(round(sheet[key] or 0.0, 1) != round(value, 1) for key, value in values.items()):
                    sheet.write(values)

    @api.model
    def _day_summary(self, user, day):
        """What the day sheet keeps from the trail."""
        points = self._day_points(user, day)
        halts = self.env['lab.location.halt'].sudo().search(
            [('user_id', '=', user.id), ('date', '=', day)])
        split = mode_km(points, steps=self._road_steps(user, day))
        return {
            'gps_km': split['travel'],
            'foot_km': split['foot'],
            'blind_km': split['blind'],
            'halt_minutes': sum(halts.mapped('minutes')),
            'overspeed_count': sum(1 for p in points if p[4]),
        }

    @api.model
    def _today_for(self, user):
        return fields.Datetime.context_timestamp(
            self.with_context(tz=tz_name(self.env, user)), fields.Datetime.now()).date()

    # ---------------------------------------------------------------- after a batch
    @api.model
    def _after_record(self, user, day, values):
        """Everything that follows from a batch just written. Never raises: the
        executive's POST must not fail because a side effect disagreed."""
        try:
            self._touch_last_fix(user, values)
            if day == self._today_for(user):
                self._auto_visits(user, day, values)
                # one read of the day for both: each batch used to read it twice
                points = self._day_points(user, day)
                self._detect_halts(user, day, points=points)
                self._refresh_trip_km(user, day, points=points)
        except Exception:                                      # noqa: BLE001
            _logger.exception("live layer: after-record step failed for %s", user.login)

    @api.model
    def _touch_last_fix(self, user, values):
        """The user's last position, only ever moved forward in time."""
        latest = max(values, key=lambda v: v['ts'])
        user = user.sudo()
        if user.fw_last_ping_at and user.fw_last_ping_at >= latest['ts']:
            return
        user.write({
            'fw_last_ping_at': latest['ts'],
            'fw_last_lat': latest['latitude'], 'fw_last_lon': latest['longitude'],
            'fw_last_speed': latest.get('speed_kmh') or 0.0,
            'fw_last_accuracy': latest.get('accuracy_m') or 0.0,
            # a phone that reports no battery keeps the last reading
            'fw_last_battery': latest.get('battery') or user.fw_last_battery or 0.0,
        })

    @api.model
    def _visit_radius(self, clinic):
        default = float(self.env['ir.config_parameter'].sudo().get_str(
            'lab_fieldwork.visit_radius_m', 300))
        return clinic.visit_radius_m or default

    @api.model
    def _auto_visits(self, user, day, values):
        """Stamp what the trail saw on today's visits - arrival at a planned clinic,
        departure from an open one. Nudges, not actions (see the module docstring)."""
        if not self._auto_arrive_enabled():
            return
        Visit = self.env['lab.visit'].sudo()
        visits = Visit.search([('user_id', '=', user.id), ('date', '=', day),
                               ('state', 'in', ('planned', 'open'))])
        visits = visits.filtered(lambda v: v.partner_id.is_geolocated)
        if not visits:
            return
        rows = sorted(values, key=lambda v: v['ts'])
        for visit in visits:
            clinic = visit.partner_id
            radius = self._visit_radius(clinic)
            for row in rows:
                metres = metres_between(row['latitude'], row['longitude'],
                                        clinic.partner_latitude, clinic.partner_longitude)
                if metres is None:
                    continue
                if visit.state == 'planned' and not visit.check_in:
                    if metres <= radius and not visit.auto_arrived_at:
                        visit.auto_arrived_at = row['ts']
                elif visit.state == 'open' and visit.check_in and not visit.check_out:
                    if metres > radius + LEAVE_MARGIN_M:
                        if not visit.auto_left_at:
                            visit.auto_left_at = row['ts']
                    elif visit.auto_left_at:
                        visit.auto_left_at = False     # came back: it was a walk, not a leave

    @api.model
    def _clinic_zones(self, user, day):
        """[(lat, lon, radius, name)] of the day's clinics and the lab itself - the
        places a long stop is NOT a halt."""
        zones = []
        visits = self.env['lab.visit'].sudo().search(
            [('user_id', '=', user.id), ('date', '=', day), ('state', '!=', 'cancel')])
        for clinic in visits.mapped('partner_id').filtered('is_geolocated'):
            zones.append((clinic.partner_latitude, clinic.partner_longitude,
                          self._visit_radius(clinic), clinic.display_name))
        lab = self.env.company.partner_id
        if has_fix(lab.partner_latitude, lab.partner_longitude):
            zones.append((lab.partner_latitude, lab.partner_longitude, 300.0, self.env.company.name))
        return zones

    @api.model
    def _detect_halts(self, user, day, points=None):
        """Stretches of the day's trail where the phone sat still away from any
        clinic. Re-reading the day never doubles a halt: they are keyed by when they
        began, and an open one is extended or closed in place."""
        points = self._day_points(user, day) if points is None else points
        if len(points) < 2:
            return self.env['lab.location.halt']
        minimum = timedelta(minutes=self._halt_minutes())
        zones = self._clinic_zones(user, day)
        found = []          # (started, ended or None, lat, lon)
        anchor = None       # (ts, lat, lon)
        last_ts = points[0][0]
        for ts, lat, lon, speed, *_rest in points:
            still = speed < HALT_SPEED_KMH and (
                anchor is None or (metres_between(anchor[1], anchor[2], lat, lon) or 0.0) <= HALT_RADIUS_M)
            if anchor is not None and (ts - last_ts).total_seconds() > TRAIL_GAP_SECONDS:
                still = False                  # a recording gap ends the stop; we cannot know
            if still:
                if anchor is None:
                    anchor = (ts, lat, lon)
            else:
                if anchor is not None and last_ts - anchor[0] >= minimum:
                    found.append((anchor[0], last_ts, anchor[1], anchor[2]))
                anchor = (ts, lat, lon) if speed < HALT_SPEED_KMH else None
            last_ts = ts
        now = fields.Datetime.now()
        if anchor is not None and (last_ts - anchor[0] >= minimum
                                   or (day == self._today_for(user) and now - anchor[0] >= minimum)):
            found.append((anchor[0], None, anchor[1], anchor[2]))   # still there

        Halt = self.env['lab.location.halt'].sudo()
        existing = {h.started_at: h for h in Halt.search(
            [('user_id', '=', user.id), ('date', '=', day)])}
        touched = Halt
        for started, ended, lat, lon in found:
            if any((metres_between(lat, lon, z[0], z[1]) or 0.0) <= z[2] for z in zones):
                continue                        # a clinic or the lab: a visit, not a halt
            place = ''
            near = [(metres_between(lat, lon, z[0], z[1]) or 1e9, z[3]) for z in zones]
            near = [n for n in near if n[0] <= NEAR_CLINIC_M]
            if near:
                place = min(near)[1]
            halt = existing.get(started)
            vals = {'ended_at': ended, 'latitude': lat, 'longitude': lon, 'place': place}
            if halt:
                if halt.ended_at != ended or halt.place != place:
                    halt.write(vals)
            else:
                halt = Halt.create(dict(vals, user_id=user.id, date=day, started_at=started,
                                        company_id=user.company_id.id))
            touched |= halt
        return touched

    @api.model
    def _refresh_trip_km(self, user, day, points=None):
        """The trip's km read again - after every batch, and after the day's roads were
        filled. Never on an approved trip: the figure it was approved with stands, also
        when fixes the phone sent late or again land in its day afterwards (2026-10-24)."""
        trips = self.env['lab.trip'].sudo().search(
            [('user_id', '=', user.id), ('date', '=', day), ('state', 'not in', ('cancel', 'approved'))])
        if trips:
            if points is not None:
                # the day already read: the trip takes its km from it (lab_trip.py)
                trips = trips.with_context(fw_trail_km={
                    '%s/%s' % (user.id, fields.Date.to_string(day)):
                        mode_km(points, steps=self._road_steps(user, day))['travel']})
            trips._compute_gps_km()

    # ---------------------------------------------------------------- the live map
    @api.model
    def _is_manager(self):
        return self.env.user.has_group('lab_fieldwork.group_fieldwork_manager') \
            or self.env.user.has_group('base.group_system')

    @api.model
    def _map_users(self, day, user_ids=None):
        """Who is on the map: the executives' group plus anyone with a trail or a
        visit that day; an executive sees only themselves."""
        if not self._is_manager():
            return self.env.user
        group = self.env.ref('lab_fieldwork.group_fieldwork_executive', raise_if_not_found=False)
        users = group.sudo().all_user_ids if group else self.env['res.users']
        pinged = self.sudo().search([('date', '=', day)]).mapped('user_id')
        visited = self.env['lab.visit'].sudo().search([('date', '=', day)]).mapped('user_id')
        users = (users | pinged | visited).filtered(lambda u: u.active and u.company_ids & self.env.companies)
        if user_ids:
            users = users.filtered(lambda u: u.id in user_ids)
        return users.sorted('name')

    @api.model
    def _status_of(self, user, last, open_visit, on_duty, is_today, now):
        """One word for the board: what the last fix, the open visit and the duty
        say together. Returns (status, label, since)."""
        if not is_today:
            return 'off', _('day over'), last[0] if last else None
        if open_visit:
            return 'at_clinic', _('at a clinic'), open_visit.check_in
        if last:
            age = (now - last[0]).total_seconds() / 60.0
            if age <= self._stale_minutes():
                if last[3] >= MOVING_KMH:
                    return 'moving', _('on the road'), last[0]
                return 'stopped', _('stopped'), last[0]
            if age <= self._offline_minutes():
                return 'stopped', _('stopped'), last[0]
            if on_duty:
                return 'offline', self._silent_word(user, now), last[0]
            return 'off', _('day ended'), last[0]
        if on_duty:
            return 'offline', self._silent_word(user, now, first=True), None
        return 'off', _('off today'), None

    @api.model
    def _heard(self, user):
        """The phone reported in - a batch, or an empty report-in: when the server
        last heard from it, to the minute (2026-10-24)."""
        now = fields.Datetime.now()
        last = user.sudo().fw_last_heard_at
        if not last or (now - last).total_seconds() >= 60:
            user.sudo().write({'fw_last_heard_at': now})

    @api.model
    def _heard_recently(self, user, now):
        """Whether the phone has reported in lately, with or without a position: the
        app posts every batch and, with nothing to send, every IDLE_CHECK_SECONDS."""
        heard = user.sudo().fw_last_heard_at
        return bool(heard) and (now - heard).total_seconds() <= HEARD_RECENT_SECONDS

    @api.model
    def _silent_word(self, user, now, first=False):
        """Which silence this is. A phone that reports in but holds no position has
        no GPS fix (Location off, or the app held back from it); one not heard from
        has no network, or the app was stopped. (client, 2026-10-24: "may be
        because of network issue or because of any other issues")"""
        if self._heard_recently(user, now):
            return _('no GPS fix')
        return _('no position yet') if first else _('no signal')

    @api.model
    def _next_clinic(self, user, day, last):
        """The next planned visit with a pinned clinic, with how far and how long."""
        visit = self.env['lab.visit'].sudo().search(
            [('user_id', '=', user.id), ('date', '=', day), ('state', '=', 'planned')],
            order='id', limit=10).filtered(lambda v: v.partner_id.is_geolocated)[:1]
        if not visit:
            return None
        clinic = visit.partner_id
        out = {'clinic_id': clinic.id, 'name': clinic.display_name, 'visit_id': visit.id,
               'lat': clinic.partner_latitude, 'lon': clinic.partner_longitude,
               'distance_km': None, 'eta_min': None}
        if last:
            metres = metres_between(last[1], last[2], clinic.partner_latitude,
                                    clinic.partner_longitude) or 0.0
            road_km = metres / 1000.0 * ROAD_FACTOR
            speed = max(last[3], ETA_FLOOR_KMH)
            out['distance_km'] = round(road_km, 1)
            out['eta_min'] = max(1, int(round(road_km / speed * 60)))
        return out

    @api.model
    def _cron_close_halts(self):
        """Every few minutes: close the halt of a phone that stopped and then went
        quiet, and open the one a parked phone has earned. This used to run for every
        executive on every read of the live map, which made a board poll cost a full
        day of pings per person. (2026-10-21)"""
        if not self._auto_arrive_enabled():
            return
        users = self.env['res.users']
        # On duty = an attendance still open (attendance_state is computed, not
        # searchable); plus anyone whose halt is still open.
        executives = self.env['lab.desk']._executives()
        users |= self.env['hr.attendance'].sudo().search(
            [('check_out', '=', False), ('employee_id.user_id', 'in', executives.ids)]
        ).mapped('employee_id.user_id')
        users |= self.env['lab.location.halt'].sudo().search(
            [('ended_at', '=', False)]).mapped('user_id')
        for user in users.filtered('active'):
            try:
                self.sudo()._detect_halts(user, self._today_for(user))
            except Exception:                                  # noqa: BLE001
                _logger.exception("halt detection failed for %s", user.login)

    @api.model
    def _map_home(self):
        """Where the map opens when nobody has a position yet: the company's pin,
        else the middle of its pinned clinics, else central Kerala."""
        lab = self.env.company.partner_id
        if has_fix(lab.partner_latitude, lab.partner_longitude):
            return {'lat': lab.partner_latitude, 'lon': lab.partner_longitude, 'zoom': 10}
        self.env.cr.execute(
            """SELECT avg(partner_latitude), avg(partner_longitude), count(*)
                 FROM res_partner
                WHERE is_clinic AND active
                  AND coalesce(partner_latitude, 0) <> 0 AND coalesce(partner_longitude, 0) <> 0""")
        lat, lon, count = self.env.cr.fetchone()
        if count:
            return {'lat': lat, 'lon': lon, 'zoom': 9}
        return {'lat': 10.5, 'lon': 76.2, 'zoom': 8}

    @api.model
    def get_live_map(self, day=None, user_ids=None, max_points=None):
        """Everything the live map and the executive's own mini-map draw (see the
        module docstring for the shape). A manager sees the team; an executive only
        themselves - the call refuses anything else rather than filtering quietly.

        `max_points` caps each trail (MAX_TRAIL_POINTS by default): Field Live asks
        for a light overview of everyone and the full trail of the person in focus
        (2026-10-21). Halts are no longer re-detected on every read: the batch that
        arrives does it, and the five-minute cron closes the halt of a phone that
        went quiet (_cron_close_halts)."""
        me = self.env.user
        if not self._is_manager() and user_ids and set(user_ids) != {me.id}:
            raise AccessError(_("Only field managers can see where others are."))
        day = fields.Date.to_date(day) if day else self._today_for(me)
        today = self._today_for(me)
        is_today = day == today
        now = fields.Datetime.now()
        users = self._map_users(day, user_ids)
        Visit = self.env['lab.visit'].sudo()
        Halt = self.env['lab.location.halt'].sudo()
        emp_by_user = {e.user_id.id: e for e in self.env['hr.employee'].sudo().search(
            [('user_id', 'in', users.ids)])}
        # The roads known for the day (filled by _cron_fill_roads), read once for all:
        # blind stretches are drawn along them. Nothing is asked of the service here.
        roads = {}
        if users and self._road_trail_enabled():
            limit = self._trail_accuracy()
            for road in self.env['lab.location.road'].sudo().search(
                    [('user_id', 'in', users.ids), ('date', '=', day)]):
                if road.steps and road.accuracy_key == limit:
                    try:
                        roads[road.user_id.id] = json.loads(road.steps)
                    except ValueError:
                        pass
        executives, clinics = [], {}
        for index, user in enumerate(users):
            fixes = self._day_fixes(user, day)
            points = self._trail_points(fixes)
            # the mode of the stretch ending at each point, carried into the legs
            modes = classify_modes(points)
            mode_of = {id(p): m for p, m in zip(points, modes)}
            split = mode_km(points, modes, roads.get(user.id))
            visits = Visit.search([('user_id', '=', user.id), ('date', '=', day),
                                   ('state', '!=', 'cancel')], order='id')
            open_visit = visits.filtered(lambda v: v.state == 'open')[:1]
            employee = emp_by_user.get(user.id)
            on_duty = bool(employee) and employee.attendance_state == 'checked_in' and is_today
            duty_since = None
            if on_duty:
                att = self.env['hr.attendance'].sudo().search(
                    [('employee_id', '=', employee.id), ('check_out', '=', False)],
                    order='check_in desc', limit=1)
                duty_since = epoch(att.check_in) if att else None
            # the latest fix whatever its accuracy: where the phone is, not a road
            last = fixes[-1] if fixes else None
            status, label, since = self._status_of(user, last, open_visit, on_duty, is_today, now)
            # One linked trail: a silence is drawn as the journey it was, along its road
            # once known (drawn_leg), never as a break (client, 2026-10-24).
            legs, gaps = ([points] if points else []), []
            budget = max(50, int(max_points or MAX_TRAIL_POINTS))
            trail_points = sum(len(leg) for leg in legs)
            keep = max(1, budget // max(1, len(legs))) if trail_points > budget else None
            # [(point, mode of the stretch it ends)]: thinned, never across a change of mode
            legs = [thin_runs(leg, [mode_of.get(id(p), 'still') for p in leg], keep or len(leg))
                    for leg in legs]
            index_of = {id(p): k for k, p in enumerate(points)}
            doors = []
            for visit in visits:
                clinic = visit.partner_id
                if clinic.is_geolocated:
                    clinics[clinic.id] = {'id': clinic.id, 'name': clinic.display_name,
                                          'lat': clinic.partner_latitude, 'lon': clinic.partner_longitude,
                                          'radius_m': self._visit_radius(clinic)}
                if visit.check_in and has_fix(visit.gps_lat, visit.gps_lon):
                    doors.append({'visit_id': visit.id, 'clinic_id': clinic.id, 'name': clinic.display_name,
                                  'lat': visit.gps_lat, 'lon': visit.gps_lon, 'epoch': epoch(visit.check_in),
                                  'left_epoch': epoch(visit.check_out), 'state': visit.gps_state or 'nofix',
                                  'kind': 'in'})
                if visit.check_out and has_fix(visit.out_gps_lat, visit.out_gps_lon):
                    doors.append({'visit_id': visit.id, 'clinic_id': clinic.id, 'name': clinic.display_name,
                                  'lat': visit.out_gps_lat, 'lon': visit.out_gps_lon, 'epoch': epoch(visit.check_out),
                                  'left_epoch': None, 'state': visit.out_gps_state or 'nofix', 'kind': 'out'})
            halts = [{'id': h.id, 'lat': h.latitude, 'lon': h.longitude,
                      'started_epoch': epoch(h.started_at), 'ended_epoch': epoch(h.ended_at),
                      'minutes': h.minutes, 'place': h.place or ''}
                     for h in Halt.search([('user_id', '=', user.id), ('date', '=', day)], order='started_at')]
            overspeed = [[p[1], p[2], epoch(p[0]), p[3]] for p in points if p[4]][-MAX_OVERSPEED_POINTS:]
            executives.append({
                'user_id': user.id, 'name': user.name,
                'phone': user.partner_id.phone or '',      # Odoo 20 has no `mobile`
                'colour': PALETTE[index % len(PALETTE)],
                'status': status, 'status_label': label, 'since_epoch': epoch(since),
                'last': {'lat': last[1], 'lon': last[2], 'epoch': epoch(last[0]),
                         'age_s': int((now - last[0]).total_seconds()), 'speed_kmh': last[3],
                         'accuracy_m': last[6], 'battery': last[5] or user.sudo().fw_last_battery or None,
                         'source': last[7]}
                if last else None,
                'at_clinic': {'clinic_id': open_visit.partner_id.id, 'name': open_visit.partner_id.display_name,
                              'visit_id': open_visit.id, 'since_epoch': epoch(open_visit.check_in)}
                if open_visit else None,
                'next': self._next_clinic(user, day, last) if is_today else None,
                'km': split['travel'],
                'foot_km': split['foot'], 'blind_km': split['blind'], 'trail_km': split['total'],
                'visits_done': len(visits.filtered(lambda v: v.state == 'done')),
                'visits_planned': len(visits),
                'overspeed': sum(1 for p in points if p[4]),
                'halt_minutes': sum(h['minutes'] for h in halts),
                'on_duty': on_duty, 'duty_since_epoch': duty_since,
                'legs': [drawn_leg(leg, index_of, points, roads.get(user.id)) for leg in legs],
                'gaps': [{'from_epoch': epoch(a), 'to_epoch': epoch(b),
                          'minutes': int((b - a).total_seconds() // 60)} for a, b in gaps],
                'doors': doors, 'halts': halts, 'overspeed_points': overspeed,
            })
        icp = self.env['ir.config_parameter'].sudo()
        return {
            'home': self._map_home(),
            'day': fields.Date.to_string(day), 'is_today': is_today, 'now_epoch': epoch(now),
            'tiles': icp.get_str('lab_fieldwork.map_tiles',
                                 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png'),
            'attribution': icp.get_str('lab_fieldwork.map_attribution',
                                       '&copy; OpenStreetMap contributors'),
            'config': {'live_ms': 20000, 'trail_accuracy_m': self._trail_accuracy(),
                       'stale_minutes': self._stale_minutes(),
                       'offline_minutes': self._offline_minutes(), 'speed_limit': self._speed_limit()},
            'executives': executives,
            'clinics': list(clinics.values()),
        }


class ResUsersLive(models.Model):
    _inherit = 'res.users'

    # The last fix, forward-only, written by the trail. Manager-only fields: where a
    # person is is theirs and their manager's, not the whole company's.
    fw_last_ping_at = fields.Datetime('Last Seen', readonly=True,
                                      groups='lab_fieldwork.group_fieldwork_manager')
    fw_last_lat = fields.Float(digits=(10, 7), readonly=True,
                               groups='lab_fieldwork.group_fieldwork_manager')
    fw_last_lon = fields.Float(digits=(10, 7), readonly=True,
                               groups='lab_fieldwork.group_fieldwork_manager')
    fw_last_speed = fields.Float('Last Speed (km/h)', digits=(6, 1), readonly=True,
                                 groups='lab_fieldwork.group_fieldwork_manager')
    fw_last_accuracy = fields.Float('Last Fix Accuracy (m)', readonly=True,
                                    groups='lab_fieldwork.group_fieldwork_manager')
    fw_last_battery = fields.Float('Phone Battery (%)', digits=(5, 1), readonly=True,
                                   groups='lab_fieldwork.group_fieldwork_manager')
    # When the phone last reported in at all - a batch, or an empty report-in - as
    # against the last POSITION above: a phone heard from with no position has no
    # GPS fix; one not heard from has no network, or the app stopped. Management
    # asked which it was (2026-10-24).
    fw_last_heard_at = fields.Datetime('Phone Last Heard', readonly=True,
                                       groups='lab_fieldwork.group_fieldwork_manager')

    # The free Traccar Client app instead of the browser (client, 2026-10-19). It
    # tracks with the screen off; the browser cannot. The identifier is the phone's
    # password to the endpoint - whoever holds it can post positions as this person -
    # so it is long, random, unique, and only an administrator sees or changes it.
    fw_gps_app = fields.Boolean(
        'Tracked by the Traccar app', readonly=True, copy=False,
        groups='lab_fieldwork.group_fieldwork_manager',
        help="This person's phone runs the free Traccar Client app, which sends positions "
             "with the screen off. The browser then stops asking for one.")
    fw_gps_device_id = fields.Char(
        'Tracking App Device ID', readonly=True, copy=False, index=True,
        groups='lab_fieldwork.group_fieldwork_admin,base.group_system')

    _fw_gps_device_unique = models.Constraint(
        'unique(fw_gps_device_id)', 'That tracking device ID is already in use.')

    @api.model
    def _fw_new_device_id(self):
        return secrets.token_hex(8).upper()

    def _fw_check_app_admin(self):
        if not (self.env.su or self.env.user.has_group('lab_fieldwork.group_fieldwork_admin')
                or self.env.user.has_group('base.group_system')):
            raise AccessError(_("Only a Field Work administrator sets up the tracking app."))

    def action_fw_tracking_app(self):
        """Switch the tracking app on or off for this person (button: a field-work
        administrator cannot edit a user record - see action_fw_set_exception)."""
        self._fw_check_app_admin()
        value = bool(self.env.context.get('fw_value'))
        for user in self.sudo():
            if user.fw_gps_app == value:
                continue
            vals = {'fw_gps_app': value}
            if value and not user.fw_gps_device_id:
                vals['fw_gps_device_id'] = self._fw_new_device_id()
            user.write(vals)
            user.partner_id.message_post(body=_(
                "Tracking app switched %(state)s by %(who)s.",
                state=_("on") if value else _("off"), who=self.env.user.name))
        return True

    def action_fw_new_device_id(self):
        """A new identifier: the old phone stops being accepted at once (a lost or
        handed-over phone)."""
        self._fw_check_app_admin()
        for user in self.sudo():
            user.fw_gps_device_id = self._fw_new_device_id()
            user.partner_id.message_post(body=_(
                "Tracking app device ID replaced by %(who)s; the phone must be set up again.",
                who=self.env.user.name))
        return True

    def _fw_app_setup(self):
        """What the executive types into Traccar Client, or None without the app."""
        self.ensure_one()
        me = self.sudo()
        if not me.fw_gps_app or not me.fw_gps_device_id:
            return None
        base = self.env['ir.config_parameter'].sudo().get_str('web.base.url', '').rstrip('/')
        last = self.env['lab.location.ping'].sudo().search(
            [('user_id', '=', me.id), ('source', '=', 'app')], order='ts desc', limit=1)
        return {
            'server_url': '%s/lab_fieldwork/gps' % base,
            'device_id': me.fw_gps_device_id,
            'last_app_fix_age_s': int((fields.Datetime.now() - last.ts).total_seconds())
            if last else None,
        }


class LabMyDayLive(models.AbstractModel):
    _inherit = 'lab.my.day'

    @api.model
    def tracking_state(self, client=None, app_version=None, app_device=None):
        """Plus what the trail noticed: the km so far, how fresh the last fix is, and
        the nudges - a planned clinic the phone is standing in, an open visit the
        phone has walked away from.

        `client` says who is asking. 'app' is the Lab app's own page (2026-10-20,
        models/field_app.py): it gets its tracking service's settings back as
        `native` - provisioning the phone on the first ask - and its page never
        watches by itself. Anyone else gets the Traccar settings if that app is on,
        or stands down while the Lab app is evidently posting (`app_live`): two GPS
        watches on one phone is double the battery for the same trail.
        """
        state = super().tracking_state()
        user = self.env.user
        state.update({'app': None, 'native': None, 'app_mode': False, 'app_live': False})
        in_app = client == 'app'
        if state.get('enabled'):
            if in_app:
                # The phone is provisioned whenever the app asks; the recorder's
                # settings go out only while the day is running. Start my day is
                # when recording starts and End the day is when it stops, and in
                # between the app is handed nothing it could record with.
                # (client, 2026-10-20)
                config = user._fw_native_config(app_version, app_device)
                state['native'] = config if state.get('on_duty') else None
            else:
                state['app'] = user._fw_app_setup()
                if not state['app']:
                    age = user._fw_native_last_fix_age()
                    state['app_live'] = age is not None and age <= APP_LIVE_SECONDS
        if in_app or state['app'] or state['app_live']:
            # Inside the app the page never watches by itself, running day or not.
            state['enabled'] = False
            state['app_mode'] = True
        me = user.sudo()
        state['native_app'] = {
            'version': me.fw_native_app_version or '',
            'last_fix_age_s': user._fw_native_last_fix_age(),
        } if me.fw_native_app else None
        # For the nudge on My Day (Android, outside the app): None until an APK is up.
        state['app_download'] = self.env['lab.field.app']._info()
        # ...and on an iPhone, the iPhone app (an Ad Hoc build or a TestFlight link)
        state['ios_download'] = self.env['lab.field.app']._ios_info()
        Ping = self.env['lab.location.ping'].sudo()
        day = Ping._today_for(user)
        fixes = Ping._day_fixes(user, day)
        state['km_today'] = mode_km(Ping._trail_points(fixes), steps=Ping._road_steps(user, day))['travel']
        state['last_fix_age_s'] = int((fields.Datetime.now() - fixes[-1][0]).total_seconds()) \
            if fixes else None
        nudges = []
        if (state.get('enabled') or state.get('app_mode')) and state.get('on_duty') \
                and Ping._auto_arrive_enabled():
            visits = self.env['lab.visit'].sudo().search(
                [('user_id', '=', user.id), ('date', '=', day),
                 ('state', 'in', ('planned', 'open'))], order='id')
            zone = tz_name(self.env, user)
            for visit in visits:
                when = None
                if visit.state == 'planned' and visit.auto_arrived_at and not visit.check_in:
                    kind, when = 'arrived', visit.auto_arrived_at
                elif visit.state == 'open' and visit.auto_left_at and not visit.check_out:
                    kind, when = 'left', visit.auto_left_at
                if when:
                    nudges.append({
                        'kind': kind, 'visit_id': visit.id, 'clinic': visit.partner_id.display_name,
                        'since_epoch': epoch(when),
                        'since_label': fields.Datetime.context_timestamp(
                            self.with_context(tz=zone), when).strftime('%H:%M'),
                    })
        state['nudges'] = nudges
        return state


class LabDeskLive(models.AbstractModel):
    _inherit = 'lab.desk'

    def _field_board(self, today):
        """The board's rows gain the live facts: when the phone was last heard from,
        where it is in words, how far it has come, how long it has sat still."""
        board = super()._field_board(today)
        Ping = self.env['lab.location.ping'].sudo()
        now = fields.Datetime.now()
        halts = self.env['lab.location.halt'].sudo().search(
            [('date', '=', today), ('user_id', 'in', [r['id'] for r in board['rows']])])
        halt_minutes = {}
        for halt in halts:
            halt_minutes[halt.user_id.id] = halt_minutes.get(halt.user_id.id, 0) + halt.minutes
        clinics = {}
        for visit in board['visits']:
            if visit.partner_id.is_geolocated:
                clinics.setdefault(visit.user_id.id, []).append(visit.partner_id)
        trips = {t.user_id.id: t for t in self.env['lab.trip'].sudo().search(
            [('date', '=', today), ('state', '!=', 'cancel'),
             ('user_id', 'in', [r['id'] for r in board['rows']])])}
        for row in board['rows']:
            user = self.env['res.users'].sudo().browse(row['id'])
            last_at = user.fw_last_ping_at
            seen_today = bool(last_at) and fields.Datetime.context_timestamp(
                self.with_context(tz=tz_name(self.env, user)), last_at).date() == today
            row['last_seen_epoch'] = epoch(last_at) if seen_today else None
            row['last_age_s'] = int((now - last_at).total_seconds()) if seen_today else None
            row['live'] = 'none'
            row['where'] = ''
            if seen_today:
                age_min = row['last_age_s'] / 60.0
                if age_min <= Ping._stale_minutes():
                    row['live'] = 'moving' if user.fw_last_speed >= MOVING_KMH else 'stopped'
                elif age_min <= Ping._offline_minutes():
                    row['live'] = 'stopped'
                else:
                    row['live'] = 'offline'
                near = []
                for clinic in clinics.get(user.id, []):
                    metres = metres_between(user.fw_last_lat, user.fw_last_lon,
                                            clinic.partner_latitude, clinic.partner_longitude)
                    if metres is not None and metres <= NEAR_CLINIC_M:
                        near.append((metres, clinic.display_name))
                if row['live'] == 'offline':
                    # silent for a while: where it was last heard, never "on the road"
                    row['where'] = _('last near %s', min(near)[1]) if near else _('no signal')
                else:
                    row['where'] = _('near %s', min(near)[1]) if near else _('on the road')
            row['halt_minutes'] = halt_minutes.get(user.id, 0)
            # The trip's own figure: every batch refreshes it (_refresh_trip_km), and
            # reading the day's pings again here doubled Field Live's work per poll.
            trip = trips.get(user.id)
            row['gps_km'] = (trip.gps_km if trip else Ping._trail_km_for(user, today)) \
                if seen_today else 0.0
        return board
