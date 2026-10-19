# -*- coding: utf-8 -*-
"""Positions from the free Traccar Client app (client, 2026-10-19: "able to track
using the free Traccar Client app").

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
  those, or ``{"locations": [...]}`` for a batch.
"""
import json
import logging
from datetime import datetime, timezone

from odoo import fields, http
from odoo.http import request

_logger = logging.getLogger(__name__)

KNOTS_TO_KMH = 1.852
MPS_TO_KMH = 3.6
MIN_DEVICE_ID = 8


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


def parse_body(body):
    """(device_id, [ping]) from a Traccar v9 JSON body."""
    device_id, pings = None, []
    items = body if isinstance(body, list) else (body.get('locations') or [body])
    for item in items:
        if not isinstance(item, dict):
            continue
        device_id = device_id or item.get('device_id') or (
            body.get('device_id') if isinstance(body, dict) else None)
        location = item.get('location', item)
        coords = location.get('coords') or {}
        battery = location.get('battery') or item.get('battery') or {}
        level = number(battery.get('level'))
        speed = number(coords.get('speed'))
        pings.append({
            'latitude': coords.get('latitude'), 'longitude': coords.get('longitude'),
            'ts': fields.Datetime.to_string(parse_time(location.get('timestamp'))),
            'speed_kmh': max(speed, 0.0) * MPS_TO_KMH if speed is not None else None,
            'heading': number(coords.get('heading')),
            'accuracy_m': number(coords.get('accuracy'), 0.0),
            'battery': (level * 100 if level is not None and level <= 1 else level),
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
    @http.route(['/lab_fieldwork/gps', '/lab_fieldwork/gps/<string:device_id>'], type='http',
                auth='none', methods=['GET', 'POST'], csrf=False, save_session=False, readonly=False)
    def gps(self, device_id=None, **params):
        body = None
        if request.httprequest.mimetype == 'application/json':
            try:
                body = json.loads(request.httprequest.get_data(as_text=True) or '{}')
            except ValueError:
                return request.make_response('bad json', status=400)
        if body:
            found, pings = parse_body(body)
        else:
            found, pings = parse_query(params)
        device_id = str(device_id or found or '')
        if len(device_id) < MIN_DEVICE_ID:
            return request.make_response('unknown device', status=403)
        user = request.env['res.users'].sudo().search(
            [('fw_gps_device_id', '=', device_id), ('fw_gps_app', '=', True)], limit=1)
        if not user:
            _logger.info("field GPS: position for an unknown device %s", device_id)
            return request.make_response('unknown device', status=403)
        kept = 0
        # An excused phone is never watched, app or browser; the app is answered
        # OK all the same, or it retries the same positions for ever.
        if pings and not user.fw_location_exception:
            kept = request.env['lab.location.ping'].with_user(user).with_company(user.company_id) \
                .with_context(fw_ping_source='app').record(pings)
        return request.make_response('OK %s' % kept, headers=[('Content-Type', 'text/plain')])
