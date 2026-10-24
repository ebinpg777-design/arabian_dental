# -*- coding: utf-8 -*-
"""The figures behind the Scan Register dashboard.

One call, one dict: what came in during the period and how it compares with the
period before, where everything open is standing now, how long each step takes,
where the scans come from, who sends them, what the machines did - plates and the
models on them - and what needs somebody's attention. Every figure carries the
domain that produced it and the model that domain is on, so a click opens exactly
those records and nothing else. Days are the lab's days, not UTC's.

The printing room has a section of its own (client, 2026-10-14: "improve the
dashboard with print order and machine details; a Today period"): the plates
from Plating to Printed, each machine's day and how busy it was, a timeline that
carries the queue forward onto the machines, when the waiting patients will be
printed, what to plate next, and why models fail.

A model is an arch (client, 2026-10-23: "one or two scans will fail, or the U or L
of one scan"): every model figure is a sum of the plate lines' printed and failed
models, with the patients' row count beside it for the click. The re-plate backlog,
the quality panels - reasons by machine, by arch, by attempt, by resin batch, the
models failed twice, the printing time and resin the failures cost, ready on time -
answer what the scan manager asks of the room. The board is the scan manager's and
the administrator's alone.
"""
import math
from collections import defaultdict
from datetime import datetime, time, timedelta

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import AccessError

from .channel import KINDS
from .print_order import (FAILURE_REASONS, LAB_HOURS, PARTS, SLICED_NOT_STARTED_HOURS,
                          STATES as PLATE_STATES, WAITING_LONG_HOURS, WASH_LONG_HOURS)
from .scan_line import OPEN

PERIODS = ('today', 'week', 'month', 'last_month', 'quarter')
STAGES = [
    ('confirm', 'Received → Confirmed', 'date_received', 'date_confirmed'),
    ('to_print', 'Confirmed → Sent for printing', 'date_confirmed', 'date_print_start'),
    ('queue', 'Waiting for a machine', 'date_print_start', 'date_in_progress'),
    ('printing', 'Print in progress', 'date_in_progress', 'date_printed'),
    ('to_order', 'Printed → Ordered', 'date_printed', 'date_ordered'),
]
# a plate that came off the machine, clean or not: the ones the period counts
CLOSED = ('washing', 'done', 'failed')
# a plate whose models are counted as printed: it came off with something usable
PRINTED = ('washing', 'done')
# a plate not yet finished with: the printing room's work in hand
LIVE_PLATES = ('draft', 'printing', 'washing')
# a plate's print time when neither its file, its machine nor the last month says
PLATE_HOURS_FALLBACK = 3.0
# the plates the room has printed lately set the forecast's pace
PACE_DAYS = 30
PRINT_NEXT = 8
DAY_MINUTES = 24 * 60
RECENT_PLATES = 10
# the re-plate backlog: how many rows the board lists, and when a wait is long
BACKLOG_ROWS = 12
REPLATE_LONG_HOURS = 4
REPEAT_ROWS = 8
# the board redraws itself: every minute on Today, every three otherwise
REFRESH_TODAY = 60
REFRESH_OTHER = 180
# the reasons as the heat table's column heads
REASON_SHORT = {'adhesion': 'Adhesion', 'supports': 'Supports', 'material': 'Resin', 'power': 'Power',
                'layers': 'Layers', 'file': 'File', 'machine': 'Machine', 'other': 'Other'}


def models_printed_domain_of(models_window):
    """The lines of a window with an arch printed."""
    return models_window + [('printed_models', '>', 0)]


def _avg(values, digits=1):
    return round(sum(values) / len(values), digits) if values else None


class Scan(models.Model):
    _inherit = 'lab.scan'

    # ------------------------------------------------------------------ windows
    @api.model
    def _dashboard_window(self, period, now=None):
        """(start, end, prev_start, prev_end) as local dates, end exclusive - or, for
        the end of today's comparison, a local datetime.

        Month to date is compared with the same days of last month, the way the
        Business Pulse compares - not with the whole of last month, which a half
        month always loses to. Today is compared with yesterday up to the same
        time: a morning is never measured against a whole day."""
        now = now or fields.Datetime.now()
        local_now = pytz.utc.localize(now).astimezone(self._lab_tz()).replace(tzinfo=None)
        today = local_now.date()
        tomorrow = today + timedelta(days=1)
        if period == 'today':
            return today, tomorrow, today - timedelta(days=1), local_now - timedelta(days=1)
        if period == 'week':
            start = today - timedelta(days=today.weekday())
            return start, tomorrow, start - timedelta(days=7), tomorrow - timedelta(days=7)
        if period == 'last_month':
            end = today.replace(day=1)
            start = (end - timedelta(days=1)).replace(day=1)
            prev_start = (start - timedelta(days=1)).replace(day=1)
            return start, end, prev_start, start
        if period == 'quarter':
            start = today - timedelta(days=89)
            return start, tomorrow, start - timedelta(days=90), start
        start = today.replace(day=1)
        prev_start = (start - timedelta(days=1)).replace(day=1)
        days = (tomorrow - start).days
        prev_end = min(prev_start + timedelta(days=days), start)
        return start, tomorrow, prev_start, prev_end

    @api.model
    def _utc(self, value):
        """A lab-local date (its midnight) or naive local datetime, as a UTC string."""
        if not isinstance(value, datetime):
            value = datetime.combine(value, time.min)
        local = self._lab_tz().localize(value)
        return fields.Datetime.to_string(local.astimezone(pytz.utc).replace(tzinfo=None))

    # ------------------------------------------------------------------ the figures
    @api.model
    def get_dashboard(self, period='month'):
        # the scan manager's board, and the administrator's (who implies the role)
        # (client, 2026-10-23); the menu is the door, this is the wall
        if not (self.env.su or self.env.user.has_group('lab_scan_register.group_scan_manager')):
            raise AccessError(_("The scan dashboard is for the scan manager."))
        period = period if period in PERIODS else 'month'
        now = fields.Datetime.now()
        start, end, prev_start, prev_end = self._dashboard_window(period, now)
        company = self.env.company
        Scan = self.with_context(tz=self._lab_tz().zone)
        Line = self.env['lab.scan.line']
        base = [('company_id', '=', company.id)]
        live = base + [('state', '!=', 'cancel')]
        window = [('date_received', '>=', self._utc(start)), ('date_received', '<', self._utc(end))]
        before = [('date_received', '>=', self._utc(prev_start)), ('date_received', '<', self._utc(prev_end))]
        open_domain = base + [('state', 'in', OPEN)]

        def count(domain):
            return Scan.search_count(domain)

        def patients(domain):
            return Line.search_count([('scan_id.' + f, op, v) for f, op, v in domain])

        avg = _avg

        # -------------------------------------------------------- headline
        received, received_prev = count(live + window), count(live + before)
        pts, pts_prev = patients(live + window), patients(live + before)
        ordered_pts = Line.search_count([('scan_id.' + f, op, v) for f, op, v in live + window]
                                        + [('sale_order_id', '!=', False)])
        done = Scan.search_read(base + [('state', '=', 'done'), ('date_ordered', '>=', self._utc(start)),
                                        ('date_ordered', '<', self._utc(end))],
                                ['date_received', 'date_ordered'])
        turnaround = [(r['date_ordered'] - r['date_received']).total_seconds() / 3600
                      for r in done if r['date_ordered'] and r['date_received']]
        headline = {
            'received': received, 'received_prev': received_prev,
            'patients': pts, 'patients_prev': pts_prev,
            'ordered_patients': ordered_pts,
            'conversion': round(ordered_pts * 100.0 / pts) if pts else None,
            'turnaround_hours': avg(turnaround),
            'turnaround_count': len(turnaround),
            'open': count(open_domain),
            'at_risk': count(open_domain + [('delivery_at_risk', '=', True)]),
        }

        # -------------------------------------------------------- pipeline, now
        labels = dict(self._fields['state'].selection)
        by_state = {state: (n, p or 0, oldest) for state, n, p, oldest in Scan._read_group(
            open_domain, ['state'], ['__count', 'patient_count:sum', 'date_received:min'])}
        now = fields.Datetime.now()
        pipeline = []
        for state in OPEN:
            n, p, oldest = by_state.get(state, (0, 0, None))
            pipeline.append({'state': state, 'label': labels[state], 'count': n, 'patients': p,
                             'oldest_days': (now - oldest).days if oldest else None,
                             'domain': base + [('state', '=', state)]})

        # -------------------------------------------------------- stage times
        rows = Scan.search_read(live + window, [s[2] for s in STAGES] + [s[3] for s in STAGES])
        stages = []
        for key, label, frm, to in STAGES:
            hours = [(r[to] - r[frm]).total_seconds() / 3600 for r in rows if r[frm] and r[to]]
            stages.append({'key': key, 'label': label, 'count': len(hours), 'hours': avg(hours)})
        timed = [s['hours'] for s in stages if s['hours'] is not None]
        slowest = max(timed) if timed else None
        for stage in stages:
            stage['slowest'] = slowest is not None and stage['hours'] == slowest and len(timed) > 1

        # -------------------------------------------------------- where from
        kinds = dict(KINDS)
        src = {k: (n, p or 0) for k, n, p in Scan._read_group(
            live + window, ['source'], ['__count', 'patient_count:sum'])}
        sources = [{'key': k, 'label': kinds[k], 'count': src.get(k, (0, 0))[0],
                    'patients': src.get(k, (0, 0))[1],
                    'domain': live + window + [('source', '=', k)]} for k, _l in KINDS]

        # -------------------------------------------------------- trend
        granularity = {'quarter': 'week', 'today': 'hour'}.get(period, 'day')
        trend_counts = defaultdict(lambda: defaultdict(int))
        for bucket, source, n in Scan._read_group(
                live + window, ['date_received:%s' % granularity, 'source'], ['__count']):
            # an hour bucket comes back as the lab's wall clock (the context's tz)
            key = bucket.hour if granularity == 'hour' else (bucket.date() if isinstance(bucket, datetime) else bucket)
            trend_counts[key][source] += n
        # NB: never `cursor` here - odoo's _() takes a local of that name for a db cursor
        trend, day = [], start
        if granularity == 'hour':
            # the lab's hours, widened to whatever the day really used
            hours = list(trend_counts)
            first = min([LAB_HOURS[0]] + hours)
            last = max([LAB_HOURS[1]] + [h + 1 for h in hours])
            for hour in range(first, last):
                a = datetime.combine(start, time(hour))
                point = trend_counts.get(hour, {})
                trend.append({'label': '%02d' % hour, 'date': '%s %02d:00' % (start, hour),
                              'total': sum(point.values()),
                              'by_source': {k: point.get(k, 0) for k, _l in KINDS},
                              'domain': live + [('date_received', '>=', self._utc(a)),
                                                ('date_received', '<', self._utc(a + timedelta(hours=1)))]})
            day = end                                            # nothing more by day
        if granularity == 'week':
            day = start - timedelta(days=start.weekday())
        step = timedelta(days=7 if granularity == 'week' else 1)
        while day < end:
            point = trend_counts.get(day, {})
            label = day.strftime('%d %b') if granularity == 'week' else day.strftime('%d')
            trend.append({'label': label, 'date': fields.Date.to_string(day),
                          'total': sum(point.values()),
                          'by_source': {k: point.get(k, 0) for k, _l in KINDS},
                          # the bar's own lab-day boundaries, so a click opens exactly it
                          'domain': live + [('date_received', '>=', self._utc(max(day, start))),
                                            ('date_received', '<', self._utc(min(day + step, end)))]})
            day += step

        # -------------------------------------------------------- who sends
        def top(field, limit=8):
            out = []
            for rec, n, p in Scan._read_group(live + window + [(field, '!=', False)], [field],
                                              ['__count', 'patient_count:sum'],
                                              order='patient_count:sum desc', limit=limit):
                out.append({'id': rec.id, 'name': rec.display_name, 'count': n, 'patients': p or 0,
                            'domain': live + window + [(field, '=', rec.id)]})
            return out

        # -------------------------------------------------------- the printing room
        room = self._dashboard_printing(company, start, end, prev_start, prev_end, now, period)
        headline.update(room.pop('headline'))
        Order = self.env['lab.scan.print.order']
        Model = self.env['lab.scan.print.order.line']
        ORDER, MODEL = Order._name, Model._name

        # -------------------------------------------------------- attention, now
        def ago(hours):
            return fields.Datetime.to_string(now - timedelta(hours=hours))

        open_lines = Line.search([('scan_id.company_id', '=', company.id), ('scan_id.state', 'in', OPEN)])
        dup_scans = open_lines.filtered('duplicate_count').scan_id
        in_printing = [('scan_id.state', 'in', ('printing', 'in_progress'))]
        attention = [
            {'key': 'at_risk', 'label': _("Delivery at risk"), 'icon': 'fa-truck', 'tone': 'danger',
             'domain': open_domain + [('delivery_at_risk', '=', True)]},
            {'key': 'failed', 'label': _("Failed print, back in the queue"), 'icon': 'fa-print',
             'tone': 'danger',
             'domain': base + [('state', 'in', ('printing', 'in_progress')), ('replate_waiting_count', '>', 0)]},
            {'key': 'half_printed', 'label': _("One arch printed, the other to print again"),
             'icon': 'fa-adjust', 'tone': 'danger', 'model': Line._name,
             'domain': base + [('replate_pending', '=', True), ('arch', '=', 'both'),
                               ('print_part', 'in', ('upper', 'lower'))]},
            {'key': 'failed_twice', 'label': _("Failed twice or more, not printed yet"), 'icon': 'fa-exclamation',
             'tone': 'danger', 'model': Line._name,
             'domain': base + [('replate_pending', '=', True), ('failed_print_count', '>=', 2)]},
            {'key': 'lost_mid_print', 'label': _("Lost mid-print, settled at Plate Off"), 'icon': 'fa-chain-broken',
             'tone': 'warning', 'model': MODEL,
             'domain': base + [('result', '=', 'pending'), ('lost_part', '!=', False)]},
            {'key': 'due_off', 'label': _("Plate due off"), 'icon': 'fa-bell', 'tone': 'danger',
             'model': ORDER, 'domain': base + [('due_off', '=', True)]},
            {'key': 'running_long', 'label': _("Print running longer than usual"), 'icon': 'fa-clock-o',
             'tone': 'warning', 'model': ORDER, 'domain': base + [('running_long', '=', True)]},
            # the patients - the queue's own rows, where Put on a Plate is
            {'key': 'replate_waiting', 'label': _("Models waiting to be re-plated"), 'icon': 'fa-repeat',
             'tone': 'danger', 'model': Line._name,
             'domain': base + in_printing + [('print_state', '=', 'to_plate'), ('last_failure', '!=', False)]},
            {'key': 'replate_waiting_long', 'label': _("Re-plates waiting over %d h", REPLATE_LONG_HOURS),
             'icon': 'fa-hourglass-end', 'tone': 'danger', 'model': Line._name,
             'domain': base + in_printing + [('print_state', '=', 'to_plate'), ('last_failure', '!=', False),
                                             ('waiting_since', '<', ago(REPLATE_LONG_HOURS))]},
            {'key': 'waiting_long', 'label': _("Waiting for a plate over a day"), 'icon': 'fa-pause-circle-o',
             'tone': 'warning', 'model': Line._name,
             'domain': base + [('print_state', '=', 'to_plate'), ('waiting_since', '<', ago(WAITING_LONG_HOURS))]},
            {'key': 'washing_long', 'label': _("In wash & cure over %d h", WASH_LONG_HOURS), 'icon': 'fa-tint',
             'tone': 'warning', 'model': ORDER,
             'domain': base + [('state', '=', 'washing'), ('date_end', '<', ago(WASH_LONG_HOURS))]},
            {'key': 'files_missing', 'label': _("Plates waiting for files"), 'icon': 'fa-paperclip',
             'tone': 'danger', 'model': ORDER,
             'domain': base + [('state', '=', 'draft'), ('files_missing_count', '>', 0)]},
            {'key': 'sliced_not_started', 'label': _("Sliced, not started for %d h", SLICED_NOT_STARTED_HOURS),
             'icon': 'fa-file-o', 'tone': 'info', 'model': ORDER,
             'domain': base + [('state', '=', 'draft'), ('date_sliced', '<', ago(SLICED_NOT_STARTED_HOURS))]},
            {'key': 'no_file', 'label': _("Printing without a sliced file"), 'icon': 'fa-question-circle-o',
             'tone': 'info', 'model': ORDER,
             'domain': base + [('state', '=', 'printing'), ('slice_parsed', '=', False),
                               ('estimated_hours', '=', 0)]},
            {'key': 'link', 'label': _("Download link expiring, no files"), 'icon': 'fa-hourglass-half',
             'tone': 'warning',
             'domain': open_domain + [('download_state', 'in', ('soon', 'expired')), ('has_attachments', '=', False)]},
            {'key': 'unmatched', 'label': _("Doctor not matched"), 'icon': 'fa-user-times', 'tone': 'warning',
             'domain': open_domain + [('partner_id', '=', False)]},
            {'key': 'no_ack', 'label': _("WhatsApp scans not acknowledged"), 'icon': 'fa-whatsapp',
             'tone': 'info',
             'domain': open_domain + [('source', '=', 'whatsapp'), ('date_acknowledged', '=', False)]},
            {'key': 'dup', 'label': _("Possible duplicates"), 'icon': 'fa-clone', 'tone': 'info',
             'domain': [('id', 'in', dup_scans.ids)]},
        ]
        for item in attention:
            item.setdefault('model', 'lab.scan')
            item['count'] = self.env[item['model']].search_count(item['domain'])

        return dict(room, **{
            'period': period,
            'is_today': period == 'today',
            'refresh_seconds': REFRESH_TODAY if period == 'today' else REFRESH_OTHER,
            'now': fields.Datetime.to_string(now),
            'start': fields.Date.to_string(start),
            'end': fields.Date.to_string(end - timedelta(days=1)),
            'window_domain': live + window,
            'open_domain': open_domain,
            'headline': headline,
            'pipeline': pipeline,
            'stages': stages,
            'sources': sources,
            'trend': trend,
            'trend_granularity': granularity,
            'clinics': top('partner_id'),
            'channels': top('channel_id', limit=6),
            'attention': attention,
        })

    # ------------------------------------------------------------------ the printing room
    @api.model
    def _dashboard_printing(self, company, start, end, prev_start, prev_end, now, period):
        """Plates and the machines they ran on, for the period - and the room as it
        stands now: what is on the machines, what is plated and waiting, the patients
        not yet on a plate, and when all of it will be printed at the room's pace.

        A plate is a print order holding several patients' models; one model can fail
        while its plate-mates print. So plates (lab.scan.print.order) and models
        (its lines) are counted on their own models, each figure with its domain. A
        plate belongs to the period it was STARTED in; a machine's busy time is every
        plate on it during the period, clipped to the period so far."""
        Order = self.env['lab.scan.print.order']
        Model = self.env['lab.scan.print.order.line']
        Printer = self.env['lab.scan.printer']
        ORDER, MODEL = Order._name, Model._name
        tz = self._lab_tz()
        base = [('company_id', '=', company.id)]
        ws, we = self._utc(start), self._utc(end)
        ws_dt, we_dt = fields.Datetime.to_datetime(ws), fields.Datetime.to_datetime(we)
        horizon = max(ws_dt, min(now, we_dt))                      # the period so far
        window_hours = (horizon - ws_dt).total_seconds() / 3600
        in_window = [('date_start', '>=', ws), ('date_start', '<', we)]
        in_before = [('date_start', '>=', self._utc(prev_start)), ('date_start', '<', self._utc(prev_end))]
        plates_domain = base + [('state', 'in', CLOSED)] + in_window
        # a model's result is written the moment its plate comes off, so an ok or a
        # failed line always sits on a closed plate; the line carries its plate's
        # start and machine, so the plate's window reads straight on the lines
        models_window = base + in_window
        # a model is an arch: these are the lines with an arch printed / lost, their
        # sums the figures and their rows what a click opens
        models_printed_domain = models_window + [('printed_models', '>', 0)]
        models_failed_domain = models_window + [('failed_models', '>', 0)]
        verdict = ['|', ('printed_models', '>', 0), ('failed_models', '>', 0)]

        def local(dt):
            return pytz.utc.localize(dt).astimezone(tz).replace(tzinfo=None)

        def stamp(dt):
            return fields.Datetime.to_string(dt) if dt else None

        by_printer = {(printer.id, state): (n, hours or 0.0, ml or 0.0, models or 0)
                      for printer, state, n, hours, ml, models in Order._read_group(
                          plates_domain, ['printer_id', 'state'],
                          ['__count', 'duration_hours:sum', 'resin_ml:sum', 'model_count:sum'])}
        # the arches are the figures; the rows (a patient per plate) are what a click opens
        models_by_printer = {printer.id: (ok or 0, lost or 0, rows) for printer, ok, lost, rows in Model._read_group(
            models_window + verdict, ['printer_id'], ['printed_models:sum', 'failed_models:sum', '__count'])}
        patients_by_printer = {(printer.id, key): n for key, dom in (('ok', models_printed_domain),
                                                                     ('failed', models_failed_domain))
                               for printer, n in Model._read_group(dom, ['printer_id'], ['__count'])}
        reasons_by_printer = defaultdict(list)
        for printer, reason, n in Model._read_group(models_failed_domain, ['printer_id', 'failure_reason'],
                                                    ['failed_models:sum'], order='failed_models:sum desc'):
            reasons_by_printer[printer.id].append((reason, n or 0))
        queued_by_printer = {printer.id: n for printer, n in Order._read_group(
            base + [('state', '=', 'draft')], ['printer_id'], ['__count'])}
        running_by_printer = {order.printer_id.id: order
                              for order in Order.search(base + [('state', '=', 'printing')])}
        # the plates' own clocks: rows, not sums, because the accuracy of an estimate
        # is an average of ratios and a ratio of sums would let one long plate hide
        # ten short ones
        plate_rows = Order.search_read(
            base + [('state', '!=', 'cancel')] + in_window,
            ['printer_id', 'state', 'create_date', 'date_start', 'date_end', 'date_cured',
             'duration_hours', 'wash_hours', 'estimated_hours', 'slice_parsed', 'resin_ml'])
        accuracy = defaultdict(list)
        for r in plate_rows:
            if r['state'] in CLOSED and r['slice_parsed'] and r['estimated_hours']:
                ratio = r['duration_hours'] * 100.0 / r['estimated_hours']
                accuracy[r['printer_id'][0] if r['printer_id'] else None].append(ratio)
                accuracy['all'].append(ratio)

        # every plate on a machine during the period - one started before it and still
        # printing too - clipped to the period so far: the machines' busy time
        spans = Order.search_read(
            base + [('printer_id', '!=', False), ('date_start', '!=', False), ('date_start', '<', we),
                    '|', ('date_end', '=', False), ('date_end', '>', ws)],
            ['printer_id', 'state', 'date_start', 'date_end', 'expected_end', 'name', 'model_count',
             'patient_count', 'failed_count'])
        busy = defaultdict(float)
        for r in spans:
            a, b = max(r['date_start'], ws_dt), min(r['date_end'] or now, horizon)
            if b > a:
                busy[r['printer_id'][0]] += (b - a).total_seconds() / 3600

        machines = Printer.search([('company_id', '=', company.id)])
        last_failed = {}
        for line in Model.search(base + [('result', '=', 'failed'), ('printer_id', 'in', machines.ids)],
                                 order='date_end desc, id desc', limit=200):
            last_failed.setdefault(line.printer_id.id, line)
        reasons = dict(FAILURE_REASONS)
        unsaid = _("Not said")          # here, not in a comprehension: _() reads its caller's frame
        printers = []
        for printer in machines:
            cells = [by_printer.get((printer.id, state), (0, 0.0, 0.0, 0)) for state in CLOSED]
            plates = sum(c[0] for c in cells)
            hours = sum(c[1] for c in cells)
            resin = sum(c[2] for c in cells)
            models_done, models_failed, patients_seen = models_by_printer.get(printer.id, (0, 0, 0))
            running = running_by_printer.get(printer.id)
            failed = last_failed.get(printer.id)
            next_plate = printer.next_order_id
            printers.append({
                'id': printer.id, 'name': printer.name, 'code': printer.code, 'state': printer.state,
                'color': printer.color or 0, 'capacity': printer.plate_capacity,
                'running': 1 if running else 0,
                'running_order': {
                    'id': running.id, 'name': running.name,
                    'started': stamp(running.date_start),
                    'expected_end': stamp(running.expected_end),
                    'progress_pct': running.progress_pct,
                    'elapsed_hours': round(running.elapsed_hours, 2),
                    'estimated_hours': round(running.estimated_hours or printer.typical_hours or 0.0, 2),
                    'patients': running.patient_count, 'models': running.model_count,
                    'running_long': running.running_long, 'due_off': running.due_off,
                    'failed_models': running.failed_models, 'lost': running.lost_count,
                } if running else None,
                # a machine in service with nothing on it: free since its last plate came off
                'idle_since': stamp(printer.last_print_date) if not running and printer.state == 'ready' else None,
                'next_plate': {'id': next_plate.id, 'name': next_plate.name,
                               'planned': next_plate.printer_id == printer} if next_plate else None,
                'queued': queued_by_printer.get(printer.id, 0),
                'plates': plates,
                'models_done': models_done, 'models_failed': models_failed,
                'patients': patients_seen,
                'patients_done': patients_by_printer.get((printer.id, 'ok'), 0),
                'patients_failed': patients_by_printer.get((printer.id, 'failed'), 0),
                'reasons_top': [{'key': r or 'none', 'label': reasons.get(r, unsaid), 'count': n,
                                 'domain': models_failed_domain + [('printer_id', '=', printer.id),
                                                                   ('failure_reason', '=', r)]}
                                for r, n in reasons_by_printer.get(printer.id, [])[:2]],
                'failure_rate': round(models_failed * 100.0 / (models_done + models_failed))
                if models_done + models_failed else None,
                'hours': round(hours, 1),
                'avg_hours': round(hours / plates, 1) if plates else None,
                'busy_hours': round(busy.get(printer.id, 0.0), 1),
                'utilisation': round(busy.get(printer.id, 0.0) * 100 / window_hours) if window_hours > 0 else None,
                'resin_ml': round(resin, 1),
                'estimate_accuracy': _avg(accuracy.get(printer.id), 0),
                'last_failure': {'reason': reasons.get(failed.failure_reason, _("Not said")),
                                 'when': stamp(failed.date_end), 'order': failed.order_id.name}
                if failed else None,
                'domain': plates_domain + [('printer_id', '=', printer.id)],
                'model': ORDER,
                'models_domain': models_window + verdict + [('printer_id', '=', printer.id)],
                'models_model': MODEL,
                'running_domain': base + [('state', '=', 'printing'), ('printer_id', '=', printer.id)],
                'running_model': ORDER,
                'queue_domain': base + [('state', '=', 'draft'), ('printer_id', '=', printer.id)],
                'queue_model': ORDER,
            })

        # -------------------------------------------------------- the headline's plates
        def closed_totals(domain):
            plates_ = Order.search_count(base + [('state', 'in', CLOSED)] + domain)
            [(done_, failed_)] = Model._read_group(base + domain + verdict, [],
                                                   ['printed_models:sum', 'failed_models:sum'])
            return plates_, done_ or 0, failed_ or 0
        plates_total, printed_total, failed_total = closed_totals(in_window)
        plates_prev, printed_prev, failed_prev = closed_totals(in_before)
        by_arch_printed = {arch: n or 0 for arch, n in Model._read_group(
            models_printed_domain, ['printed_arches'], ['printed_models:sum'])}
        models_total = sum(v[3] for (_p, state), v in by_printer.items() if state in CLOSED)
        resin_total = sum(v[2] for (_p, state), v in by_printer.items() if state in CLOSED)
        # resin is a figure only once a file has been read (or a figure typed); a
        # period of unparsed plates has no consumption to show, not a nought
        parsed = any(r['slice_parsed'] for r in plate_rows if r['state'] in CLOSED)
        ready = machines.filtered(lambda p: p.state == 'ready')

        def rate(failed_, printed_):
            return round(failed_ * 100.0 / (printed_ + failed_)) if printed_ + failed_ else None
        headline = {
            'plates': plates_total, 'plates_prev': plates_prev,
            'models_printed': printed_total, 'models_failed': failed_total,
            'models_printed_prev': printed_prev,
            # the rows behind the two figures: patients with an arch printed / lost
            'patients_printed': Model.search_count(models_printed_domain),
            'patients_failed': Model.search_count(models_failed_domain),
            'models_upper': by_arch_printed.get('upper', 0) + by_arch_printed.get('both', 0) // 2,
            'models_lower': by_arch_printed.get('lower', 0) + by_arch_printed.get('both', 0) // 2,
            'half_failed': Model.search_count(models_printed_domain + [('failed_models', '>', 0)]),
            'half_failed_domain': models_printed_domain + [('failed_models', '>', 0)],
            'plates_partial': Order.search_count(plates_domain + [('is_partial', '=', True)]),
            'model_failure_rate': rate(failed_total, printed_total),
            'model_failure_rate_prev': rate(failed_prev, printed_prev),
            'avg_models_per_plate': round(models_total / float(plates_total), 1) if plates_total else None,
            'resin_ml': round(resin_total, 1) if parsed or resin_total else None,
            'estimate_accuracy': _avg(accuracy.get('all'), 0),
            'printing_hours': round(sum(busy.values()), 1),
            'machines_busy': len(running_by_printer),
            'machines_ready': len(ready),
            'fleet_utilisation': round(sum(busy.get(p.id, 0.0) for p in ready) * 100 / (window_hours * len(ready)))
            if window_hours > 0 and ready else None,
        }

        # -------------------------------------------------------- the plate's steps
        # Plating counts every plate that went on in the period; printing and the
        # estimate only the ones that came off with something usable (an aborted
        # plate's ten minutes say nothing about how long a print takes); wash & cure
        # only the ones somebody pressed Cured on.
        plating = [(r['date_start'] - r['create_date']).total_seconds() / 3600
                   for r in plate_rows if r['create_date'] and r['date_start']]
        printed_rows = [r for r in plate_rows if r['state'] in PRINTED and r['date_end']]
        printing = [r['duration_hours'] for r in printed_rows]
        estimated = [r['estimated_hours'] for r in printed_rows if r['estimated_hours']]
        washing = [r['wash_hours'] for r in plate_rows if r['date_end'] and r['date_cured']]
        plate_stages = [
            {'key': 'plating', 'label': _("Plated → started"), 'count': len(plating), 'hours': _avg(plating)},
            {'key': 'printing', 'label': _("Printing"), 'count': len(printing), 'hours': _avg(printing),
             'estimated': _avg(estimated)},
            {'key': 'washing', 'label': _("Wash & cure"), 'count': len(washing), 'hours': _avg(washing)},
        ]

        # -------------------------------------------------------- the plates, now and in the period
        draft = Order.search(base + [('state', '=', 'draft')])
        on_machines = Order.search(base + [('state', '=', 'printing')])
        in_wash = Order.search(base + [('state', '=', 'washing')])
        oldest_wash = min([d for d in in_wash.mapped('date_end') if d], default=None)
        printed_window = base + [('state', 'in', PRINTED)] + in_window
        failed_window = base + [('state', '=', 'failed')] + in_window
        plate_flow = [
            {'key': 'draft', 'label': _("Plating"), 'icon': 'fa-th', 'count': len(draft),
             'models': sum(draft.mapped('model_count')),
             'replate_models': sum(draft.line_ids.filtered(
                 lambda l: l.replated_from_id and l.result == 'pending').mapped('model_count')),
             'ready': len(draft.filtered('is_ready')), 'files': len(draft.filtered('files_missing_count')),
             'unplanned': len(draft.filtered(lambda o: not o.printer_id)),
             'domain': base + [('state', '=', 'draft')]},
            {'key': 'printing', 'label': _("Printing"), 'icon': 'fa-print', 'count': len(on_machines),
             'models': sum(on_machines.mapped('model_count')),
             'late': len(on_machines.filtered(lambda o: o.running_long or o.due_off)),
             'domain': base + [('state', '=', 'printing')]},
            {'key': 'washing', 'label': _("Wash & cure"), 'icon': 'fa-tint', 'count': len(in_wash),
             'models': sum(in_wash.mapped('model_count')),
             'oldest_hours': round((now - oldest_wash).total_seconds() / 3600, 1) if oldest_wash else None,
             'domain': base + [('state', '=', 'washing')]},
            {'key': 'printed', 'label': _("Printed in the period"), 'icon': 'fa-check', 'period': True,
             'count': Order.search_count(printed_window), 'models': printed_total,
             'partial': Order.search_count(printed_window + [('is_partial', '=', True)]),
             'domain': printed_window},
            {'key': 'failed', 'label': _("Failed in the period"), 'icon': 'fa-exclamation-triangle', 'period': True,
             'count': Order.search_count(failed_window), 'models': failed_total,
             'domain': failed_window},
        ]
        for step in plate_flow:
            step['model'] = ORDER

        # -------------------------------------------------------- the queue, carried onto the machines
        # Every ready machine is free from now, or from when its plate is due off; the
        # plates planned on it follow in their order, then the plated ones nobody
        # planned go to whichever machine is free first, then the patients not yet on
        # a plate, as plates of the machines' usual size, at the room's recent pace.
        paced = Order.search_read(base + [('state', 'in', PRINTED), ('date_end', '!=', False),
                                          ('date_start', '>=', fields.Datetime.to_string(now - timedelta(days=PACE_DAYS)))],
                                  ['duration_hours'])
        typical = [p.typical_hours for p in ready if p.typical_hours]
        pace = _avg([r['duration_hours'] for r in paced if r['duration_hours']], 2) or _avg(typical, 2) \
            or PLATE_HOURS_FALLBACK
        free_at = {}
        for p in ready:
            running = running_by_printer.get(p.id)
            free_at[p.id] = max(now, running.expected_end or now) if running else now
        projected = defaultdict(list)       # printer id -> [(start, end, kind, plate)]

        def place(plate, hours, printer_id=None):
            if not free_at:
                return None
            pid = printer_id if printer_id in free_at else min(free_at, key=lambda k: (free_at[k], k))
            a = free_at[pid]
            b = a + timedelta(hours=hours)
            free_at[pid] = b
            projected[pid].append((a, b, plate))
            return pid
        for p in ready:
            running = running_by_printer.get(p.id)
            if running and running.expected_end and running.expected_end > now:
                projected[p.id].append((now, running.expected_end, ('running', running)))
        planned = draft.filtered(lambda o: o.printer_id in ready).sorted(lambda o: (o.sequence, o.id))
        for plate in planned:
            place(('planned', plate), plate.estimated_hours or plate.printer_id.typical_hours or pace, plate.printer_id.id)
        unplanned = (draft - planned).sorted(lambda o: (o.priority != '1', o.sequence, o.id))
        for plate in unplanned:
            place(('suggested', plate), plate.estimated_hours or pace)
        waiting = Order._waiting_lines(company)
        # the arches still to print: a patient waiting for their lower again is one
        models_waiting = sum(line.print_models or (2 if line.arch == 'both' else 1) for line in waiting)
        caps = [p.plate_capacity for p in ready if p.plate_capacity]
        capacity = round(sum(caps) / len(caps)) if caps else 8
        plates_needed = math.ceil(models_waiting / float(capacity)) if models_waiting else 0
        for _i in range(plates_needed):
            place(('patients', None), pace)
        anything = bool(draft or waiting or on_machines)
        clears_at = max(free_at.values()) if free_at and anything else None
        local_today = local(now).date()
        forecast = {
            'patients': len(waiting), 'models': models_waiting,
            'urgent': len(waiting.filtered(lambda l: l.priority == '1')),
            'replates': len(waiting.filtered('last_failure')),
            'replate_models': sum(waiting.filtered('last_failure').mapped('print_models')),
            'upper': len(waiting.filtered(lambda l: (l.print_part or l.arch) in ('upper', 'both'))),
            'lower': len(waiting.filtered(lambda l: (l.print_part or l.arch) in ('lower', 'both'))),
            'due_soon': len(waiting.filtered(lambda l: l.date_delivery and l.date_delivery <= local_today + timedelta(days=1))),
            'plates_needed': plates_needed, 'capacity': capacity,
            'plated': len(draft), 'pace_hours': pace,
            'print_hours': round(len(draft) * pace + plates_needed * pace, 1),
            'machines': len(ready),
            'clears_at': stamp(clears_at),
            'domain': [('id', 'in', waiting.ids)], 'model': 'lab.scan.line',
        }
        print_next = [{
            'id': line.id, 'scan_id': line.scan_id.id, 'scan': line.scan_id.name,
            'patient': line.patient_name or '', 'doctor': line.scan_id._doctor_label(),
            'delivery': fields.Date.to_string(line.date_delivery) if line.date_delivery else None,
            'late': bool(line.date_delivery and line.date_delivery <= local_today + timedelta(days=1)),
            'urgent': line.priority == '1', 'replate': bool(line.last_failure),
            # the arch still to print, not the register's: after a lost lower, Lower
            'arch': dict(PARTS).get(line.print_part or line.arch, ''),
            'part_key': line.print_part or line.arch, 'attempts': line.print_attempts,
            'since': stamp(line.scan_id.date_print_start or line.scan_id.date_received),
        } for line in waiting[:PRINT_NEXT]]

        # -------------------------------------------------------- the timeline
        lanes_of = machines.filtered(lambda p: p.state != 'retired') | machines.browse(
            {r['printer_id'][0] for r in spans})
        timeline = {'mode': 'hours' if period == 'today' else 'days', 'lanes': []}
        if period == 'today':
            midnight = datetime.combine(start, time.min)

            def minute(dt):
                return int((local(dt) - midnight).total_seconds() // 60)
            now_m = minute(now)
            lo, hi = LAB_HOURS[0] * 60, max(LAB_HOURS[1] * 60, now_m + 60)
            for p in lanes_of:
                segments = []
                for r in spans:
                    if r['printer_id'][0] != p.id:
                        continue
                    kind = {'printing': 'printing', 'failed': 'failed', 'cancel': 'cancel'}.get(r['state'], 'done')
                    a, b = minute(max(r['date_start'], ws_dt)), minute(min(r['date_end'] or now, we_dt))
                    segments.append({'a': a, 'b': max(b, a + 1), 'kind': kind, 'order_id': r['id'], 'name': r['name'],
                                     'models': r['model_count'], 'patients': r['patient_count'],
                                     'failed': r['failed_count']})
                for a_dt, b_dt, (kind, plate) in projected.get(p.id, []):
                    a, b = minute(a_dt), minute(b_dt)
                    if kind == 'running':
                        a = now_m              # the rest of the plate on the machine
                    segments.append({'a': a, 'b': max(b, a + 1), 'kind': 'projected' if kind == 'running' else kind,
                                     'order_id': plate.id if plate else False, 'name': plate.name if plate else _("Patients waiting"),
                                     'models': plate.model_count if plate else capacity,
                                     'patients': plate.patient_count if plate else 0, 'failed': 0,
                                     'tomorrow': b > DAY_MINUTES})
                for seg in segments:
                    lo = min(lo, seg['a'])
                    hi = max(hi, min(seg['b'], DAY_MINUTES))
                timeline['lanes'].append(dict(self._lane(p, busy, window_hours), segments=segments))
            timeline.update({'lo': max(0, lo // 60 * 60), 'hi': min(DAY_MINUTES, -(-hi // 60) * 60), 'now': now_m})
        else:
            days, day = [], start
            while day < end and day <= local_today:
                days.append(day)
                day += timedelta(days=1)
            hours_by = defaultdict(float)
            plates_by = defaultdict(int)
            for r in spans:
                a, b = local(max(r['date_start'], ws_dt)), local(min(r['date_end'] or now, horizon))
                if r['date_start'] >= ws_dt:
                    plates_by[(r['printer_id'][0], local(r['date_start']).date())] += 1
                while b > a:
                    cut = min(b, datetime.combine(a.date() + timedelta(days=1), time.min))
                    hours_by[(r['printer_id'][0], a.date())] += (cut - a).total_seconds() / 3600
                    a = cut
            timeline['days'] = [{'date': fields.Date.to_string(d), 'day': d.day, 'dow': d.strftime('%a')} for d in days]
            for p in lanes_of:
                cells = [{'date': fields.Date.to_string(d), 'hours': round(hours_by.get((p.id, d), 0.0), 1),
                          'plates': plates_by.get((p.id, d), 0),
                          'domain': base + [('printer_id', '=', p.id), ('date_start', '>=', self._utc(d)),
                                            ('date_start', '<', self._utc(d + timedelta(days=1)))]} for d in days]
                timeline['lanes'].append(dict(self._lane(p, busy, window_hours), cells=cells))

        # -------------------------------------------------------- why models fail
        by_reason = Model._read_group(models_failed_domain, ['failure_reason'], ['failed_models:sum', '__count'])
        failed_n = sum(n or 0 for _r, n, _rows in by_reason)
        not_said = _("Not said")         # outside the generator: _() reads its caller's frame for the language
        failure_reasons = sorted(({'key': r or 'none', 'label': reasons.get(r, not_said), 'count': n or 0,
                                   'rows': rows, 'pct': round((n or 0) * 100.0 / failed_n) if failed_n else 0,
                                   'domain': models_failed_domain + [('failure_reason', '=', r)]}
                                  for r, n, rows in by_reason), key=lambda x: -x['count'])
        parts = dict(PARTS)
        # by the arch lost: Upper, Lower, or both of a patient
        failed_parts = [{'key': k, 'label': parts.get(k, k), 'count': rows, 'models': n or 0,
                         'domain': models_failed_domain + [('failed_arches', '=', k)]}
                        for k, n, rows in Model._read_group(models_failed_domain, ['failed_arches'],
                                                            ['failed_models:sum', '__count']) if k]

        # -------------------------------------------------------- the latest plates
        state_label = dict(PLATE_STATES)
        rank = {'printing': 0, 'washing': 1, 'draft': 2}
        recent = Order.search(base + [('state', '!=', 'cancel'), '|', ('state', 'in', LIVE_PLATES),
                                      ('date_start', '>=', ws)], order='id desc', limit=60)
        recent = sorted(recent, key=lambda o: (rank.get(o.state, 3), -(o.date_start or o.create_date).timestamp(),
                                               -o.id))[:RECENT_PLATES]
        recent_plates = [{
            'id': o.id, 'name': o.name, 'state': o.state, 'state_label': state_label.get(o.state, o.state),
            'printed_models': o.printed_models, 'failed_models': o.failed_models,
            'replate': bool(o.replate_count), 'restart': bool(o.restarted_from_id),
            'replated_on': ', '.join(sorted(set(o.line_ids.filtered(lambda l: l.result == 'failed')
                                                .replated_by_id.order_id.mapped('name')))),
            'printer': {'id': o.printer_id.id, 'code': o.printer_id.code, 'name': o.printer_id.name,
                        'color': o.printer_id.color or 0} if o.printer_id else None,
            'patients': o.patient_count, 'models': o.model_count, 'failed': o.failed_count,
            'started': stamp(o.date_start), 'ended': stamp(o.date_end),
            'hours': round(o.duration_hours if o.date_end else o.elapsed_hours, 2) if o.date_start else None,
            'estimated': round(o.estimated_hours, 2) if o.estimated_hours else None,
            'progress': o.progress_pct if o.state == 'printing' else None,
            'ready': o.is_ready, 'files_missing': o.files_missing_count, 'urgent': o.priority == '1',
            'late': o.state == 'printing' and (o.running_long or o.due_off),
        } for o in recent]

        quality = self._dashboard_quality(company, base, models_window, models_failed_domain, in_window,
                                          in_before, plates_domain, busy, machines, models_by_printer, tz, now)
        return {
            'headline': headline,
            'quality': quality,
            'replate_backlog': self._dashboard_backlog(company, base, now),
            'print_trend': self._dashboard_print_trend(base, models_window, verdict, start, end, period),
            'printers': printers,
            'plate_stages': plate_stages,
            'plate_flow': plate_flow,
            'timeline': timeline,
            'forecast': forecast,
            'print_next': print_next,
            'failure_reasons': failure_reasons,
            'failed_parts': failed_parts,
            'recent_plates': recent_plates,
            # plated and ready, no machine yet: any free machine can take them at Start
            'unplanned_ready': Order.search_count(base + [('state', '=', 'draft'), ('printer_id', '=', False),
                                                          ('is_ready', '=', True)]),
            'unplanned_domain': base + [('state', '=', 'draft'), ('printer_id', '=', False)],
            'plates_domain': plates_domain, 'plates_model': ORDER,
            'models_printed_domain': models_printed_domain, 'models_printed_model': MODEL,
            'models_failed_domain': models_failed_domain, 'models_failed_model': MODEL,
        }

    @api.model
    def _dashboard_backlog(self, company, base, now):
        """Every failed model still waiting for a plate, oldest first: the arch it needs,
        how often it failed, why, on which machine, how long it waits, and the plate
        being plated with room for it."""
        Line = self.env['lab.scan.line']
        Order = self.env['lab.scan.print.order']
        domain = base + [('print_state', '=', 'to_plate'), ('last_failure', '!=', False),
                         ('scan_id.state', 'in', ('printing', 'in_progress'))]
        backlog = Line.search(domain, order='waiting_since asc, id')
        parts = dict(PARTS)

        def age(line):
            return (now - line.waiting_since).total_seconds() / 3600 if line.waiting_since else 0.0
        buckets = [{'key': 'fresh', 'label': _("under 2 h"), 'count': 0},
                   {'key': 'hours', 'label': _("2 – 8 h"), 'count': 0},
                   {'key': 'old', 'label': _("over 8 h"), 'count': 0}]
        for line in backlog:
            hours = age(line)
            buckets[0 if hours < 2 else 1 if hours < 8 else 2]['count'] += 1
        rows = []
        for line in backlog[:BACKLOG_ROWS]:
            failed = line._live_print_lines().filtered(lambda p: p.result == 'failed')[-1:]
            fits = Order._suggest_plate(line.print_models or 1, company=company)
            capacity, used = Order._plate_room(fits) if fits else (0, 0)
            rows.append({
                'id': line.id, 'scan_id': line.scan_id.id, 'scan': line.scan_id.name,
                'patient': line.patient_name or '', 'doctor': line.scan_id._doctor_label(),
                'part_key': line.print_part or line.arch, 'part_label': parts.get(line.print_part or line.arch, ''),
                'models': line.print_models, 'attempts': line.failed_print_count,
                'reason': line.last_failure or '',
                'machine': {'code': failed.printer_id.code, 'color': failed.printer_id.color or 0}
                if failed.printer_id else None,
                'failed_at': fields.Datetime.to_string(line.waiting_since) if line.waiting_since else None,
                'age_hours': round(age(line), 2),
                'delivery': fields.Date.to_string(line.date_delivery) if line.date_delivery else None,
                'late': bool(line.delivery_at_risk), 'urgent': line.priority == '1',
                'suggested_plate': {'id': fits.id, 'name': fits.name, 'fill': used, 'capacity': capacity,
                                    'machine_code': fits.printer_id.code or ''} if fits else None,
            })
        return {
            'count': len(backlog), 'models': sum(backlog.mapped('print_models')),
            'oldest_hours': round(age(backlog[0]), 1) if backlog else None,
            'failed_twice': len(backlog.filtered(lambda l: l.failed_print_count >= 2)),
            'buckets': buckets, 'rows': rows, 'domain': domain, 'model': Line._name,
        }

    @api.model
    def _dashboard_quality(self, company, base, models_window, models_failed_domain, in_window, in_before,
                           plates_domain, busy, machines, models_by_printer, tz, now):
        """Where the failures are: reasons by machine, by attempt, by resin batch; the
        models lost more than once; what the failures cost in printing time and
        resin; and whether printed models were ready for their delivery dates."""
        Model = self.env['lab.scan.print.order.line']
        Order = self.env['lab.scan.print.order']
        MODEL, ORDER = Model._name, Order._name
        reasons = dict(FAILURE_REASONS)
        verdict = ['|', ('printed_models', '>', 0), ('failed_models', '>', 0)]

        # reasons x machine: a heat table, each cell its failed models
        cells = defaultdict(dict)
        for printer, reason, n, rows in Model._read_group(
                models_failed_domain, ['printer_id', 'failure_reason'], ['failed_models:sum', '__count']):
            cells[printer.id][reason or 'none'] = (n or 0, rows)
        columns = [{'key': key, 'label': label, 'short': REASON_SHORT.get(key, label)}
                   for key, label in FAILURE_REASONS]
        matrix_rows, totals, total_rows = [], defaultdict(int), defaultdict(int)
        for printer in machines.filtered(lambda p: p.id in cells or p.state != 'retired'):
            mine = cells.get(printer.id, {})
            if not mine and printer.state == 'retired':
                continue
            done, lost, _rows = models_by_printer.get(printer.id, (0, 0, 0))
            row_cells = []
            for col in columns:
                n, rows = mine.get(col['key'], (0, 0))
                totals[col['key']] += n
                total_rows[col['key']] += rows
                row_cells.append({'reason': col['key'], 'count': n, 'rows': rows,
                                  'domain': models_failed_domain + [('printer_id', '=', printer.id),
                                                                    ('failure_reason', '=', col['key'])]})
            matrix_rows.append({
                'printer': {'id': printer.id, 'code': printer.code, 'name': printer.name, 'color': printer.color or 0},
                'cells': row_cells, 'total': sum(c['count'] for c in row_cells),
                'rows': sum(c['rows'] for c in row_cells),
                'rate': round(lost * 100.0 / (done + lost)) if done + lost else None,
                'domain': models_failed_domain + [('printer_id', '=', printer.id)],
            })
        matrix = {'reasons': columns, 'rows': matrix_rows,
                  'total_row': {'cells': [{'reason': c['key'], 'count': totals[c['key']], 'rows': total_rows[c['key']],
                                           'domain': models_failed_domain + [('failure_reason', '=', c['key'])]}
                                          for c in columns],
                                'total': sum(totals.values())}}

        # by attempt: do re-plates fail again?
        attempts = defaultdict(lambda: [0, 0, 0])
        for attempt, ok, lost, rows in Model._read_group(models_window + verdict, ['attempt'],
                                                         ['printed_models:sum', 'failed_models:sum', '__count']):
            key = min(attempt or 1, 3)
            attempts[key][0] += ok or 0
            attempts[key][1] += lost or 0
            attempts[key][2] += rows
        labels = {1: _("First print"), 2: _("Second print"), 3: _("Third or later")}
        by_attempt = [{'attempt': key, 'label': labels[key], 'ok': ok, 'failed': lost, 'rows': rows,
                       'rate': round(lost * 100.0 / (ok + lost)) if ok + lost else None,
                       'domain': models_window + verdict + ([('attempt', '>=', 3)] if key == 3
                                                            else [('attempt', '=', key)])}
                      for key, (ok, lost, rows) in sorted(attempts.items())]

        # by resin batch: a bad bottle shows as a red rate
        by_batch = []
        for batch, ok, lost, rows in Model._read_group(
                models_window + verdict + [('order_id.resin_batch', '!=', False)], ['order_id.resin_batch'],
                ['printed_models:sum', 'failed_models:sum', '__count']):
            total = (ok or 0) + (lost or 0)
            by_batch.append({'batch': batch, 'failed': lost or 0, 'total': total, 'rows': rows,
                             'rate': round((lost or 0) * 100.0 / total) if total else None,
                             'domain': models_window + verdict + [('order_id.resin_batch', '=', batch)]})
        by_batch = sorted(by_batch, key=lambda b: (-b['failed'], -b['total']))[:6]

        # the models lost twice or more in the period, with the path they took
        repeat = []
        unsaid = _("Not said")
        twice = Model.search(models_failed_domain + [('attempt', '>=', 2)], order='date_start desc, id desc')
        for patient in twice.scan_line_id[:REPEAT_ROWS]:
            path = [{'reason': reasons.get(p.failure_reason, unsaid), 'machine': p.printer_id.code or '',
                     'when': fields.Datetime.to_string(p.date_result or p.date_end) if (p.date_result or p.date_end)
                     else None} for p in patient._live_print_lines().filtered(lambda p: p.result == 'failed')]
            state = dict(patient._fields['print_state'].selection).get(patient.print_state, '')
            repeat.append({'line_id': patient.id, 'scan_id': patient.scan_id.id, 'patient': patient.patient_name or '',
                           'doctor': patient.scan_id._doctor_label(), 'attempts': len(path), 'path': path,
                           'where': patient.current_order_id.name or state})

        # what the failures cost: the lost share of each plate's time and resin
        def rework(window):
            hours = resin = 0.0
            for plate in Order.search(base + [('state', 'in', CLOSED), ('failed_models', '>', 0)] + window):
                share = plate.failed_models / float(plate.model_count) if plate.model_count else 1.0
                hours += (plate.duration_hours or 0.0) * share
                resin += (plate.resin_ml or 0.0) * share
            return round(hours, 1), round(resin, 1)
        hours, resin = rework(in_window)
        hours_prev, resin_prev = rework(in_before)
        printing = sum(busy.values())
        replate_lines = Model.search(models_window + [('replated_from_id', '!=', False), ('result', '!=', 'withdrawn')])

        # ready on time: each patient printed in the period against their delivery date
        def local_day(dt):
            return pytz.utc.localize(dt).astimezone(tz).date()
        on_time, same_day, late = [], [], []
        latest = {}
        for line in Model.search(models_printed_domain_of(models_window) + [('date_delivery', '!=', False)],
                                 order='id'):
            latest[line.scan_line_id.id] = line
        for patient_id, line in latest.items():
            done_on = line.date_result or line.date_end
            if not done_on:
                continue
            day = local_day(done_on)
            (on_time if day < line.date_delivery else same_day if day == line.date_delivery else late).append(patient_id)
        Line = self.env['lab.scan.line']
        # at risk is a computed reading of the delivery date, not a column: read on the rows
        at_risk = Line.search(base + [('print_state', 'in', ('to_plate', 'plated', 'printing')),
                                      ('date_delivery', '!=', False)]).filtered('delivery_at_risk')
        with_due = len(on_time) + len(same_day) + len(late)
        return {
            'matrix': matrix,
            'by_attempt': by_attempt,
            'by_batch': by_batch,
            'repeat': repeat,
            'rework': {'hours': hours, 'resin_ml': resin, 'hours_prev': hours_prev, 'resin_ml_prev': resin_prev,
                       'share_pct': round(hours * 100.0 / printing) if printing else None,
                       'replate_plates': len(replate_lines.order_id), 'replate_models': sum(replate_lines.mapped('model_count')),
                       'domain': models_failed_domain, 'model': MODEL,
                       'plates_domain': plates_domain + [('failed_models', '>', 0)], 'plates_model': ORDER},
            'on_time': {'with_due': with_due, 'on_time': len(on_time), 'same_day': len(same_day), 'late': len(late),
                        'pct': round(len(on_time) * 100.0 / with_due) if with_due else None,
                        'at_risk_now': len(at_risk), 'model': Line._name,
                        'on_time_domain': [('id', 'in', on_time)], 'same_day_domain': [('id', 'in', same_day)],
                        'late_domain': [('id', 'in', late)], 'at_risk_domain': [('id', 'in', at_risk.ids)]},
        }

    @api.model
    def _dashboard_print_trend(self, base, models_window, verdict, start, end, period):
        """Models printed and failed, bucket by bucket - the hours of today, the weeks of
        the quarter, the days otherwise - each bucket opening its own lines."""
        Model = self.env['lab.scan.print.order.line'].with_context(tz=self._lab_tz().zone)
        granularity = {'quarter': 'week', 'today': 'hour'}.get(period, 'day')
        sums = {}
        for bucket, ok, lost, rows in Model._read_group(models_window + verdict, ['date_start:%s' % granularity],
                                                        ['printed_models:sum', 'failed_models:sum', '__count']):
            if not bucket:
                continue
            key = bucket.hour if granularity == 'hour' else (bucket.date() if isinstance(bucket, datetime) else bucket)
            sums[key] = (ok or 0, lost or 0, rows)
        out = []

        def point(key, label, a, b):
            ok, lost, rows = sums.get(key, (0, 0, 0))
            out.append({'key': str(key), 'label': label, 'ok': ok, 'failed': lost, 'rows': rows,
                        'rate': round(lost * 100.0 / (ok + lost)) if ok + lost else None,
                        'domain': models_window + verdict + [('date_start', '>=', self._utc(a)),
                                                             ('date_start', '<', self._utc(b))]})
        if granularity == 'hour':
            hours = list(sums)
            for hour in range(min([LAB_HOURS[0]] + hours), max([LAB_HOURS[1]] + [h + 1 for h in hours])):
                a = datetime.combine(start, time(hour))
                point(hour, '%02d' % hour, a, a + timedelta(hours=1))
            return out
        day = start - timedelta(days=start.weekday()) if granularity == 'week' else start
        step = timedelta(days=7 if granularity == 'week' else 1)
        while day < end:
            point(day, day.strftime('%d %b') if granularity == 'week' else day.strftime('%d'),
                  max(day, start), min(day + step, end))
            day += step
        return out

    @api.model
    def _lane(self, printer, busy, window_hours):
        return {'id': printer.id, 'code': printer.code, 'name': printer.name, 'color': printer.color or 0,
                'state': printer.state, 'busy_hours': round(busy.get(printer.id, 0.0), 1),
                'utilisation': round(busy.get(printer.id, 0.0) * 100 / window_hours) if window_hours > 0 else None}
