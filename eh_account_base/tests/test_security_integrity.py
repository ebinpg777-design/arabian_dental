# -*- encoding: utf-8 -*-
##############################################################################
#
# ERP Heritage
# Copyright (C) 2026 (https://www.erpheritage.com.au/)
#
##############################################################################
"""
Security and integrity regressions for the shared reporting engine.

Covers:

* Execution audit rows are engine-only: users cannot create or write them
  (not even with the old eh_internal_audit_write context flag), while the
  engine lifecycle still works for them.
* The execution company rule hides consolidated rows from a user who does
  not have every company of the row.
* XLSX export attachments are readable by their creator only.
* Shared saved views are editable by their owner (and managers) only.
* Partner / analytic edits on posted lines invalidate the cache, and a
  render that includes drafts is never served from cache.
* The cache key carries the language.
* Presentation currency is not applied twice, and is refused across
  companies with different currencies.
* Previous-period comparison shifts whole months by months.
* Cash-basis totals mirror the SQL path filters (open date bound, cancelled
  entries, empty account types, per-company account code).
* The analytic filter matches combined "a,b" distribution keys and a plan
  filter includes its sub-plans.
"""

import base64
from datetime import date
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from odoo.addons.eh_account_base.tools.sql_builder import MoveLineQuery
from .common import EhAccountIntegrationTestCase

_BASE_HANDLER = 'eh.account.dynamic.report.handler'
_SECTIONED_HANDLER = 'eh.account.dynamic.report.handler.sectioned'


def _monetary_payload(meta=None):
    payload = {
        'columns': [
            {'expression_label': 'value', 'name': "Value",
             'figure_type': 'monetary'},
        ],
        'lines': [
            {'id': 'line-1', 'name': "Line", 'level': 1,
             'columns': [{'expression_label': 'value', 'value': 50.0}]},
        ],
        'totals': {'value': 50.0},
    }
    if meta is not None:
        payload['meta'] = meta
    return payload


@tagged('eh_account_base', 'integration', 'post_install', '-at_install')
class TestEngineSecurityIntegrity(EhAccountIntegrationTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Execution = cls.env['eh.account.report.execution']
        cls.eh_user = cls._eh_make_user(
            'eh_sec_user', 'eh_account_base.group_eh_user')
        cls.eh_user_2 = cls._eh_make_user(
            'eh_sec_user_2', 'eh_account_base.group_eh_user')
        cls.eh_manager = cls._eh_make_user(
            'eh_sec_manager', 'eh_account_base.group_eh_manager')

    @classmethod
    def _eh_make_user(cls, login, group_xmlid):
        return cls.env['res.users'].create({
            'name': login,
            'login': login,
            'email': '%s@example.com' % login,
            'company_id': cls.company.id,
            'group_ids': [(6, 0, [
                cls.env.ref('base.group_user').id,
                cls.env.ref(group_xmlid).id,
            ])],
        })

    def _start(self, Execution, company_ids=None, options=None):
        return Execution.start_execution(
            report_code='eh_sec_tb',
            name='TB',
            options=options or {'date': '2026-05-31'},
            company_ids=company_ids or [self.company.id],
        )

    def _make_report(self, code):
        return self.env['eh.account.dynamic.report'].create({
            'code': code,
            'name': code,
            'handler_model': _BASE_HANDLER,
        })

    def _patch_compute(self, func):
        return patch.object(type(self.env[_BASE_HANDLER]), 'compute', func)

    def _version(self):
        self.company.invalidate_recordset(['eh_move_version'])
        return self.company.eh_move_version

    def _analytic_plan(self):
        # Reuse a root plan: creating a ROOT plan adds a column to the
        # journal-item table, which a test has no business doing.
        root = self.env['account.analytic.plan'].search(
            [('parent_id', '=', False)], limit=1)
        if not root:
            root = self.env['account.analytic.plan'].create(
                {'name': 'EH Sec Root Plan'})
        return root

    # ---- execution audit rows (B1) ----

    def test_user_cannot_create_execution_row(self):
        with self.assertRaises(AccessError):
            self.Execution.with_user(self.eh_user).create({
                'report_code': 'eh_sec_tb',
                'name': 'Planted',
                'company_ids': [(6, 0, self.company.ids)],
                'options_snapshot': '{}',
                'options_hash': 'f' * 64,
                'state': 'done',
                'move_version_at_start': self._version(),
            })

    def test_user_cannot_write_execution_even_with_engine_flag(self):
        execution = self._start(self.Execution.with_user(self.eh_user))
        self.assertEqual(execution.sudo().executed_by, self.eh_user)
        forged = execution.with_user(self.eh_user).with_context(
            eh_internal_audit_write=True)
        with self.assertRaises(UserError):
            forged.write({
                'state': 'done',
                'result_payload': base64.b64encode(b'planted'),
            })
        self.assertEqual(execution.sudo().state, 'running')

    def test_engine_lifecycle_works_for_plain_user(self):
        Execution = self.Execution.with_user(self.eh_user)
        execution = self._start(Execution)
        execution.complete_execution(row_count=3)
        self.assertEqual(execution.sudo().state, 'done')
        found = Execution.find_cached(
            'eh_sec_tb', execution.options_hash, [self.company.id])
        self.assertEqual(found.id, execution.id)

    # ---- execution company rule (B3) ----

    def test_consolidated_execution_hidden_from_single_company_user(self):
        company_b = self.env['res.company'].create({
            'name': 'EH Sec Company B',
            'currency_id': self.company.currency_id.id,
        })
        consolidated = self._start(
            self.Execution, company_ids=[self.company.id, company_b.id])
        single = self._start(self.Execution, options={'date': '2026-06-30'})
        visible = self.Execution.with_user(self.eh_user).search(
            [('report_code', '=', 'eh_sec_tb')])
        self.assertIn(single.id, visible.ids)
        self.assertNotIn(
            consolidated.id, visible.ids,
            "A user of company A alone must not read an A+B payload")

    # ---- export attachment (B2) ----

    def test_xlsx_export_attachment_is_creator_only(self):
        report = self._make_report('eh_sec_export')
        with patch.object(
                type(report), 'render_xlsx', return_value=b'PK-eh-test'):
            action = report.with_user(self.eh_user).export_xlsx_attachment({
                'date': {'date_from': '2026-01-01',
                         'date_to': '2026-12-31'},
                'company_ids': [self.company.id],
            })
        attachment_id = int(
            action['url'].split('/web/content/')[1].split('?')[0])
        attachment = self.env['ir.attachment'].browse(attachment_id)
        self.assertFalse(attachment.res_model)
        self.assertFalse(attachment.res_id)
        # The creator (the downloader) can read it ...
        self.assertTrue(
            attachment.with_user(self.eh_user).read(['name']))
        # ... another EH user guessing the id cannot.
        with self.assertRaises(AccessError):
            attachment.with_user(self.eh_user_2).read(['name'])

    # ---- saved views (B11) ----

    def test_shared_saved_view_is_owner_only_editable(self):
        SavedView = self.env['eh.account.report.saved_view']
        view = SavedView.browse(
            SavedView.with_user(self.eh_user).save_view(
                'Month end', 'eh_sec_tb', {'posted_only': True},
                shared=True))
        # Shared: the colleague can read it ...
        self.assertTrue(view.with_user(self.eh_user_2).read(['name']))
        # ... but can neither overwrite nor take it over.
        with self.assertRaises(AccessError):
            view.with_user(self.eh_user_2).write(
                {'options_json': '{"posted_only": false}'})
        with self.assertRaises(AccessError):
            view.with_user(self.eh_user_2).write(
                {'user_id': self.eh_user_2.id})
        # The owner and a manager may edit.
        view.with_user(self.eh_user).write({'notes': 'owner edit'})
        view.with_user(self.eh_manager).write({'notes': 'manager edit'})
        self.assertEqual(view.notes, 'manager edit')
        self.assertEqual(view.user_id, self.eh_user)

    def test_owner_cannot_transfer_or_plant_saved_view(self):
        SavedView = self.env['eh.account.report.saved_view']
        view = SavedView.browse(
            SavedView.with_user(self.eh_user).save_view(
                'Mine', 'eh_sec_tb', {}))
        with self.assertRaises(AccessError):
            view.with_user(self.eh_user).write(
                {'user_id': self.eh_user_2.id})
        with self.assertRaises(AccessError):
            SavedView.with_user(self.eh_user).create({
                'name': 'Planted', 'report_code': 'eh_sec_tb',
                'options_json': '{}', 'shared': True,
                'user_id': self.eh_user_2.id,
            })

    # ---- cache freshness (B4) ----

    def test_posted_line_partner_edit_bumps_version(self):
        move = self.post_balanced_move([
            {'account': self.account_revenue, 'credit': 40.0,
             'partner': self.partner_a},
            {'account': self.account_cash, 'debit': 40.0},
        ])
        before = self._version()
        move.line_ids.filtered(
            lambda l: l.account_id == self.account_revenue,
        ).write({'partner_id': self.partner_b.id})
        self.assertGreater(self._version(), before)

    def test_posted_line_analytic_edit_bumps_version(self):
        analytic = self.env['account.analytic.account'].create({
            'name': 'EH Sec Project', 'plan_id': self._analytic_plan().id,
        })
        move = self.post_balanced_move([
            {'account': self.account_revenue, 'credit': 60.0},
            {'account': self.account_cash, 'debit': 60.0},
        ])
        before = self._version()
        move.line_ids.filtered(
            lambda l: l.account_id == self.account_revenue,
        ).write({'analytic_distribution': {str(analytic.id): 100}})
        self.assertGreater(self._version(), before)

    def test_render_including_drafts_is_never_cached(self):
        report = self._make_report('eh_sec_drafts')
        options = {
            'date': {'date_from': '2026-01-01', 'date_to': '2026-12-31'},
            'company_ids': [self.company.id],
            'posted_only': False,
        }
        with self._patch_compute(lambda *a, **k: _monetary_payload()):
            report.render(options)
            with_drafts = report.render(options)
            posted = dict(options, posted_only=True)
            report.render(posted)
            posted_again = report.render(posted)
        self.assertFalse(with_drafts['from_cache'])
        self.assertTrue(posted_again['from_cache'])

    # ---- language in the cache key (B10) ----

    def test_cache_key_depends_on_language(self):
        self.env['res.lang']._activate_lang('fr_FR')
        options = {'date': {'date_to': '2026-12-31'}, 'posted_only': True}
        english = self.Execution.with_context(
            lang='en_US')._options_hash(options)
        french = self.Execution.with_context(
            lang='fr_FR')._options_hash(options)
        self.assertNotEqual(english, french)

    # ---- presentation currency (B5) ----

    def _other_currency(self, code):
        currency = self.env['res.currency'].create(
            {'name': code, 'symbol': code[-1], 'rounding': 0.01})
        self.env['res.currency.rate'].create({
            'currency_id': currency.id, 'name': '2026-01-01',
            'rate': 2.0, 'company_id': self.company.id,
        })
        return currency

    def test_presentation_currency_not_applied_twice(self):
        other = self._other_currency('ZZS')
        report = self._make_report('eh_sec_ccy')
        options = {
            'date': {'date_from': '2026-01-01', 'date_to': '2026-12-31'},
            'company_ids': [self.company.id],
            'presentation_currency_id': other.id,
        }
        # A handler that already converted discloses it in its meta.
        with self._patch_compute(lambda *a, **k: _monetary_payload(
                {'multi_currency': True,
                 'presentation_currency_id': other.id})):
            converted = report.render(options, use_cache=False)
        self.assertAlmostEqual(
            converted['lines'][0]['columns'][0]['value'], 50.0, places=2)
        # Without that disclosure the orchestrator restates once (rate 2.0).
        with self._patch_compute(lambda *a, **k: _monetary_payload()):
            restated = report.render(options, use_cache=False)
        self.assertAlmostEqual(
            restated['lines'][0]['columns'][0]['value'], 100.0, places=2)

    def test_presentation_currency_refused_across_currencies(self):
        other = self._other_currency('ZZT')
        company_b = self.env['res.company'].create({
            'name': 'EH Sec Company ZZT', 'currency_id': other.id,
        })
        self.env.user.write({'company_ids': [(4, company_b.id)]})
        report = self._make_report('eh_sec_ccy_multi')
        options = {
            'date': {'date_from': '2026-01-01', 'date_to': '2026-12-31'},
            'company_ids': [self.company.id, company_b.id],
            'presentation_currency_id': other.id,
        }
        with self._patch_compute(lambda *a, **k: _monetary_payload()):
            with self.assertRaises(UserError):
                report.render(options, use_cache=False)

    # ---- comparison window (B6) ----

    def test_previous_period_shifts_whole_months(self):
        handler = self.env[_SECTIONED_HANDLER]
        cases = [
            ((date(2026, 3, 1), date(2026, 3, 31)),
             (date(2026, 2, 1), date(2026, 2, 28))),
            ((date(2026, 2, 1), date(2026, 2, 28)),
             (date(2026, 1, 1), date(2026, 1, 31))),
            ((date(2026, 4, 1), date(2026, 6, 30)),
             (date(2026, 1, 1), date(2026, 3, 31))),
            ((date(2026, 1, 1), date(2026, 12, 31)),
             (date(2025, 1, 1), date(2025, 12, 31))),
            # Not whole months: shift by the day count.
            ((date(2026, 3, 10), date(2026, 3, 19)),
             (date(2026, 2, 28), date(2026, 3, 9))),
        ]
        for (date_from, date_to), expected in cases:
            prior_from, prior_to, _label = handler._resolve_comparison_dates(
                'previous_period', date_from, date_to)
            self.assertEqual((prior_from, prior_to), expected,
                             "window %s..%s" % (date_from, date_to))

    # ---- cash basis (B7) ----

    def test_cash_basis_totals_mirror_sql_filters(self):
        handler = self.env[_SECTIONED_HANDLER]
        self.post_balanced_move([
            {'account': self.account_revenue, 'credit': 100.0},
            {'account': self.account_cash, 'debit': 100.0},
        ], date=fields.Date.from_string('2026-06-10'))
        cancelled = self.post_balanced_move([
            {'account': self.account_revenue, 'credit': 900.0},
            {'account': self.account_cash, 'debit': 900.0},
        ], date=fields.Date.from_string('2026-06-11'))
        cancelled.button_draft()
        cancelled.button_cancel()
        self.assertEqual(cancelled.state, 'cancel')
        # No date_from (balance-sheet snapshot), drafts included, no
        # account-type filter.
        rows = handler._cash_basis_grouped_totals(
            account_types=[], company_ids=[self.company.id],
            date_from=None, date_to=date(2026, 12, 31),
            posted_only=False, options={}, sign=1,
        )
        by_account = {r['account_id']: r for r in rows}
        self.assertAlmostEqual(
            by_account[self.account_revenue.id]['amount'], -100.0, places=2)
        self.assertAlmostEqual(
            by_account[self.account_cash.id]['amount'], 100.0, places=2)
        self.assertEqual(
            by_account[self.account_cash.id]['account_code'],
            self.account_cash.with_company(self.company).code)

    # ---- analytic filter (B9) ----

    def test_analytic_filter_matches_combined_keys_and_sub_plans(self):
        root = self._analytic_plan()
        sub_plan = self.env['account.analytic.plan'].create({
            'name': 'EH Sec Sub Plan', 'parent_id': root.id,
        })
        acc_root = self.env['account.analytic.account'].create(
            {'name': 'EH Sec Root Account', 'plan_id': root.id})
        acc_sub = self.env['account.analytic.account'].create(
            {'name': 'EH Sec Sub Account', 'plan_id': sub_plan.id})
        move = self.post_balanced_move([
            {'account': self.account_revenue, 'credit': 70.0},
            {'account': self.account_cash, 'debit': 70.0},
        ])
        move.line_ids.filtered(
            lambda l: l.account_id == self.account_revenue,
        ).write({'analytic_distribution': {
            '%s,%s' % (acc_root.id, acc_sub.id): 100}})
        self.env.flush_all()
        for analytic in (acc_root, acc_sub):
            rows = (
                MoveLineQuery(self.env, company_ids=[self.company.id])
                .select_balance_sum()
                .where_analytic_accounts([analytic.id])
                .execute()
            )
            self.assertAlmostEqual(
                rows[0]['balance'] or 0.0, -70.0, places=2,
                msg="combined key must match account %s" % analytic.name)
        # A ROOT plan filter must reach accounts of its sub-plans: reference
        # only the sub-plan account, then filter on the root plan.
        move.line_ids.filtered(
            lambda l: l.account_id == self.account_revenue,
        ).write({'analytic_distribution': {str(acc_sub.id): 100}})
        self.env.flush_all()
        rows = (
            MoveLineQuery(self.env, company_ids=[self.company.id])
            .select_balance_sum()
            .where_analytic_plans([root.id])
            .execute()
        )
        self.assertAlmostEqual(rows[0]['balance'] or 0.0, -70.0, places=2)
