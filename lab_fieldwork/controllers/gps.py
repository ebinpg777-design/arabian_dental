# -*- coding: utf-8 -*-
"""Positions from a tracking app: the lab's own Lab app (models/field_app.py,
2026-10-20) or the free Traccar Client app (client, 2026-10-19: "able to track using
the free Traccar Client app").

A web page cannot track once the phone is locked; a native app can. Traccar Client
(Android / iPhone, free) sends its position to a URL every minute or so, screen off,
and buffers while out of coverage. This is that URL. Each executive's phone is told
their own device identifier (res.users.fw_gps_device_id); a position for an unknown
identifier is refused.

What arrives goes through `lab.location.ping.record` as that executive - the same
door the browser uses - so every rule of the trail still holds: kept only inside an
attendance window (the app may run all day; the lab keeps only the working day),
thinned to movement, and feeding the live map, the arrival reminders and the halts.

Two protocols, as the gas-delivery project's endpoint accepts them:
* OsmAnd query (Traccar Client up to v8, GPSLogger, OsmAnd):
  ``?id=<device>&lat=..&lon=..&timestamp=..&speed=<knots>&bearing=..&accuracy=..&batt=..``
* Traccar Client v9 JSON: ``{"device_id", "location": {"timestamp", "coords": {latitude,
  longitude, speed (m/s), heading, accuracy}, "battery": {"level": 0..1}}}``, a list of
  those, or ``{"locations": [...]}`` for a batch. A location may carry the phone's own
  ``"activity": {"type": "in_vehicle"|"on_foot"|"still"|..., "confidence": 0..100}``
  (Traccar Client v9 and the Lab app from 1.0.4 send it): what the motion sensors
  say the person was doing, kept as the ping's `motion`.

The Lab app gzips its batches (``Content-Encoding: gzip``, 1.0.4).
"""
import gzip
import json
import zlib
import logging
from datetime import datetime, timezone

from odoo import fields, http
from odoo.http import request

_logger = logging.getLogger(__name__)

KNOTS_TO_KMH = 1.852
MPS_TO_KMH = 3.6
MIN_DEVICE_ID = 8
# The phone's activity recognition, in the plugin's words -> the trail's.
ACTIVITY_MOTION = {
    'in_vehicle': 'vehicle', 'on_bicycle': 'vehicle',
    'on_foot': 'foot', 'walking': 'foot', 'running': 'foot',
    'still': 'still',
}
MIN_ACTIVITY_CONFIDENCE = 50


# A batch of 200 fixes is about 40 KB plain: nothing honest comes near this.
MAX_BODY_BYTES = 2 * 1024 * 1024


def inflate(raw, limit):
    """`raw` gunzipped, or ValueError('bad gzip') when it is not gzip and
    ValueError('too big') when it would inflate past `limit` (a decompression bomb -
    it is never inflated further than that)."""
    inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        data = inflater.decompress(raw, limit + 1)
    except zlib.error as exc:
        raise ValueError('bad gzip') from exc
    if len(data) > limit or inflater.unconsumed_tail:
        raise ValueError('too big')
    return data


def parse_time(value):
    """Epoch seconds or milliseconds, or ISO 8601 -> naive UTC (now when missing)."""
    if value in (None, ''):
        return fields.Datetime.now()
    try:
        number = float(value)
        if number > 1e11:                                   # milliseconds
            number /= 1000.0
        return datetime.fromtimestamp(number, tz=timezone.utc).replace(tzinfo=None)
    except (TypeError, ValueError):
        pass
    try:
        moment = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if moment.tzinfo:
            moment = moment.astimezone(timezone.utc).replace(tzinfo=None)
        return moment
    except ValueError:
        return fields.Datetime.now()


def number(value, default=None):
    try:
        return float(value) if value not in (None, '') else default
    except (TypeError, ValueError):
        return default


def _dict(value):
    return value if isinstance(value, dict) else {}


def parse_activity(activity):
    """'vehicle' / 'foot' / 'still' from a location's activity, or None."""
    if isinstance(activity, str):
        activity = {'type': activity}
    activity = _dict(activity)
    confidence = number(activity.get('confidence'))
    if confidence is not None and confidence < MIN_ACTIVITY_CONFIDENCE:
        return None
    return ACTIVITY_MOTION.get(str(activity.get('type') or '').lower())


def parse_body(body):
    """(device_id, [ping]) from a Traccar v9 JSON body. Anything of another shape is
    skipped, never an error: this door is public, and a 500 with a traceback for a
    stray `5` or `{"location": "x"}` helps nobody."""
    device_id, pings = None, []
    if isinstance(body, list):
        items = body
    elif isinstance(body, dict):
        items = body.get('locations') or [body]
        items = items if isinstance(items, list) else [items]
    else:
        return None, []
    for item in items:
        if not isinstance(item, dict):
            continue
        found = item.get('device_id') or (body.get('device_id') if isinstance(body, dict) else None)
        if not device_id and isinstance(found, (str, int)):
            device_id = found
        location = item.get('location', item)
        if not isinstance(location, dict):
            continue
        coords = _dict(location.get('coords'))
        battery = _dict(location.get('battery') or item.get('battery'))
        level = number(battery.get('level'))
        speed = number(coords.get('speed'))
        pings.append({
            'latitude': coords.get('latitude'), 'longitude': coords.get('longitude'),
            'ts': fields.Datetime.to_string(parse_time(location.get('timestamp'))),
            'speed_kmh': max(speed, 0.0) * MPS_TO_KMH if speed is not None else None,
            'heading': number(coords.get('heading')),
            'accuracy_m': number(coords.get('accuracy'), 0.0),
            'battery': (level * 100 if level is not None and level <= 1 else level),
            'motion': parse_activity(location.get('activity') or item.get('activity')),
        })
    return device_id, pings


def parse_query(params):
    """(device_id, [ping]) from an OsmAnd query string."""
    device_id = params.get('id') or params.get('deviceid')
    lat, lon = params.get('lat'), params.get('lon') or params.get('lng')
    if params.get('location') and not lat:
        lat, _sep, lon = params['location'].partition(',')
    if not (lat and lon):
        return device_id, []
    speed = number(params.get('speed'))
    return device_id, [{
        'latitude': lat, 'longitude': lon,
        'ts': fields.Datetime.to_string(parse_time(params.get('timestamp'))),
        'speed_kmh': speed * KNOTS_TO_KMH if speed is not None else None,
        'heading': number(params.get('bearing') or params.get('heading')),
        'accuracy_m': number(params.get('accuracy') or params.get('hdop'), 0.0),
        'battery': number(params.get('batt') or params.get('battery')),
    }]


class FieldGpsController(http.Controller):

    # auth='none' routes are read-only in Odoo 20 unless told otherwise
    # max_content_length: Werkzeug refuses a body past the cap before it is read
    # (413), instead of buffering Odoo's default 128 MB first.
    @http.route(['/lab_fieldwork/gps', '/lab_fieldwork/gps/<string:device_id>'], type='http',
                auth='none', methods=['GET', 'POST'], csrf=False, save_session=False, readonly=False,
                max_content_length=MAX_BODY_BYTES)
    def gps(self, device_id=None, **params):
        body = None
        if request.httprequest.mimetype == 'application/json':
            raw = request.httprequest.get_data()
            if len(raw) > MAX_BODY_BYTES:
                return request.make_response('too big', status=413)
            # The app posts its batches gzipped (a day's batch is 6x smaller on the
            # mobile connection); Werkzeug does not undo that for a request. Inflated
            # under a cap: this door is public, and a 3 KB "batch" that inflates to
            # gigabytes must cost nothing.
            if (request.httprequest.headers.get('Content-Encoding') or '').lower() == 'gzip':
                try:
                    raw = inflate(raw, MAX_BODY_BYTES)
                except ValueError as exc:
                    return request.make_response(str(exc), status=413 if 'big' in str(exc) else 400)
            try:
                body = json.loads(raw.decode('utf-8') or '{}')
            except (ValueError, UnicodeDecodeError, RecursionError):
                return request.make_response('bad json', status=400)
        if body:
            found, pings = parse_body(body)
        else:
            found, pings = parse_query(params)
        device_id = str(device_id or found or '')
        if len(device_id) < MIN_DEVICE_ID:
            return request.make_response('unknown device', status=403)
        # The Lab app (2026-10-20, models/field_app.py) provisions the same identifier
        # itself; the Traccar switch is only for Traccar.
        user = request.env['res.users'].sudo().search(
            [('fw_gps_device_id', '=', device_id),
             '|', ('fw_gps_app', '=', True), ('fw_native_app', '=', True)], limit=1)
        if not user:
            _logger.info("field GPS: position for an unknown device %s", device_id)
            return request.make_response('unknown device', status=403)
        # heard from, position or not: which silence it is, when one comes (2026-10-24)
        request.env['lab.location.ping'].sudo()._heard(user)
        kept = 0
        # An excused phone is never watched, app or browser; the app is answered
        # OK all the same, or it retries the same positions for ever.
        if pings and not user.fw_location_exception:
            kept = request.env['lab.location.ping'].with_user(user).with_company(user.company_id) \
                .with_context(fw_ping_source='app').record(pings)
        # Whether the day is still running, for the Lab app: it stops its service on
        # "off", so a day ended on the office computer stops the phone as well. The
        # app reports in even with nothing to send, to hear this.
        running = request.env['lab.my.day'].with_user(user).with_company(user.company_id) \
            ._attendance_payload().get('state') == 'checked_in'
        return request.make_response('OK %s' % kept, headers=[
            ('Content-Type', 'text/plain'), ('X-Lab-Track', 'on' if running else 'off')])
