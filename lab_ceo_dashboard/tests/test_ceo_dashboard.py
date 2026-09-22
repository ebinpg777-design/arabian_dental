# -*- coding: utf-8 -*-
from unittest.mock import patch

from odoo import fields, tools
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestTheManagementApp(TransactionCase):
    """The app opens on the hub and its bar carries only the incentive screens.
    (client, 2026-08-28)"""

    def test_the_app_opens_the_hub(self):
        """Either the app root opens the hub, or its first entry does."""
        hub = self.env.ref('lab_ceo_dashboard.action_lab_ceo_dashboard')
        root = self.env.ref('lab_ceo_dashboard.menu_lab_ceo_root')
        first = self.env['ir.ui.menu'].with_context(
            **{'ir.ui.menu.full_list': True}).search(
            [('parent_id', '=', root.id)], order='sequence, id', limit=1)
        self.assertTrue(root.action == hub or first.action == hub,
                        "the hub must be the way into the app")

    def test_the_old_entries_are_gone(self):
        for xmlid in ('menu_lab_ceo_dashboard', 'menu_lab_ceo_collections',
                      'menu_mgmt_sales', 'menu_mgmt_field_force', 'menu_mgmt_production',
                      'menu_mgmt_production_people', 'menu_mgmt_new_clinics',
                      'menu_mgmt_redo', 'menu_mgmt_money'):
            self.assertFalse(
                self.env.ref('lab_ceo_dashboard.%s' % xmlid, raise_if_not_found=False),
                xmlid)

    def test_only_incentive_screens_remain_on_the_bar(self):
        root = self.env.ref('lab_ceo_dashboard.menu_lab_ceo_root')
        children = self.env['ir.ui.menu'].with_context(
            **{'ir.ui.menu.full_list': True}).search([('parent_id', '=', root.id)])
        data = self.env['ir.model.data'].sudo().search_read(
            [('model', '=', 'ir.ui.menu'), ('res_id', 'in', children.ids)],
            ['res_id', 'module'])
        module_of = {d['res_id']: d['module'] for d in data}
        # The hub's own entry is the way into the app, not another management
        # screen, so it is allowed alongside the incentive ones.
        hub = self.env.ref('lab_ceo_dashboard.action_lab_ceo_dashboard')
        strangers = [m.name for m in children
                     if module_of.get(m.id) != 'lab_incentive'
                     and m.action != hub]
        self.assertEqual(strangers, [],
                         "every other management screen is a launcher on the hub")

    def _reader(self, login):
        return self.env['res.users'].create({
            'name': 'Board Reader', 'login': login,
            'group_ids': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref('lab_ceo_dashboard.group_lab_executive').id,
                self.env.ref('account.group_account_readonly').id])]})

    def test_collections_is_reached_from_the_hub_and_loads(self):
        user = self._reader('ceo_hub_test')
        rows = self.env['lab.ceo.dashboard'].with_user(user).get_launchers()
        coll = [r for r in rows if r['key'] == 'collections']
        self.assertTrue(coll)
        self.assertEqual(coll[0]['action_id'],
                         self.env.ref('lab_collections.action_collection_dashboard').id)
        self.assertTrue(
            self.env['lab.collection.performance'].with_user(user).dashboard_data())


@tagged('post_install', '-at_install')
class TestManagementDashboards(TransactionCase):
    """The Management menu's own boards, replacing four configured on a third-party
    tile engine that was uninstalled. (client, 2026-08-27)"""

    DASHBOARDS = (
        ('lab_ceo_dashboard.action_mgmt_sales', 'sale.report'),
        ('lab_ceo_dashboard.action_mgmt_field_force', 'lab.visit'),
        ('lab_ceo_dashboard.action_mgmt_money', 'account.invoice.report'),
    )

    def test_each_board_opens_on_something_a_reader_can_slice(self):
        for xml_id, model in self.DASHBOARDS:
            action = self.env.ref(xml_id)
            self.assertEqual(action.res_model, model, xml_id)
            self.assertIn('graph', action.view_mode, xml_id)
            self.assertIn('pivot', action.view_mode,
                          "%s must be regroupable, not a fixed tile" % xml_id)

    def _launcher_action(self, key):
        rows = self.env['lab.ceo.dashboard'].with_user(
            self.env.ref('base.user_admin')).get_launchers()
        row = next(r for r in rows if r['key'] == key)
        return row['action_id']

    def test_the_boards_are_on_the_hub(self):
        keys = {r['key'] for r in self.env['lab.ceo.dashboard'].with_user(
            self.env.ref('base.user_admin')).get_launchers()}
        for key in ('sales', 'field', 'floor', 'money'):
            self.assertIn(key, keys)

    def test_production_reuses_the_boards_the_lab_already_has(self):
        # Two entries because they answer two different questions: where the work
        # is standing right now, and who got through what.
        self.assertEqual(self._launcher_action('floor'),
                         self.env.ref('lab_workcenter_scan.action_flow_board').id)
        self.assertEqual(self._launcher_action('production'),
                         self.env.ref('lab_workcenter_scan.action_mrp_report').id)

    def test_new_clinics_is_the_same_screen_the_field_team_uses(self):
        self.assertEqual(self._launcher_action('clinics'),
                         self.env.ref('lab_fieldwork.action_new_clinics').id)

    def test_money_does_not_count_payments_that_are_not_recorded_as_payments(self):
        """Receipts on this database are journal entries, not account.payment records —
        there are 2 of those — so a board measuring payments would read as nearly zero.
        This one measures what was INVOICED and sends the reader to Collections for what
        is still owed."""
        action = self.env.ref('lab_ceo_dashboard.action_mgmt_money')
        self.assertNotEqual(action.res_model, 'account.payment')
        self.assertIn('Collections', action.help or '')

    def test_the_board_can_scroll(self):
        """Its root carries `o_action`, which the web client styles
        `height: 100%; overflow: hidden` — so without a rule that matches that
        selector's specificity the page is simply clipped and the wheel does nothing."""
        bundle = self.env['ir.qweb']._get_asset_bundle('web.assets_backend')
        bundle.preprocess_css()
        self.assertFalse(bundle.css_errors)
        scss = tools.file_open(
            'lab_ceo_dashboard/static/src/scss/ceo_dashboard.scss').read()
        selector = 'html .o_web_client > .o_action_manager > .o_action'
        self.assertIn(selector, scss)
        # rsplit, not split: the selector is quoted in the comment that explains it,
        # and the block that matters is the LAST occurrence — the rule itself.
        rule = scss.rsplit(selector, 1)[1][:220]
        self.assertIn('o_ceo_dash', rule)
        self.assertIn('overflow-y', rule)


@tagged('post_install', '-at_install')
class TestLaunchers(TransactionCase):
    """The hub's launchers: every management screen, each with a live figure and a
    key, hidden when the person could not open the screen anyway.
    (client, 2026-08-28)"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Dash = cls.env['lab.ceo.dashboard']

    def test_every_management_screen_is_offered_to_an_administrator(self):
        names = [r['name'] for r in self.Dash.with_user(
            self.env.ref('base.user_admin')).get_launchers()]
        for expected in ('Collections', 'Sales & Cases', 'Field Force',
                         'Production Floor', 'Production Reports', 'New Clinics',
                         'Redo Works', 'Money'):
            self.assertIn(expected, names)
        for name in names:
            self.assertNotIn('incentive', name.lower())

    def test_each_launcher_has_an_action_a_key_and_a_reading(self):
        rows = self.Dash.with_user(self.env.ref('base.user_admin')).get_launchers()
        self.assertEqual([r['shortcut'] for r in rows],
                         [str(i + 1) for i in range(len(rows))])
        for row in rows:
            self.assertTrue(row['action_id'], row['name'])
            self.assertTrue(row['icon'].startswith('fa-'), row['name'])
            self.assertTrue(row['hint'], row['name'])
            # a figure is a number or honestly absent - never an error string
            self.assertTrue(row['value'] is None or isinstance(row['value'], (int, float)),
                            row['name'])

    def test_a_screen_whose_records_the_user_cannot_read_is_not_offered(self):
        bare = self.env['res.users'].create({
            'name': 'Bare Exec', 'login': 'bare_exec_hub',
            'group_ids': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref('lab_ceo_dashboard.group_lab_executive').id])]})
        rows = self.Dash.with_user(bare).get_launchers()
        names = [r['name'] for r in rows]
        # Redo Works is a list of lab.mrp.redo, which a bare executive cannot read
        self.assertNotIn('Redo Works', names)
        # client actions are always offered: their screens scope themselves
        self.assertIn('Sales & Cases', names)

    def test_the_figures_answer_the_question_each_screen_asks(self):
        rows = {r['key']: r for r in self.Dash.with_user(
            self.env.ref('base.user_admin')).get_launchers()}
        self.assertEqual(rows['collections']['unit'], 'overdue')
        self.assertTrue(rows['collections']['money'])
        self.assertEqual(rows['clinics']['value'],
                         self.env['lab.new.clinics'].count_new_clinics())
        # Money carries no figure at all: the bank balance is the lab's most
        # sensitive number and belongs inside the screen, not on a tile every
        # management reader sees on every visit. (client, 2026-08-31)
        self.assertIsNone(rows['money']['value'])

    def test_receivables_split_is_consistent(self):
        ar = self.Dash._receivables()
        self.assertGreaterEqual(ar['total'], ar['overdue'])
        self.assertTrue(0 <= ar['overdue_pct'] <= 100)

    def test_an_outsider_is_refused(self):
        from odoo.exceptions import AccessError
        nobody = self.env['res.users'].create({
            'name': 'Hub Nobody', 'login': 'hub_nobody',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        with self.assertRaises(AccessError):
            self.Dash.with_user(nobody).get_launchers()

    def test_each_launcher_says_how_to_mount_it_inside_the_hub(self):
        rows = {r['key']: r for r in self.Dash.with_user(
            self.env.ref('base.user_admin')).get_launchers()}
        self.assertEqual(rows['sales']['kind'], 'client')
        self.assertEqual(rows['sales']['tag'], 'lab_mgmt_pulse')
        self.assertEqual(rows['sales']['params'].get('section'), 'sales')
        self.assertEqual(rows['redo']['kind'], 'window')
        self.assertEqual(rows['redo']['res_model'], 'lab.mrp.redo')
        self.assertTrue(rows['redo']['views'])
        self.assertIsInstance(rows['redo']['domain'], str)

    def test_a_broken_figure_costs_only_the_figure(self):
        with patch.object(type(self.Dash), '_receivables',
                          side_effect=RuntimeError('boom')):
            rows = {r['key']: r for r in self.Dash.with_user(
                self.env.ref('base.user_admin')).get_launchers()}
        self.assertIn('collections', rows)
        self.assertIsNone(rows['collections']['value'])


@tagged('post_install', '-at_install')
class TestFieldCommand(TransactionCase):
    """The Field Force tile is whichever screens the reader's role earns.

    One user's "field force" is a queue to sign, another's a chart to read: the
    tile opens a switcher over the role desks plus the analysis pulse, and what
    is IN the switcher is decided here, by group, on the server. What is pinned:
    the pulse is always last and never lost, the most specific role leads, and
    a reader with no field work role sees exactly what they saw before.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Dash = cls.env['lab.ceo.dashboard']
        base = [cls.env.ref('base.group_user').id,
                cls.env.ref('lab_ceo_dashboard.group_lab_executive').id]
        cls.reader = cls.env['res.users'].create({
            'name': 'FC Reader', 'login': 'fc_reader',
            'group_ids': [(6, 0, base)]})
        cls.ops = cls.env['res.users'].create({
            'name': 'FC Ops', 'login': 'fc_ops',
            'group_ids': [(6, 0, base + [cls.env.ref(
                'lab_fieldwork.group_fieldwork_ops_manager').id])]})
        cls.fw_admin = cls.env['res.users'].create({
            'name': 'FC FW Admin', 'login': 'fc_fw_admin',
            'group_ids': [(6, 0, base + [cls.env.ref(
                'lab_fieldwork.group_fieldwork_admin').id])]})

    def _keys(self, user):
        return [s['key'] for s in
                self.Dash.with_user(user).get_field_command()['screens']]

    def test_a_reader_with_no_field_role_keeps_the_pulse_alone(self):
        self.assertEqual(self._keys(self.reader), ['pulse'])

    def test_a_desk_holder_gets_their_desk_first_and_the_pulse_last(self):
        self.assertEqual(self._keys(self.ops), ['ops', 'pulse'],
                         "gaining a desk must never cost the analysis")

    def test_the_administrator_gets_every_screen_most_specific_first(self):
        self.assertEqual(self._keys(self.fw_admin),
                         ['admin', 'ops', 'mkt', 'pulse'])

    def test_the_badges_count_what_is_waiting_on_each_desk(self):
        before = {s['key']: s['badge'] for s in self.Dash.with_user(
            self.fw_admin).get_field_command()['screens']}
        self.env['lab.daily.update'].sudo().create({
            'user_id': self.ops.id, 'date': '2026-01-05',
        }).write({'state': 'submitted'})
        after = {s['key']: s['badge'] for s in self.Dash.with_user(
            self.fw_admin).get_field_command()['screens']}
        self.assertEqual(after['ops'] - before['ops'], 1)
        self.assertEqual(after['admin'] - before['admin'], 1)
        self.assertEqual(after['mkt'], before['mkt'],
                         "a submitted sheet is not the marketing desk's yet")

    def test_the_launcher_points_at_the_switcher_for_everyone(self):
        for user in (self.reader, self.ops):
            rows = {r['key']: r for r in
                    self.Dash.with_user(user).get_launchers()}
            self.assertEqual(rows['field']['kind'], 'client')
            self.assertEqual(rows['field']['tag'], 'lab_field_command')

    def test_the_tile_figure_is_what_waits_on_you_for_a_desk_holder(self):
        rows = {r['key']: r for r in self.Dash.with_user(self.ops).get_launchers()}
        self.assertEqual(rows['field']['unit'], 'waiting on you')
        # A reader with no desk keeps the rounds figure - and this minimal
        # fixture reader cannot read visits at all, which by the hub's own
        # design costs them the figure, never the tile. Either way, the queue
        # number is not theirs to see.
        rows = {r['key']: r for r in self.Dash.with_user(self.reader).get_launchers()}
        self.assertIn(rows['field']['unit'], ('visits done today', ''))
        self.assertNotEqual(rows['field']['unit'], 'waiting on you')

    def test_an_outsider_is_refused_the_field_command_too(self):
        from odoo.exceptions import AccessError
        nobody = self.env['res.users'].create({
            'name': 'FC Nobody', 'login': 'fc_nobody',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        with self.assertRaises(AccessError):
            self.Dash.with_user(nobody).get_field_command()


@tagged('post_install', '-at_install')
class TestRaisedSheetsReachTheAdmin(TransactionCase):
    """A day raised by a desk is waiting on the administrator, so it has to
    show up in the number that tells them so. (client, 2026-09-05)"""

    def test_the_admin_badge_counts_raised_days(self):
        Hub = self.env['lab.ceo.dashboard'].sudo()
        Sheet = self.env['lab.daily.update'].sudo()
        before = Hub._field_backlog('admin')
        user = self.env['res.users'].create({
            'name': 'Badge Exec', 'login': 'badge_exec',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        sheet = Sheet.create({
            'user_id': user.id,
            'date': fields.Date.subtract(
                fields.Date.context_today(self.env.user), days=40)})
        sheet.state = 'flagged'
        self.assertEqual(Hub._field_backlog('admin'), before + 1)

    def test_a_raised_day_is_not_on_either_desks_badge(self):
        """It left the desk that raised it — that is the point of raising."""
        Hub = self.env['lab.ceo.dashboard'].sudo()
        Sheet = self.env['lab.daily.update'].sudo()
        ops_before, mkt_before = (Hub._field_backlog('ops'),
                                  Hub._field_backlog('mkt'))
        user = self.env['res.users'].create({
            'name': 'Badge Exec 2', 'login': 'badge_exec2',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        sheet = Sheet.create({
            'user_id': user.id,
            'date': fields.Date.subtract(
                fields.Date.context_today(self.env.user), days=41)})
        sheet.state = 'flagged'
        self.assertEqual(Hub._field_backlog('ops'), ops_before)
        self.assertEqual(Hub._field_backlog('mkt'), mkt_before)
