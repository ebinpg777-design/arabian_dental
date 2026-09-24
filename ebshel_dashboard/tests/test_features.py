# -*- coding: utf-8 -*-
"""Tests for the 1.1 features: focus, ratios, thresholds, history, the
generator, the new card kinds and the plain-words summary."""
import base64
import json
from datetime import datetime, timedelta

from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import new_test_user, tagged

from .test_dashboard import TestDashboardCommon


@tagged('post_install', '-at_install')
class TestFocus(TestDashboardCommon):

    def _focus(self, item, key, field='is_company'):
        return {'item_id': item.id, 'model': 'res.partner', 'field': field, 'key': key}

    def test_focus_narrows_other_cards_of_the_model(self):
        source = self._card(name='Split', kind='bar',
                            group_by_field_id=self._field('res.partner', 'is_company').id)
        counter = self._card(name='Count')
        payload = counter.compute_values('all', focus=self._focus(source, True))
        self.assertEqual(payload['value'], 2)
        self.assertTrue(payload['focused'])

    def test_focus_exempts_its_source(self):
        """The card the focus came from keeps every bar, so the reader can hop."""
        source = self._card(name='Split', kind='bar',
                            group_by_field_id=self._field('res.partner', 'is_company').id)
        payload = source.compute_values('all', focus=self._focus(source, True))
        self.assertEqual(len(payload['points']), 2)
        self.assertTrue(payload['focus_source'])
        self.assertFalse(payload['focused'])

    def test_focus_ignores_other_models(self):
        source = self._card(name='Split', kind='bar',
                            group_by_field_id=self._field('res.partner', 'is_company').id)
        users = self.env['ir.model']._get('res.users')
        other = self._card(name='Users', model_id=users.id, domain='[]')
        payload = other.compute_values('all', focus=self._focus(source, True))
        self.assertFalse(payload['focused'])

    def test_focus_field_is_validated(self):
        """A field name from the browser that the model does not have is ignored."""
        source = self._card(name='Split', kind='bar',
                            group_by_field_id=self._field('res.partner', 'is_company').id)
        counter = self._card(name='Count')
        payload = counter.compute_values('all', focus=self._focus(source, 1, field='no_such'))
        self.assertEqual(payload['value'], 3)
        self.assertFalse(payload['focused'])

    def test_focus_on_the_unset_bucket(self):
        source = self._card(name='Split', kind='bar',
                            group_by_field_id=self._field('res.partner', 'function').id)
        counter = self._card(name='Count')
        payload = counter.compute_values('all', focus=self._focus(source, None, field='function'))
        self.assertEqual(payload['value'], 3)
        self.assertTrue(payload['focused'])

    def test_drill_honours_the_focus(self):
        source = self._card(name='Split', kind='bar',
                            group_by_field_id=self._field('res.partner', 'is_company').id)
        counter = self._card(name='Count')
        action = self.env['dashboard.item'].drill_action(
            counter.id, period='all', focus=self._focus(source, False))
        self.assertEqual(self.env['res.partner'].search_count(action['domain']), 1)

    def test_read_board_passes_the_focus_through(self):
        source = self._card(name='Split', kind='bar',
                            group_by_field_id=self._field('res.partner', 'is_company').id)
        self._card(name='Count')
        data = self.env['dashboard.board'].read_board(
            self.board.id, period='all', focus=self._focus(source, True))
        counted = next(item for item in data['items'] if item['name'] == 'Count')
        self.assertEqual(counted['value'], 2)
        self.assertEqual(data['focus']['key'], True)


class TestReaderSwitches(TestDashboardCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(cls.env, login='reader_switches', groups='base.group_user')

    def test_mine_switch_applies_to_cards_that_know_their_user(self):
        self.partners[0].user_id = self.user
        card = self._card(user_field_id=self._field('res.partner', 'user_id').id)
        self.assertEqual(card.with_user(self.user).compute_values('all', mine=True)['value'], 1)
        self.assertEqual(card.with_user(self.user).compute_values('all', mine=False)['value'], 3)

    def test_mine_switch_needs_the_board_to_allow_it(self):
        self.partners[0].user_id = self.user
        self._card(user_field_id=self._field('res.partner', 'user_id').id)
        self.board.allow_mine = False
        data = self.env['dashboard.board'].with_user(self.user).read_board(
            self.board.id, period='all', mine=True)
        self.assertFalse(data['mine'])
        self.assertEqual(data['items'][0]['value'], 3)

    def test_favourite_board_opens_first(self):
        # Sequence 0 sorts before every other board, the demo one included.
        other = self.env['dashboard.board'].create({'name': 'Other', 'sequence': 0})
        Board = self.env['dashboard.board'].with_user(self.user)
        self.assertEqual(Board._default_board(), other)
        Board.browse(self.board.id).set_default(True)
        self.assertEqual(Board._default_board(), self.board)
        self.assertTrue(Board.browse(self.board.id)._is_default())
        Board.browse(self.board.id).set_default(False)
        self.assertEqual(Board._default_board(), other)


class TestNumberCards(TestDashboardCommon):

    def test_ratio_is_a_share_of_the_whole_model(self):
        card = self._card(domain=f"[('category_id', 'in', [{self.tag.id}]), ('is_company', '=', True)]",
                          as_ratio=True)
        payload = card.compute_values('all')
        whole = self.env['res.partner'].search_count([])
        self.assertEqual(payload['whole'], whole)
        self.assertEqual(payload['ratio'], round(2 / whole * 100, 1))

    def test_own_period_ignores_the_board(self):
        card = self._card(date_field_id=self._field('res.partner', 'create_date').id,
                          period_mode='own', own_period='last_year')
        self.assertEqual(card.compute_values('today')['value'], 0)
        self.assertEqual(card.compute_values('today')['period'], 'last_year')

    def test_sparkline_covers_the_period(self):
        card = self._card(date_field_id=self._field('res.partner', 'create_date').id)
        payload = card.compute_values('this_month')
        self.assertTrue(payload['spark'])
        self.assertEqual(sum(point['value'] for point in payload['spark']), 3)

    def test_sparkline_over_all_time_is_twelve_months(self):
        card = self._card(date_field_id=self._field('res.partner', 'create_date').id)
        self.assertEqual(len(card.compute_values('all')['spark']), 12)

    def test_threshold_levels(self):
        card = self._card(alert_operator='gt', alert_value=2, alert_level='danger')
        self.assertEqual(card.compute_values('all')['alert'], 'danger')
        card.write({'alert_operator': 'lt'})
        self.assertFalse(card.compute_values('all')['alert'])
        card.write({'alert_operator': 'gte', 'alert_value': 3, 'alert_level': 'warning'})
        self.assertEqual(card.compute_values('all')['alert'], 'warning')

    def test_threshold_only_on_number_cards(self):
        with self.assertRaises(ValidationError):
            self._card(kind='bar', group_by_field_id=self._field('res.partner', 'is_company').id,
                       alert_operator='gt', alert_value=1)

    def test_gauge_needs_a_target(self):
        with self.assertRaises(ValidationError):
            self._card(kind='gauge')
        card = self._card(kind='gauge', target_value=10)
        self.assertEqual(card.compute_values('all')['value'], 3)

    def test_status_card_carries_the_alert(self):
        card = self._card(kind='status', alert_operator='gt', alert_value=1)
        self.assertEqual(card.compute_values('all')['alert'], 'warning')

    def test_compare_on_a_chart(self):
        card = self._card(kind='bar', group_by_field_id=self._field('res.partner', 'is_company').id,
                          date_field_id=self._field('res.partner', 'create_date').id, compare=True)
        payload = card.compute_values('this_month')
        self.assertEqual(payload['previous'], 0)
        self.assertEqual(payload['delta'], 3)


class TestThresholdCron(TestDashboardCommon):

    def test_rising_edge_marks_the_card(self):
        card = self._card(alert_operator='gt', alert_value=1, alert_notify=False)
        self.assertTrue(card._check_alert_now())
        self.assertTrue(card.alert_triggered)
        self.assertTrue(card.alert_triggered_on)
        card.write({'alert_value': 100})
        self.assertFalse(card._check_alert_now())
        self.assertFalse(card.alert_triggered)

    def test_cron_only_looks_at_cards_with_a_threshold(self):
        plain = self._card(name='Plain')
        armed = self._card(name='Armed', alert_operator='gt', alert_value=1)
        self.env['dashboard.item']._cron_check_alerts()
        self.assertFalse(plain.alert_triggered)
        self.assertTrue(armed.alert_triggered)

    def test_audience_of_a_personal_board(self):
        user = new_test_user(
            self.env, login='board_owner',
            groups='base.group_user,ebshel_dashboard.group_dashboard_manager')
        board = self.env['dashboard.board'].with_user(user).create(
            {'name': 'Mine', 'owner_id': user.id})
        # Built by the owner: nobody else may put a card on their board.
        card = self.env['dashboard.item'].with_user(user).create({
            'name': 'Card', 'board_id': board.id, 'model_id': self.partner_model.id,
            'domain': self.base_domain, 'kind': 'kpi', 'aggregate': 'count',
            'alert_operator': 'gt', 'alert_value': 1,
        }).sudo()
        self.assertEqual(card._alert_audience(), user)

    def test_audience_of_a_shared_board_is_the_managers(self):
        card = self._card(alert_operator='gt', alert_value=1)
        audience = card._alert_audience()
        self.assertIn(self.env.ref('base.user_admin'), audience)


class TestHistory(TestDashboardCommon):

    def test_capture_records_one_point_per_day(self):
        card = self._card()
        Snapshot = self.env['dashboard.snapshot']
        Snapshot._cron_capture()
        Snapshot._cron_capture()
        points = Snapshot.search([('item_id', '=', card.id)])
        self.assertEqual(len(points), 1)
        self.assertEqual(points.value, 3)

    def test_viewer_dependent_cards_are_not_recorded(self):
        card = self._card(only_mine=True, user_field_id=self._field('res.partner', 'user_id').id)
        self.env['dashboard.snapshot']._cron_capture()
        self.assertFalse(self.env['dashboard.snapshot'].search([('item_id', '=', card.id)]))

    def test_history_sparkline(self):
        card = self._card(spark_source='history')
        Snapshot = self.env['dashboard.snapshot']
        today = fields.Date.context_today(card)
        for offset, value in ((3, 1.0), (2, 2.0), (1, 3.0)):
            Snapshot._record(card, value, day=fields.Date.subtract(today, days=offset))
        spark = card.compute_values('all')['spark']
        self.assertEqual([point['value'] for point in spark], [1.0, 2.0, 3.0])


class TestKinds(TestDashboardCommon):

    def test_stacked_matrix(self):
        card = self._card(kind='stacked',
                          group_by_field_id=self._field('res.partner', 'is_company').id,
                          stack_field_id=self._field('res.partner', 'type').id)
        payload = card.compute_values('all')
        self.assertEqual([c['label'] for c in payload['categories']], ['Yes', 'No'])
        self.assertEqual(len(payload['series']), 1)
        self.assertEqual(payload['series'][0]['values'], [2.0, 1.0])
        self.assertEqual(payload['value'], 3)

    def test_stacked_needs_two_different_fields(self):
        with self.assertRaises(ValidationError):
            self._card(kind='stacked',
                       group_by_field_id=self._field('res.partner', 'is_company').id,
                       stack_field_id=self._field('res.partner', 'is_company').id)

    def test_split_kinds_share_the_engine(self):
        for kind in ('hbar', 'donut', 'polar', 'radar', 'funnel', 'progress', 'table'):
            card = self._card(name=kind, kind=kind,
                              group_by_field_id=self._field('res.partner', 'is_company').id)
            payload = card.compute_values('all')
            self.assertEqual(len(payload['points']), 2, kind)
            self.assertEqual(payload['points'][0]['share'], 66.7, kind)

    def test_area_is_a_series(self):
        card = self._card(kind='area', date_field_id=self._field('res.partner', 'create_date').id)
        self.assertEqual(len(card.compute_values('this_year')['points']), 12)

    def test_note_card_needs_no_model(self):
        note = self.env['dashboard.item'].create({
            'name': 'Read me', 'board_id': self.board.id, 'kind': 'text',
            'note': '<p>Numbers are refreshed nightly.</p>',
        })
        payload = note.compute_values('all')
        self.assertIn('nightly', payload['note'])
        self.assertFalse(self.env['dashboard.item'].drill_action(note.id))

    def test_other_kinds_need_a_model(self):
        with self.assertRaises(ValidationError):
            self.env['dashboard.item'].create({
                'name': 'Lost', 'board_id': self.board.id, 'kind': 'kpi'})

    def test_drill_view_choice(self):
        card = self._card(drill_view='kanban')
        action = self.env['dashboard.item'].drill_action(card.id)
        self.assertEqual(action['views'][0][1], 'kanban')
        pivot = self._card(kind='bar', drill_view='pivot',
                           group_by_field_id=self._field('res.partner', 'is_company').id)
        action = self.env['dashboard.item'].drill_action(pivot.id)
        self.assertEqual(action['views'][0][1], 'pivot')
        self.assertEqual(action['context']['pivot_row_groupby'], ['is_company'])

    def test_refresh_item(self):
        card = self._card()
        self.assertEqual(self.env['dashboard.item'].refresh_item(card.id, 'all')['value'], 3)
        self.assertFalse(self.env['dashboard.item'].refresh_item(0))

    def test_duplicate(self):
        card = self._card(name='Original')
        action = card.action_duplicate()
        copy = self.env['dashboard.item'].browse(action['res_id'])
        self.assertEqual(copy.name, 'Original (copy)')
        self.assertEqual(copy.board_id, self.board)
        self.assertEqual(copy.sequence, card.sequence + 1)


class TestPreviewAndWords(TestDashboardCommon):

    def test_preview_computes_an_unsaved_card(self):
        values = {
            'name': 'Draft', 'kind': 'kpi', 'model_id': self.partner_model.id,
            'domain': self.base_domain, 'aggregate': 'count', 'color': 'sky', 'icon': 'fa-bolt',
            'width': 3,
        }
        payload = self.env['dashboard.item'].preview_values(values)
        self.assertEqual(payload['value'], 3)
        self.assertEqual(payload['id'], 0)
        self.assertEqual(payload['color'], 'sky')

    def test_preview_accepts_many2one_pairs(self):
        payload = self.env['dashboard.item'].preview_values({
            'name': 'Draft', 'kind': 'bar', 'aggregate': 'count',
            'model_id': [self.partner_model.id, 'Contact'], 'domain': self.base_domain,
            'group_by_field_id': [self._field('res.partner', 'is_company').id, 'Is a Company'],
        })
        self.assertEqual(len(payload['points']), 2)

    def test_preview_without_a_model_is_empty(self):
        self.assertTrue(self.env['dashboard.item'].preview_values({'kind': 'kpi'})['empty'])

    def test_preview_survives_a_broken_domain(self):
        payload = self.env['dashboard.item'].preview_values({
            'name': 'Draft', 'kind': 'kpi', 'model_id': self.partner_model.id,
            'domain': "[('nope', '=', 1)]", 'aggregate': 'count'})
        self.assertTrue(payload['error'])

    def test_summary_in_plain_words(self):
        card = self._card(
            aggregate='sum', measure_field_id=self._field('res.partner', 'partner_latitude').id,
            domain=f"[('category_id', 'in', [{self.tag.id}]), ('is_company', '=', True)]",
            group_by_field_id=self._field('res.partner', 'country_id').id,
            date_field_id=self._field('res.partner', 'create_date').id, kind='bar')
        words = card.summary
        self.assertIn('Sum of Geo Latitude of Contact', words)
        self.assertIn('Is a Company', words)
        self.assertIn('split by Country', words)
        self.assertIn("board's period", words)

    def test_summary_of_a_note(self):
        note = self.env['dashboard.item'].create({
            'name': 'Read me', 'board_id': self.board.id, 'kind': 'text'})
        self.assertEqual(note.summary, 'A note')


class TestGenerator(TestDashboardCommon):

    def test_build_from_partner(self):
        board = self.env['dashboard.board'].build_from_model('res.partner')
        self.assertIn('Contact', board.name)
        kinds = board.item_ids.mapped('kind')
        self.assertIn('kpi', kinds)
        self.assertIn('area', kinds)
        self.assertIn('list', kinds)
        self.assertGreaterEqual(len(board.item_ids), 4)
        # Every generated card passes its own constraints and computes.
        data = self.env['dashboard.board'].read_board(board.id)
        self.assertFalse([item['name'] for item in data['items'] if item.get('error')])

    def test_build_names_the_board(self):
        board = self.env['dashboard.board'].build_from_model('res.partner', name='People')
        self.assertEqual(board.name, 'People')

    def test_build_refuses_a_missing_model(self):
        with self.assertRaises(UserError):
            self.env['dashboard.board'].build_from_model('no.such.model')

    def test_pick_fields_prefers_state_and_money(self):
        Board = self.env['dashboard.board']
        date, split, _second, user, measure = Board._pick_fields(self.env['res.partner'])
        self.assertEqual(date, 'create_date')
        self.assertEqual(user, 'user_id')
        self.assertTrue(split)
        self.assertTrue(measure)

    def test_wizard_builds_and_opens(self):
        wizard = self.env['dashboard.build'].create({'model_id': self.partner_model.id})
        self.assertIn('Found', wizard.hint)
        action = wizard.action_build()
        self.assertEqual(action['tag'], 'ebshel_dashboard.board')


class TestGridAndExport(TestDashboardCommon):

    def test_board_grid_settings_travel(self):
        self.board.write({'columns': '8', 'density': 'compact', 'allow_mine': False})
        self._card(name='Note', kind='text', model_id=False, note='<p>Hi</p>')
        payload = json.loads(self.board.export_boards())
        entry = payload['boards'][0]
        self.assertEqual(entry['columns'], '8')
        self.assertEqual(entry['density'], 'compact')
        result = self.env['dashboard.board'].import_boards(payload)
        imported = self.env['dashboard.board'].browse(result['boards'])
        self.assertEqual(imported.columns, '8')
        self.assertEqual(imported.item_ids.kind, 'text')
        self.assertFalse(result['skipped'])

    def test_resize_keeps_height_in_bounds(self):
        card = self._card()
        self.board.resize_item(card.id, 6, 400)
        self.assertEqual((card.width, card.height), (6, 400))
        with self.assertRaises(ValidationError):
            self.board.resize_item(card.id, 6, 50)

    def test_export_carries_the_new_fields(self):
        self._card(name='Ratio', as_ratio=True, drill_view='pivot', prefix='$',
                   alert_operator='gt', alert_value=5)
        entry = json.loads(self.board.export_boards())['boards'][0]['items'][0]
        self.assertTrue(entry['as_ratio'])
        self.assertEqual(entry['drill_view'], 'pivot')
        self.assertEqual(entry['prefix'], '$')
        self.assertEqual(entry['alert_operator'], 'gt')
        base64.b64encode(b'')  # keep the import honest: used by the wizard tests


class TestShell(TestDashboardCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(cls.env, login='shell_reader', groups='base.group_user')

    def test_watchlist_pins_number_cards_only(self):
        Pref = self.env['dashboard.preference'].with_user(self.user)
        number = self._card(name='Count')
        chart = self._card(name='Split', kind='bar',
                           group_by_field_id=self._field('res.partner', 'is_company').id)
        self.assertTrue(Pref.toggle_watch(number.id))
        self.assertFalse(Pref.toggle_watch(chart.id))
        payload = Pref.watchlist_payload()
        self.assertEqual([entry['id'] for entry in payload], [number.id])
        self.assertEqual(payload[0]['board_name'], self.board.name)
        self.assertFalse(Pref.toggle_watch(number.id))
        self.assertEqual(Pref.watchlist_payload(), [])

    def test_watchlist_drops_cards_the_reader_lost(self):
        Pref = self.env['dashboard.preference'].with_user(self.user)
        card = self._card(name='Count')
        Pref.toggle_watch(card.id)
        self.board.write({'group_ids': [(4, self.env.ref('base.group_system').id)]})
        # Still pinned, but the board is no longer offered: the watch is hidden.
        self.board.write({'owner_id': self.env.ref('base.user_admin').id, 'group_ids': [(5, 0, 0)]})
        self.assertEqual(Pref.watchlist_payload(), [])

    def test_note_is_per_reader_and_board(self):
        Note = self.env['dashboard.note']
        mine = Note.with_user(self.user).save_note(self.board.id, '<p>Check on Monday</p>')
        self.assertIn('Monday', mine['body'])
        self.assertEqual(Note._mine(self.board.id).body or '', '')
        again = Note.with_user(self.user).save_note(self.board.id, '<p>Done</p>')
        self.assertEqual(again['id'], mine['id'])
        self.assertFalse(Note.with_user(self.user).search([('user_id', '!=', self.user.id)]))

    def test_read_board_carries_the_shell_data(self):
        card = self._card(name='Count')
        self.env['dashboard.preference'].toggle_watch(card.id)
        self.env['dashboard.note'].save_note(self.board.id, '<p>hello</p>')
        data = self.env['dashboard.board'].read_board(self.board.id)
        self.assertEqual([w['id'] for w in data['watchlist']], [card.id])
        self.assertIn('hello', data['note'])
        self.assertTrue(data['user_name'])

    def test_quick_find_respects_visibility(self):
        self._card(name='Find me')
        hits = self.env['dashboard.item'].with_user(self.user).search_cards('find')
        self.assertEqual([hit['name'] for hit in hits], ['Find me'])
        self.assertEqual(hits[0]['board_name'], self.board.name)
        group = self.env['res.groups'].create({'name': 'Finders'})
        self.board.write({'group_ids': [(4, group.id)]})
        self.assertEqual(self.env['dashboard.item'].with_user(self.user).search_cards('find'), [])
        self.assertEqual(self.env['dashboard.item'].search_cards(''), [])


class TestEngineBatch(TestDashboardCommon):
    """The 1.2 engine: periods, comparison mode, sort, transforms, multiplier,
    columns, scatter, bullet, formula, visibility rules, timing, saved views."""

    def test_new_period_windows(self):
        Item = self.env['dashboard.item']
        today = self.today
        start, end = Item._period_range('last_week')
        self.assertEqual((end - start).days, 7)
        self.assertLess(end, today + relativedelta(days=1))
        self.assertEqual(Item._period_range('mtd'), (today.replace(day=1), today + relativedelta(days=1)))
        self.assertEqual(Item._period_range('ytd')[0], today.replace(month=1, day=1))
        start, end = Item._period_range('next_7')
        self.assertEqual(start, today + relativedelta(days=1))
        self.assertEqual((end - start).days, 7)
        start, end = Item._period_range('last_12_months')
        self.assertEqual(end, today.replace(day=1) + relativedelta(months=1))
        self.assertEqual((end - start).days >= 365 - 31, True)
        self.assertEqual(Item._period_range('last_quarter')[1], Item._period_range('this_quarter')[0])

    def test_compare_with_last_year(self):
        card = self._card(date_field_id=self._field('res.partner', 'create_date').id,
                          compare=True, compare_mode='year')
        current = card._period_leaves('this_month')
        previous = card._period_leaves('this_month', shift=1)
        self.assertEqual(previous[0][2], fields.Date.to_string(
            self.today.replace(day=1) - relativedelta(years=1)))
        self.assertEqual(current[0][2], fields.Date.to_string(self.today.replace(day=1)))

    def test_sort_orders(self):
        base = dict(kind='bar', group_by_field_id=self._field('res.partner', 'is_company').id)
        by_value = self._card(sort='value_asc', **base).compute_values('all')['points']
        self.assertEqual([p['value'] for p in by_value], [1.0, 2.0])
        by_label = self._card(sort='label_asc', **base).compute_values('all')['points']
        self.assertEqual([p['label'] for p in by_label], ['No', 'Yes'])
        by_label_desc = self._card(sort='label_desc', **base).compute_values('all')['points']
        self.assertEqual([p['label'] for p in by_label_desc], ['Yes', 'No'])

    def test_transforms(self):
        base = dict(kind='line', date_field_id=self._field('res.partner', 'create_date').id)
        plain = self._card(**base).compute_values('this_year')['points']
        running = self._card(transform='cumulative', **base).compute_values('this_year')['points']
        self.assertEqual(running[-1]['value'], sum(p['value'] for p in plain))
        self.assertTrue(all(running[i]['value'] <= running[i + 1]['value'] for i in range(11)))
        moving = self._card(transform='moving', **base).compute_values('this_year')['points']
        self.assertEqual(len(moving), 12)
        self.assertAlmostEqual(moving[-1]['value'], sum(p['value'] for p in plain[-3:]) / 3)

    def test_multiplier_scales_what_is_shown(self):
        card = self._card(aggregate='sum', multiplier=0.01,
                          measure_field_id=self._field('res.partner', 'partner_latitude').id)
        self.assertAlmostEqual(card.compute_values('all')['value'], 10.0)
        split = self._card(kind='bar', multiplier=2.0,
                           group_by_field_id=self._field('res.partner', 'is_company').id)
        self.assertEqual([p['value'] for p in split.compute_values('all')['points']], [4.0, 2.0])

    def test_list_columns(self):
        card = self._card(kind='list', limit=3,
                          list_field_ids=[(6, 0, [self._field('res.partner', 'is_company').id,
                                                  self._field('res.partner', 'function').id])])
        payload = card.compute_values('all')
        self.assertEqual([c['name'] for c in payload['columns']], ['is_company', 'function'])
        self.assertEqual(len(payload['rows'][0]['cells']), 2)
        self.assertIn(payload['rows'][0]['cells'][0], ('Yes', 'No'))

    def test_scatter_points(self):
        card = self._card(kind='scatter', aggregate='sum',
                          measure_field_id=self._field('res.partner', 'partner_latitude').id,
                          measure2_field_id=self._field('res.partner', 'partner_longitude').id)
        payload = card.compute_values('all')
        self.assertEqual(len(payload['points']), 3)
        self.assertEqual(payload['points'][0]['x'], 600.0)
        self.assertIn('label', payload['points'][0])

    def test_scatter_needs_two_measures(self):
        with self.assertRaises(ValidationError):
            self._card(kind='scatter', aggregate='sum',
                       measure_field_id=self._field('res.partner', 'partner_latitude').id)

    def test_bullet_needs_a_target(self):
        with self.assertRaises(ValidationError):
            self._card(kind='bullet')
        card = self._card(kind='bullet', target_value=10)
        self.assertEqual(card.compute_values('all')['value'], 3)

    def test_formula_card(self):
        card = self._card(kind='formula', formula='a / b * 100', variable_ids=[
            (0, 0, {'name': 'a', 'label': 'Companies', 'domain': "[('is_company', '=', True)]"}),
            (0, 0, {'name': 'b', 'label': 'All', 'domain': '[]'}),
        ])
        payload = card.compute_values('all')
        self.assertAlmostEqual(payload['value'], 200 / 3)
        self.assertEqual([v['name'] for v in payload['variables']], ['a', 'b'])
        self.assertIn('a = Companies', card.summary)

    def test_formula_division_by_zero_is_zero(self):
        card = self._card(kind='formula', formula='a / b', variable_ids=[
            (0, 0, {'name': 'a', 'domain': '[]'}),
            (0, 0, {'name': 'b', 'domain': "[('name', '=', 'nobody-has-this-name')]"}),
        ])
        self.assertEqual(card.compute_values('all')['value'], 0.0)

    def test_formula_is_checked(self):
        with self.assertRaises(ValidationError):
            self._card(kind='formula', formula='a +', variable_ids=[(0, 0, {'name': 'a'})])
        with self.assertRaises(ValidationError):
            self._card(kind='formula', formula='__import__("os")', variable_ids=[(0, 0, {'name': 'a'})])
        with self.assertRaises(ValidationError):
            self._card(kind='formula', formula='a', variable_ids=[
                (0, 0, {'name': 'a'}), (0, 0, {'name': 'a'})])

    def test_hide_rule(self):
        card = self._card(hide_operator='lt', hide_value=10)
        self.assertTrue(card.compute_values('all')['hidden'])
        card.write({'hide_operator': 'gt'})
        self.assertFalse(card.compute_values('all')['hidden'])

    def test_card_groups_restrict_the_reader(self):
        user = new_test_user(self.env, login='engine_reader', groups='base.group_user')
        group = self.env['res.groups'].create({'name': 'Card Audience'})
        self._card(name='Open')
        self._card(name='Closed', group_ids=[(4, group.id)])
        data = self.env['dashboard.board'].with_user(user).read_board(self.board.id)
        self.assertEqual([item['name'] for item in data['items']], ['Open'])
        data = self.env['dashboard.board'].read_board(self.board.id)
        self.assertEqual(len(data['items']), 2)

    def test_timing_and_snapshot_delta(self):
        card = self._card()
        self.env['dashboard.snapshot']._record(card, 1.0, day=fields.Date.subtract(self.today, days=1))
        payload = card.compute_values('all')
        self.assertIn('ms', payload)
        self.assertEqual(payload['since_snapshot']['delta'], 2.0)

    def test_board_palette_and_slideshow_travel(self):
        self.board.write({'palette': 'warm', 'slide_seconds': 20})
        payload = json.loads(self.board.export_boards())
        result = self.env['dashboard.board'].import_boards(payload)
        imported = self.env['dashboard.board'].browse(result['boards'])
        self.assertEqual((imported.palette, imported.slide_seconds), ('warm', 20))

    def test_formula_travels(self):
        self._card(kind='formula', formula='a * 2', variable_ids=[(0, 0, {'name': 'a', 'domain': '[]'})])
        payload = json.loads(self.board.export_boards())
        result = self.env['dashboard.board'].import_boards(payload)
        imported = self.env['dashboard.board'].browse(result['boards'])
        self.assertEqual(imported.item_ids.variable_ids.name, 'a')
        self.assertEqual(imported.item_ids.compute_values('all')['value'], 6.0)

    def test_saved_views(self):
        View = self.env['dashboard.view']
        saved = View.save_view(self.board.id, 'Belgium', period='this_year',
                               focus={'field': 'country_id', 'key': 1}, mine=True)
        self.assertEqual(saved['focus']['key'], 1)
        again = View.save_view(self.board.id, 'Belgium', period='last_year')
        self.assertEqual(again['id'], saved['id'])
        listed = View.list_views(self.board.id)
        self.assertEqual([v['name'] for v in listed], ['Belgium'])
        self.assertEqual(listed[0]['period'], 'last_year')
        other = new_test_user(self.env, login='view_reader', groups='base.group_user')
        self.assertEqual(View.with_user(other).list_views(self.board.id), [])
        View.remove_view(saved['id'])
        self.assertEqual(View.list_views(self.board.id), [])

    def test_board_menu(self):
        """The button now asks where to put it; the installer does the work."""
        action = self.board.action_create_menu()
        self.assertEqual(action['res_model'], 'dashboard.menu.wizard')
        self.assertEqual(action['context']['default_board_id'], self.board.id)
        self.board._install_menu()
        self.assertTrue(self.board.menu_id)
        self.assertEqual(self.board.menu_id.parent_id,
                         self.env.ref('ebshel_dashboard.menu_dashboard_root'))
        self.assertEqual(self.board.menu_action_id.tag, 'ebshel_dashboard.board')
        self.board.action_remove_menu()
        self.assertFalse(self.board.menu_id)


class TestPagesAndPacing(TestDashboardCommon):

    def test_custom_range_period(self):
        card = self._card(date_field_id=self._field('res.partner', 'create_date').id)
        today = fields.Date.to_string(self.today)
        self.assertEqual(card.compute_values({'start': today, 'end': today})['value'], 3)
        gone = fields.Date.to_string(fields.Date.subtract(self.today, days=10))
        self.assertEqual(card.compute_values({'start': gone, 'end': gone})['value'], 0)
        # An inverted or unreadable range is "all time", never an error.
        self.assertEqual(card.compute_values({'start': today, 'end': gone})['value'], 3)
        self.assertEqual(card.compute_values({'start': 'nope', 'end': today})['value'], 3)

    def test_board_can_refuse_custom_ranges(self):
        card = self._card(date_field_id=self._field('res.partner', 'create_date').id)
        gone = fields.Date.to_string(fields.Date.subtract(self.today, days=10))
        self.board.write({'allow_range': False, 'period': 'all'})
        data = self.env['dashboard.board'].read_board(self.board.id, period={'start': gone, 'end': gone})
        self.assertEqual(data['items'][0]['value'], 3)

    def test_heatmap_and_pivot_share_the_matrix(self):
        for kind in ('heatmap', 'pivot'):
            card = self._card(name=kind, kind=kind,
                              group_by_field_id=self._field('res.partner', 'is_company').id,
                              stack_field_id=self._field('res.partner', 'type').id)
            payload = card.compute_values('all')
            self.assertEqual([c['label'] for c in payload['categories']], ['Yes', 'No'], kind)
            self.assertEqual(payload['series'][0]['values'], [2.0, 1.0], kind)

    def test_pace_against_the_calendar(self):
        card = self._card(target_value=30, date_field_id=self._field('res.partner', 'create_date').id)
        pace = card.compute_values('this_month')['pace']
        self.assertIn(pace['status'], ('ahead', 'on_track', 'behind'))
        self.assertGreater(pace['elapsed'], 0)
        self.assertLessEqual(pace['elapsed'], 100)
        self.assertNotIn('pace', card.compute_values('all'))
        plain = self._card(date_field_id=self._field('res.partner', 'create_date').id)
        self.assertNotIn('pace', plain.compute_values('this_month'))

    def test_tabs(self):
        Tab = self.env['dashboard.tab']
        first = self.board.add_tab('Sales')
        second = self.board.add_tab('Ops')
        self.assertLess(first['sequence'], second['sequence'])
        card = self._card(name='On ops')
        self.board.move_to_tab(card.id, second['id'])
        self.assertEqual(card.tab_id.name, 'Ops')
        data = self.env['dashboard.board'].read_board(self.board.id)
        self.assertEqual([t['name'] for t in data['board']['tabs']], ['Sales', 'Ops'])
        self.assertEqual(data['items'][0]['tab_id'], second['id'])
        self.board.move_to_tab(card.id, False)
        self.assertFalse(card.tab_id)
        self.assertEqual(Tab.search_count([('board_id', '=', self.board.id)]), 2)

    def test_tabs_travel_and_copy(self):
        tab = self.board.add_tab('Sales')
        card = self._card(name='Tabbed')
        self.board.move_to_tab(card.id, tab['id'])
        payload = json.loads(self.board.export_boards())
        self.assertEqual(payload['boards'][0]['items'][0]['tab_name'], 'Sales')
        result = self.env['dashboard.board'].import_boards(payload)
        imported = self.env['dashboard.board'].browse(result['boards'])
        self.assertEqual(imported.item_ids.tab_id.name, 'Sales')
        self.assertEqual(imported.item_ids.tab_id.board_id, imported)
        copy = self.board.copy()
        self.assertEqual(copy.item_ids.tab_id.board_id, copy)
        self.assertEqual(copy.item_ids.tab_id.name, 'Sales')

    def test_tabs_need_edit_rights(self):
        user = new_test_user(self.env, login='tab_reader', groups='base.group_user')
        with self.assertRaises(UserError):
            self.board.with_user(user).add_tab('Nope')


@tagged('post_install', '-at_install')
class TestPdf(TestDashboardCommon):
    """The board on paper: composed here, so no wkhtmltopdf is involved."""

    PIXEL = ('data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ'
             'AAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==')

    def _pdf(self, action):
        self.assertEqual(action['type'], 'ir.actions.act_url')
        attachment = self.env['ir.attachment'].browse(
            int(action['url'].split('/web/content/')[1].split('?')[0]))
        self.assertEqual(attachment.mimetype, 'application/pdf')
        return attachment, attachment.raw

    def test_a_board_downloads_as_a_pdf(self):
        self._card(name='Count')
        self._card(name='Split', kind='bar',
                   group_by_field_id=self._field('res.partner', 'is_company').id)
        attachment, pdf = self._pdf(self.board.download_pdf())
        self.assertTrue(pdf.startswith(b'%PDF'))
        self.assertTrue(pdf.rstrip().endswith(b'%%EOF'))
        self.assertEqual(attachment.name, 'Test_Board.pdf')
        self.assertGreater(len(pdf), 1000)

    def test_the_chart_from_the_screen_goes_in(self):
        card = self._card(name='Split', kind='bar',
                          group_by_field_id=self._field('res.partner', 'is_company').id)
        _attachment, plain = self._pdf(self.board.download_pdf())
        _attachment, withchart = self._pdf(self.board.download_pdf(images={str(card.id): self.PIXEL}))
        # ProcSet always names /ImageB and friends; a real picture is an XObject.
        self.assertIn(b'/Subtype /Image', withchart)
        self.assertNotIn(b'/Subtype /Image', plain)

    def test_one_card_is_named_after_itself(self):
        self._card(name='Count')
        card = self._card(name='Only me')
        attachment, pdf = self._pdf(self.board.download_pdf(item_ids=[card.id]))
        self.assertEqual(attachment.name, 'Only_me.pdf')
        self.assertTrue(pdf.startswith(b'%PDF'))

    def test_a_broken_image_is_skipped_not_raised(self):
        card = self._card(name='Count')
        _attachment, pdf = self._pdf(self.board.download_pdf(
            images={str(card.id): 'data:image/png;base64,not really base64'}))
        self.assertTrue(pdf.startswith(b'%PDF'))

    def test_a_name_outside_latin_1_still_prints(self):
        self._card(name='売上 · Ventes · Продажи')
        _attachment, pdf = self._pdf(self.board.download_pdf())
        self.assertTrue(pdf.startswith(b'%PDF'))

    def test_nothing_to_print_says_so(self):
        with self.assertRaises(UserError):
            self.board.download_pdf()

    def test_a_hidden_card_stays_out_of_the_file(self):
        self._card(name='Count')
        hidden = self._card(name='Hidden', hide_operator='lt', hide_value=1000)
        self.assertTrue(hidden.compute_values('all')['hidden'])
        _attachment, pdf = self._pdf(self.board.download_pdf())
        self.assertTrue(pdf.startswith(b'%PDF'))
        # One card only: a second would push the file over one row of boxes.
        self.assertNotIn(b'Hidden', pdf)

    def test_the_period_is_the_one_the_reader_sees(self):
        self._card(name='Count', date_field_id=self._field('res.partner', 'create_date').id)
        today = fields.Date.to_string(self.today)
        _attachment, pdf = self._pdf(self.board.download_pdf(period={'start': today, 'end': today}))
        self.assertTrue(pdf.startswith(b'%PDF'))


@tagged('post_install', '-at_install')
class TestDigest(TestDashboardCommon):
    """The board in an inbox: when it goes, to whom, and with what in it."""

    def setUp(self):
        super().setUp()
        self.card = self._card(name='Contacts')
        self.reader = new_test_user(self.env, login='digest_reader', groups='base.group_user',
                                    email='reader@example.com')

    def _sent(self):
        """Every mail `_post_digest` handed to the server, without an SMTP.

        Patched through the registry: 18 and 19 do not name the class alike.
        """
        from unittest.mock import patch
        return patch.object(type(self.env['ir.mail_server']), 'send_email', autospec=True)

    def test_a_digest_says_when_it_goes(self):
        self.board.write({'digest_active': True, 'digest_interval': 'weekly',
                          'digest_weekday': '2', 'digest_hour': 7})
        self.assertIn('Wednesday', self.board.digest_next_send)
        self.board.digest_interval = 'daily'
        self.assertIn('07:00', self.board.digest_next_send)
        self.board.digest_active = False
        self.assertFalse(self.board.digest_next_send)

    def test_the_schedule_decides_when_it_is_due(self):
        board = self.board
        board.write({'digest_active': True, 'digest_interval': 'daily', 'digest_hour': 7})
        morning = datetime(2026, 9, 2, 6, 0)  # a Wednesday, before the hour
        self.assertFalse(board._digest_due(morning))
        self.assertTrue(board._digest_due(morning.replace(hour=8)))
        # One send per day, whatever the cron does.
        board.digest_last_sent = morning.replace(hour=8)
        self.assertFalse(board._digest_due(morning.replace(hour=20)))
        self.assertTrue(board._digest_due(morning.replace(hour=8) + timedelta(days=1)))
        board.write({'digest_interval': 'weekly', 'digest_weekday': '2', 'digest_last_sent': False})
        self.assertTrue(board._digest_due(morning.replace(hour=8)))     # Wednesday
        self.assertFalse(board._digest_due(morning.replace(hour=8) + timedelta(days=1)))
        board.write({'digest_interval': 'monthly', 'digest_monthday': 2})
        self.assertTrue(board._digest_due(morning.replace(hour=8)))
        self.assertFalse(board._digest_due(morning.replace(day=3, hour=8)))

    def test_an_hour_or_a_day_outside_the_calendar_is_refused(self):
        with self.assertRaises(UserError):
            self.board.digest_hour = 25
        with self.assertRaises(UserError):
            self.board.digest_monthday = 31

    def test_it_needs_somebody_to_send_to(self):
        self.board.digest_active = True
        with self.assertRaises(UserError):
            self.board.action_send_digest()

    def test_a_digest_carries_the_numbers_and_the_pdf(self):
        self.board.write({'digest_active': True, 'digest_user_ids': [(6, 0, self.reader.ids)],
                          'digest_period': 'all'})
        with self._sent() as send:
            self.board.action_send_digest()
        self.assertEqual(send.call_count, 1)
        message = send.call_args[0][1]
        self.assertEqual(message['To'], 'reader@example.com')
        self.assertIn('Test Board', message['Subject'])
        parts = list(message.walk())
        html = next(part for part in parts if part.get_content_type() == 'text/html')
        self.assertIn('Contacts', html.get_content())
        pdf = next(part for part in parts if part.get_content_type() == 'application/pdf')
        self.assertTrue(pdf.get_payload(decode=True).startswith(b'%PDF'))
        self.assertTrue(self.board.digest_last_sent)

    def test_the_pdf_can_be_left_off(self):
        self.board.write({'digest_active': True, 'digest_emails': 'wall@example.com',
                          'digest_attach_pdf': False, 'digest_period': 'all'})
        with self._sent() as send:
            self.board.action_send_digest()
        message = send.call_args[0][1]
        self.assertFalse([p for p in message.walk() if p.get_content_type() == 'application/pdf'])

    def test_a_reader_who_lost_the_board_is_skipped(self):
        stranger = new_test_user(self.env, login='digest_stranger', groups='base.group_user',
                                 email='stranger@example.com')
        group = self.env['res.groups'].create({'name': 'Digest only'})
        self.board.write({'digest_active': True, 'group_ids': [(6, 0, group.ids)],
                          'digest_user_ids': [(6, 0, (self.reader | stranger).ids)]})
        self.reader.write({'group_ids' if 'group_ids' in self.reader._fields else 'groups_id':
                           [(4, group.id)]})
        with self._sent() as send:
            self.board.action_send_digest()
        self.assertEqual(send.call_count, 1)
        self.assertEqual(send.call_args[0][1]['To'], 'reader@example.com')

    def test_the_cron_sends_only_what_is_due(self):
        self.board.write({'digest_active': True, 'digest_interval': 'daily', 'digest_hour': 0,
                          'digest_emails': 'wall@example.com', 'digest_period': 'all'})
        quiet = self.env['dashboard.board'].create({'name': 'Quiet', 'period': 'all'})
        quiet.write({'digest_active': False, 'digest_emails': 'nobody@example.com'})
        with self._sent() as send:
            self.env['dashboard.board']._cron_send_digests()
        self.assertEqual(send.call_count, 1)
        self.assertTrue(self.board.digest_last_sent)
        # A second run in the same day sends nothing.
        with self._sent() as send:
            self.env['dashboard.board']._cron_send_digests()
        self.assertEqual(send.call_count, 0)


@tagged('post_install', '-at_install')
class TestProgressiveLoad(TestDashboardCommon):
    """Layout first, numbers after: the same rights, the same figures."""

    def test_a_board_can_come_back_without_its_numbers(self):
        card = self._card(name='Count')
        data = self.env['dashboard.board'].read_board(self.board.id, with_values=False)
        self.assertEqual(len(data['items']), 1)
        item = data['items'][0]
        self.assertTrue(item['pending'])
        self.assertEqual(item['id'], card.id)
        self.assertEqual(item['name'], 'Count')
        self.assertEqual(item['width'], card.width)   # enough to draw the grid
        self.assertEqual(item['value'], 0.0)          # but no figure yet

    def test_the_values_come_back_the_same(self):
        first = self._card(name='Count')
        second = self._card(name='Companies', domain="[('is_company', '=', True), ('category_id', 'in', [%s])]" % self.tag.id)
        whole = self.env['dashboard.board'].read_board(self.board.id)['items']
        chunk = self.env['dashboard.board'].board_values(
            self.board.id, [first.id, second.id])
        self.assertEqual([v['id'] for v in chunk], [first.id, second.id])
        self.assertEqual([v['value'] for v in chunk], [v['value'] for v in whole])

    def test_a_chunk_only_answers_for_the_cards_asked(self):
        first = self._card(name='One')
        self._card(name='Two')
        values = self.env['dashboard.board'].board_values(self.board.id, [first.id])
        self.assertEqual([v['id'] for v in values], [first.id])

    def test_a_chunk_keeps_the_readers_rights(self):
        self._card(name='Count')
        stranger = new_test_user(self.env, login='chunk_stranger', groups='base.group_user')
        group = self.env['res.groups'].create({'name': 'Chunk only'})
        self.board.group_ids = [(6, 0, group.ids)]
        with self.assertRaises(AccessError):
            self.env['dashboard.board'].with_user(stranger).board_values(
                self.board.id, self.board.item_ids.ids)

    def test_a_card_hidden_from_the_reader_is_not_in_a_chunk(self):
        card = self._card(name='Secret')
        group = self.env['res.groups'].create({'name': 'Card only'})
        card.group_ids = [(6, 0, group.ids)]
        reader = new_test_user(self.env, login='chunk_reader', groups='base.group_user')
        self.board.owner_id = False
        values = self.env['dashboard.board'].with_user(reader).board_values(
            self.board.id, [card.id])
        self.assertEqual(values, [])

    def test_the_page_can_actually_call_these(self):
        """Both are called from the browser with no ids, so both must be
        `@api.model` - without it the RPC dies with "list index out of range"
        while every in-process test still passes."""
        Board = type(self.env['dashboard.board'])
        for name in ('read_board', 'board_values'):
            method = getattr(Board, name)
            # 18 marks it `_api = 'model'`, 19 `_api_model = True`.
            marked = getattr(method, '_api_model', False) or getattr(method, '_api', None) == 'model'
            self.assertTrue(marked, "%s must be an @api.model method" % name)

    def test_one_memo_serves_a_whole_chunk(self):
        """The counting card and the ratio beside it are one query, not three."""
        counter = self._card(name='Count')
        ratio = self._card(name='Share', as_ratio=True,
                           domain="[('is_company', '=', True), ('category_id', 'in', [%s])]" % self.tag.id)
        with self.assertQueryCount(__system__=25):
            values = self.env['dashboard.board'].board_values(
                self.board.id, [counter.id, ratio.id])
        self.assertEqual(values[0]['value'], 3)
        # The ratio's denominator is the model, not the card's own filter, so
        # the share depends on the database; what matters here is that it was
        # computed and that the chunk stayed inside its query budget.
        self.assertEqual(values[1]['value'], 2)
        self.assertIsNotNone(values[1]['ratio'])


@tagged('post_install', '-at_install')
class TestFilterBar(TestDashboardCommon):
    """A question the board asks every time it opens."""

    def _filter(self, field='is_company', model='res.partner'):
        return self.env['dashboard.filter'].create({
            'board_id': self.board.id,
            'model_id': self.env['ir.model']._get(model).id,
            'field_id': self._field(model, field).id,
        })

    def test_a_filter_narrows_every_card_of_its_model(self):
        card = self._card(name='Count')
        bar = [{'model': 'res.partner', 'field': 'is_company', 'value': True}]
        self.assertEqual(card.compute_values('all')['value'], 3)
        self.assertEqual(card.compute_values('all', filters=bar)['value'], 2)

    def test_it_leaves_other_models_whole(self):
        card = self._card(name='Count')
        bar = [{'model': 'res.currency', 'field': 'no_such_field_here', 'value': 'EUR'}]
        self.assertEqual(card.compute_values('all', filters=bar)['value'], 3)

    def test_rubbish_from_the_browser_is_not_a_filter(self):
        card = self._card(name='Count')
        for bar in ([{'field': 'no_such_field', 'value': 1}],
                    [{'field': 'is_company', 'value': ''}],
                    [{'field': '', 'value': 'x'}],
                    ['not a dict'],
                    [{'field': 'country_id', 'value': 'not-an-id'}]):
            self.assertEqual(card.compute_values('all', filters=bar)['value'], 3, bar)

    def test_the_whole_page_follows_it(self):
        self._card(name='Count')
        self._card(name='Split', kind='bar',
                   group_by_field_id=self._field('res.partner', 'type').id)
        bar = [{'model': 'res.partner', 'field': 'is_company', 'value': True}]
        data = self.env['dashboard.board'].read_board(self.board.id, filters=bar)
        self.assertEqual(data['items'][0]['value'], 2)
        self.assertEqual(data['filters'], bar)
        chunk = self.env['dashboard.board'].board_values(
            self.board.id, self.board.item_ids.ids, filters=bar)
        self.assertEqual([v['value'] for v in chunk], [v['value'] for v in data['items']])

    def test_a_drill_through_carries_the_filter(self):
        card = self._card(name='Count')
        bar = [{'model': 'res.partner', 'field': 'is_company', 'value': True}]
        action = self.env['dashboard.item'].drill_action(card.id, filters=bar)
        self.assertIn(('is_company', '=', True), action['domain'])

    def test_the_dropdown_offers_what_the_data_holds(self):
        payload = self._filter('country_id')._payload()
        self.assertEqual(payload['field'], 'country_id')
        self.assertEqual(payload['model'], 'res.partner')
        self.assertTrue(payload['label'])
        booleans = self._filter('is_company')._payload()
        self.assertEqual([o['key'] for o in booleans['options']], ['true', 'false'])

    def test_the_board_hands_its_filters_to_the_page(self):
        self._filter('is_company')
        data = self.env['dashboard.board'].read_board(self.board.id, with_values=False)
        self.assertEqual(len(data['board']['filters']), 1)
        self.assertEqual(data['board']['filters'][0]['field'], 'is_company')


@tagged('post_install', '-at_install')
class TestComboCard(TestDashboardCommon):
    """Bars for one measure, a line for another."""

    def _combo(self, **values):
        return self._card(
            name='Combo', kind='combo',
            group_by_field_id=self._field('res.partner', 'is_company').id,
            aggregate='sum',
            measure_field_id=self._field('res.partner', 'partner_latitude').id,
            measure2_field_id=self._field('res.partner', 'partner_longitude').id,
            **values)

    def test_both_measures_come_back_in_one_group_by(self):
        payload = self._combo().compute_values('all')
        self.assertEqual([p['label'] for p in payload['points']], ['No', 'Yes'])
        self.assertEqual(payload['value'], 1000.0)
        self.assertIn('value2', payload['points'][0])
        self.assertEqual(payload['value2'], sum(p['value2'] for p in payload['points']))

    def test_it_refuses_to_exist_without_its_second_measure(self):
        with self.assertRaises(ValidationError):
            self._card(name='Half a combo', kind='combo', aggregate='sum',
                       measure_field_id=self._field('res.partner', 'partner_latitude').id,
                       group_by_field_id=self._field('res.partner', 'is_company').id)

    def test_it_still_needs_a_field_to_split_by(self):
        with self.assertRaises(ValidationError):
            self._card(name='No split', kind='combo', aggregate='sum',
                       measure_field_id=self._field('res.partner', 'partner_latitude').id,
                       measure2_field_id=self._field('res.partner', 'partner_longitude').id)

    def test_the_second_axis_travels_with_the_card(self):
        combo = self._combo(dual_axis=True)
        self.assertTrue(combo.compute_values('all')['dual_axis'])
        payload = json.loads(self.board.export_boards())
        self.assertTrue(payload['boards'][0]['items'][0]['dual_axis'])


@tagged('post_install', '-at_install')
class TestWebhook(TestDashboardCommon):
    """A threshold that tells an outside service."""

    def _hooked(self, **values):
        values.setdefault('alert_webhook', 'https://example.test/hook')
        return self._card(name='Watched', alert_operator='gt', alert_value=1, **values)

    def _posted(self):
        from unittest.mock import patch
        return patch('odoo.addons.ebshel_dashboard.models.dashboard_item.urlopen')

    def test_crossing_posts_once(self):
        card = self._hooked()
        with self._posted() as opened:
            self.assertTrue(card._check_alert_now())
            self.assertEqual(opened.call_count, 1)
            request = opened.call_args[0][0]
            self.assertEqual(request.full_url, 'https://example.test/hook')
            self.assertEqual(request.get_header('Content-type'), 'application/json')
            body = json.loads(request.data.decode())
            self.assertIn('Watched', body['text'])
            self.assertEqual(body['value'], 3)
            self.assertEqual(body['threshold'], 1)
            self.assertEqual(body['level'], 'warning')
            self.assertTrue(card._check_alert_now())
            self.assertEqual(opened.call_count, 1)

    def test_an_unreachable_hook_is_not_an_error(self):
        card = self._hooked()
        with self._posted() as opened:
            opened.side_effect = OSError('connection refused')
            self.assertTrue(card._check_alert_now())
        self.assertTrue(card.alert_triggered)

    def test_only_a_real_address_is_posted_to(self):
        card = self._hooked(alert_webhook='javascript:alert(1)')
        with self._posted() as opened:
            card._check_alert_now()
            self.assertEqual(opened.call_count, 0)

    def test_the_test_button_needs_an_address(self):
        card = self._card(name='Plain', alert_operator='gt', alert_value=1)
        with self.assertRaises(UserError):
            card.action_test_webhook()

    def test_the_test_button_posts(self):
        card = self._hooked()
        with self._posted() as opened:
            result = card.action_test_webhook()
            self.assertEqual(opened.call_count, 1)
        self.assertEqual(result['params']['type'], 'success')


@tagged('post_install', '-at_install')
class TestMenuWizard(TestDashboardCommon):
    """Where a dashboard sits in the menu, and what it is called there."""

    def _wizard(self, **values):
        return self.env['dashboard.menu.wizard'].with_context(
            default_board_id=self.board.id).create(dict({'board_id': self.board.id}, **values))

    def _root(self):
        return self.env.ref('ebshel_dashboard.menu_dashboard_root')

    def test_it_opens_on_the_boards_own_name(self):
        values = self.env['dashboard.menu.wizard'].with_context(
            default_board_id=self.board.id).default_get(
                ['name', 'placement', 'restrict', 'board_id'])
        self.assertEqual(values['name'], self.board.name)
        self.assertEqual(values['placement'], 'dashboards')

    def test_under_dashboards(self):
        result = self._wizard(name='Sales Today').action_apply()
        self.assertEqual(result['tag'], 'reload')   # menus are read once per session
        menu = self.board.menu_id
        self.assertEqual(menu.name, 'Sales Today')
        self.assertEqual(menu.parent_id, self._root())
        self.assertFalse(menu.web_icon)
        self.assertEqual(self.board.menu_action_id.name, 'Sales Today')
        self.assertEqual(self.board.menu_action_id.params['board_id'], self.board.id)
        self.assertEqual(self.board.menu_path, menu.complete_name)

    def test_under_any_other_menu(self):
        parent = self.env['ir.ui.menu'].create({'name': 'Somewhere Else'})
        self._wizard(placement='menu', parent_id=parent.id, name='Inside').action_apply()
        self.assertEqual(self.board.menu_id.parent_id, parent)

    def test_it_needs_the_parent_it_promised(self):
        with self.assertRaises(UserError):
            self._wizard(placement='menu').action_apply()

    def test_as_an_app_of_its_own(self):
        self._wizard(placement='app', name='Ops').action_apply()
        menu = self.board.menu_id
        self.assertFalse(menu.parent_id)
        self.assertTrue(menu.web_icon)   # a top menu needs a face on the switcher

    def test_applying_twice_moves_the_one_entry(self):
        self._wizard(name='First').action_apply()
        first = self.board.menu_id
        parent = self.env['ir.ui.menu'].create({'name': 'Elsewhere'})
        self._wizard(name='Second', placement='menu', parent_id=parent.id).action_apply()
        self.assertEqual(self.board.menu_id, first, "the entry moved, it was not duplicated")
        self.assertEqual(self.board.menu_id.name, 'Second')
        self.assertEqual(self.board.menu_id.parent_id, parent)
        self.assertEqual(self.env['ir.ui.menu'].search_count(
            [('action', '=', 'ir.actions.client,%s' % self.board.menu_action_id.id)]), 1)

    def test_the_wizard_reopens_on_where_the_menu_is(self):
        parent = self.env['ir.ui.menu'].create({'name': 'Over Here'})
        self._wizard(placement='menu', parent_id=parent.id, name='Moved').action_apply()
        values = self.env['dashboard.menu.wizard'].with_context(
            default_board_id=self.board.id).default_get(['name', 'placement', 'parent_id'])
        self.assertEqual(values['placement'], 'menu')
        self.assertEqual(values['parent_id'], parent.id)
        self.assertEqual(values['name'], 'Moved')

    def test_restricting_it_to_the_boards_groups(self):
        group = self.env['res.groups'].create({'name': 'Menu readers'})
        self.board.group_ids = [(6, 0, group.ids)]
        wizard = self._wizard(restrict=True)
        self.assertTrue(wizard.has_groups)
        wizard.action_apply()
        menu = self.board.menu_id
        field = 'group_ids' if 'group_ids' in menu._fields else 'groups_id'
        self.assertEqual(menu[field], group)
        # And taking the restriction off clears it again.
        self._wizard(restrict=False).action_apply()
        self.assertFalse(self.board.menu_id[field])

    def test_a_personal_board_has_no_business_in_the_menu(self):
        self.board.owner_id = self.env.user
        with self.assertRaises(UserError):
            self._wizard().action_apply()

    def test_only_a_manager_may(self):
        user = new_test_user(self.env, login='menu_reader', groups='base.group_user')
        with self.assertRaises(UserError):
            self.board.with_user(user).action_create_menu()
        with self.assertRaises(UserError):
            self._wizard().with_user(user).action_apply()

    def test_removing_takes_the_action_with_it(self):
        self._wizard().action_apply()
        menu, action = self.board.menu_id, self.board.menu_action_id
        self._wizard().action_remove()
        self.assertFalse(menu.exists())
        self.assertFalse(action.exists())
        self.assertFalse(self.board.menu_id)
        self.assertFalse(self.board.menu_path)


@tagged('post_install', '-at_install')
class TestMoreCharts(TestDashboardCommon):
    """The kinds added on top of the split reader, and the switches beside them."""

    def _split(self, kind, **values):
        return self._card(name=kind, kind=kind,
                          group_by_field_id=self._field('res.partner', 'is_company').id,
                          aggregate='sum',
                          measure_field_id=self._field('res.partner', 'partner_latitude').id,
                          **values)

    def test_a_waterfall_reads_like_a_bar_chart(self):
        payload = self._split('waterfall').compute_values('all')
        self.assertEqual([p['label'] for p in payload['points']], ['No', 'Yes'])
        self.assertEqual(payload['value'], 1000.0)

    def test_a_pareto_reads_like_a_bar_chart(self):
        payload = self._split('pareto').compute_values('all')
        self.assertEqual(payload['value'], 1000.0)
        self.assertTrue(all('share' in point for point in payload['points']))

    def test_a_bubble_carries_two_measures_and_a_count(self):
        card = self._split(
            'bubble', measure2_field_id=self._field('res.partner', 'partner_longitude').id)
        payload = card.compute_values('all')
        point = payload['points'][0]
        for key in ('value', 'value2', 'count'):
            self.assertIn(key, point)
        self.assertEqual(sum(p['count'] for p in payload['points']), 3)

    def test_a_bubble_needs_its_second_measure(self):
        with self.assertRaises(ValidationError):
            self._split('bubble')

    def test_the_switches_travel_with_the_card(self):
        card = self._card(name='Stacked', kind='stacked',
                          group_by_field_id=self._field('res.partner', 'is_company').id,
                          stack_field_id=self._field('res.partner', 'type').id,
                          stack_percent=True)
        self.assertTrue(card.compute_values('all')['stack_percent'])
        line = self._card(name='Steps', kind='line', stepped=True,
                          date_field_id=self._field('res.partner', 'create_date').id)
        self.assertTrue(line.compute_values('all')['stepped'])
        payload = json.loads(self.board.export_boards())
        exported = {item['name']: item for item in payload['boards'][0]['items']}
        self.assertTrue(exported['Stacked']['stack_percent'])
        self.assertTrue(exported['Steps']['stepped'])


@tagged('post_install', '-at_install')
class TestRowFormatting(TestDashboardCommon):
    """Colour a row when it meets a rule, and the switches around it."""

    def _list(self, **values):
        return self._card(name='Top', kind='list', limit=10, aggregate='sum',
                          measure_field_id=self._field('res.partner', 'partner_latitude').id,
                          **values)

    def test_a_number_rule_flags_the_rows_above_it(self):
        card = self._list(cf_field_id=self._field('res.partner', 'partner_latitude').id,
                          cf_operator='gt', cf_value='250')
        rows = card.compute_values('all')['rows']
        flagged = {row['label']: row['flagged'] for row in rows}
        self.assertEqual(flagged, {'Gamma': True, 'Beta': True, 'Alpha': False})

    def test_a_text_rule_matches_what_it_contains(self):
        card = self._list(cf_field_id=self._field('res.partner', 'name').id,
                          cf_operator='contains', cf_value='amm')
        flagged = {row['label']: row['flagged'] for row in card.compute_values('all')['rows']}
        self.assertTrue(flagged['Gamma'])
        self.assertFalse(flagged['Alpha'])

    def test_set_and_unset_need_no_value(self):
        card = self._list(cf_field_id=self._field('res.partner', 'function').id,
                          cf_operator='unset')
        self.assertTrue(all(row['flagged'] for row in card.compute_values('all')['rows']))
        card.cf_operator = 'set'
        self.assertFalse(any(row['flagged'] for row in card.compute_values('all')['rows']))

    def test_a_rule_that_cannot_be_read_flags_nothing(self):
        card = self._list(cf_field_id=self._field('res.partner', 'partner_latitude').id,
                          cf_operator='gt', cf_value='not a number')
        self.assertFalse(any(row['flagged'] for row in card.compute_values('all')['rows']))
        plain = self._list()
        self.assertFalse(any(row['flagged'] for row in plain.compute_values('all')['rows']))

    def test_the_look_of_the_rows_travels(self):
        card = self._list(rank_medals=True, show_bars=False, cf_color='amber')
        payload = card.compute_values('all')
        self.assertTrue(payload['rank_medals'])
        self.assertFalse(payload['show_bars'])
        self.assertEqual(payload['cf_color'], 'amber')


@tagged('post_install', '-at_install')
class TestRemoving(TestDashboardCommon):
    """Taking a card, or a whole dashboard, away."""

    def test_a_card_can_be_removed_from_the_board(self):
        keep = self._card(name='Keep')
        gone = self._card(name='Gone')
        self.env['dashboard.item'].delete_card(gone.id)
        self.assertFalse(gone.exists())
        self.assertEqual(self.board.item_ids, keep)

    def test_removing_a_card_that_is_already_gone_is_not_an_error(self):
        card = self._card(name='Gone')
        card.unlink()
        self.assertTrue(self.env['dashboard.item'].delete_card(card.id))

    def test_a_reader_removes_nothing(self):
        card = self._card(name='Theirs')
        reader = new_test_user(self.env, login='remove_reader', groups='base.group_user')
        with self.assertRaises(AccessError):
            card.with_user(reader).unlink()
        self.assertTrue(card.exists())

    def test_deleting_a_board_takes_its_cards_with_it(self):
        card = self._card(name='On it')
        result = self.env['dashboard.board'].delete_board(self.board.id)
        self.assertEqual(result['name'], 'Test Board')
        self.assertFalse(self.board.exists())
        self.assertFalse(card.exists())

    def test_deleting_a_board_takes_its_menu_with_it(self):
        self.board._install_menu(name='Menu board')
        menu, action = self.board.menu_id, self.board.menu_action_id
        self.assertTrue(menu.exists())
        self.board.unlink()
        self.assertFalse(menu.exists(), "a menu pointing at nothing is worse than none")
        self.assertFalse(action.exists())

    def test_deleting_says_which_board_comes_next(self):
        """The reader is standing on the board that just went."""
        self.env['dashboard.board'].create({'name': 'The other one', 'period': 'all'})
        deleted = self.board.id
        result = self.env['dashboard.board'].delete_board(deleted)
        self.assertTrue(result['next'])
        self.assertNotEqual(result['next'], deleted)
        self.assertTrue(self.env['dashboard.board'].browse(result['next']).exists())

    def test_a_reader_deletes_nothing(self):
        reader = new_test_user(self.env, login='delete_reader', groups='base.group_user')
        with self.assertRaises(AccessError):
            self.board.with_user(reader).unlink()
        self.assertTrue(self.board.exists())


@tagged('post_install', '-at_install')
class TestDiscardArrangement(TestDashboardCommon):
    """Arranging writes as you drag, so discarding puts it back."""

    def test_the_layout_goes_back_where_it_was(self):
        first = self._card(name='First', width=3, sequence=1)
        second = self._card(name='Second', width=4, sequence=2)
        before = [{'id': item.id, 'sequence': item.sequence, 'width': item.width,
                   'height': item.height, 'tab_id': False}
                  for item in (first | second)]
        # ...the reader drags things about...
        self.board.arrange_items([second.id, first.id])
        self.board.resize_item(first.id, 9)
        self.assertEqual(first.width, 9)
        self.assertGreater(first.sequence, second.sequence)
        # ...and then thinks better of it.
        self.board.restore_layout(before)
        self.assertEqual(first.width, 3)
        self.assertLess(first.sequence, second.sequence)

    def test_it_puts_a_card_back_on_its_tab(self):
        tab = self.env['dashboard.tab'].create({'board_id': self.board.id, 'name': 'Ops'})
        card = self._card(name='Tabbed', tab_id=tab.id)
        before = [{'id': card.id, 'sequence': card.sequence, 'width': card.width,
                   'height': card.height, 'tab_id': tab.id}]
        self.board.move_to_tab(card.id, False)
        self.assertFalse(card.tab_id)
        self.board.restore_layout(before)
        self.assertEqual(card.tab_id, tab)

    def test_a_card_from_another_board_is_ignored(self):
        other = self.env['dashboard.board'].create({'name': 'Elsewhere', 'period': 'all'})
        stranger = self._card(name='Stranger', board_id=other.id, width=4)
        self.board.restore_layout([{'id': stranger.id, 'width': 12}])
        self.assertEqual(stranger.width, 4)

    def test_a_reader_cannot_restore_anything(self):
        reader = new_test_user(self.env, login='discard_reader', groups='base.group_user')
        with self.assertRaises(UserError):
            self.board.with_user(reader).restore_layout([])


@tagged('post_install', '-at_install')
class TestSubvalues(TestDashboardCommon):
    """The small numbers under a big one."""

    def _kpi_with_subs(self, **values):
        card = self._card(name='Contacts', **values)
        self.env['dashboard.item.subvalue'].create([
            {'item_id': card.id, 'name': 'Companies', 'domain': "[('is_company', '=', True)]",
             'color': 'teal'},
            {'item_id': card.id, 'name': 'People', 'domain': "[('is_company', '=', False)]",
             'color': 'amber', 'show_share': False},
        ])
        return card

    def test_they_sit_under_the_number(self):
        payload = self._kpi_with_subs().compute_values('all')
        self.assertEqual(payload['value'], 3)
        subs = payload['subvalues']
        self.assertEqual([s['label'] for s in subs], ['Companies', 'People'])
        self.assertEqual([s['value'] for s in subs], [2.0, 1.0])
        self.assertEqual(subs[0]['share'], round(2 / 3 * 100, 1))
        self.assertIsNone(subs[1]['share'], "a sub-value can decline the share")
        self.assertEqual(subs[0]['color'], 'teal')

    def test_they_keep_the_cards_own_filter(self):
        """The card counts tagged partners only; so do its sub-values."""
        card = self._kpi_with_subs()
        self.env['res.partner'].create({'name': 'Untagged Co', 'is_company': True})
        payload = card.compute_values('all')
        self.assertEqual(payload['value'], 3)
        self.assertEqual(payload['subvalues'][0]['value'], 2.0)

    def test_they_follow_the_period_and_the_bar(self):
        card = self._kpi_with_subs(date_field_id=self._field('res.partner', 'create_date').id)
        gone = fields.Date.to_string(fields.Date.subtract(self.today, days=30))
        empty = card.compute_values({'start': gone, 'end': gone})
        self.assertEqual([s['value'] for s in empty['subvalues']], [0.0, 0.0])
        bar = [{'model': 'res.partner', 'field': 'is_company', 'value': True}]
        narrowed = card.compute_values('all', filters=bar)
        self.assertEqual([s['value'] for s in narrowed['subvalues']], [2.0, 0.0])

    def test_a_sub_value_can_measure_a_field(self):
        card = self._card(name='Latitude', aggregate='sum',
                          measure_field_id=self._field('res.partner', 'partner_latitude').id)
        self.env['dashboard.item.subvalue'].create({
            'item_id': card.id, 'name': 'Companies', 'domain': "[('is_company', '=', True)]",
            'aggregate': 'sum', 'measure_field_id': self._field('res.partner', 'partner_latitude').id})
        payload = card.compute_values('all')
        self.assertEqual(payload['value'], 1000.0)
        self.assertEqual(payload['subvalues'][0]['value'], 400.0)
        self.assertEqual(payload['subvalues'][0]['share'], 40.0)

    def test_a_measured_sub_value_needs_its_field(self):
        card = self._card(name='Count')
        with self.assertRaises(ValidationError):
            self.env['dashboard.item.subvalue'].create({
                'item_id': card.id, 'name': 'Broken', 'aggregate': 'sum'})

    def test_a_bad_filter_is_refused_at_once(self):
        card = self._card(name='Count')
        with self.assertRaises(ValidationError):
            self.env['dashboard.item.subvalue'].create({
                'item_id': card.id, 'name': 'Broken', 'domain': "this is not a domain"})

    def test_a_sub_value_opens_its_own_records(self):
        card = self._kpi_with_subs()
        sub = card.subvalue_ids[0]
        action = self.env['dashboard.item'].drill_action(card.id, part={'subvalue': sub.id})
        found = self.env['res.partner'].search(action['domain'])
        self.assertEqual(found, self.partners.filtered('is_company'))

    def test_they_travel_with_the_card(self):
        self._kpi_with_subs()
        payload = json.loads(self.board.export_boards())
        exported = payload['boards'][0]['items'][0]
        self.assertEqual([s['name'] for s in exported['subvalues']], ['Companies', 'People'])
        result = self.env['dashboard.board'].import_boards(payload)
        imported = self.env['dashboard.board'].browse(result['boards'])
        self.assertEqual(imported.item_ids.subvalue_ids.mapped('name'), ['Companies', 'People'])
        self.assertEqual(imported.item_ids.subvalue_ids[0].color, 'teal')
        copy = self.board.copy()
        self.assertEqual(len(copy.item_ids.subvalue_ids), 2)

    def test_one_page_load_asks_each_question_once(self):
        """Two sub-values that ask the same thing share one query."""
        card = self._card(name='Count')
        self.env['dashboard.item.subvalue'].create([
            {'item_id': card.id, 'name': 'A', 'domain': "[('is_company', '=', True)]"},
            {'item_id': card.id, 'name': 'B', 'domain': "[('is_company', '=', True)]"},
        ])
        with self.assertQueryCount(__system__=12):
            payload = card.compute_values('all')
        self.assertEqual([s['value'] for s in payload['subvalues']], [2.0, 2.0])


@tagged('post_install', '-at_install')
class TestCompanies(TestDashboardCommon):
    """A board or a card for some companies, or for all of them."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.main = cls.env.company
        cls.other = cls.env['res.company'].create({'name': 'Elsewhere Ltd'})
        # Tests run as the superuser, whom record rules do not touch: the
        # switcher is simulated for a real user, the administrator.
        cls.admin = cls.env.ref('base.user_admin')
        cls.admin.write({'company_ids': [(4, cls.other.id)]})

    def _as(self, company):
        """The environment of a real user with that company active in the switcher."""
        return self.env(user=self.admin.id,
                        context=dict(self.env.context, allowed_company_ids=[company.id]))

    def test_no_company_means_everywhere(self):
        Board = self.env['dashboard.board']
        for company in (self.main, self.other):
            listed = self._as(company)['dashboard.board'].get_boards()
            self.assertIn(self.board.id, [b['id'] for b in listed])

    def test_a_board_for_one_company_stays_there(self):
        self.board.company_ids = [(6, 0, self.other.ids)]
        listed_here = self._as(self.main)['dashboard.board'].get_boards()
        self.assertNotIn(self.board.id, [b['id'] for b in listed_here])
        listed_there = self._as(self.other)['dashboard.board'].get_boards()
        self.assertIn(self.board.id, [b['id'] for b in listed_there])
        self.assertEqual([b for b in listed_there if b['id'] == self.board.id][0]['companies'],
                         ['Elsewhere Ltd'])

    def test_the_rule_hides_it_from_the_list_view_too(self):
        self.board.company_ids = [(6, 0, self.other.ids)]
        found = self._as(self.main)['dashboard.board'].search([('id', '=', self.board.id)])
        self.assertFalse(found)
        found = self._as(self.other)['dashboard.board'].search([('id', '=', self.board.id)])
        self.assertEqual(found, self.board)

    def test_a_card_for_one_company_is_left_off_the_page_elsewhere(self):
        everywhere = self._card(name='Everywhere')
        pinned = self._card(name='Pinned', company_ids=[(6, 0, self.other.ids)])
        here = self._as(self.main)['dashboard.board'].read_board(self.board.id, with_values=False)
        self.assertEqual([i['name'] for i in here['items']], ['Everywhere'])
        # One transaction, two companies: a one2many's cache is keyed by the
        # user, not by the active companies. A real switch reloads the page;
        # the test has to forget the first read by hand.
        self.env.invalidate_all()
        there = self._as(self.other)['dashboard.board'].read_board(self.board.id, with_values=False)
        self.assertEqual({i['name'] for i in there['items']}, {'Everywhere', 'Pinned'})
        # ...and editors are not exempt: it is a fact about the company.
        self.assertTrue(self.board._can_edit())
        self.assertFalse(self._as(self.main)['dashboard.item'].browse(pinned.id)._visible_to_reader())

    def test_the_card_rule_matches_the_boards(self):
        pinned = self._card(name='Pinned', company_ids=[(6, 0, self.other.ids)])
        self.assertFalse(self._as(self.main)['dashboard.item'].search([('id', '=', pinned.id)]))
        self.assertEqual(self._as(self.other)['dashboard.item'].search([('id', '=', pinned.id)]), pinned)

    def test_the_old_single_company_is_carried_over(self):
        """What the 1.2 migration does, on a board that still has the 1.1 column."""
        self.env.cr.execute("UPDATE dashboard_board SET company_id = %s WHERE id = %s",
                            (self.other.id, self.board.id))
        self.board.invalidate_recordset(['company_id', 'company_ids'])
        self.assertFalse(self.board.company_ids)
        from odoo.modules.module import get_manifest, get_module_path
        import importlib.util
        # The folder is named after the version the manifest declares - and
        # that differs between the cores the module ships for.
        version = get_manifest('ebshel_dashboard')['version']
        path = '%s/migrations/%s/post-migrate.py' % (get_module_path('ebshel_dashboard'), version)
        spec = importlib.util.spec_from_file_location('post_migrate_test', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.migrate(self.env.cr, '19.0.1.1.0')
        module.migrate(self.env.cr, '19.0.1.1.0')   # running twice adds nothing
        self.board.invalidate_recordset(['company_ids'])
        self.assertEqual(self.board.company_ids, self.other)


@tagged('post_install', '-at_install')
class TestMoreKinds(TestDashboardCommon):
    """Treemap, lollipop, the calendar, sideways stacks, and the unusual badge."""

    def test_a_treemap_and_a_lollipop_read_like_a_bar_chart(self):
        for kind in ('treemap', 'lollipop'):
            card = self._card(name=kind, kind=kind,
                              group_by_field_id=self._field('res.partner', 'is_company').id)
            payload = card.compute_values('all')
            self.assertEqual({p['label'] for p in payload['points']}, {'Yes', 'No'}, kind)
            self.assertEqual(payload['value'], 3, kind)
            self.assertTrue(all('share' in p for p in payload['points']), kind)

    def test_a_calendar_is_one_cell_per_day(self):
        card = self._card(name='Calendar', kind='calendar', group_by_interval='month',
                          date_field_id=self._field('res.partner', 'create_date').id)
        month = card.compute_values('this_month')
        keys = [p['key'] for p in month['points']]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertTrue(28 <= len(keys) <= 31, len(keys))
        self.assertEqual(sum(p['value'] for p in month['points']), 3)
        year = card.compute_values('all')
        self.assertEqual(len(year['points']), 364)
        self.assertEqual(year['points'][-1]['key'], fields.Date.to_string(self.today))

    def test_a_calendar_needs_its_date_field(self):
        with self.assertRaises(ValidationError):
            self._card(name='No date', kind='calendar')

    def test_sideways_stacks_travel(self):
        card = self._card(name='Sideways', kind='stacked', stack_horizontal=True,
                          group_by_field_id=self._field('res.partner', 'is_company').id,
                          stack_field_id=self._field('res.partner', 'type').id)
        self.assertTrue(card.compute_values('all')['stack_horizontal'])
        payload = json.loads(self.board.export_boards())
        self.assertTrue(payload['boards'][0]['items'][0]['stack_horizontal'])

    def _history(self, card, values):
        Snapshot = self.env['dashboard.snapshot']
        for offset, value in enumerate(values, start=1):
            Snapshot._record(card, value, day=fields.Date.subtract(self.today, days=offset))

    def test_a_number_out_of_line_with_its_past_is_called_out(self):
        card = self._card(name='Count')      # today's value is 3
        self._history(card, [30, 31, 29, 30, 32, 28, 30, 31, 29, 30])
        anomaly = card.compute_values('all')['anomaly']
        self.assertEqual(anomaly['direction'], 'below')
        self.assertGreaterEqual(abs(anomaly['z']), 2)
        self.assertEqual(anomaly['days'], 10)
        self.assertAlmostEqual(anomaly['mean'], 30.0)

    def test_a_number_in_line_with_its_past_is_not(self):
        card = self._card(name='Count')
        self._history(card, [3, 4, 2, 3, 3, 4, 2, 3])
        self.assertNotIn('anomaly', card.compute_values('all'))

    def test_too_little_past_says_nothing(self):
        card = self._card(name='Count')
        self._history(card, [30, 30, 30])
        self.assertNotIn('anomaly', card.compute_values('all'))

    def test_a_flat_past_says_nothing(self):
        """Ten identical days give a sigma of zero: no division, no badge."""
        card = self._card(name='Count')
        self._history(card, [3] * 10)
        self.assertNotIn('anomaly', card.compute_values('all'))


@tagged('post_install', '-at_install')
class TestNumberSystems(TestDashboardCommon):
    """How a number is written, and the shorthands a filter understands."""

    def test_the_system_travels_with_the_card(self):
        card = self._card(name='Indian', number_system='indian')
        self.assertEqual(card.compute_values('all')['number_system'], 'indian')
        payload = json.loads(self.board.export_boards())
        self.assertEqual(payload['boards'][0]['items'][0]['number_system'], 'indian')

    def test_the_pdf_writes_lakhs_and_crores(self):
        from odoo.addons.ebshel_dashboard.models.dashboard_pdf import DashboardPdf
        board = {'name': 'Numbers', 'period': 'all'}
        composer = DashboardPdf(self.env, board, [])
        indian = {'number_system': 'indian', 'aggregate': 'sum', 'kind': 'kpi', 'digits': 0}
        self.assertEqual(composer._format(2_50_00_000, indian), '2.50 Cr')
        self.assertEqual(composer._format(3_50_000, indian), '3.50 L')
        self.assertEqual(composer._format(4200, indian), '4,200')
        short = dict(indian, number_system='short')
        self.assertEqual(composer._format(4200, short), '4.2k')
        self.assertEqual(composer._format(4_200_000, short), '4.2M')
        plain = dict(indian, number_system='plain')
        self.assertEqual(composer._format(4_200_000, plain), '4,200,000')
        auto = dict(indian, number_system='auto')
        self.assertEqual(composer._format(4200, auto), '4,200')
        self.assertEqual(composer._format(420_000, auto), '420.0k')

    def test_a_filter_understands_the_shorthands(self):
        card = self._card(name='Mine', domain="[('user_id', '=', %UID)]")
        self.assertEqual(card._eval_domain(card.domain), [('user_id', '=', self.env.uid)])
        company = self._card(name='Ours', domain="[('company_id', '=', %MYCOMPANY)]")
        self.assertEqual(company._eval_domain(company.domain),
                         [('company_id', '=', self.env.company.id)])
        today = self._card(name='Today', domain="[('create_date', '>=', %TODAY)]")
        self.assertEqual(today._eval_domain(today.domain),
                         [('create_date', '>=', fields.Date.context_today(today))])

    def test_the_shorthands_reach_the_numbers(self):
        self.partners[0].user_id = self.env.user
        card = self._card(name='Mine',
                          domain="[('user_id', '=', %%UID), ('category_id', 'in', [%s])]" % self.tag.id)
        self.assertEqual(card.compute_values('all')['value'], 1)


@tagged('post_install', '-at_install')
class TestSpreadsheet(TestDashboardCommon):
    """The numbers behind the pictures, as a file that carries on."""

    def _book(self, action):
        self.assertEqual(action['type'], 'ir.actions.act_url')
        attachment = self.env['ir.attachment'].browse(
            int(action['url'].split('/web/content/')[1].split('?')[0]))
        self.assertEqual(attachment.mimetype,
                         'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        return attachment, attachment.raw

    def test_a_board_downloads_as_a_spreadsheet(self):
        self._card(name='Count')
        self._card(name='Split', kind='bar',
                   group_by_field_id=self._field('res.partner', 'is_company').id)
        attachment, raw = self._book(self.board.download_xlsx())
        self.assertEqual(attachment.name, 'Test_Board.xlsx')
        self.assertTrue(raw.startswith(b'PK'), "an xlsx is a zip")
        self.assertGreater(len(raw), 2000)

    def test_one_card_is_named_after_itself(self):
        self._card(name='Count')
        card = self._card(name='Only me', kind='bar',
                          group_by_field_id=self._field('res.partner', 'type').id)
        attachment, raw = self._book(self.board.download_xlsx(item_ids=[card.id]))
        self.assertEqual(attachment.name, 'Only_me.xlsx')
        self.assertTrue(raw.startswith(b'PK'))

    def test_every_kind_writes_a_sheet(self):
        self._card(name='Number')
        self._card(name='Split', kind='bar',
                   group_by_field_id=self._field('res.partner', 'is_company').id)
        self._card(name='Crossed', kind='stacked',
                   group_by_field_id=self._field('res.partner', 'is_company').id,
                   stack_field_id=self._field('res.partner', 'type').id)
        self._card(name='Top', kind='list', limit=5)
        _attachment, raw = self._book(self.board.download_xlsx())
        import io, zipfile
        with zipfile.ZipFile(io.BytesIO(raw)) as book:
            workbook = book.read('xl/workbook.xml').decode()
        for name in ('Number', 'Split', 'Crossed', 'Top'):
            self.assertIn(name, workbook)

    def test_two_cards_of_the_same_name_get_two_sheets(self):
        self._card(name='Same')
        self._card(name='Same')
        _attachment, raw = self._book(self.board.download_xlsx())
        import io, zipfile
        with zipfile.ZipFile(io.BytesIO(raw)) as book:
            workbook = book.read('xl/workbook.xml').decode()
        self.assertIn('Same (2)', workbook)

    def test_a_name_excel_would_refuse_is_cleaned(self):
        self._card(name='Rate [%] / month: 2026')
        _attachment, raw = self._book(self.board.download_xlsx())
        self.assertTrue(raw.startswith(b'PK'))

    def test_nothing_to_export_says_so(self):
        with self.assertRaises(UserError):
            self.board.download_xlsx()


@tagged('post_install', '-at_install')
class TestRenaming(TestDashboardCommon):
    """Renaming a dashboard from the page that shows it."""

    def test_a_board_is_renamed_in_place(self):
        result = self.board.rename('  Sales Today  ')
        self.assertEqual(result['name'], 'Sales Today')
        self.assertFalse(result['menu'])
        self.assertEqual(self.board.name, 'Sales Today')

    def test_its_menu_follows_the_new_name(self):
        self.board._install_menu(name='Old name')
        menu, action = self.board.menu_id, self.board.menu_action_id
        result = self.board.rename('New name')
        self.assertTrue(result['menu'])
        self.assertEqual(menu.name, 'New name')
        self.assertEqual(action.name, 'New name')

    def test_a_dashboard_needs_a_name(self):
        with self.assertRaises(UserError):
            self.board.rename('   ')

    def test_a_reader_renames_nothing(self):
        reader = new_test_user(self.env, login='rename_reader', groups='base.group_user')
        with self.assertRaises(UserError):
            self.board.with_user(reader).rename('Hijacked')
        self.assertEqual(self.board.name, 'Test Board')


@tagged('post_install', '-at_install')
class TestMenuVisibility(TestDashboardCommon):
    """A reader is offered the dashboard, not the workshop."""

    def _menu_names(self, user):
        """The names the web client would draw under *our* app for this user.

        `search` on ir.ui.menu does not apply group visibility - the menu tree
        the browser gets does, so that is what is asked here. It is walked from
        our own root: half the apps in a database have a "Configuration".
        """
        loaded = self.env['ir.ui.menu'].with_user(user).load_menus(False)
        root_id = self.env.ref('ebshel_dashboard.menu_dashboard_root').id
        names = set()
        pending = [root_id]
        while pending:
            menu = loaded.get(pending.pop())
            if not isinstance(menu, dict):
                continue
            if menu.get('name'):
                names.add(menu['name'])
            pending.extend(menu.get('children') or [])
        return names

    def test_a_reader_sees_the_dashboard_only(self):
        reader = new_test_user(self.env, login='menu_only_reader', groups='base.group_user')
        names = self._menu_names(reader)
        self.assertIn('Dashboard', names)
        for hidden in ('Build from a Model', 'Configuration', 'Cards', 'Card History',
                       'Import a Dashboard'):
            self.assertNotIn(hidden, names, hidden)

    def test_a_manager_sees_the_workshop(self):
        manager = new_test_user(
            self.env, login='menu_only_builder',
            groups='base.group_user,ebshel_dashboard.group_dashboard_manager')
        names = self._menu_names(manager)
        for shown in ('Dashboard', 'Build from a Model', 'Configuration', 'Cards',
                      'Card History', 'Import a Dashboard'):
            self.assertIn(shown, names, shown)


class TestDrawingOptions(TestDashboardCommon):
    """The drawing options of a card: defaults that change nothing, values
    that reach the client, travel with an export and hold in a preview."""

    def _split(self):
        return self._field('res.partner', 'is_company').id

    def test_defaults_keep_the_old_picture(self):
        payload = self._card(kind='bar', group_by_field_id=self._split()).compute_values()
        self.assertEqual(payload['palette'], 'board')
        self.assertEqual(payload['bar_shape'], 'rounded')
        self.assertTrue(payload['show_grid'])
        self.assertFalse(payload['axis_titles'])
        self.assertFalse(payload['free_axis'])
        self.assertFalse(payload['center_total'])
        self.assertEqual(payload['emphasis'], 'none')

    def test_options_reach_the_client(self):
        card = self._card(
            kind='donut', group_by_field_id=self._split(), palette='warm', show_grid=False,
            center_total=True, emphasis='max', bar_shape='pill', axis_titles=True, free_axis=True)
        payload = card.compute_values()
        self.assertEqual(payload['palette'], 'warm')
        self.assertEqual(payload['bar_shape'], 'pill')
        self.assertFalse(payload['show_grid'])
        self.assertTrue(payload['axis_titles'])
        self.assertTrue(payload['free_axis'])
        self.assertTrue(payload['center_total'])
        self.assertEqual(payload['emphasis'], 'max')

    def test_options_travel_with_an_export(self):
        self._card(name='Warm', kind='bar', group_by_field_id=self._split(),
                   palette='warm', bar_shape='pill', show_grid=False, emphasis='last')
        result = self.env['dashboard.board'].import_boards(self.board.export_boards())
        imported = self.env['dashboard.board'].browse(result['boards']).item_ids
        self.assertEqual(imported.palette, 'warm')
        self.assertEqual(imported.bar_shape, 'pill')
        self.assertFalse(imported.show_grid)
        self.assertEqual(imported.emphasis, 'last')

    def test_preview_honours_the_options(self):
        payload = self.env['dashboard.item'].preview_values({
            'name': 'Draft', 'kind': 'bar', 'aggregate': 'count', 'model_id': self.partner_model.id,
            'domain': self.base_domain, 'group_by_field_id': self._split(),
            'palette': 'cool', 'show_grid': False, 'emphasis': 'last'})
        self.assertEqual(payload['palette'], 'cool')
        self.assertFalse(payload['show_grid'])
        self.assertEqual(payload['emphasis'], 'last')
        self.assertEqual(len(payload['points']), 2)

    def test_preview_takes_list_columns_as_a_command(self):
        """The form sends its x2many as the one command new() understands."""
        payload = self.env['dashboard.item'].preview_values({
            'name': 'Draft', 'kind': 'list', 'aggregate': 'count', 'model_id': self.partner_model.id,
            'domain': self.base_domain,
            'list_field_ids': [[6, 0, [self._field('res.partner', 'email').id]]]})
        self.assertEqual([column['name'] for column in payload['columns']], ['email'])
        self.assertEqual(len(payload['rows']), 3)

    def test_the_form_offers_the_new_controls(self):
        arch = self.env['dashboard.item'].get_view(view_type='form')['arch']
        for needle in ('dashboard_setup_chips', 'dashboard_palette_picker', 'dashboard_number_sample',
                       'name="chart"', 'o_dbf_seg', 'o_dbf_opt', 'name="emphasis"', 'name="bar_shape"'):
            self.assertIn(needle, arch)
        # Every kind that needs a split gets the box that sets it.
        for kind in ('lollipop', 'waterfall', 'pareto', 'treemap', 'bubble'):
            self.assertIn("'%s'" % kind, arch.split('name="split"')[1].split('invisible=')[1].split('>')[0])


class TestBoardIcon(TestDashboardCommon):
    """The dashboard's icon, changed from the page that shows it."""

    def test_the_owner_changes_the_icon(self):
        self.assertEqual(self.board.icon, 'fa-tachometer')
        self.assertEqual(self.board.set_icon('fa-line-chart'), {'icon': 'fa-line-chart'})
        self.assertEqual(self.board.icon, 'fa-line-chart')

    def test_only_a_class_is_accepted(self):
        for bad in ('', '   ', 'fa line-chart', '<script>', 'line-chart',
                    'fa-x" onload="alert(1)'):
            with self.assertRaises(UserError):
                self.board.set_icon(bad)
        self.assertEqual(self.board.icon, 'fa-tachometer')

    def test_a_reader_cannot(self):
        # new_test_user, not a plain create: the field naming the user's groups
        # is `group_ids` on 19.0 and `groups_id` on 18.0.
        reader = new_test_user(self.env, login='icon_reader', groups='base.group_user')
        with self.assertRaises(UserError):
            self.board.with_user(reader).set_icon('fa-bug')
