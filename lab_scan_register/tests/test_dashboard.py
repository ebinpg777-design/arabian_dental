# -*- coding: utf-8 -*-
"""The dashboard's figures, on a company of its own so every number is exact."""
from datetime import datetime, timedelta
from unittest.mock import patch

import pytz

from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from .common import PrintingSteps
from .test_slice_file import ctb


@tagged('post_install', '-at_install')
class TestDashboard(PrintingSteps, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env['res.company'].create({'name': 'Scan Dashboard Lab'})
        cls.env.user.write({'company_ids': [(4, cls.company.id)], 'tz': 'Asia/Kolkata'})
        cls.env = cls.env(context=dict(cls.env.context, allowed_company_ids=[cls.company.id]))
        cls.Scan = cls.env['lab.scan']
        cls.clinic = cls.env['res.partner'].create({'name': 'Busy Clinic', 'is_company': True})
        cls.quiet = cls.env['res.partner'].create({'name': 'Quiet Clinic', 'is_company': True})
        Channel = cls.env['lab.scan.channel']
        cls.group = Channel.create({'name': 'Dash Group', 'kind': 'whatsapp', 'company_id': cls.company.id})
        cls.mailbox = Channel.create({'name': 'Dash Mail', 'kind': 'email', 'company_id': cls.company.id})
        cls.product = cls.env['product.product'].create({'name': 'Dash Appliance', 'type': 'consu', 'sale_ok': True})
        cls.printer = cls.env['lab.scan.printer'].create({'name': 'Dash Printer', 'company_id': cls.company.id})

    def _scan(self, channel, partner, patients, received=None, **vals):
        scan = self.Scan.create(dict({
            'channel_id': channel.id, 'partner_id': partner.id if partner else False,
            'company_id': self.company.id,
            'date_received': received or fields.Datetime.now(),
            'line_ids': [(0, 0, {'patient_name': p, 'product_id': self.product.id}) for p in patients],
        }, **vals))
        return scan

    def _dash(self, period='month'):
        return self.Scan.get_dashboard(period)

    def test_what_came_in_counts_registers_and_patients_by_source(self):
        self._scan(self.group, self.clinic, ['A', 'B'])
        self._scan(self.group, self.clinic, ['C'])
        self._scan(self.mailbox, self.quiet, ['D'])
        cancelled = self._scan(self.mailbox, self.quiet, ['E'])
        cancelled.action_cancel()
        d = self._dash()
        self.assertEqual(d['headline']['received'], 3, "a cancelled register is not counted")
        self.assertEqual(d['headline']['patients'], 4)
        sources = {s['key']: (s['count'], s['patients']) for s in d['sources']}
        self.assertEqual(sources, {'whatsapp': (2, 3), 'email': (1, 1), 'transfer': (0, 0)})
        self.assertEqual(sum(p['total'] for p in d['trend']), 3, "the trend adds up to the headline")

    def test_the_pipeline_is_where_everything_open_stands_now(self):
        self._scan(self.group, self.clinic, ['A'])
        confirmed = self._scan(self.group, self.clinic, ['B', 'C'])
        confirmed.action_confirm()
        printing = self._scan(self.group, self.clinic, ['D'], download_url='https://x')
        printing.action_confirm()
        printing._send_to_print()
        pipe = {p['state']: (p['count'], p['patients']) for p in self._dash()['pipeline']}
        self.assertEqual(pipe, {'draft': (1, 1), 'confirmed': (1, 2), 'printing': (1, 1), 'in_progress': (0, 0),
                                'printed': (0, 0)})
        self.assertEqual(self._dash()['headline']['open'], 3)

    def test_turnaround_and_the_slowest_step(self):
        scan = self._scan(self.group, self.clinic, ['A'], download_url='https://x')
        scan.action_confirm()
        order = self._start(self._plate(scan.line_ids))
        self._off(order)
        order.action_cure()
        scan.action_create_orders()
        t0 = fields.Datetime.now() - timedelta(hours=30)
        scan.with_context(scan_workflow=True).write({
            'date_received': t0, 'date_confirmed': t0 + timedelta(hours=2),
            'date_print_start': t0 + timedelta(hours=4), 'date_in_progress': t0 + timedelta(hours=5),
            'date_printed': t0 + timedelta(hours=24), 'date_ordered': t0 + timedelta(hours=26)})
        d = self._dash()
        stages = {s['key']: s['hours'] for s in d['stages']}
        self.assertEqual(stages, {'confirm': 2.0, 'to_print': 2.0, 'queue': 1.0, 'printing': 19.0, 'to_order': 2.0})
        self.assertTrue(next(s for s in d['stages'] if s['key'] == 'printing')['slowest'])
        self.assertEqual(d['headline']['turnaround_hours'], 26.0)
        self.assertEqual(d['headline']['conversion'], 100)

    def test_the_period_before_is_compared(self):
        """Like for like: this week so far against the same weekdays of last week."""
        self._scan(self.group, self.clinic, ['A'], received=fields.Datetime.now() - timedelta(days=7))
        self._scan(self.group, self.clinic, ['Too early'], received=fields.Datetime.now() - timedelta(days=15))
        self._scan(self.group, self.clinic, ['B'])
        self._scan(self.group, self.clinic, ['C'])
        d = self._dash('week')
        self.assertEqual(d['headline']['received_prev'], 1)
        self.assertEqual(d['headline']['received'], 2)

    def test_who_sends_the_most_is_ranked_by_patients(self):
        self._scan(self.group, self.quiet, ['A'])
        self._scan(self.group, self.clinic, ['B', 'C', 'D'])
        clinics = self._dash()['clinics']
        self.assertEqual([c['name'] for c in clinics], ['Busy Clinic', 'Quiet Clinic'])
        self.assertEqual(clinics[0]['patients'], 3)

    def test_attention_counts_open_work_that_needs_somebody(self):
        today = fields.Date.context_today(self.env.user)
        risky = self._scan(self.group, self.clinic, ['A'])
        risky.line_ids.date_delivery = today + timedelta(days=1)
        self._scan(self.mailbox, None, ['B'], download_url='https://wetransfer.com/x',
                   download_expiry=today + timedelta(days=1))
        att = {a['key']: a['count'] for a in self._dash()['attention']}
        self.assertEqual(att['at_risk'], 1)
        self.assertEqual(att['link'], 1)
        self.assertEqual(att['unmatched'], 1)
        self.assertEqual(att['no_ack'], 1, "the WhatsApp one has not been acknowledged")

    def _confirmed_patients(self, *names):
        """One confirmed register per patient, with a link to print from."""
        scans = self.Scan
        for name in names:
            scan = self._scan(self.group, self.clinic, [name], download_url='https://x')
            scan.action_confirm()
            scans |= scan
        return scans

    def test_the_machines_and_their_failures_are_on_the_board(self):
        """A plate that lost one model of three counts one plate, two models printed
        and one failed - on the machine it ran on, and on the board."""
        other = self.env['lab.scan.printer'].create({'name': 'Idle Printer', 'company_id': self.company.id})
        a1, a2, a3 = self._confirmed_patients('A1', 'A2', 'A3')
        plate = self._start(self._plate((a1 | a2 | a3).line_ids))
        self._off(plate, failed=a1.line_ids)
        running = self._start(self._plate(self._confirmed_patients('B').line_ids))
        d = self._dash()
        h = d['headline']
        self.assertEqual((h['plates'], h['models_printed'], h['models_failed'], h['model_failure_rate']),
                         (1, 2, 1, 33))
        self.assertEqual(h['avg_models_per_plate'], 6.0, "three patients, upper and lower each")
        printers = {p['id']: p for p in d['printers']}
        mine, idle = printers[self.printer.id], printers[other.id]
        self.assertEqual((mine['plates'], mine['models_done'], mine['models_failed'], mine['failure_rate']),
                         (1, 2, 1, 33))
        self.assertEqual((mine['running'], mine['running_order']['name']), (1, running.name))
        self.assertEqual((idle['plates'], idle['models_done'], idle['running'], idle['failure_rate']), (0, 0, 0, None))
        Order, Line = self.env[mine['model']], self.env[mine['models_model']]
        self.assertEqual(Order.search(mine['domain']), plate, "the plate behind the figures")
        self.assertEqual(Line.search_count(mine['models_domain']), 3)
        self.assertEqual(Line.search(d['models_failed_domain']).scan_id, a1)
        self.assertEqual(self.env[mine['running_model']].search(mine['running_domain']), running)
        att = {a['key']: a['count'] for a in d['attention']}
        self.assertEqual(att['failed'], 1, "A1's register is back in the queue")
        self.assertEqual(att['replate_waiting'], 1, "and its model is not on a plate yet")
        self.assertEqual(att['running_long'], 0)

    def test_the_machines_and_the_failed_prints(self):
        """A failed model re-plated on another machine; then that whole plate fails."""
        spare = self.env['lab.scan.printer'].create({'name': 'Spare', 'company_id': self.company.id})
        good, bad = self._confirmed_patients('Good', 'Bad')
        first = self._start(self._plate((good | bad).line_ids))
        replate = self._off(first, failed=bad.line_ids, replate=True, replate_mode='new')
        self._start(replate, printer=spare)          # the machine is picked at Start
        d = self._dash()
        self.assertEqual((d['headline']['models_printed'], d['headline']['models_failed'],
                          d['headline']['model_failure_rate']), (1, 1, 50))
        machines = {p['name']: p for p in d['printers']}
        self.assertEqual((machines['Dash Printer']['models_done'], machines['Dash Printer']['models_failed'],
                          machines['Dash Printer']['running']), (1, 1, 0))
        self.assertEqual((machines['Spare']['plates'], machines['Spare']['running']), (0, 1))
        att = {a['key']: a['count'] for a in d['attention']}
        self.assertEqual(att['failed'], 0, "re-plated and printing, so not waiting in the queue")
        self.assertEqual(att['replate_waiting'], 0)
        self._fail(replate, reason='power')
        d = self._dash()
        att = {a['key']: a['count'] for a in d['attention']}
        self.assertEqual(att['failed'], 1)
        self.assertEqual({p['name']: p['plates'] for p in d['printers']}['Spare'], 1, "a failed plate is a plate")
        self.assertEqual(d['headline']['models_failed'], 2)

    def test_every_plate_figure_and_attention_line_opens_exactly_its_rows(self):
        """Whatever the board counts, a click opens those rows and no others - on the
        model each figure is about."""
        spare = self.env['lab.scan.printer'].create({'name': 'Spare', 'company_id': self.company.id})
        a, b, c, e = self._confirmed_patients('A', 'B', 'C', 'E')
        self._off(self._start(self._plate((a | b).line_ids)), failed=b.line_ids)
        self._start(self._plate(c.line_ids, printer=spare))
        queued = self._plate(e.line_ids)
        queued._apply_slice_head(ctb(), 'E.ctb', 0)
        queued.with_context(print_workflow=True).write(
            {'date_sliced': fields.Datetime.now() - timedelta(hours=5)})
        d = self._dash()
        h = d['headline']
        checks = [(d['plates_model'], d['plates_domain'], h['plates']),
                  (d['models_printed_model'], d['models_printed_domain'], h['models_printed']),
                  (d['models_failed_model'], d['models_failed_domain'], h['models_failed'])]
        for pr in d['printers']:
            checks += [(pr['model'], pr['domain'], pr['plates']),
                       (pr['models_model'], pr['models_domain'], pr['models_done'] + pr['models_failed']),
                       (pr['running_model'], pr['running_domain'], pr['running']),
                       (pr['queue_model'], pr['queue_domain'], pr['queued'])]
        for item in d['attention']:
            checks.append((item['model'], item['domain'], item['count']))
        for model, domain, count in checks:
            with self.subTest(model=model, domain=domain):
                self.assertEqual(self.env[model].search_count(domain), count)
        att = {a['key']: a['count'] for a in d['attention']}
        self.assertEqual(att['sliced_not_started'], 1, "sliced five hours ago, still not on a machine")
        self.assertEqual(att['no_file'], 1, "the spare's plate started with no file read")

    def test_plate_stages_and_estimate_accuracy(self):
        """Plated an hour before it started, ran 2h30 against ChituBox's 2h, washed
        and cured in half an hour."""
        scan = self._confirmed_patients('A')
        plate = self._plate(scan.line_ids)
        plate._apply_slice_head(ctb(), 'A.ctb', 0)
        self.assertEqual(plate.estimated_hours, 2.0)
        self._off(self._start(plate))
        plate.action_cure()
        t0 = fields.Datetime.now() - timedelta(hours=5)
        plate.with_context(print_workflow=True).write({
            'date_start': t0, 'date_end': t0 + timedelta(hours=2.5),
            'date_cured': t0 + timedelta(hours=3)})
        self.env.cr.execute("UPDATE lab_scan_print_order SET create_date = %s WHERE id = %s",
                            (t0 - timedelta(hours=1), plate.id))
        plate.invalidate_recordset(['create_date'])
        d = self._dash()
        stages = {s['key']: s for s in d['plate_stages']}
        self.assertEqual((stages['plating']['hours'], stages['printing']['hours'], stages['washing']['hours']),
                         (1.0, 2.5, 0.5))
        self.assertEqual(stages['printing']['estimated'], 2.0)
        self.assertEqual(d['headline']['estimate_accuracy'], 125, "ran a quarter longer than the file said")
        self.assertEqual({p['id']: p for p in d['printers']}[self.printer.id]['estimate_accuracy'], 125)
        self.assertEqual(self.printer.estimate_accuracy, 125)

    def test_every_figure_opens_exactly_the_registers_behind_it(self):
        self._scan(self.group, self.clinic, ['A'])
        self._scan(self.mailbox, self.quiet, ['B'])
        d = self._dash()
        for src in d['sources']:
            self.assertEqual(self.Scan.search_count(src['domain']), src['count'], src['key'])
        for step in d['pipeline']:
            self.assertEqual(self.Scan.search_count(step['domain']), step['count'], step['state'])
        for point in d['trend']:
            self.assertEqual(self.Scan.search_count(point['domain']), point['total'], point['date'])
        self.assertEqual(self.Scan.search_count(d['window_domain']), d['headline']['received'])

    def test_a_scan_late_in_the_evening_counts_on_the_labs_day(self):
        """23:00 in the lab is 17:30 UTC the same day - and 00:30 the next lab day is
        19:00 UTC the day before. Days are the lab's, not UTC's."""
        tz_now = datetime.now()
        if tz_now.day == 1:
            self.skipTest("the first of the month has no earlier day in this month to test against")
        start = self.Scan._dashboard_window('month')[0]
        evening_utc = datetime.combine(start, datetime.min.time()) - timedelta(minutes=30)   # 23:30 UTC the day before
        self._scan(self.group, self.clinic, ['Late'], received=evening_utc)          # 05:00 local, on the 1st
        self.assertEqual(self._dash()['headline']['received'], 1)

    def test_the_hub_offers_it_last_with_a_live_figure(self):
        if 'lab.ceo.dashboard' not in self.env:
            self.skipTest("the Management hub is not installed")
        self._scan(self.group, self.clinic, ['A'])
        admin = self.env.ref('base.user_admin')
        # the lab's hub is for its executives, and the administrator is not one by itself
        admin.write({'company_ids': [(4, self.company.id)],
                     'group_ids': [(4, self.env.ref('lab_ceo_dashboard.group_lab_executive').id)]})
        rows = self.env['lab.ceo.dashboard'].with_user(admin).with_company(self.company).get_launchers()
        self.assertEqual(rows[-1]['key'], 'scans', "last, so no other shortcut moves")
        self.assertEqual(rows[-1]['value'], 1)
        self.assertEqual(rows[-1]['tag'], 'lab_scan_dashboard')

    # ------------------------------------------------------------------ today, and the printing room
    # (client, 2026-10-14: "improve the dashboard with print order and machine details;
    # a today filter in the period"). The day below is a fixed Wednesday on the lab's
    # clock, stopped at 15:00, so every minute is exact whenever the test runs.
    DAY = datetime(2026, 3, 11)

    def _at(self, hour, minute=0, days=0):
        """The lab's wall clock on the test day, as the naive UTC Odoo stores."""
        local = self.DAY.replace(hour=hour, minute=minute) + timedelta(days=days)
        return self.Scan._lab_tz().localize(local).astimezone(pytz.utc).replace(tzinfo=None)

    def _clock(self, hour=15, minute=0):
        return patch.object(fields.Datetime, 'now', return_value=self._at(hour, minute))

    def _unplanned(self, lines):
        """A plate made with no machine - the way New Print Order makes them."""
        wiz = self.env['lab.scan.print.order.make'].with_context(
            active_model='lab.scan.line', active_ids=lines.ids).create({})
        return self.env['lab.scan.print.order'].browse(wiz.action_create()['res_id'])

    def test_today_compares_with_yesterday_up_to_the_same_time(self):
        self._scan(self.group, self.clinic, ['Today'], received=self._at(10))
        self._scan(self.group, self.clinic, ['Yesterday morning'], received=self._at(10, days=-1))
        self._scan(self.group, self.clinic, ['Yesterday evening'], received=self._at(18, days=-1))
        with self._clock(15):
            d = self._dash('today')
        self.assertEqual((d['period'], d['is_today'], d['start'], d['end']), ('today', True, '2026-03-11', '2026-03-11'))
        self.assertEqual((d['headline']['received'], d['headline']['received_prev']), (1, 1),
                         "yesterday only up to 15:00: a morning is not measured against a whole day")
        self.assertEqual(d['trend_granularity'], 'hour')
        self.assertEqual(next(p for p in d['trend'] if p['label'] == '10')['total'], 1)
        self.assertEqual(sum(p['total'] for p in d['trend']), 1)
        for point in d['trend']:
            self.assertEqual(self.Scan.search_count(point['domain']), point['total'], point['date'])

    def test_the_timeline_carries_the_queue_onto_the_machines(self):
        """15:00. Dash Printer printed A 09:00-11:00 and has B on since 13:00 (3 h on the
        file, so off at 16:00), with C (2 h) planned after it. Spare is free. E is plated
        and ready with no machine (1 h); W is waiting for a plate."""
        spare = self.env['lab.scan.printer'].create({'name': 'Spare', 'company_id': self.company.id})
        with self._clock(15):
            a, b, c, e, w = self._confirmed_patients('A', 'B', 'C', 'E', 'W')
            done = self._start(self._plate(a.line_ids))
            self._off(done)
            done._move({'date_start': self._at(9), 'date_end': self._at(11)})
            running = self._plate(b.line_ids)
            running.estimated_hours = 3.0
            self._start(running)
            running._move({'date_start': self._at(13)})
            planned = self._plate(c.line_ids)
            planned.estimated_hours = 2.0
            ready = self._unplanned(e.line_ids)
            ready.estimated_hours = 1.0
            d = self._dash('today')
        tl = d['timeline']
        self.assertEqual((tl['mode'], tl['now']), ('hours', 15 * 60))
        lanes = {lane['id']: lane for lane in tl['lanes']}
        mine = {(s['kind'], s['a'], s['b']) for s in lanes[self.printer.id]['segments']}
        self.assertTrue({('done', 540, 660), ('printing', 780, 900), ('projected', 900, 960),
                         ('planned', 960, 1080)} <= mine, mine)
        theirs = [(s['kind'], s['a'], s['b'], s['order_id']) for s in lanes[spare.id]['segments']]
        self.assertEqual(theirs, [('suggested', 900, 960, ready.id), ('patients', 960, 1080, False)],
                         "the ready plate goes to the machine free first; W's plate follows at the room's pace (A's 2 h)")
        self.assertEqual((lanes[self.printer.id]['busy_hours'], lanes[self.printer.id]['utilisation']), (4.0, 27),
                         "A's two hours and B's two so far, of the fifteen the day has had")
        fc = d['forecast']
        self.assertEqual((fc['patients'], fc['models'], fc['plates_needed'], fc['plated'], fc['machines']), (1, 2, 1, 2, 2))
        self.assertEqual(fc['clears_at'], fields.Datetime.to_string(self._at(18)))
        self.assertEqual([n['patient'] for n in d['print_next']], ['W'])
        cards = {p['id']: p for p in d['printers']}
        self.assertEqual((cards[self.printer.id]['running_order']['name'], cards[self.printer.id]['running_order']['estimated_hours']),
                         (running.name, 3.0))
        self.assertEqual(cards[spare.id]['next_plate']['id'], ready.id, "a free machine is offered the ready plate")
        self.assertFalse(cards[spare.id]['next_plate']['planned'])
        self.assertEqual(d['headline']['machines_busy'], 1)

    def test_the_plates_flow_and_every_new_figure_opens_exactly_its_rows(self):
        with self._clock(15):
            a, b, c, e = self._confirmed_patients('A', 'B', 'C', 'E')
            self._off(self._start(self._plate((a | b).line_ids)), failed=[(b.line_ids, 'upper', 'supports')],
                      reason='adhesion')
            self._start(self._plate(c.line_ids))
            self._unplanned(e.line_ids)
            for period in ('today', 'month'):
                d = self._dash(period)
                flow = {f['key']: f for f in d['plate_flow']}
                self.assertEqual({k: f['count'] for k, f in flow.items()},
                                 {'draft': 1, 'printing': 1, 'washing': 1, 'printed': 1, 'failed': 0}, period)
                self.assertEqual((flow['draft']['unplanned'], flow['draft']['models']), (1, 2))
                checks = [(f['model'], f['domain'], f['count']) for f in d['plate_flow']]
                checks += [(d['models_failed_model'], r['domain'], r['count']) for r in d['failure_reasons']]
                checks += [(d['models_failed_model'], fp['domain'], fp['count']) for fp in d['failed_parts']]
                checks.append((d['forecast']['model'], d['forecast']['domain'], d['forecast']['patients']))
                for lane in d['timeline']['lanes']:
                    for cell in lane.get('cells', []):
                        checks.append(('lab.scan.print.order', cell['domain'], cell['plates']))
                for model, domain, count in checks:
                    with self.subTest(period=period, model=model, domain=domain):
                        self.assertEqual(self.env[model].search_count(domain), count)
                self.assertEqual([(r['key'], r['count']) for r in d['failure_reasons']], [('supports', 1)],
                                 "the row's own reason beats the plate's")
                self.assertEqual([(fp['key'], fp['count']) for fp in d['failed_parts']], [('upper', 1)])
            self.assertEqual(d['timeline']['mode'], 'days')
            today = next(c for c in d['timeline']['lanes'][0]['cells'] if c['date'] == '2026-03-11')
            self.assertGreaterEqual(today['plates'], 1)

    def test_the_latest_plates_put_the_room_s_work_first(self):
        with self._clock(15):
            a, b, c = self._confirmed_patients('A', 'B', 'C')
            done = self._start(self._plate(a.line_ids))
            self._off(done)
            done.action_cure()
            printing = self._start(self._plate(b.line_ids))
            plating = self._unplanned(c.line_ids)
            d = self._dash('today')
        self.assertEqual([p['id'] for p in d['recent_plates']], [printing.id, plating.id, done.id])
        first = d['recent_plates'][0]
        self.assertEqual((first['state'], first['printer']['id'], first['patients']), ('printing', self.printer.id, 1))
        self.assertIsNone(d['recent_plates'][1]['printer'], "plated, the machine is picked at Start")

