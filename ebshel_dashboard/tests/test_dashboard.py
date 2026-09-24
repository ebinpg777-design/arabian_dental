# -*- coding: utf-8 -*-
"""Tests for Dynamic Dashboards.

The engine is a small set of promises, and each one is tested here:

* a card counts what its filter says, under the reader's own rights;
* a period restricts a card only where the card names a date field;
* a chart's parts and the records behind them are the *same* domain;
* a personal board belongs to one person and a shared one to everybody.
"""
import base64
import json
from datetime import date

from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestDashboardCommon(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner_model = cls.env['ir.model']._get('res.partner')
        cls.board = cls.env['dashboard.board'].create({
            'name': 'Test Board',
            'period': 'all',
        })
        cls.tag_model = cls.env['ir.model']._get('res.partner.category')
        # A known, isolated population: every card here filters on this tag, so
        # nothing the database happens to contain can move a number.
        cls.tag = cls.env['res.partner.category'].create({'name': 'DashboardTest'})
        cls.today = fields.Date.context_today(cls.env['dashboard.board'])
        cls.partners = cls.env['res.partner'].create([
            {'name': 'Alpha', 'category_id': [(4, cls.tag.id)], 'is_company': True,
             'partner_latitude': 100.0},
            {'name': 'Beta', 'category_id': [(4, cls.tag.id)], 'is_company': True,
             'partner_latitude': 300.0},
            {'name': 'Gamma', 'category_id': [(4, cls.tag.id)], 'is_company': False,
             'partner_latitude': 600.0},
        ])
        cls.base_domain = f"[('category_id', 'in', [{cls.tag.id}])]"

    def _field(self, model, name):
        return self.env['ir.model.fields']._get(model, name)

    def _card(self, **values):
        base = {
            'name': 'Card',
            'board_id': self.board.id,
            'model_id': self.partner_model.id,
            'domain': self.base_domain,
            'kind': 'kpi',
            'aggregate': 'count',
        }
        base.update(values)
        return self.env['dashboard.item'].create(base)


class TestDashboardValues(TestDashboardCommon):

    def test_count_card(self):
        """A plain card counts exactly what its filter says."""
        card = self._card()
        payload = card.compute_values('all')
        self.assertEqual(payload['value'], 3)
        self.assertFalse(payload.get('error'))

    def test_sum_card(self):
        """An aggregate card sums the field it was given."""
        card = self._card(
            aggregate='sum',
            measure_field_id=self._field('res.partner', 'partner_latitude').id,
        )
        self.assertEqual(card.compute_values('all')['value'], 1000.0)

    def test_average_card(self):
        card = self._card(
            aggregate='avg',
            measure_field_id=self._field('res.partner', 'partner_latitude').id,
        )
        self.assertAlmostEqual(card.compute_values('all')['value'], 1000.0 / 3)

    def test_max_and_min(self):
        values = {}
        for aggregate in ('max', 'min'):
            card = self._card(
                aggregate=aggregate,
                measure_field_id=self._field('res.partner', 'partner_latitude').id,
            )
            values[aggregate] = card.compute_values('all')['value']
        self.assertEqual(values['max'], 600.0)
        self.assertEqual(values['min'], 100.0)

    def test_domain_narrows_the_card(self):
        card = self._card(
            domain=f"[('category_id', 'in', [{self.tag.id}]), ('is_company', '=', True)]")
        self.assertEqual(card.compute_values('all')['value'], 2)

    def test_bar_card_splits_by_field(self):
        """A bar card comes back with one point per group, biggest first."""
        card = self._card(
            kind='bar',
            group_by_field_id=self._field('res.partner', 'is_company').id,
        )
        payload = card.compute_values('all')
        labels = {point['label']: point['value'] for point in payload['points']}
        self.assertEqual(labels, {'Yes': 2.0, 'No': 1.0})
        self.assertEqual(payload['points'][0]['value'], 2.0)

    def test_pie_folds_the_tail(self):
        """Anything past the limit becomes one "Others" entry, not a long tail."""
        extra = self.env['res.partner.category'].create({'name': 'DashboardTest2'})
        self.env['res.partner'].create([
            {'name': f'Extra {index}', 'category_id': [(4, extra.id)],
             'function': f'Role {index}'}
            for index in range(5)
        ])
        card = self._card(
            kind='pie',
            domain=f"[('category_id', 'in', [{extra.id}])]",
            group_by_field_id=self._field('res.partner', 'function').id,
            limit=2,
        )
        payload = card.compute_values('all')
        self.assertEqual(len(payload['points']), 3)
        folded = payload['points'][-1]
        self.assertTrue(folded['folded'])
        self.assertEqual(folded['count'], 3)

    def test_line_card_fills_gaps(self):
        """Inside a period a line draws every bucket, empty ones included."""
        card = self._card(
            kind='line',
            date_field_id=self._field('res.partner', 'create_date').id,
            group_by_interval='month',
        )
        payload = card.compute_values('this_year')
        self.assertEqual(len(payload['points']), 12)
        self.assertTrue(all('value' in point for point in payload['points']))
        self.assertEqual(sum(point['value'] for point in payload['points']), 3)

    def test_list_card_returns_records(self):
        card = self._card(
            kind='list',
            aggregate='sum',
            measure_field_id=self._field('res.partner', 'partner_latitude').id,
            limit=2,
        )
        payload = card.compute_values('all')
        self.assertEqual([row['label'] for row in payload['rows']], ['Gamma', 'Beta'])
        self.assertEqual(payload['count'], 3)

    def test_broken_card_does_not_break_the_board(self):
        """A card that cannot be computed is flagged, not raised."""
        card = self._card()
        # Bypass the create-time domain check the way a database migration or a
        # renamed field would: the stored domain is only valid until it is not.
        self.env.cr.execute(
            "UPDATE dashboard_item SET domain = %s WHERE id = %s",
            ("[('no_such_field', '=', 1)]", card.id))
        card.invalidate_recordset(['domain'])
        payload = card.compute_values('all')
        self.assertTrue(payload['error'])
        self.assertEqual(payload['value'], 0)


class TestDashboardPeriods(TestDashboardCommon):

    def test_period_range_boundaries(self):
        """A period is a half-open window: no 23:59:59 rounding to get wrong."""
        Item = self.env['dashboard.item']
        start, end = Item._period_range('this_month')
        self.assertEqual(start, self.today.replace(day=1))
        self.assertEqual(end, start + relativedelta(months=1))
        self.assertIsNone(Item._period_range('all'))
        self.assertIsNone(Item._period_range(False))

    def test_period_ignored_without_a_date_field(self):
        """A card that names no date field shows the same number every period."""
        card = self._card()
        self.assertEqual(card.compute_values('today')['value'], 3)
        self.assertEqual(card.compute_values('last_year')['value'], 3)

    def test_period_applies_to_the_date_field(self):
        card = self._card(date_field_id=self._field('res.partner', 'create_date').id)
        self.assertEqual(card.compute_values('today')['value'], 3)
        self.assertEqual(card.compute_values('last_year')['value'], 0)

    def test_comparison_reads_the_previous_window(self):
        """The comparison window is the same length, shifted back once."""
        card = self._card(
            date_field_id=self._field('res.partner', 'create_date').id,
            compare=True,
        )
        payload = card.compute_values('this_month')
        self.assertEqual(payload['value'], 3)
        self.assertEqual(payload['previous'], 0)
        self.assertEqual(payload['delta'], 3)

    def test_comparison_needs_a_period(self):
        """Over all time there is no previous window, so no comparison."""
        card = self._card(
            date_field_id=self._field('res.partner', 'create_date').id,
            compare=True,
        )
        self.assertNotIn('previous', card.compute_values('all'))

    def test_shifted_leaves_move_the_whole_window(self):
        card = self._card(date_field_id=self._field('res.partner', 'create_date').id)
        current = card._period_leaves('this_month')
        previous = card._period_leaves('this_month', shift=1)
        self.assertEqual(current[0][2], fields.Date.to_string(self.today.replace(day=1)))
        self.assertEqual(previous[1][2], current[0][2])


class TestDashboardDrill(TestDashboardCommon):

    def test_drill_opens_what_the_card_counted(self):
        card = self._card()
        action = self.env['dashboard.item'].drill_action(card.id, period='all')
        self.assertEqual(action['res_model'], 'res.partner')
        self.assertEqual(self.env['res.partner'].search_count(action['domain']), 3)

    def test_drill_on_a_bar_opens_only_that_bar(self):
        """The slice the user clicked, not an approximation of it."""
        card = self._card(
            kind='bar',
            group_by_field_id=self._field('res.partner', 'is_company').id,
        )
        payload = card.compute_values('all')
        point = next(p for p in payload['points'] if p['label'] == 'Yes')
        action = self.env['dashboard.item'].drill_action(card.id, period='all', part={'key': point['key']})
        self.assertEqual(self.env['res.partner'].search_count(action['domain']), point['count'])

    def test_drill_on_a_line_point_opens_that_bucket(self):
        card = self._card(
            kind='line',
            date_field_id=self._field('res.partner', 'create_date').id,
            group_by_interval='month',
        )
        payload = card.compute_values('this_year')
        point = next(p for p in payload['points'] if p['value'])
        action = self.env['dashboard.item'].drill_action(
            card.id, period='this_year', part={'key': point['key'], 'on_date': True})
        self.assertEqual(self.env['res.partner'].search_count(action['domain']), point['count'])

    def test_drill_keeps_the_period(self):
        card = self._card(date_field_id=self._field('res.partner', 'create_date').id)
        action = self.env['dashboard.item'].drill_action(card.id, period='last_year')
        self.assertEqual(self.env['res.partner'].search_count(action['domain']), 0)

    def test_drill_uses_a_named_action(self):
        """Pointed at an action, the drill opens that action's own views."""
        window = self.env['ir.actions.act_window'].create({
            'name': 'Partners',
            'res_model': 'res.partner',
            'view_mode': 'kanban,list,form',
        })
        card = self._card(action_id=window.id)
        action = self.env['dashboard.item'].drill_action(card.id, period='all')
        self.assertEqual(action['view_mode'], 'kanban,list,form')
        self.assertEqual(self.env['res.partner'].search_count(action['domain']), 3)

    def test_drill_on_a_missing_card(self):
        self.assertFalse(self.env['dashboard.item'].drill_action(0))


class TestDashboardBoard(TestDashboardCommon):

    def test_read_board_returns_the_whole_page(self):
        self._card()
        self._card(name='Second', kind='bar',
                   group_by_field_id=self._field('res.partner', 'is_company').id)
        data = self.env['dashboard.board'].read_board(self.board.id)
        self.assertEqual(data['board']['id'], self.board.id)
        self.assertEqual(len(data['items']), 2)
        self.assertTrue(data['periods'])

    def test_read_board_honours_the_asked_period(self):
        self._card(date_field_id=self._field('res.partner', 'create_date').id)
        data = self.env['dashboard.board'].read_board(self.board.id, period='last_year')
        self.assertEqual(data['board']['period'], 'last_year')
        self.assertEqual(data['items'][0]['value'], 0)

    def test_cards_come_back_in_order(self):
        first = self._card(name='B', sequence=20)
        second = self._card(name='A', sequence=10)
        data = self.env['dashboard.board'].read_board(self.board.id)
        self.assertEqual([item['id'] for item in data['items']], [second.id, first.id])

    def test_arrange_items_renumbers(self):
        first = self._card(name='One', sequence=10)
        second = self._card(name='Two', sequence=20)
        self.board.arrange_items([second.id, first.id])
        self.assertLess(second.sequence, first.sequence)

    def test_arrange_ignores_foreign_cards(self):
        """Ids that are not on this board are ignored, not trusted."""
        other_board = self.env['dashboard.board'].create({'name': 'Other'})
        stranger = self._card(name='Stranger', board_id=other_board.id, sequence=99)
        mine = self._card(name='Mine', sequence=10)
        self.board.arrange_items([stranger.id, mine.id])
        self.assertEqual(stranger.sequence, 99)

    def test_resize_item_clamps(self):
        card = self._card()
        self.board.resize_item(card.id, 40)
        self.assertEqual(card.width, 12)
        self.board.resize_item(card.id, 0)
        self.assertEqual(card.width, 1)

    def test_default_board_when_none_asked(self):
        data = self.env['dashboard.board'].read_board()
        self.assertTrue(data['board'])

    def test_open_board_action(self):
        action = self.board.action_open_board()
        self.assertEqual(action['tag'], 'ebshel_dashboard.board')
        self.assertEqual(action['params']['board_id'], self.board.id)


class TestDashboardConstraints(TestDashboardCommon):

    def test_bad_domain_is_refused(self):
        with self.assertRaises(ValidationError):
            self._card(domain="[('no_such_field', '=', 1)]")

    def test_chart_needs_a_split_field(self):
        with self.assertRaises(ValidationError):
            self._card(kind='bar')

    def test_line_needs_a_date_field(self):
        with self.assertRaises(ValidationError):
            self._card(kind='line')

    def test_aggregate_needs_a_measure(self):
        with self.assertRaises(ValidationError):
            self._card(aggregate='sum')

    def test_width_is_bounded(self):
        with self.assertRaises(ValidationError):
            self._card(width=13)

    def test_height_is_bounded_or_zero(self):
        card = self._card(height=0)
        self.assertEqual(card.height, 0)
        with self.assertRaises(ValidationError):
            self._card(height=50)

    def test_personal_board_takes_no_groups(self):
        with self.assertRaises(ValidationError):
            self.env['dashboard.board'].create({
                'name': 'Mixed',
                'owner_id': self.env.uid,
                'group_ids': [(4, self.env.ref('base.group_user').id)],
            })


class TestDashboardAccess(TestDashboardCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(cls.env, login='dashboard_user', groups='base.group_user')
        cls.other = new_test_user(cls.env, login='dashboard_other', groups='base.group_user')

    def test_a_reader_cannot_build_at_all(self):
        """Building is a role. Without it there is nothing to own or edit."""
        with self.assertRaises(AccessError):
            self.env['dashboard.board'].with_user(self.user).create({'name': 'Mine'})
        with self.assertRaises(AccessError):
            self.env['dashboard.item'].with_user(self.user).create({
                'name': 'Card', 'board_id': self.board.id,
                'model_id': self.partner_model.id})

    def test_a_reader_still_reads_and_keeps_their_own_notes(self):
        """Read-only is not use-less: the page is still theirs to work with."""
        data = self.env['dashboard.board'].with_user(self.user).read_board(self.board.id)
        self.assertFalse(data['board']['can_edit'])
        self.env['dashboard.note'].with_user(self.user).save_note(self.board.id, 'my own note')
        self.assertIn(
            'my own note',
            self.env['dashboard.note'].with_user(self.user)._mine(self.board.id).body)
        self.board.with_user(self.user).set_default()
        self.assertTrue(self.board.with_user(self.user)._is_default())
        self.assertTrue(self.env['dashboard.view'].with_user(self.user).save_view(
            self.board.id, 'My view', 'all', False, False))

    def test_a_manager_board_is_shared(self):
        board = self.env['dashboard.board'].create({'name': 'Shared'})
        self.assertFalse(board.owner_id)

    def test_a_personal_board_is_invisible_to_others(self):
        builder = self._builder('personal_builder')
        board = self.env['dashboard.board'].with_user(builder).create(
            {'name': 'Mine', 'owner_id': builder.id})
        visible = self.env['dashboard.board'].with_user(self.other).search([('id', '=', board.id)])
        self.assertFalse(visible)

    def test_a_user_cannot_edit_a_shared_board(self):
        board = self.env['dashboard.board'].create({'name': 'Shared'})
        with self.assertRaises(AccessError):
            board.with_user(self.user).write({'name': 'Hijacked'})

    def test_group_restriction_hides_a_board_from_the_list(self):
        """A board built for one group is simply not offered to the others."""
        group = self.env['res.groups'].create({'name': 'Dashboard Audience'})
        board = self.env['dashboard.board'].create({
            'name': 'Restricted', 'group_ids': [(4, group.id)]})
        listed = self.env['dashboard.board'].with_user(self.user).get_boards()
        self.assertNotIn(board.id, [entry['id'] for entry in listed])
        self.user.write({'group_ids': [(4, group.id)]})
        self.user.env.registry.clear_cache()
        listed = self.env['dashboard.board'].with_user(self.user).get_boards()
        self.assertIn(board.id, [entry['id'] for entry in listed])

    def test_can_edit_flag(self):
        """Shared boards belong to the role; a personal one to its owner."""
        builder = self._builder('edit_builder')
        other_builder = self._builder('edit_builder_two')
        shared = self.env['dashboard.board'].create({'name': 'Shared'})
        self.assertFalse(shared.with_user(self.user)._can_edit())
        self.assertTrue(shared.with_user(builder)._can_edit())
        mine = self.env['dashboard.board'].with_user(builder).create(
            {'name': 'Mine', 'owner_id': builder.id})
        self.assertTrue(mine.with_user(builder)._can_edit())
        # Another builder reads it but does not touch it.
        self.assertFalse(mine.with_user(other_builder)._can_edit())

    def _builder(self, login):
        """A user with the role that lets them build dashboards."""
        return new_test_user(
            self.env, login=login,
            groups='base.group_user,ebshel_dashboard.group_dashboard_manager')

    def test_only_mine_answers_per_reader(self):
        """One card, two people, two numbers - and each opens their own records."""
        self.partners[0].user_id = self.user
        self.partners[1].user_id = self.other
        card = self._card(
            only_mine=True,
            user_field_id=self._field('res.partner', 'user_id').id,
        )
        as_user = card.with_user(self.user).compute_values('all')
        as_other = card.with_user(self.other).compute_values('all')
        self.assertEqual(as_user['value'], 1)
        self.assertEqual(as_other['value'], 1)
        action = self.env['dashboard.item'].with_user(self.user).drill_action(card.id, period='all')
        records = self.env['res.partner'].with_user(self.user).search(action['domain'])
        self.assertEqual(records, self.partners[0])

    def test_only_mine_ignores_an_invented_field(self):
        """The user field is checked against the model, never trusted."""
        card = self._card(only_mine=True)
        card.invalidate_recordset()
        self.assertEqual(card._mine_leaves(), [])


class TestDashboardPortability(TestDashboardCommon):

    def test_export_then_import_rebuilds_the_board(self):
        self._card(name='Count')
        self._card(name='Split', kind='bar',
                   group_by_field_id=self._field('res.partner', 'is_company').id)
        payload = self.board.export_boards()
        result = self.env['dashboard.board'].import_boards(payload)
        imported = self.env['dashboard.board'].browse(result['boards'])
        self.assertEqual(len(imported.item_ids), 2)
        self.assertEqual(
            imported.item_ids.filtered(lambda i: i.kind == 'bar').group_by_field_id.name,
            'is_company')
        self.assertFalse(result['skipped'])

    def test_import_skips_a_missing_model(self):
        """A board from a database with more apps installed lands anyway."""
        payload = {'version': 1, 'boards': [{
            'name': 'From Elsewhere',
            'items': [{'name': 'Ghost', 'model': 'no.such.model', 'kind': 'kpi',
                       'aggregate': 'count', 'domain': '[]'}],
        }]}
        result = self.env['dashboard.board'].import_boards(payload)
        self.assertEqual(result['skipped'], ['Ghost (no.such.model)'])
        self.assertEqual(len(self.env['dashboard.board'].browse(result['boards']).item_ids), 0)

    def test_copy_carries_the_cards(self):
        self._card(name='Count')
        copy = self.board.copy()
        self.assertEqual(len(copy.item_ids), 1)
        self.assertIn('copy', copy.name)


class TestDashboardLabels(TestDashboardCommon):

    def test_date_labels_by_interval(self):
        Item = self.env['dashboard.item']
        day = date(2026, 5, 17)
        self.assertEqual(Item._date_label(day, 'month'), 'May 2026')
        self.assertEqual(Item._date_label(day, 'quarter'), 'Q2 2026')
        self.assertEqual(Item._date_label(day, 'year'), '2026')

    def test_bucket_starts(self):
        Item = self.env['dashboard.item']
        day = date(2026, 5, 17)
        self.assertEqual(Item._bucket_start(day, 'month'), date(2026, 5, 1))
        self.assertEqual(Item._bucket_start(day, 'quarter'), date(2026, 4, 1))
        self.assertEqual(Item._bucket_start(day, 'year'), date(2026, 1, 1))
        self.assertEqual(Item._bucket_start(day, 'week'), date(2026, 5, 11))

    def test_unset_group_is_labelled_not_dropped(self):
        """An empty group is a real answer: it gets a name and a drill-through."""
        card = self._card(
            kind='bar',
            group_by_field_id=self._field('res.partner', 'function').id,
        )
        payload = card.compute_values('all')
        self.assertEqual(len(payload['points']), 1)
        point = payload['points'][0]
        self.assertIsNone(point['key'])
        action = self.env['dashboard.item'].drill_action(card.id, period='all', part={'key': point['key']})
        self.assertIn(('function', '=', False), action['domain'])


class TestDashboardExportImportUI(TestDashboardCommon):
    """The door to export/import, not the engine behind it."""

    def test_export_returns_a_downloadable_file(self):
        self._card(name='Count')
        action = self.board.action_export_boards()
        self.assertEqual(action['type'], 'ir.actions.act_url')
        attachment_id = int(action['url'].split('/web/content/')[1].split('?')[0])
        attachment = self.env['ir.attachment'].browse(attachment_id)
        self.assertEqual(attachment.mimetype, 'application/json')
        payload = json.loads(base64.b64decode(attachment.datas).decode('utf-8'))
        self.assertEqual(payload['boards'][0]['name'], self.board.name)
        self.assertEqual(len(payload['boards'][0]['items']), 1)

    def test_export_names_the_file_after_a_single_board(self):
        board = self.env['dashboard.board'].create({'name': 'Sales / Overview 2026'})
        action = board.action_export_boards()
        attachment_id = int(action['url'].split('/web/content/')[1].split('?')[0])
        # Only characters that survive a filesystem, and still recognisable.
        self.assertEqual(self.env['ir.attachment'].browse(attachment_id).name,
                         'Sales_Overview_2026.json')

    def test_export_of_several_boards_is_one_file(self):
        other = self.env['dashboard.board'].create({'name': 'Second'})
        action = (self.board | other).action_export_boards()
        attachment_id = int(action['url'].split('/web/content/')[1].split('?')[0])
        attachment = self.env['ir.attachment'].browse(attachment_id)
        self.assertEqual(attachment.name, 'dashboards.json')
        payload = json.loads(base64.b64decode(attachment.datas).decode('utf-8'))
        self.assertEqual(len(payload['boards']), 2)

    def _wizard(self, payload):
        return self.env['dashboard.import'].create({
            'file_data': base64.b64encode(json.dumps(payload).encode('utf-8')),
            'file_name': 'board.json',
        })

    def test_wizard_imports_and_reports(self):
        self._card(name='Count')
        payload = json.loads(self.board.export_boards())
        wizard = self._wizard(payload)
        wizard.action_import()
        self.assertEqual(wizard.state, 'done')
        self.assertEqual(len(wizard.board_ids), 1)
        self.assertIn('1 dashboard(s) imported', wizard.result)
        self.assertEqual(len(wizard.board_ids.item_ids), 1)

    def test_wizard_names_what_it_could_not_bring(self):
        """An import from a database with more apps must say what stayed behind."""
        wizard = self._wizard({'version': 1, 'boards': [{
            'name': 'From Elsewhere',
            'items': [{'name': 'Ghost', 'model': 'no.such.model', 'kind': 'kpi',
                       'aggregate': 'count', 'domain': '[]'}],
        }]})
        wizard.action_import()
        self.assertIn('Ghost (no.such.model)', wizard.result)
        self.assertEqual(len(wizard.board_ids.item_ids), 0)

    def test_wizard_refuses_a_file_that_is_not_one(self):
        wizard = self.env['dashboard.import'].create({
            'file_data': base64.b64encode(b'this is not json'),
            'file_name': 'notes.txt',
        })
        with self.assertRaises(UserError):
            wizard.action_import()

    def test_wizard_opens_what_it_imported(self):
        self._card(name='Count')
        wizard = self._wizard(json.loads(self.board.export_boards()))
        wizard.action_import()
        action = wizard.action_open_imported()
        self.assertEqual(action['tag'], 'ebshel_dashboard.board')
        self.assertEqual(action['params']['board_id'], wizard.board_ids.id)
