# -*- encoding: utf-8 -*-
##############################################################################
#
# ERP Heritage
# Copyright (C) 2026 (https://www.erpheritage.com.au/)
#
##############################################################################
"""Regression tests for the security / integrity hardening pass.

* Schedule record rules: only the creator (or a manager) may redirect a
  schedule's recipients / webhook, run or pause it.
* Saved view record rules: shared views are readable, but only their owner
  (or a manager) may edit or delete them.
* Cron isolation: each schedule runs in its own savepoint, so a database
  error in one delivery neither rolls back nor aborts the others.
* A mail.mail that ends in 'exception' is a failed channel, not a success.
* next_run advances from the previous slot (no creep) and catches up.
* Relative date tokens in stored options resolve before rendering.
* Webhook POST never follows a redirect; its download attachments are
  creator-only and expire.
* Forecast periods follow the baseline month by month; percentages are
  not scaled by growth.
* Builder publish/duplicate never trip the unique code constraint; the
  handler refuses archived / foreign-company builders and sees
  company-less ones.

Network is never touched: urllib's opener is built with stub handlers.
"""

import base64
import calendar
import email.message
import io
import json
import urllib.error
import urllib.request
import urllib.response
from datetime import date, datetime, timedelta
from unittest.mock import patch

from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged
from odoo.tools import mute_logger

from odoo.addons.eh_account_base.tests.common import EhAccountIntegrationTestCase
from odoo.addons.eh_account_dynamic_reports_pro.models.date_tokens import (
    resolve_relative_dates,
)
from odoo.addons.eh_account_dynamic_reports_pro.models.report_builder import (
    BUILDER_HANDLER_MODEL,
)
from odoo.addons.eh_account_dynamic_reports_pro.models.report_schedule import (
    NoRedirectHandler,
    WEBHOOK_ATTACHMENT_RETENTION_DAYS,
    WEBHOOK_ATTACHMENT_TAG,
)

PUBLIC_HOOK_URL = 'https://93.184.216.34/hook'  # public literal: no DNS
XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


def _stub_response(url, code, msg, location=None, body=b''):
    headers = email.message.Message()
    if location:
        headers['Location'] = location
    resp = urllib.response.addinfourl(io.BytesIO(body), headers, url, code)
    resp.msg = msg
    return resp


def _stub_opener_factory(opened, https_code=200, https_location=None):
    """build_opener replacement whose HTTP(S) handlers never hit the network."""
    real_build_opener = urllib.request.build_opener

    class StubHTTPSHandler(urllib.request.HTTPSHandler):
        def https_open(self, req):
            opened.append(req.full_url)
            return _stub_response(
                req.full_url, https_code,
                'Found' if https_location else 'OK',
                location=https_location, body=b'ok')

    class StubHTTPHandler(urllib.request.HTTPHandler):
        def http_open(self, req):
            opened.append(req.full_url)
            return _stub_response(req.full_url, 200, 'OK', body=b'secret')

    def build_opener(*handlers):
        return real_build_opener(*handlers, StubHTTPSHandler, StubHTTPHandler)

    return build_opener


class HardeningCase(EhAccountIntegrationTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        DynRep = cls.env['eh.account.dynamic.report']
        cls.report = DynRep.search([('code', '=', 'trial_balance')], limit=1)
        if not cls.report:
            cls.report = DynRep.create({
                'code': 'trial_balance',
                'name': 'Trial Balance',
                'handler_model':
                    'eh.account.dynamic.report.handler.trial_balance',
            })
        cls.Schedule = cls.env['eh.report.schedule']
        cls.SavedView = cls.env['eh.report.saved.view']

        def _mk(login, group_xmlids):
            return cls.env['res.users'].create({
                'name': login,
                'login': login,
                'email': '%s@example.com' % login,
                'company_id': cls.company.id,
                'group_ids': [(6, 0, [
                    cls.env.ref(x).id for x in group_xmlids])],
            })

        cls.manager = _mk('eh_hard_mgr', [
            'base.group_user', 'eh_account_base.group_eh_manager'])
        cls.plain = _mk('eh_hard_plain', [
            'base.group_user', 'eh_account_base.group_eh_user'])
        cls.other = _mk('eh_hard_other', [
            'base.group_user', 'eh_account_base.group_eh_user'])

    def _schedule_vals(self, **overrides):
        vals = {
            'name': 'Hardening TB',
            'report_id': self.report.id,
            'options_json': json.dumps({
                'date': {'date_from': '2026-01-01', 'date_to': '2026-12-31'},
                'company_ids': [self.company.id],
            }),
            'interval': 1,
            'interval_unit': 'month',
            'next_run': fields.Datetime.now() - timedelta(minutes=1),
            'subject': 'Hardening',
            'recipient_emails': 'owner@example.com',
            'delivery_format': 'xlsx',
        }
        vals.update(overrides)
        return vals

    def _stub_attachments(self):
        self.patch(
            type(self.Schedule), '_build_attachments',
            lambda rec, options: [{
                'name': 'tb.xlsx',
                'datas': base64.b64encode(b'PK'),
                'mimetype': XLSX_MIME,
            }],
        )


@tagged('eh_account_dynamic_reports_pro', 'integration', 'post_install', '-at_install')
class TestScheduleAccessRules(HardeningCase):

    def test_other_user_cannot_redirect_schedule(self):
        schedule = self.Schedule.with_user(self.plain).create(
            self._schedule_vals())
        with self.assertRaises(AccessError):
            schedule.with_user(self.other).write(
                {'recipient_emails': 'attacker@example.com'})
        with self.assertRaises(AccessError):
            schedule.with_user(self.other).write(
                {'delivery_channel': 'webhook',
                 'webhook_url': 'https://attacker.example.com/hook'})
        schedule.invalidate_recordset()
        self.assertEqual(schedule.recipient_emails, 'owner@example.com')
        self.assertFalse(schedule.webhook_url)

    def test_other_user_cannot_run_or_pause_schedule(self):
        schedule = self.Schedule.with_user(self.plain).create(
            self._schedule_vals())
        with self.assertRaises(AccessError):
            schedule.with_user(self.other).action_run_now()
        with self.assertRaises(AccessError):
            schedule.with_user(self.other).action_pause()

    def test_other_user_can_still_read_schedule(self):
        schedule = self.Schedule.with_user(self.plain).create(
            self._schedule_vals())
        found = self.Schedule.with_user(self.other).search(
            [('id', '=', schedule.id)])
        self.assertEqual(found, schedule)

    def test_creator_and_manager_may_edit(self):
        schedule = self.Schedule.with_user(self.plain).create(
            self._schedule_vals())
        schedule.with_user(self.plain).write(
            {'recipient_emails': 'team@example.com'})
        schedule.with_user(self.manager).write(
            {'recipient_emails': 'finance@example.com'})
        schedule.invalidate_recordset()
        self.assertEqual(schedule.recipient_emails, 'finance@example.com')


@tagged('eh_account_dynamic_reports_pro', 'integration', 'post_install', '-at_install')
class TestSavedViewAccessRules(HardeningCase):

    def setUp(self):
        super().setUp()
        self.options = {
            'date': {'date_from': '2026-01-01', 'date_to': '2026-12-31'},
            'company_ids': [self.company.id],
        }

    def test_shared_view_readable_but_not_editable_by_others(self):
        shared = self.SavedView.with_user(self.plain).save_view(
            self.report.id, "Team baseline", self.options, is_shared=True)
        as_other = self.SavedView.with_user(self.other)
        self.assertEqual(as_other.search([('id', '=', shared.id)]), shared)
        with self.assertRaises(AccessError):
            shared.with_user(self.other).write({'name': 'Hijacked'})
        with self.assertRaises(AccessError):
            shared.with_user(self.other).unlink()
        shared.invalidate_recordset()
        self.assertTrue(shared.exists())
        self.assertEqual(shared.name, "Team baseline")

    def test_private_view_invisible_to_others(self):
        private = self.SavedView.with_user(self.plain).save_view(
            self.report.id, "Mine only", self.options, is_shared=False)
        found = self.SavedView.with_user(self.other).search(
            [('id', '=', private.id)])
        self.assertFalse(found)

    def test_cannot_create_view_owned_by_someone_else(self):
        with self.assertRaises(AccessError):
            self.SavedView.with_user(self.other).create({
                'name': 'Planted',
                'report_id': self.report.id,
                'user_id': self.plain.id,
                'options_json': json.dumps(self.options),
            })

    def test_owner_and_manager_may_edit(self):
        shared = self.SavedView.with_user(self.plain).save_view(
            self.report.id, "Editable", self.options, is_shared=True)
        shared.with_user(self.plain).write({'notes': 'owner note'})
        shared.with_user(self.manager).write({'notes': 'manager note'})
        shared.invalidate_recordset()
        self.assertEqual(shared.notes, 'manager note')

    def test_toggle_pin_on_others_shared_view_is_ignored(self):
        shared = self.SavedView.with_user(self.plain).save_view(
            self.report.id, "Pin target", self.options, is_shared=True)
        shared.with_user(self.other).action_toggle_pinned()
        shared.invalidate_recordset()
        self.assertFalse(shared.pinned)
        shared.with_user(self.plain).action_toggle_pinned()
        shared.invalidate_recordset()
        self.assertTrue(shared.pinned)


@tagged('eh_account_dynamic_reports_pro', 'integration', 'post_install', '-at_install')
class TestScheduleDelivery(HardeningCase):

    # ---- cron savepoints ----

    def test_cron_savepoint_isolates_database_error(self):
        now = fields.Datetime.now()
        bad = self.Schedule.create(self._schedule_vals(
            name='Bad', subject='Original subject',
            next_run=now - timedelta(hours=2)))
        good = self.Schedule.create(self._schedule_vals(
            name='Good', next_run=now - timedelta(hours=1)))

        def fake_send(rec):
            if rec.name == 'Bad':
                rec.write({'subject': 'mutated before failure'})
                rec.flush_recordset()
                # Aborts the transaction unless a savepoint contains it.
                rec.env.cr.execute("SELECT 1/0")
            rec.write({
                'last_run': fields.Datetime.now(),
                'last_run_status': 'success',
            })
            return True

        self.patch(type(self.Schedule), '_send_now', fake_send)
        # The deliberate division by zero is logged by sql_db at ERROR level.
        with mute_logger('odoo.sql_db'):
            self.Schedule._cron_run_due()
        bad.invalidate_recordset()
        good.invalidate_recordset()
        self.assertEqual(bad.last_run_status, 'error')
        self.assertIn('division by zero', (bad.last_error or '').lower())
        self.assertEqual(bad.subject, 'Original subject')
        self.assertGreater(bad.next_run, fields.Datetime.now())
        self.assertEqual(good.last_run_status, 'success')
        self.assertGreater(good.next_run, fields.Datetime.now())

    # ---- mail outcome ----

    def test_mail_exception_state_is_a_failed_channel(self):
        schedule = self.Schedule.create(self._schedule_vals())
        self._stub_attachments()

        def fake_send(mails, auto_commit=False, raise_exception=False,
                      post_send_callback=None):
            mails.write({
                'state': 'exception',
                'failure_reason': 'SMTP 550 relay denied',
            })
            return True

        self.patch(type(self.env['mail.mail']), 'send', fake_send)
        with self.assertRaises(UserError) as caught:
            schedule._send_now()
        self.assertIn('SMTP 550 relay denied', str(caught.exception))

    def test_mail_sent_state_is_success(self):
        schedule = self.Schedule.create(self._schedule_vals())
        self._stub_attachments()

        def fake_send(mails, auto_commit=False, raise_exception=False,
                      post_send_callback=None):
            mails.write({'state': 'sent'})
            return True

        self.patch(type(self.env['mail.mail']), 'send', fake_send)
        schedule._send_now()
        self.assertEqual(schedule.last_run_status, 'success')

    # ---- next_run cadence ----

    def test_next_run_does_not_creep_with_run_duration(self):
        anchor = fields.Datetime.now() - timedelta(minutes=30)
        schedule = self.Schedule.create(self._schedule_vals(
            interval=1, interval_unit='day', next_run=anchor))
        # The delivery finished well after the slot it was due in.
        schedule.last_run = fields.Datetime.now()
        schedule._advance_next_run()
        self.assertEqual(schedule.next_run, anchor + timedelta(days=1))

    def test_next_run_catches_up_missed_slots(self):
        anchor = fields.Datetime.now() - timedelta(days=10)
        schedule = self.Schedule.create(self._schedule_vals(
            interval=3, interval_unit='day', next_run=anchor))
        schedule._advance_next_run()
        # 3, 6 and 9 days are still in the past; 12 is the next future slot.
        self.assertEqual(schedule.next_run, anchor + timedelta(days=12))

    def test_next_run_month_end_anchor_does_not_decay(self):
        now = fields.Datetime.now()
        anchor = datetime(now.year - 1, 1, 31, 12, 0, 0)
        schedule = self.Schedule.create(self._schedule_vals(
            interval=1, interval_unit='month', next_run=anchor))
        schedule._advance_next_run()
        result = schedule.next_run
        months = (result.year - anchor.year) * 12 + result.month - anchor.month
        self.assertEqual(result, anchor + relativedelta(months=months))
        self.assertLessEqual(anchor + relativedelta(months=months - 1), now)
        self.assertGreater(result, now)
        self.assertEqual(
            result.day, calendar.monthrange(result.year, result.month)[1])

    def test_future_next_run_left_unchanged(self):
        future = fields.Datetime.now() + timedelta(days=2)
        schedule = self.Schedule.create(self._schedule_vals(next_run=future))
        schedule._advance_next_run()
        self.assertEqual(schedule.next_run, future)


@tagged('eh_account_dynamic_reports_pro', 'integration', 'post_install', '-at_install')
class TestRelativeDateTokens(HardeningCase):

    def test_resolver_period_tokens(self):
        today = date(2026, 5, 20)
        options = {
            'date': {'mode': 'range', 'date_from': 'auto_qtd',
                     'date_to': 'today'},
            'comparison': {'date_from': 'prev_quarter_start',
                           'date_to': 'AUTO_PREV_QUARTER_END'},
            'posted_only': True,
            'comparative': 'prior_year',
            'label': 'today',
        }
        resolved = resolve_relative_dates(options, today)
        self.assertEqual(resolved['date']['date_from'], '2026-04-01')
        self.assertEqual(resolved['date']['date_to'], '2026-05-20')
        self.assertEqual(resolved['date']['mode'], 'range')
        self.assertEqual(resolved['comparison']['date_from'], '2026-01-01')
        self.assertEqual(resolved['comparison']['date_to'], '2026-03-31')
        # Non date keys are never rewritten; the input is not mutated.
        self.assertEqual(resolved['label'], 'today')
        self.assertEqual(resolved['comparative'], 'prior_year')
        self.assertEqual(options['date']['date_from'], 'auto_qtd')

    def test_resolver_month_year_and_passthrough(self):
        today = date(2026, 5, 20)

        def one(value, key='date_from'):
            return resolve_relative_dates(
                {'date': {key: value}}, today)['date'][key]

        self.assertEqual(one('auto_prev_month_start'), '2026-04-01')
        self.assertEqual(one('auto_prev_month_end', 'date_to'), '2026-04-30')
        self.assertEqual(one('month_start'), '2026-05-01')
        self.assertEqual(one('month_end', 'date_to'), '2026-05-31')
        self.assertEqual(one('yesterday', 'date_to'), '2026-05-19')
        self.assertEqual(one('year_start'), '2026-01-01')
        self.assertEqual(one('prev_year_end', 'date_to'), '2025-12-31')
        self.assertEqual(one('mtd'), '2026-05-01')
        self.assertEqual(one('mtd', 'date_to'), '2026-05-20')
        self.assertEqual(one('auto_ytd'), '2026-01-01')
        self.assertEqual(one('2026-02-03'), '2026-02-03')
        self.assertEqual(one('someday'), 'someday')

    def test_ytd_follows_company_fiscal_year(self):
        self.company.sudo().write({
            'fiscalyear_last_month': '6',
            'fiscalyear_last_day': 30,
        })
        resolved = resolve_relative_dates(
            {'date': {'date_from': 'auto_ytd', 'date_to': 'today'}},
            date(2026, 5, 20), self.company)
        self.assertEqual(resolved['date']['date_from'], '2025-07-01')
        self.assertEqual(resolved['date']['date_to'], '2026-05-20')

    def test_schedule_resolves_tokens_and_renders(self):
        schedule = self.Schedule.create(self._schedule_vals(
            options_json=json.dumps({
                'date': {'mode': 'range',
                         'date_from': 'auto_prev_month_start',
                         'date_to': 'auto_prev_month_end'},
                'company_ids': [self.company.id],
                'posted_only': True,
            }),
        ))
        today = fields.Date.context_today(
            schedule.with_user(schedule.create_uid))
        month_start = today.replace(day=1)
        options = schedule._parse_options()
        self.assertEqual(
            options['date']['date_from'],
            (month_start - relativedelta(months=1)).isoformat())
        self.assertEqual(
            options['date']['date_to'],
            (month_start - timedelta(days=1)).isoformat())
        # The render used to die in fields.Date.from_string('auto_...').
        schedule._send_now()
        self.assertEqual(schedule.last_run_status, 'success')

    def test_saved_view_load_resolves_tokens(self):
        view = self.SavedView.save_view(
            self.report.id, "Rolling YTD",
            {'date': {'date_from': 'auto_ytd', 'date_to': 'today'},
             'posted_only': True})
        loaded = view.load_view()
        self.assertEqual(
            loaded['date']['date_to'],
            fields.Date.context_today(view).isoformat())
        # date_from is now a real ISO date the handlers can parse.
        self.assertTrue(fields.Date.from_string(loaded['date']['date_from']))
        self.assertTrue(loaded['posted_only'])


@tagged('eh_account_dynamic_reports_pro', 'integration', 'post_install', '-at_install')
class TestWebhookHardening(HardeningCase):

    def test_redirect_handler_refuses_every_redirect(self):
        req = urllib.request.Request(PUBLIC_HOOK_URL, data=b'{}', method='POST')
        with self.assertRaises(urllib.error.HTTPError):
            NoRedirectHandler().redirect_request(
                req, None, 302, 'Found', {},
                'http://169.254.169.254/latest/meta-data/')

    def test_dispatch_does_not_follow_redirect_to_internal_host(self):
        schedule = self.Schedule.create(self._schedule_vals(
            delivery_channel='webhook', webhook_format='generic',
            webhook_url=PUBLIC_HOOK_URL))
        opened = []
        factory = _stub_opener_factory(
            opened, https_code=302,
            https_location='http://169.254.169.254/latest/meta-data/')
        with patch('urllib.request.build_opener', side_effect=factory):
            with self.assertRaises(UserError) as caught:
                schedule._dispatch_webhook([{
                    'name': 'tb.xlsx', 'datas': base64.b64encode(b'PK'),
                    'mimetype': XLSX_MIME,
                }], '<p>x</p>')
        self.assertEqual(opened, [PUBLIC_HOOK_URL])
        self.assertIn('302', str(caught.exception))

    def test_webhook_attachment_is_creator_only_and_expires(self):
        schedule = self.Schedule.with_user(self.plain).create(
            self._schedule_vals(
                delivery_channel='webhook', webhook_format='generic',
                webhook_url=PUBLIC_HOOK_URL)).sudo()
        attachments = [{
            'name': 'tb.xlsx', 'datas': base64.b64encode(b'PK'),
            'mimetype': XLSX_MIME,
        }]
        opened = []
        with patch('urllib.request.build_opener',
                   side_effect=_stub_opener_factory(opened)):
            schedule._dispatch_webhook(attachments, '<p>x</p>')
        self.assertEqual(opened, [PUBLIC_HOOK_URL])

        Attachment = self.env['ir.attachment'].sudo()
        tag = '%s:%d' % (WEBHOOK_ATTACHMENT_TAG, schedule.id)
        old = Attachment.search([('description', '=', tag)])
        self.assertEqual(len(old), 1)
        self.assertFalse(old.res_model)
        self.assertFalse(old.res_id)
        self.assertEqual(old.create_uid, self.plain)
        old.with_user(self.plain).check_access('read')
        with self.assertRaises(AccessError):
            old.with_user(self.other).check_access('read')

        # Age the first attachment past retention, dispatch again, collect.
        self.env.cr.execute(
            "UPDATE ir_attachment SET create_date = %s WHERE id = %s",
            [fields.Datetime.now()
             - timedelta(days=WEBHOOK_ATTACHMENT_RETENTION_DAYS + 1), old.id])
        old.invalidate_recordset()
        with patch('urllib.request.build_opener',
                   side_effect=_stub_opener_factory(opened)):
            schedule._dispatch_webhook(attachments, '<p>x</p>')
        self.Schedule._gc_webhook_attachments()
        self.assertFalse(old.exists())
        self.assertEqual(len(Attachment.search([('description', '=', tag)])), 1)


@tagged('eh_account_dynamic_reports_pro', 'integration', 'post_install', '-at_install')
class TestForecastFixes(HardeningCase):

    def _forecast(self, **overrides):
        DynRep = self.env['eh.account.dynamic.report']
        report = DynRep.search([('code', '=', 'profit_and_loss')], limit=1) \
            or self.report
        vals = {
            'name': 'Hardening forecast',
            'base_report_id': report.id,
            'base_date_from': date(2026, 1, 1),
            'base_date_to': date(2026, 1, 31),
            'horizon_months': 3,
            'growth_method': 'linear',
            'monthly_growth_pct': 10.0,
        }
        vals.update(overrides)
        return self.env['eh.report.forecast'].create(vals)

    def test_periods_are_whole_months_after_baseline(self):
        periods = self._forecast()._project_periods({'lines': [], 'totals': {}})
        self.assertEqual(
            [(p['date_from'], p['date_to'], p['period_label']) for p in periods],
            [('2026-02-01', '2026-02-28', '2026-02'),
             ('2026-03-01', '2026-03-31', '2026-03'),
             ('2026-04-01', '2026-04-30', '2026-04')],
        )

    def test_percentage_cells_and_totals_not_scaled(self):
        baseline = {
            'columns': [
                {'expression_label': 'account', 'figure_type': 'string'},
                {'expression_label': 'amount', 'figure_type': 'monetary'},
                {'expression_label': 'variance_pct',
                 'figure_type': 'percentage'},
            ],
            'lines': [{
                'name': 'Revenue',
                'columns': [
                    {'expression_label': 'amount', 'value': 100.0},
                    {'expression_label': 'variance_pct', 'value': 0.05},
                ],
            }],
            'totals': {'amount': 100.0, 'variance_pct': 0.05,
                       'margin_pct': 0.2},
        }
        out = self.env['eh.report.forecast']._apply_factor(baseline, 1.1)
        cells = {c['expression_label']: c['value']
                 for c in out['lines'][0]['columns']}
        self.assertAlmostEqual(cells['amount'], 110.0, places=2)
        self.assertEqual(cells['variance_pct'], 0.05)
        self.assertAlmostEqual(out['totals']['amount'], 110.0, places=2)
        self.assertEqual(out['totals']['variance_pct'], 0.05)
        self.assertEqual(out['totals']['margin_pct'], 0.2)


@tagged('eh_account_dynamic_reports_pro', 'integration', 'post_install', '-at_install')
class TestBuilderFixes(HardeningCase):

    def _builder(self, code, **vals):
        return self.env['eh.report.builder'].create(dict(
            {'code': code, 'name': code.replace('_', ' ').title()}, **vals))

    def _handler(self, code):
        return self.env['eh.account.dynamic.report.handler.builder'] \
            .with_context(eh_report_code=code)

    def test_publish_reuses_orphaned_builder_report(self):
        DynRep = self.env['eh.account.dynamic.report'].with_context(
            active_test=False)
        orphan = DynRep.create({
            'code': 'orphan_pub', 'name': 'Orphan',
            'handler_model': BUILDER_HANDLER_MODEL, 'active': False,
        })
        builder = self._builder('orphan_pub')
        builder.action_publish()
        self.assertEqual(builder.published_report_id, orphan)
        self.assertTrue(orphan.active)
        self.assertEqual(DynRep.search_count([('code', '=', 'orphan_pub')]), 1)
        # Publishing again stays on the same record.
        builder.action_publish()
        self.assertEqual(builder.published_report_id, orphan)

    def test_publish_refuses_code_of_standard_report(self):
        std = self.env['eh.account.dynamic.report'].create({
            'code': 'std_clash', 'name': 'Standard',
            'handler_model':
                'eh.account.dynamic.report.handler.trial_balance',
        })
        builder = self._builder('std_clash')
        with self.assertRaises(UserError):
            builder.action_publish()
        self.assertEqual(
            std.handler_model,
            'eh.account.dynamic.report.handler.trial_balance')

    def test_duplicate_twice_generates_unique_codes(self):
        builder = self._builder('dup_src')
        builder.action_duplicate()
        builder.action_duplicate()
        codes = self.env['eh.report.builder'].search(
            [('code', '=like', 'dup_src_copy%')]).mapped('code')
        self.assertEqual(sorted(codes), ['dup_src_copy', 'dup_src_copy_2'])

    def test_archived_builder_is_unpublished_and_refused(self):
        builder = self._builder('arch_pub')
        builder.action_publish()
        report = builder.published_report_id
        builder.active = False
        self.assertFalse(report.active)
        self.assertFalse(builder.is_published)
        with self.assertRaises(UserError) as caught:
            self._handler('arch_pub')._resolve_builder([self.company.id])
        self.assertIn('archived', str(caught.exception))

    def test_company_less_builder_resolves_for_plain_user(self):
        builder = self._builder('shared_def', company_id=False)
        found = self._handler('shared_def').with_user(
            self.plain)._resolve_builder([self.company.id])
        self.assertEqual(found.id, builder.id)

    def test_builder_of_other_company_refused(self):
        other_company = self.env['res.company'].create(
            {'name': 'EH Hardening Other Co'})
        self._builder('foreign_def', company_id=other_company.id)
        with self.assertRaises(UserError):
            self._handler('foreign_def')._resolve_builder([self.company.id])
