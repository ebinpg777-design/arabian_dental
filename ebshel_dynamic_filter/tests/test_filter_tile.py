# -*- coding: utf-8 -*-
"""Tests for the tile engine.

Everything runs against ``res.partner``, which exists in every database, and
against a plain internal user - because "does this still work for someone who is
not an administrator" is where a module like this breaks.
"""
import datetime

from odoo import fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestFilterTile(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner_model = cls.env['ir.model']._get('res.partner')
        cls.Tile = cls.env['filter.tile']

        cls.companies = cls.env['res.partner'].create([
            {'name': 'Tile Test Company %s' % index, 'is_company': True}
            for index in range(3)
        ])
        cls.people = cls.env['res.partner'].create([
            {'name': 'Tile Test Person %s' % index, 'is_company': False}
            for index in range(5)
        ])

        cls.tile_companies = cls.Tile.create({
            'name': 'Test companies',
            'model_id': cls.partner_model.id,
            'domain': "[('is_company', '=', True), ('name', 'like', 'Tile Test')]",
            'color': 'sky',
        })
        cls.tile_people = cls.Tile.create({
            'name': 'Test people',
            'model_id': cls.partner_model.id,
            'domain': "[('is_company', '=', False), ('name', 'like', 'Tile Test')]",
            'color': 'violet',
        })

        cls.user = cls.env['res.users'].create({
            'name': 'Tile User',
            'login': 'tile_user_test',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])],
        })

    # ------------------------------------------------------------------
    # Registry & values
    # ------------------------------------------------------------------
    def test_registry_lists_tiles_per_model(self):
        registry = self.Tile.get_tile_registry()
        names = [tile['name'] for tile in registry['tiles']['res.partner']]
        self.assertIn('Test companies', names)
        self.assertTrue(registry['can_manage'], "admin should manage tiles")

    def test_compute_counts_and_total(self):
        payloads = [self.tile_companies._tile_payload(), self.tile_people._tile_payload()]
        result = self.Tile.compute_tiles('res.partner', payloads)
        self.assertEqual(result['tiles'][str(self.tile_companies.id)]['count'], 3)
        self.assertEqual(result['tiles'][str(self.tile_people.id)]['count'], 5)
        self.assertEqual(result['tiles'][str(self.tile_people.id)]['value'], 5.0)

    def test_compute_respects_the_base_domain(self):
        """A tile counts inside the view, not beside it."""
        result = self.Tile.compute_tiles(
            'res.partner',
            [self.tile_people._tile_payload()],
            [('name', 'like', 'Tile Test Person 1')],
        )
        self.assertEqual(result['tiles'][str(self.tile_people.id)]['count'], 1)

    def test_compute_with_a_measure(self):
        self.companies.write({'partner_latitude': 2.0})
        self.tile_companies.measure_field_id = self.env['ir.model.fields']._get(
            'res.partner', 'partner_latitude')
        result = self.Tile.compute_tiles('res.partner', [self.tile_companies._tile_payload()])
        entry = result['tiles'][str(self.tile_companies.id)]
        self.assertEqual(entry['value'], 6.0, "sum of the measure, not the record count")
        self.assertEqual(entry['count'], 3)

    def test_broken_domain_is_reported_not_raised(self):
        payload = dict(self.tile_people._tile_payload(), domain="[('nope', '=', 1)]")
        entry = self.Tile.compute_tiles('res.partner', [payload])['tiles'][str(self.tile_people.id)]
        self.assertTrue(entry['error'], "a broken tile must not take the ribbon down")

    # ------------------------------------------------------------------
    # Alerts
    # ------------------------------------------------------------------
    def test_alert_triggers_on_the_rising_edge_only(self):
        self.tile_people.write({'alert_operator': 'gt', 'alert_value': 4, 'alert_notify': True})
        self.assertTrue(self.tile_people._check_alert())
        self.assertTrue(self.tile_people.alert_triggered)
        triggered_on = self.tile_people.alert_triggered_on
        self.assertTrue(triggered_on)

        # Still over the threshold: state unchanged, no second notification.
        self.tile_people._check_alert()
        self.assertEqual(self.tile_people.alert_triggered_on, triggered_on)

        self.tile_people.alert_value = 99
        self.tile_people._check_alert()
        self.assertFalse(self.tile_people.alert_triggered)
        self.assertFalse(self.tile_people.alert_triggered_on)

    def test_alert_shows_up_in_the_payload(self):
        self.tile_people.write({'alert_operator': 'gt', 'alert_value': 4})
        entry = self.Tile.compute_tiles(
            'res.partner', [self.tile_people._tile_payload()])['tiles'][str(self.tile_people.id)]
        self.assertTrue(entry['alert'])

    def test_alert_audience_is_the_owner_of_a_private_tile(self):
        self.tile_people.owner_id = self.user
        self.assertEqual(self.tile_people._alert_audience(), self.user)

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------
    def test_snapshots_are_one_per_day(self):
        self.tile_people.trend_source = 'history'
        self.tile_people._capture_snapshot()
        self.tile_people._capture_snapshot()
        self.assertEqual(self.tile_people.snapshot_count, 1, "same day, same point")
        self.assertEqual(self.tile_people.snapshot_ids.record_count, 5)

    def test_history_feeds_the_sparkline(self):
        self.tile_people.trend_source = 'history'
        today = fields.Date.context_today(self.tile_people)
        for days in range(4):
            self.tile_people._capture_snapshot(day=today - datetime.timedelta(days=days))
        entry = self.Tile.compute_tiles(
            'res.partner', [self.tile_people._tile_payload()])['tiles'][str(self.tile_people.id)]
        self.assertEqual(len(entry['trend']), 4)

    # ------------------------------------------------------------------
    # Breakdown
    # ------------------------------------------------------------------
    def test_breakdown_splits_on_a_chosen_field(self):
        payload = dict(self.tile_companies._tile_payload(), breakdown_field='is_company')
        result = self.Tile.tile_breakdown('res.partner', payload)
        self.assertEqual(result['field'], 'is_company')
        self.assertEqual(len(result['rows']), 1)
        self.assertEqual(result['rows'][0]['count'], 3)
        self.assertEqual(result['rows'][0]['domain'], [('is_company', '=', True)])

    def test_breakdown_honours_the_base_domain(self):
        result = self.Tile.tile_breakdown(
            'res.partner',
            dict(self.tile_people._tile_payload(), breakdown_field='is_company'),
            [('name', 'like', 'Tile Test Person 1')],
        )
        self.assertEqual(result['rows'][0]['count'], 1)

    # ------------------------------------------------------------------
    # Overview
    # ------------------------------------------------------------------
    def test_overview_groups_by_model(self):
        overview = self.Tile.get_overview()
        section = next(s for s in overview['sections'] if s['model'] == 'res.partner')
        self.assertTrue(any(tile['name'] == 'Test companies' for tile in section['tiles']))
        self.assertIn('data', section['tiles'][0])

    def test_overview_can_show_alerts_only(self):
        self.tile_people.write({'alert_operator': 'gt', 'alert_value': 4})
        sections = self.Tile.get_overview(only_alerts=True)['sections']
        tiles = [tile for section in sections for tile in section['tiles']]
        # Other tiles in the database may legitimately be alerting too; what
        # matters is that this one is in, and that nothing calm got through.
        self.assertIn('Test people', [tile['name'] for tile in tiles])
        self.assertNotIn('Test companies', [tile['name'] for tile in tiles])
        self.assertTrue(all(tile['data']['alert'] for tile in tiles))

    # ------------------------------------------------------------------
    # Portability
    # ------------------------------------------------------------------
    def test_export_import_round_trip(self):
        self.tile_companies.write({'alert_operator': 'gt', 'alert_value': 2, 'icon': 'fa-truck'})
        payload = self.Tile.export_tiles('res.partner')
        self.assertTrue(payload['tiles'])

        result = self.Tile.import_tiles(payload)
        created = self.Tile.browse(result['created'])
        clone = created.filtered(lambda tile: tile.name == 'Test companies')
        self.assertTrue(clone)
        self.assertEqual(clone[0].icon, 'fa-truck')
        self.assertEqual(clone[0].alert_operator, 'gt')

    def test_group_by_travels_with_the_tile(self):
        self.tile_companies.write({
            'group_by_field_id': self.env['ir.model.fields']._get('res.partner', 'create_date').id,
            'group_by_interval': 'week',
        })
        payload = self.Tile.export_tiles('res.partner')
        entry = next(entry for entry in payload['tiles'] if entry['name'] == 'Test companies')
        self.assertEqual(entry['group_by_field'], 'create_date')
        self.assertEqual(entry['group_by_interval'], 'week')

        result = self.Tile.import_tiles(payload)
        clone = self.Tile.browse(result['created']).filtered(
            lambda tile: tile.name == 'Test companies')
        self.assertEqual(clone.group_by_field_id.name, 'create_date')
        self.assertEqual(clone.group_by_interval, 'week')

    def test_import_skips_what_this_database_does_not_have(self):
        result = self.Tile.import_tiles({'tiles': [{'name': 'X', 'model': 'no.such.model'}]})
        self.assertFalse(result['created'])
        self.assertEqual(len(result['skipped']), 1)

    # ------------------------------------------------------------------
    # Ownership & rights
    # ------------------------------------------------------------------
    def test_a_plain_user_gets_a_private_tile(self):
        tile = self.Tile.with_user(self.user).create({
            'name': 'Mine',
            'model_id': self.partner_model.id,
            'domain': '[]',
        })
        self.assertEqual(tile.owner_id, self.user, "a non-manager's tile is stamped private")

    def test_a_private_tile_is_invisible_to_everybody_else(self):
        self.Tile.with_user(self.user).create({
            'name': 'Mine',
            'model_id': self.partner_model.id,
            'domain': '[]',
        })
        other = self.env['res.users'].create({
            'name': 'Someone Else',
            'login': 'tile_other_test',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])],
        })
        self.assertFalse(self.Tile.with_user(other).search([('name', '=', 'Mine')]))

    def test_a_plain_user_cannot_touch_a_shared_tile(self):
        with self.assertRaises(AccessError):
            self.tile_companies.with_user(self.user).write({'name': 'hijacked'})

    def test_a_plain_user_cannot_publish(self):
        tile = self.Tile.with_user(self.user).create({
            'name': 'Mine', 'model_id': self.partner_model.id, 'domain': '[]',
        })
        with self.assertRaises(AccessError):
            tile.action_publish()

    def test_a_plain_user_cannot_publish_by_writing_the_owner(self):
        """action_publish is not the only door: a raw write must not open either."""
        tile = self.Tile.with_user(self.user).create({
            'name': 'Mine', 'model_id': self.partner_model.id, 'domain': '[]',
        })
        other = self.env['res.users'].create({
            'name': 'Someone Else',
            'login': 'tile_other_owner_test',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])],
        })
        for vals in ({'owner_id': False},
                     {'owner_id': other.id},
                     {'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]}):
            with self.subTest(vals=vals), self.assertRaises(AccessError):
                tile.write(vals)
        self.assertEqual(tile.owner_id, self.user)
        self.assertFalse(tile.group_ids)
        tile.write({'name': 'Still mine', 'owner_id': self.user.id})
        self.assertEqual(tile.name, 'Still mine')

    def test_a_manager_still_publishes_by_writing_the_owner(self):
        manager = self.env['res.users'].create({
            'name': 'Tile Manager',
            'login': 'tile_manager_write_test',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id,
                                  self.env.ref('ebshel_dynamic_filter.group_tile_manager').id])],
        })
        tile = self.Tile.with_user(manager).create({
            'name': 'Draft', 'model_id': self.partner_model.id, 'domain': '[]',
            'owner_id': manager.id,
        })
        tile.write({'owner_id': False,
                    'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        self.assertFalse(tile.owner_id)
        self.assertEqual(tile.group_ids, self.env.ref('base.group_user'))

    def test_copy_to_me_leaves_the_original_alone(self):
        copy = self.tile_companies.with_user(self.user).action_copy_to_me()
        copied = self.Tile.browse(copy)
        self.assertEqual(copied.owner_id, self.user)
        self.assertFalse(self.tile_companies.owner_id)

    def test_payload_works_for_a_user_who_cannot_read_ir_model_fields(self):
        """base grants group_user 0,0,0,0 on ir.model.fields - the tile still
        has to serialise, or the whole ribbon disappears for regular users."""
        self.tile_companies.write({
            'measure_field_id': self.env['ir.model.fields']._get(
                'res.partner', 'partner_latitude').id,
            'breakdown_field_id': self.env['ir.model.fields']._get('res.partner', 'country_id').id,
            'trend_field_id': self.env['ir.model.fields']._get('res.partner', 'create_date').id,
        })
        payload = self.tile_companies.with_user(self.user)._tile_payload()
        self.assertEqual(payload['measure'], 'partner_latitude')
        self.assertEqual(payload['breakdown_field'], 'country_id')
        self.assertFalse(payload['editable'], "a shared tile is not editable by a plain user")

    # ------------------------------------------------------------------
    # Group by
    # ------------------------------------------------------------------
    def test_a_plain_tile_groups_nothing(self):
        payload = self.tile_companies._tile_payload()
        self.assertEqual(payload['group_by'], '')
        self.assertEqual(payload['group_by_interval'], 'month')

    def test_the_payload_names_the_group_by_field(self):
        """Read as a plain user too: ir.model.fields is closed to them, and the
        grouping must still reach the browser."""
        self.tile_companies.write({
            'group_by_field_id': self.env['ir.model.fields']._get('res.partner', 'country_id').id,
        })
        payload = self.tile_companies.with_user(self.user)._tile_payload()
        self.assertEqual(payload['group_by'], 'country_id')
        self.assertEqual(payload['group_by_type'], 'many2one')
        self.assertEqual(payload['group_by_label'], 'Country')

    def test_a_date_group_by_carries_its_interval(self):
        self.tile_companies.write({
            'group_by_field_id': self.env['ir.model.fields']._get('res.partner', 'create_date').id,
            'group_by_interval': 'quarter',
        })
        payload = self.tile_companies._tile_payload()
        self.assertEqual(payload['group_by'], 'create_date')
        self.assertEqual(payload['group_by_type'], 'datetime')
        self.assertEqual(payload['group_by_interval'], 'quarter')

    def test_a_group_by_field_of_another_model_is_refused(self):
        with self.assertRaises(ValidationError):
            self.tile_companies.write({
                'group_by_field_id': self.env['ir.model.fields']._get('res.users', 'login').id,
            })

    def test_a_group_by_needs_a_stored_field(self):
        with self.assertRaises(ValidationError):
            self.tile_companies.write({
                'group_by_field_id': self.env['ir.model.fields']._get(
                    'res.partner', 'display_name').id,
            })

    def test_changing_the_model_drops_the_group_by(self):
        tile = self.Tile.new({
            'name': 'Moving',
            'model_id': self.partner_model.id,
            'group_by_field_id': self.env['ir.model.fields']._get('res.partner', 'country_id').id,
        })
        tile.model_id = self.env['ir.model']._get('res.users')
        tile._onchange_model_id()
        self.assertFalse(tile.group_by_field_id)

    # ------------------------------------------------------------------
    # Rows
    # ------------------------------------------------------------------
    def test_tiles_start_on_the_first_row(self):
        self.assertEqual(self.tile_companies.row, 1)
        self.assertEqual(self.tile_companies._tile_payload()['row'], 1)

    def test_arrange_moves_tiles_between_rows(self):
        self.Tile.arrange_tiles([
            {'row': 1, 'tiles': [self.tile_people.id]},
            {'row': 2, 'tiles': [self.tile_companies.id]},
        ])
        self.assertEqual(self.tile_people.row, 1)
        self.assertEqual(self.tile_companies.row, 2)
        self.assertEqual(self.tile_people.sequence, 10)
        self.assertEqual(self.tile_companies.sequence, 10, "each row is sequenced from its start")

    def test_arrange_orders_within_a_row(self):
        self.Tile.arrange_tiles([
            {'row': 1, 'tiles': [self.tile_people.id, self.tile_companies.id]},
        ])
        self.assertEqual(self.tile_people.sequence, 10)
        self.assertEqual(self.tile_companies.sequence, 20)

    def test_arrange_clamps_a_row_out_of_range(self):
        self.Tile.arrange_tiles([{'row': 99, 'tiles': [self.tile_companies.id]}])
        self.assertEqual(self.tile_companies.row, 6, "clamped to the last row, not refused")

    def test_arrange_skips_tiles_the_user_may_not_write(self):
        mine = self.Tile.with_user(self.user).create({
            'name': 'Mine', 'model_id': self.partner_model.id, 'domain': '[]',
        })
        self.Tile.with_user(self.user).arrange_tiles([
            {'row': 3, 'tiles': [self.tile_companies.id, mine.id]},
        ])
        self.assertEqual(self.tile_companies.row, 1, "shared tile stayed where it was")
        self.assertEqual(mine.row, 3, "the user's own tile did move")

    def test_deleting_a_row_keeps_its_tiles(self):
        """A row is a layout choice; the tiles on it are work somebody did."""
        self.tile_people.row = 2
        # Other tiles of this model may share row 2; only ours is asserted on.
        moved = self.Tile.delete_row('res.partner', 2)
        self.assertGreaterEqual(moved, 1)
        self.assertTrue(self.tile_people.exists(), "the tile survived the row")
        self.assertEqual(self.tile_people.row, 1, "and joined the row above")

    def test_deleting_a_row_pulls_the_rows_below_up(self):
        self.tile_people.row = 3
        third = self.Tile.create({
            'name': 'Third row tile', 'model_id': self.partner_model.id,
            'domain': '[]', 'row': 4,
        })
        self.Tile.delete_row('res.partner', 3)
        self.assertEqual(self.tile_people.row, 2, "merged into the row above")
        self.assertEqual(third.row, 3, "and row 4 became row 3")

    def test_deleting_the_first_row_merges_downwards(self):
        self.tile_people.row = 2
        self.Tile.delete_row('res.partner', 1)
        self.assertEqual(self.tile_companies.row, 1)
        self.assertEqual(self.tile_people.row, 1,
                         "row 2 moved up into the row that was just emptied")

    def test_deleting_a_row_takes_its_name_and_shifts_the_others(self):
        Row = self.env['filter.tile.row']
        Row.set_row_name('res.partner', 1, 'First')
        Row.set_row_name('res.partner', 2, 'Second')
        Row.set_row_name('res.partner', 3, 'Third')
        self.Tile.delete_row('res.partner', 2)
        names = Row.get_row_names('res.partner')['res.partner']
        self.assertEqual(names.get('1'), 'First')
        self.assertEqual(names.get('2'), 'Third', "the names below moved up too")
        self.assertNotIn('3', names)

    def test_deleting_a_row_takes_the_whole_row_the_user_sees(self):
        """On a scoped view the row mixes model-wide tiles with pinned ones;
        both move. A tile pinned to another menu is not on that ribbon at all."""
        here, elsewhere = self.env['ir.actions.act_window'].create([
            {'name': 'Here', 'res_model': 'res.partner', 'domain': "[('is_company','=',True)]"},
            {'name': 'Elsewhere', 'res_model': 'res.partner', 'domain': "[('is_company','=',False)]"},
        ])
        mine = self.Tile.create({
            'name': 'Pinned here', 'model_id': self.partner_model.id, 'domain': '[]',
            'row': 2, 'action_id': here.id,
        })
        other = self.Tile.create({
            'name': 'Pinned elsewhere', 'model_id': self.partner_model.id, 'domain': '[]',
            'row': 2, 'action_id': elsewhere.id,
        })
        self.tile_people.row = 2

        self.Tile.delete_row('res.partner', 2, action_id=here.id)
        self.assertEqual(mine.row, 1, "the pinned tile moved up")
        self.assertEqual(self.tile_people.row, 1, "so did the model-wide tile beside it")
        self.assertEqual(other.row, 2, "another menu's ribbon is untouched")

    def test_deleting_a_row_without_a_scope_leaves_pinned_tiles_alone(self):
        action = self.env['ir.actions.act_window'].create({
            'name': 'Pinned', 'res_model': 'res.partner', 'domain': "[('is_company','=',True)]",
        })
        pinned = self.Tile.create({
            'name': 'Pinned tile', 'model_id': self.partner_model.id, 'domain': '[]',
            'row': 2, 'action_id': action.id,
        })
        self.tile_people.row = 2
        self.Tile.delete_row('res.partner', 2)
        self.assertEqual(self.tile_people.row, 1)
        self.assertEqual(pinned.row, 2, "it was never on the model-wide ribbon")

    def test_a_row_out_of_range_is_refused_on_the_form(self):
        with self.assertRaises(Exception):
            self.tile_companies.write({'row': 0})

    def test_defaults_carry_the_row_the_plus_was_clicked_on(self):
        defaults = self.Tile.prepare_tile_defaults('res.partner', row=3)
        self.assertEqual(defaults['default_row'], 3)

    def test_row_travels_with_the_tile(self):
        self.tile_companies.row = 2
        payload = self.Tile.export_tiles('res.partner')
        exported = next(t for t in payload['tiles'] if t['name'] == 'Test companies')
        self.assertEqual(exported['row'], 2)

    # ------------------------------------------------------------------
    # Action scoping
    # ------------------------------------------------------------------
    def test_a_tile_is_model_wide_by_default(self):
        self.assertFalse(self.tile_companies.action_id)
        self.assertEqual(self.tile_companies._tile_payload()['action_id'], 0,
                         "0 means every view of the model")

    def test_the_scope_dropdown_shows_each_name_once(self):
        """A model can carry several act_window records with the same name;
        offering all of them is a list of identical labels nobody can pick from."""
        Action = self.env['ir.actions.act_window']
        Action.create({'name': 'Twin', 'res_model': 'res.partner', 'domain': "[]"})
        bound = Action.create({'name': 'Twin', 'res_model': 'res.partner', 'domain': "[]"})
        self.env['ir.ui.menu'].create({
            'name': 'Twin menu', 'action': f'ir.actions.act_window,{bound.id}',
        })

        candidates = self.tile_companies.action_candidate_ids
        twins = candidates.filtered(lambda a: a.name == 'Twin')
        self.assertEqual(len(twins), 1, "one entry per name")
        self.assertEqual(twins, bound, "and it is the one a menu leads to")

    def test_the_current_scope_stays_selectable(self):
        """Even an action that lost the de-duplication - or that no menu points
        at - must remain in the list once a tile is using it."""
        orphan = self.env['ir.actions.act_window'].create({
            'name': 'Opened from a button', 'res_model': 'res.partner', 'domain': "[]",
        })
        self.tile_companies.action_id = orphan
        self.assertIn(orphan, self.tile_companies.action_candidate_ids)

    def test_candidates_are_limited_to_the_tile_model(self):
        candidates = self.tile_companies.action_candidate_ids
        self.assertTrue(candidates)
        self.assertEqual(set(candidates.mapped('res_model')), {'res.partner'})

    def test_a_tile_can_be_scoped_to_one_action(self):
        action = self.env['ir.actions.act_window'].create({
            'name': 'Only companies',
            'res_model': 'res.partner',
            'domain': "[('is_company', '=', True)]",
        })
        self.tile_companies.action_id = action
        payload = self.tile_companies._tile_payload()
        self.assertEqual(payload['action_id'], action.id)

    def test_defaults_scope_to_an_action_that_narrows_the_model(self):
        narrowing = self.env['ir.actions.act_window'].create({
            'name': 'Companies only', 'res_model': 'res.partner',
            'domain': "[('is_company', '=', True)]",
        })
        plain = self.env['ir.actions.act_window'].create({
            'name': 'All contacts', 'res_model': 'res.partner', 'domain': "[]",
        })
        other_model = self.env['ir.actions.act_window'].create({
            'name': 'Users', 'res_model': 'res.users', 'domain': "[('share', '=', False)]",
        })
        self.assertEqual(
            self.Tile.prepare_tile_defaults('res.partner', action_id=narrowing.id)['default_action_id'],
            narrowing.id, "an action that filters the model is worth scoping to")
        self.assertFalse(
            self.Tile.prepare_tile_defaults('res.partner', action_id=plain.id)['default_action_id'],
            "an 'all records' action would scope the tile for no reason")
        self.assertFalse(
            self.Tile.prepare_tile_defaults('res.partner', action_id=other_model.id)['default_action_id'],
            "an action on another model is never the scope")

    def test_generated_sets_stay_inside_their_scope(self):
        action = self.env['ir.actions.act_window'].create({
            'name': 'Some crons', 'res_model': 'ir.cron', 'domain': "[('active', '=', True)]",
        })
        scoped = self.Tile.browse(self.Tile.autogenerate_tiles(
            'ir.cron', 'interval_type', replace=True, action_id=action.id))
        self.assertTrue(scoped)
        self.assertEqual(set(scoped.mapped('action_id')), {action})

        # A model-wide generation must not wipe the scoped set, and vice versa.
        wide = self.Tile.browse(self.Tile.autogenerate_tiles(
            'ir.cron', 'interval_type', replace=True))
        self.assertTrue(all(tile.exists() for tile in scoped),
                        "replacing the model-wide ribbon left the scoped one alone")
        self.assertFalse(any(wide.mapped('action_id')))

    def test_a_generated_set_lands_on_the_chosen_row(self):
        created = self.Tile.browse(self.Tile.autogenerate_tiles(
            'ir.cron', 'interval_type', replace=True, row=3))
        self.assertTrue(created)
        self.assertEqual(set(created.mapped('row')), {3})
        self.assertEqual(min(created.mapped('sequence')), 10,
                         "an empty row starts its own numbering")

    def test_a_generated_row_out_of_range_is_clamped(self):
        created = self.Tile.browse(self.Tile.autogenerate_tiles(
            'ir.cron', 'interval_type', replace=True, row=99))
        self.assertEqual(set(created.mapped('row')), {6})

    def test_scope_travels_by_external_id(self):
        action = self.env.ref('base.action_partner_form', raise_if_not_found=False)
        if not action:
            self.skipTest('base.action_partner_form is not in this database')
        self.tile_companies.action_id = action
        exported = next(t for t in self.Tile.export_tiles('res.partner')['tiles']
                        if t['name'] == 'Test companies')
        self.assertEqual(exported['action'], 'base.action_partner_form')

        created = self.Tile.browse(self.Tile.import_tiles({'tiles': [exported]})['created'])
        self.assertEqual(created.action_id, action)

    def test_import_keeps_a_tile_when_the_action_is_missing(self):
        entry = {'name': 'Orphan', 'model': 'res.partner', 'domain': '[]',
                 'action': 'no_such_module.no_such_action'}
        result = self.Tile.import_tiles({'tiles': [entry]})
        self.assertTrue(result['created'], "the tile is kept, model-wide")
        self.assertFalse(self.Tile.browse(result['created']).action_id)
        self.assertTrue(result['skipped'], "and the lost scope is reported")

    # ------------------------------------------------------------------
    # Row names
    # ------------------------------------------------------------------
    def test_naming_a_row(self):
        Row = self.env['filter.tile.row']
        self.assertEqual(Row.set_row_name('res.partner', 2, ' Pipeline '), 'Pipeline',
                         "the name is stored trimmed")
        # Other rows of this model may well be named already; only ours matters.
        self.assertEqual(Row.get_row_names('res.partner')['res.partner']['2'], 'Pipeline')

    def test_renaming_a_row_keeps_one_record(self):
        Row = self.env['filter.tile.row']
        Row.set_row_name('res.partner', 2, 'Pipeline')
        Row.set_row_name('res.partner', 2, 'Operations')
        rows = Row.search([('model_name', '=', 'res.partner'), ('number', '=', 2)])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows.name, 'Operations')

    def test_clearing_a_row_name_drops_the_record(self):
        Row = self.env['filter.tile.row']
        Row.set_row_name('res.partner', 2, 'Pipeline')
        self.assertEqual(Row.set_row_name('res.partner', 2, '  '), '')
        self.assertFalse(Row.search([('model_name', '=', 'res.partner'), ('number', '=', 2)]))

    def test_row_names_ride_along_with_the_registry(self):
        self.env['filter.tile.row'].set_row_name('res.partner', 1, 'Overview')
        registry = self.Tile.get_tile_registry()
        self.assertEqual(registry['rows']['res.partner']['1'], 'Overview')

    def test_a_plain_user_cannot_name_a_row(self):
        with self.assertRaises(AccessError):
            self.env['filter.tile.row'].with_user(self.user).set_row_name(
                'res.partner', 1, 'Nope')

    def test_a_row_number_out_of_range_is_clamped(self):
        Row = self.env['filter.tile.row']
        Row.set_row_name('res.partner', 99, 'Last')
        self.assertTrue(Row.search([('model_name', '=', 'res.partner'), ('number', '=', 6)]))

    # ------------------------------------------------------------------
    # Sizing
    # ------------------------------------------------------------------
    def test_resize_clamps_instead_of_refusing(self):
        """A drag that overshoots stops at the edge; it does not raise."""
        self.assertEqual(self.Tile.resize_tile(self.tile_companies.id, 9999), 520)
        self.assertEqual(self.tile_companies.width, 520)
        self.assertEqual(self.Tile.resize_tile(self.tile_companies.id, 10), 120)
        self.assertEqual(self.Tile.resize_tile(self.tile_companies.id, 0), 0,
                         "0 puts the tile back to the standard width")

    def test_resize_is_refused_for_a_tile_the_user_cannot_write(self):
        self.assertFalse(
            self.Tile.with_user(self.user).resize_tile(self.tile_companies.id, 300))
        self.assertFalse(self.tile_companies.width)

    def test_width_is_validated_on_the_form(self):
        with self.assertRaises(Exception):
            self.tile_companies.write({'width': 15})

    def test_width_travels_with_the_tile(self):
        self.tile_companies.width = 300
        self.assertEqual(self.tile_companies._tile_payload()['width'], 300)
        payload = self.Tile.export_tiles('res.partner')
        exported = next(t for t in payload['tiles'] if t['name'] == 'Test companies')
        self.assertEqual(exported['width'], 300)

    # ------------------------------------------------------------------
    # Studio helpers
    # ------------------------------------------------------------------
    def test_autogenerate_builds_one_tile_per_selection_value(self):
        created = self.Tile.autogenerate_tiles('ir.cron', 'interval_type', replace=True)
        tiles = self.Tile.browse(created)
        self.assertEqual(len(tiles), len(
            self.env['ir.cron']._fields['interval_type']._description_selection(self.env)))
        self.assertTrue(all(tile.icon for tile in tiles), "every tile gets an icon")

    def test_defaults_capture_the_current_filter(self):
        defaults = self.Tile.prepare_tile_defaults('res.partner', "[('is_company', '=', True)]")
        self.assertEqual(defaults['default_model_id'], self.partner_model.id)
        self.assertEqual(defaults['default_domain'], "[('is_company', '=', True)]")
        self.assertFalse(defaults['default_owner_id'], "a manager builds shared tiles by default")

    def test_defaults_are_private_for_a_plain_user(self):
        defaults = self.Tile.with_user(self.user).prepare_tile_defaults('res.partner')
        self.assertEqual(defaults['default_owner_id'], self.user.id)

    # ------------------------------------------------------------------
    # Batched values
    # ------------------------------------------------------------------
    # compute_tiles answers a whole ribbon with conditional aggregation - one
    # scan of the table instead of one per tile. The batch is only ever allowed
    # to agree with _gather_one_by_one, which stays the definition of the right
    # answer, so these tests compare the two rather than hard-coding numbers.
    def _reference(self, model_name, tiles, base_domain=None, periods=6):
        """The unbatched answer for the same ribbon."""
        Tile = self.Tile
        Model = self.env[model_name]
        base = Tile._eval_domain(base_domain)
        result = {'total': 0, 'tiles': {}}
        prepared = []
        for tile in tiles:
            key = str(tile.get('key') or tile.get('id') or '')
            result['tiles'][key] = {
                'value': 0.0, 'count': 0, 'trend': [], 'delta': None,
                'alert': False, 'error': False,
            }
            prepared.append((key, tile, Tile._eval_domain(tile.get('domain'))))
        Tile._gather_one_by_one(Model, base, prepared, result, with_total=True)
        for key, tile, domain in prepared:
            entry = result['tiles'][key]
            if entry['error'] or not tile.get('show_trend'):
                continue
            if tile.get('trend_source') == 'history':
                entry['trend'] = Tile._history_trend(tile)
            else:
                entry['trend'] = Tile._compute_trend(Model, base + domain, tile, periods)
            entry['delta'] = Tile._compute_delta(entry['trend'])
        for key, tile, _domain in prepared:
            result['tiles'][key]['alert'] = Tile._evaluate_alert(
                tile, result['tiles'][key]['value'])
        return result

    def _assert_matches_reference(self, model_name, tiles, base=None, msg=''):
        batched = self.Tile.compute_tiles(model_name, tiles, base)
        self.assertEqual(batched, self._reference(model_name, tiles, base), msg)
        return batched

    def test_batched_values_match_the_reference(self):
        tiles = [
            {'key': 'companies', 'domain': "[('is_company', '=', True)]"},
            {'key': 'people', 'domain': "[('is_company', '=', False)]"},
            {'key': 'everything', 'domain': "[]"},
            {'key': 'none', 'domain': "[('id', '=', 0)]"},
        ]
        for base in ([], [('name', 'like', 'Tile Test')], [('id', '=', -1)]):
            self._assert_matches_reference('res.partner', tiles, base, f'base={base}')

    def test_batched_measure_and_trend_match_the_reference(self):
        tiles = [
            {'key': 'sum', 'domain': "[('name', 'like', 'Tile Test')]",
             'measure': 'partner_latitude', 'aggregate': 'sum', 'show_trend': True},
            {'key': 'avg', 'domain': "[('is_company', '=', True)]",
             'measure': 'partner_latitude', 'aggregate': 'avg', 'show_trend': True},
            {'key': 'count', 'domain': "[]", 'show_trend': True},
        ]
        self._assert_matches_reference('res.partner', tiles)

    def test_a_tile_on_archived_records_is_not_batched_away(self):
        """The batch asks the base domain once, where a tile's own filter is
        invisible - so `active` has to keep its own query or an "Archived" tile
        silently reads zero."""
        self.people[0].active = False
        tiles = [
            {'key': 'archived', 'domain': "[('active', '=', False), ('name', 'like', 'Tile Test')]"},
            {'key': 'live', 'domain': "[('name', 'like', 'Tile Test')]"},
        ]
        batched = self._assert_matches_reference('res.partner', tiles)
        self.assertEqual(batched['tiles']['archived']['count'], 1)
        self.assertEqual(batched['tiles']['live']['count'], 7)

    def test_a_tile_needing_a_join_falls_back(self):
        """A joined domain cannot be a FILTER clause without changing the rows
        its neighbours see, so the ribbon drops to the unbatched path - and must
        still be right."""
        tiles = [
            {'key': 'dotted', 'domain': "[('country_id.code', '=', 'BE')]"},
            {'key': 'plain', 'domain': "[('is_company', '=', True)]"},
        ]
        self._assert_matches_reference('res.partner', tiles)

    def test_a_broken_domain_only_breaks_its_own_tile(self):
        tiles = [
            {'key': 'broken', 'domain': "[('no_such_field', '=', 1)]"},
            {'key': 'fine', 'domain': "[('is_company', '=', True)]"},
        ]
        batched = self.Tile.compute_tiles('res.partner', tiles, [])
        self.assertTrue(batched['tiles']['broken']['error'])
        self.assertFalse(batched['tiles']['fine']['error'])
        self.assertEqual(batched['tiles']['fine']['count'],
                         self.env['res.partner'].search_count([('is_company', '=', True)]))

    def test_the_batch_obeys_record_rules(self):
        """Every tile is a FILTER over the *base* query, which is where the
        rules live - a tile must not be able to count past them."""
        self.env['ir.rule'].create({
            'name': 'Tile test: companies only',
            'model_id': self.partner_model.id,
            'domain_force': "[('is_company', '=', True)]",
            'groups': [(6, 0, [self.env.ref('base.group_user').id])],
            'perm_read': True,
            'perm_write': False, 'perm_create': False, 'perm_unlink': False,
        })
        self.env.registry.clear_cache()
        Tile = self.Tile.with_user(self.user)
        visible = self.env['res.partner'].with_user(self.user).search_count([])
        tiles = [
            {'key': 'people', 'domain': "[('is_company', '=', False)]"},
            {'key': 'all', 'domain': "[]"},
        ]
        batched = Tile.compute_tiles('res.partner', tiles, [])
        self.assertEqual(batched['tiles']['people']['count'], 0,
                         "the rule hides every non-company")
        self.assertEqual(batched['tiles']['all']['count'], visible)
        self.assertLessEqual(max(t['count'] for t in batched['tiles'].values()), visible)

    def test_a_whole_ribbon_costs_a_constant_number_of_queries(self):
        """The point of the batch: adding tiles must not add round-trips."""
        def queries_for(count):
            tiles = [
                {'key': f't{index}', 'domain': f"[('color', '>=', {index})]", 'show_trend': True}
                for index in range(count)
            ]
            self.Tile.compute_tiles('res.partner', tiles, [])  # warm the caches
            self.env.flush_all()
            before = self.cr.sql_log_count
            self.Tile.compute_tiles('res.partner', tiles, [])
            return self.cr.sql_log_count - before

        few, many = queries_for(2), queries_for(20)
        self.assertEqual(few, many,
                         "20 tiles must cost the same as 2 - got %s vs %s" % (many, few))

    def test_one_joined_tile_does_not_unbatch_the_ribbon(self):
        """The split is per tile: a dotted domain pays for itself, while its
        plain neighbours still share one statement."""
        Model = self.env['res.partner']
        dotted = ('dotted', {'key': 'dotted', 'domain': "[('country_id.code', '=', 'BE')]"},
                  self.Tile._eval_domain("[('country_id.code', '=', 'BE')]"))
        plain = ('plain', {'key': 'plain', 'domain': "[('is_company', '=', True)]"},
                 self.Tile._eval_domain("[('is_company', '=', True)]"))
        archived = ('arch', {'key': 'arch', 'domain': "[('active', '=', False)]"},
                    self.Tile._eval_domain("[('active', '=', False)]"))
        batched, manual = self.Tile._split_batchable(Model, [dotted, plain, archived])
        self.assertEqual([item[0] for item in batched], ['plain'])
        self.assertEqual([item[0] for item in manual], ['dotted', 'arch'])

    def test_a_mixed_ribbon_stays_cheap_and_right(self):
        """Adding plain tiles beside a joined one must not add round-trips."""
        def ribbon(plain_count):
            tiles = [{'key': 'dotted', 'domain': "[('country_id.code', '=', 'BE')]",
                      'show_trend': True}]
            tiles += [
                {'key': f'p{index}', 'domain': f"[('color', '>=', {index})]", 'show_trend': True}
                for index in range(plain_count)
            ]
            return tiles

        for tiles in (ribbon(2), ribbon(12)):
            self._assert_matches_reference('res.partner', tiles)

        def queries_for(tiles):
            self.Tile.compute_tiles('res.partner', tiles, [])  # warm
            self.env.flush_all()
            before = self.cr.sql_log_count
            self.Tile.compute_tiles('res.partner', tiles, [])
            return self.cr.sql_log_count - before

        few, many = queries_for(ribbon(2)), queries_for(ribbon(12))
        self.assertEqual(few, many,
                         "extra plain tiles beside a joined one must be free - "
                         "got %s vs %s queries" % (many, few))


@tagged('post_install', '-at_install')
class TestOnlyMine(TransactionCase):
    """One shared tile, a different number for every viewer.

    The domain has understood ``uid`` all along, but the tree editor gives an
    ordinary user no way to type an expression - so "Only My Records" is the
    switch, and the person field rides the payload as ``mine_field``. The id it
    compares against is always ``env.uid``: whoever asks, counts their own.
    (client, 2026-08-28)
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Tile = cls.env['filter.tile']
        cls.task_model = cls.env['ir.model']._get('res.partner')
        cls.first = cls.env['res.users'].create({
            'name': 'Mine One', 'login': 'mine.one@test',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])]})
        cls.second = cls.env['res.users'].create({
            'name': 'Mine Two', 'login': 'mine.two@test',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])]})
        # res.partner.user_id: the salesperson - a stored many2one to res.users
        # that exists in every database.
        cls.env['res.partner'].create([
            {'name': 'Mine Partner A1', 'user_id': cls.first.id},
            {'name': 'Mine Partner A2', 'user_id': cls.first.id},
            {'name': 'Mine Partner B1', 'user_id': cls.second.id},
        ])
        cls.field_user = cls.env['ir.model.fields'].sudo().search([
            ('model', '=', 'res.partner'), ('name', '=', 'user_id')], limit=1)
        cls.tile = cls.Tile.create({
            'name': 'My partners',
            'model_id': cls.task_model.id,
            'domain': "[('name', 'like', 'Mine Partner')]",
            'only_mine': True,
            'user_field_id': cls.field_user.id,
        })

    def _count_as(self, user):
        tiles = self.Tile.with_user(user).compute_tiles(
            'res.partner', [self.tile.with_user(user)._tile_payload()])
        return tiles['tiles'][str(self.tile.id)]['count']

    def test_each_viewer_counts_their_own(self):
        self.assertEqual(self._count_as(self.first), 2)
        self.assertEqual(self._count_as(self.second), 1)

    def test_the_payload_names_the_person_field(self):
        payload = self.tile._tile_payload()
        self.assertEqual(payload['mine_field'], 'user_id')
        untied = self.Tile.create({
            'name': 'Everybody', 'model_id': self.task_model.id, 'domain': '[]'})
        self.assertEqual(untied._tile_payload()['mine_field'], '',
                         'a tile without the switch sends no field')

    def test_a_forged_field_name_contributes_nothing(self):
        """The definition dict arrives from the browser: a field that is not a
        stored many2one to res.users must be ignored, not obeyed."""
        Model = self.env['res.partner']
        for forged in ('name', 'parent_id', 'no_such_field', ''):
            self.assertEqual(self.Tile._mine_leaf(Model, {'mine_field': forged}), [])
        self.assertEqual(
            self.Tile._mine_leaf(Model, {'mine_field': 'user_id'}),
            [('user_id', '=', self.env.uid)])

    def test_breakdown_narrows_to_the_viewer_too(self):
        result = self.Tile.with_user(self.first).tile_breakdown(
            'res.partner',
            {'domain': self.tile.domain, 'mine_field': 'user_id',
             'breakdown_field': 'user_id'})
        total = sum(row['count'] for row in result['rows'])
        self.assertEqual(total, 2, "the split sees only the viewer's records")

    def test_typed_uid_still_works_for_the_brave(self):
        typed = self.Tile.create({
            'name': 'Typed uid',
            'model_id': self.task_model.id,
            'domain': "[('name', 'like', 'Mine Partner'), ('user_id', '=', uid)]",
        })
        tiles = typed.with_user(self.second).compute_tiles(
            'res.partner', [typed.with_user(self.second)._tile_payload()])
        self.assertEqual(tiles['tiles'][str(typed.id)]['count'], 1)

    def test_the_switch_needs_a_person_field(self):
        from odoo.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            self.Tile.create({
                'name': 'Half switched', 'model_id': self.task_model.id,
                'domain': '[]', 'only_mine': True})

    def test_the_person_field_is_guessed(self):
        tile = self.Tile.new({'model_id': self.task_model.id})
        tile._onchange_model_id()
        tile.only_mine = True
        tile._onchange_only_mine()
        self.assertEqual(tile.user_field_id.name, 'user_id')

    def test_a_shared_per_viewer_tile_keeps_no_history_and_no_alerts(self):
        self.tile.write({'trend_source': 'history'})
        self.assertFalse(self.tile._capture_snapshot(),
                         'whose number would the point record?')
        self.tile.write({'alert_operator': 'gt', 'alert_value': 0})
        self.assertFalse(self.tile._check_alert())
        # a PRIVATE per-viewer tile has an owner to measure through
        mine = self.Tile.create({
            'name': 'Private mine', 'model_id': self.task_model.id,
            'domain': "[('name', 'like', 'Mine Partner')]",
            'only_mine': True, 'user_field_id': self.field_user.id,
            'owner_id': self.first.id, 'trend_source': 'history'})
        self.assertTrue(mine._capture_snapshot())
        snap = self.env['filter.tile.snapshot'].sudo().search(
            [('tile_id', '=', mine.id)], limit=1)
        self.assertEqual(snap.record_count, 2, "measured through the owner's eyes")


@tagged('post_install', '-at_install')
class TestTheBrowserCanEvaluateIt(TransactionCase):
    """A tile's domain is evaluated twice: here, to count it, and in the browser,
    to filter the view when it is clicked.

    The browser's evaluator knows far less Python, and it does not fail
    politely - it throws inside the rendering, where nothing catches it, and
    the view dies for everybody every time it is opened. That is what a tile
    saved with `context_today().replace(day=1)` did to the Products screen on
    2026-09-10. safe_eval accepts all of it, so the server has to check the
    browser's vocabulary itself.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Tile = cls.env['filter.tile']
        cls.model = cls.env['ir.model']._get('res.partner')

    def _tile(self, domain):
        return self.Tile.create({
            'name': 'Browser check', 'model_id': self.model.id, 'domain': domain})

    def _refused(self, domain):
        with self.assertRaises(ValidationError) as caught:
            self._tile(domain)
        return str(caught.exception)

    # ------------------------------------------------------------ accepted
    def test_the_expressions_the_browser_does_know(self):
        for domain in (
            "[('name', '=', 'x')]",
            "[('user_id', '=', uid)]",
            "[('write_date', '>=', context_today().strftime('%Y-%m-01'))]",
            "[('write_date', '>=', (context_today() - relativedelta(days=7)).strftime('%Y-%m-%d'))]",
            "[('write_date', '>=', (context_today() + relativedelta(weekday=0, days=-6)).strftime('%Y-%m-%d'))]",
            "[('create_date', '>=', datetime.datetime.combine(context_today(), datetime.time(0, 0, 0)))]",
            "[('company_id', 'in', allowed_company_ids)]",
        ):
            tile = self._tile(domain)
            self.assertFalse(tile._tile_payload()['broken'], domain)

    # ------------------------------------------------------------ refused
    def test_a_date_method_the_browser_lacks_is_refused_with_the_answer(self):
        message = self._refused(
            "[('write_date', '>=', (context_today().replace(day=1)).strftime('%Y-%m-%d'))]")
        self.assertIn('replace()', message)
        self.assertIn("%Y-%m-01", message, "the message says what to write instead")

    def test_weekday_is_refused_with_the_answer(self):
        message = self._refused(
            "[('write_date', '>=', (context_today() - relativedelta("
            "days=context_today().weekday())).strftime('%Y-%m-%d'))]")
        self.assertIn('weekday()', message)
        self.assertIn('weekday=0', message)

    def test_a_python_builtin_the_browser_lacks_is_refused(self):
        self.assertIn('len()', self._refused("[('name', '!=', len('abc'))]"))

    def test_a_name_that_exists_only_on_the_server_is_refused(self):
        """`user` is a record-rule helper; in the browser it is not defined."""
        message = self._refused("[('id', '=', user.partner_id.id)]")
        self.assertIn('user', message)

    # ------------------------------------------------------------ old rows
    def test_a_tile_saved_before_the_check_is_flagged_not_hidden(self):
        """The ribbon greys it out and refuses to apply it; the manager can
        still see it and fix it, which they cannot do if it vanishes."""
        tile = self._tile("[('name', '=', 'x')]")
        self.env.cr.execute(
            "UPDATE filter_tile SET domain = %s WHERE id = %s",
            ["[('write_date', '>=', context_today().replace(day=1))]", tile.id])
        tile.invalidate_recordset()
        self.assertTrue(tile._tile_payload()['broken'])
        self.assertEqual(tile._tile_payload()['name'], 'Browser check',
                         "it is still sent to the ribbon, so it can be found and fixed")

    def test_the_check_says_nothing_about_a_domain_python_already_refuses(self):
        """A syntax error is the other check's business, not this one's."""
        self.assertIsNone(self.Tile._browser_domain_problem("[('a', '=',"))

@tagged('post_install', '-at_install')
class TestModelDefinedMine(TransactionCase):
    """A model may say what "mine" means, and both counting and filtering follow.

    A dental lab's work order carries two people - the technician who carried it
    out and, at a finishing bench, the one who finished it - so a polisher's
    "Only My Records" tile counted nothing. `_dft_mine_leaf` on the model is how
    a model says so; the tile counts by it and the browser filters by it.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Tile = cls.env['filter.tile']
        cls.model = cls.env['ir.model']._get('res.partner')
        cls.field_user = cls.env['ir.model.fields'].sudo().search([
            ('model', '=', 'res.partner'), ('name', '=', 'user_id')], limit=1)
        cls.tile = cls.Tile.create({
            'name': 'Mine by model', 'model_id': cls.model.id,
            'domain': "[('name', 'like', 'Mine Model')]",
            'only_mine': True, 'user_field_id': cls.field_user.id})

    def test_without_a_hook_the_leaf_is_the_plain_one(self):
        leaf = self.Tile._mine_leaf(self.env['res.partner'], {'mine_field': 'user_id'})
        self.assertEqual(leaf, [('user_id', '=', self.env.uid)])
        self.assertEqual(self.tile._tile_payload()['mine_domain'],
                         [('user_id', '=', self.env.uid)])

    def test_a_model_can_widen_what_mine_means(self):
        Partner = self.env['res.partner']
        wide = ['|', ('user_id', '=', self.env.uid), ('create_uid', '=', self.env.uid)]
        # On the class, not the recordset: a record refuses a stray attribute.
        with patch.object(type(Partner), '_dft_mine_leaf',
                          lambda self, field_name, uid: wide, create=True):
            self.assertEqual(
                self.Tile._mine_leaf(Partner, {'mine_field': 'user_id'}), wide)
            self.assertEqual(self.tile._tile_payload()['mine_domain'], wide)

    def test_a_hook_that_answers_with_nothing_is_ignored(self):
        Partner = self.env['res.partner']
        with patch.object(type(Partner), '_dft_mine_leaf',
                          lambda self, field_name, uid: [], create=True):
            self.assertEqual(self.Tile._mine_leaf(Partner, {'mine_field': 'user_id'}),
                             [('user_id', '=', self.env.uid)])

    def test_a_forged_field_is_still_refused_before_the_hook(self):
        self.assertEqual(self.Tile._mine_leaf(
            self.env['res.partner'], {'mine_field': 'name'}), [])

@tagged('post_install', '-at_install')
class TestHeadlineTiles(TransactionCase):
    """A tile that counts the screen, not the reader's slice of it.

    Tiles normally count inside the filters on screen - that is what makes a
    ribbon a breakdown of the list under it. A "Finished" tile on a screen
    whose default filter is "to do" then reads 0 for ever, which looks like an
    answer and is not one. (client, 2026-09-10)
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Tile = cls.env['filter.tile']
        cls.model = cls.env['ir.model']._get('res.partner')
        cls.tag = 'Headline %s' % cls.env.cr.dbname[:6]
        cls.env['res.partner'].create([
            {'name': '%s kept' % cls.tag, 'ref': 'HL-KEEP'},
            {'name': '%s other' % cls.tag, 'ref': 'HL-OTHER'},
        ])
        cls.mine = cls.Tile.create({
            'name': 'Kept', 'model_id': cls.model.id,
            'domain': "[('ref', '=', 'HL-KEEP')]"})
        cls.headline = cls.Tile.create({
            'name': 'Kept regardless', 'model_id': cls.model.id,
            'domain': "[('ref', '=', 'HL-KEEP')]", 'ignore_filters': True})

    def _counts(self, screen, unfiltered):
        payloads = [self.mine._tile_payload(), self.headline._tile_payload()]
        result = self.Tile.compute_tiles('res.partner', payloads, screen, 6, unfiltered)
        return (result['tiles'][str(self.mine.id)]['count'],
                result['tiles'][str(self.headline.id)]['count'])

    def test_a_filter_that_hides_the_tile_zeroes_only_the_ordinary_one(self):
        hides = [('ref', '=', 'HL-OTHER')]
        whole = [('name', 'like', self.tag)]
        ordinary, headline = self._counts(hides + whole, whole)
        self.assertEqual(ordinary, 0, "inside the reader's filter there is nothing")
        self.assertEqual(headline, 1, "the headline counts the screen, not the slice")

    def test_without_the_flag_both_count_the_same(self):
        whole = [('name', 'like', self.tag)]
        ordinary, headline = self._counts(whole, whole)
        self.assertEqual((ordinary, headline), (1, 1))

    def test_an_older_client_that_sends_no_second_domain_still_works(self):
        whole = [('name', 'like', self.tag)]
        result = self.Tile.compute_tiles(
            'res.partner', [self.headline._tile_payload()], whole)
        self.assertEqual(result['tiles'][str(self.headline.id)]['count'], 1)

    def test_the_flag_rides_the_payload(self):
        self.assertTrue(self.headline._tile_payload()['ignore_filters'])
        self.assertFalse(self.mine._tile_payload()['ignore_filters'])
