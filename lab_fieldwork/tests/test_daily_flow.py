# -*- coding: utf-8 -*-
"""The split manager role and the day sheet that crosses both desks.

What is pinned here is the ORDER and the OWNERSHIP: operations signs first and
only an Operational Manager can; marketing signs second and only a Marketing
Manager can; a sheet nobody signs is escalated; and the whole week lands with
the administrator. Each of these is a rule somebody could quietly regress into
"anyone approves anything", which is exactly the single-role world the split
exists to end.
"""
from datetime import datetime, time, timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import HttpCase, TransactionCase, tagged
from odoo.tools import mute_logger

LAT, LON = 9.9312, 76.2673


class DailyFlowCase(TransactionCase):
    """The fixture the flow and the desks share: four role users, a pinned
    clinic, and helpers to make a finished visit and its day sheet."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['ir.config_parameter'].sudo().set_param(
            'lab_fieldwork.require_attendance', 'False')
        ref = cls.env.ref
        cls.g_exec = ref('lab_fieldwork.group_fieldwork_executive')
        cls.g_base = ref('lab_fieldwork.group_fieldwork_manager')
        cls.g_ops = ref('lab_fieldwork.group_fieldwork_ops_manager')
        cls.g_mkt = ref('lab_fieldwork.group_fieldwork_marketing_manager')
        cls.g_admin = ref('lab_fieldwork.group_fieldwork_admin')
        base_user = [(4, ref('base.group_user').id)]
        cls.exec_user = cls.env['res.users'].create({
            'name': 'Flow Exec', 'login': 'flow.exec@test', 'tz': 'Asia/Kolkata',
            'group_ids': [(4, cls.g_exec.id)] + base_user})
        cls.ops_user = cls.env['res.users'].create({
            'name': 'Flow Ops Manager', 'login': 'flow.ops@test',
            'group_ids': [(4, cls.g_ops.id)] + base_user})
        cls.mkt_user = cls.env['res.users'].create({
            'name': 'Flow Marketing Manager', 'login': 'flow.mkt@test',
            'group_ids': [(4, cls.g_mkt.id)] + base_user})
        cls.admin_user = cls.env['res.users'].create({
            'name': 'Flow FW Admin', 'login': 'flow.admin@test',
            'group_ids': [(4, cls.g_admin.id)] + base_user})
        cls.clinic = cls.env['res.partner'].create({
            'name': 'Flow Clinic', 'partner_latitude': LAT,
            'partner_longitude': LON})
        # The fixtures assign visits to other people, which the own-record mixin
        # only lets a field manager do - same idiom as test_rule_pairing.
        cls.env.user.sudo().write({'group_ids': [(4, cls.g_admin.id)]})
        cls.env.user.invalidate_recordset()
        cls.today = fields.Date.context_today(cls.env.user)

    # ------------------------------------------------------------- fixtures
    def _visit(self, user=None, gps=True, done=True, **vals):
        """A finished visit, written the way the day really leaves it."""
        now = fields.Datetime.now()
        visit = self.env['lab.visit'].create(dict({
            'partner_id': self.clinic.id, 'user_id': (user or self.exec_user).id,
            'date': self.today, 'purpose': 'round'}, **vals))
        writes = {}
        if done:
            writes.update(state='done', outcome='order',
                          check_in=now - timedelta(hours=1), check_out=now)
        if gps:
            writes.update(gps_lat=LAT, gps_lon=LON)
        if writes:
            visit.write(writes)
        return visit

    def _sheet(self, user=None):
        return self.env['lab.daily.update'].create({
            'user_id': (user or self.exec_user).id, 'date': self.today})

    def _visible_menu_ids(self, user):
        # ormcached per group set - stale between users without this.
        self.env.registry.clear_cache()
        menus = self.env['ir.ui.menu'].search([])
        return menus.with_user(user)._filter_visible_menus().ids


@tagged('post_install', '-at_install')
class TestDailyFlow(DailyFlowCase):

    # ------------------------------------------------------------- the split
    def test_the_two_roles_imply_the_shared_base(self):
        self.assertIn(self.g_base, self.ops_user.all_group_ids)
        self.assertIn(self.g_base, self.mkt_user.all_group_ids)
        self.assertNotIn(self.g_mkt, self.ops_user.all_group_ids,
                         "the desks are separate roles, not one role twice")
        self.assertNotIn(self.g_ops, self.mkt_user.all_group_ids)

    def test_the_administrator_holds_both_desks(self):
        self.assertIn(self.g_ops, self.admin_user.all_group_ids)
        self.assertIn(self.g_mkt, self.admin_user.all_group_ids)

    def test_old_managers_land_on_the_operational_desk_once(self):
        icp = self.env['ir.config_parameter'].sudo()
        icp.set_param('lab_fieldwork.manager_split_done', '')
        legacy = self.env['res.users'].create({
            'name': 'Legacy Manager', 'login': 'flow.legacy@test',
            'group_ids': [(4, self.g_base.id),
                          (4, self.env.ref('base.group_user').id)]})
        self.env['res.users']._fw_migrate_manager_split()
        self.assertIn(self.g_ops, legacy.all_group_ids)
        # One-shot: taking the role away later must STAY taken away.
        legacy.write({'group_ids': [(3, self.g_ops.id)]})
        self.env['res.users']._fw_migrate_manager_split()
        self.assertNotIn(self.g_ops, legacy.all_group_ids,
                         "the migration must not re-grant what an administrator "
                         "deliberately removed")

    # ------------------------------------------------------------- day facts
    def test_the_sheet_assembles_itself_from_the_day(self):
        self._visit()
        trip = self.env['lab.trip'].create({
            'user_id': self.exec_user.id, 'date': self.today,
            'odo_start': 1000.0, 'odo_end': 1042.5, 'state': 'closed'})
        sheet = self._sheet()
        self.assertEqual(sheet.visit_done_count, 1)
        self.assertEqual(sheet.gps_ok_count, 1)
        self.assertAlmostEqual(sheet.km, trip.distance)
        self.assertTrue(sheet.is_clean,
                        "GPS at the door, everything closed - a clean day")

    def test_a_far_checkin_makes_the_day_an_exception(self):
        self._visit(gps=False)   # check-in with no fix at a pinned clinic
        sheet = self._sheet()
        self.assertTrue(sheet.flag_gps)
        self.assertFalse(sheet.is_clean)
        self.assertIn('away from the clinic', sheet.anomaly_note)

    def test_open_work_is_flagged_not_hidden(self):
        self._visit(done=False)
        sheet = self._sheet()
        self.assertTrue(sheet.flag_open_work)
        self.assertFalse(sheet.is_clean)

    def test_one_sheet_per_person_per_day(self):
        self._sheet()
        with self.assertRaises(Exception), \
                mute_logger('odoo.sql_db'), self.env.cr.savepoint():
            self._sheet()

    # ------------------------------------------------------------- the chain
    def test_operations_signs_first_and_only_operations_can(self):
        sheet = self._sheet()
        sheet.with_user(self.exec_user).action_submit()
        self.assertEqual(sheet.state, 'submitted')
        with self.assertRaises(UserError):
            sheet.with_user(self.mkt_user).action_ops_approve()
        with self.assertRaises(UserError):
            # Marketing cannot leapfrog the operational desk either.
            sheet.with_user(self.mkt_user).action_marketing_approve()
        sheet.with_user(self.ops_user).action_ops_approve()
        self.assertEqual(sheet.state, 'ops_approved')
        self.assertEqual(sheet.ops_approved_by_id, self.ops_user)

    def test_marketing_signs_second_and_only_marketing_can(self):
        sheet = self._sheet()
        sheet.with_user(self.exec_user).action_submit()
        sheet.with_user(self.ops_user).action_ops_approve()
        with self.assertRaises(UserError):
            sheet.with_user(self.ops_user).action_marketing_approve()
        sheet.with_user(self.mkt_user).write({'marketing_score': '4'})
        sheet.with_user(self.mkt_user).action_marketing_approve()
        self.assertEqual(sheet.state, 'approved')
        self.assertEqual(sheet.approved_by_id, self.mkt_user)

    def test_the_administrator_can_work_either_desk(self):
        sheet = self._sheet()
        sheet.with_user(self.exec_user).action_submit()
        sheet.with_user(self.admin_user).action_ops_approve()
        sheet.with_user(self.admin_user).action_marketing_approve()
        self.assertEqual(sheet.state, 'approved')

    def test_a_send_back_needs_a_reason_and_returns_to_the_executive(self):
        sheet = self._sheet()
        sheet.with_user(self.exec_user).action_submit()
        with self.assertRaises(UserError):
            sheet.with_user(self.ops_user).action_send_back()
        sheet.with_user(self.ops_user).write(
            {'send_back_reason': 'The trip has no closing odometer.'})
        sheet.with_user(self.ops_user).action_send_back()
        self.assertEqual(sheet.state, 'sent_back')
        # …and the executive can fix and resubmit.
        sheet.with_user(self.exec_user).action_submit()
        self.assertEqual(sheet.state, 'submitted')

    def test_what_was_approved_stays_what_was_seen(self):
        sheet = self._sheet()
        sheet.with_user(self.exec_user).action_submit()
        with self.assertRaises(UserError):
            sheet.action_refresh()

    def test_an_approved_sheet_cannot_be_deleted(self):
        sheet = self._sheet()
        sheet.action_submit()
        sheet.with_user(self.ops_user).action_ops_approve()
        with self.assertRaises(UserError):
            sheet.unlink()

    # ------------------------------------------------------------- exceptions
    def test_approve_all_clean_leaves_the_exceptions_on_the_desk(self):
        self._visit()
        clean = self._sheet()
        other = self.env['res.users'].create({
            'name': 'Flow Exec B', 'login': 'flow.execb@test',
            'group_ids': [(4, self.g_exec.id),
                          (4, self.env.ref('base.group_user').id)]})
        self._visit(user=other, gps=False)
        flagged = self._sheet(user=other)
        (clean + flagged).action_submit()
        (clean + flagged).with_user(self.ops_user).action_approve_clean()
        self.assertEqual(clean.state, 'ops_approved',
                         "a clean day needs no reading")
        self.assertEqual(flagged.state, 'submitted',
                         "the exception stays for a person")

    def test_streak_counts_consecutive_approved_clean_days(self):
        Sheet = self.env['lab.daily.update']
        for offset in (3, 2, 1):
            day = self.today - timedelta(days=offset)
            self.env['lab.visit'].create({
                'partner_id': self.clinic.id, 'user_id': self.exec_user.id,
                'date': day, 'purpose': 'round'}).write({
                    'state': 'done', 'outcome': 'order',
                    'check_in': fields.Datetime.now() - timedelta(days=offset),
                    'check_out': fields.Datetime.now() - timedelta(days=offset),
                    'gps_lat': LAT, 'gps_lon': LON})
            sheet = Sheet.create({'user_id': self.exec_user.id, 'date': day})
            sheet.action_submit()
            sheet.with_user(self.ops_user).action_ops_approve()
            sheet.with_user(self.mkt_user).action_marketing_approve()
        today_sheet = self._sheet()
        self.assertEqual(today_sheet.streak, 3)

    # ------------------------------------------------------------- escalation
    def test_a_sheet_nobody_signs_is_raised_with_the_administrators_once(self):
        sheet = self._sheet()
        sheet.action_submit()
        sheet.write({'submitted_at': fields.Datetime.now() - timedelta(days=5)})
        count = self.env['lab.daily.update']._cron_escalate_stale()
        self.assertEqual(count, 1)
        self.assertTrue(sheet.escalated)
        self.assertEqual(self.env['lab.daily.update']._cron_escalate_stale(), 0,
                         "warned once, not every morning")

    # ------------------------------------------------------------- the week
    def test_the_week_assembles_for_the_administrator(self):
        Report = self.env['lab.weekly.report']
        week = (self.today - timedelta(days=6), self.today)
        # The week counts every sheet in it, and this database has its own.
        before = Report._build_week(*week, self.env.company)
        self._visit()
        sheet = self._sheet()
        sheet.action_submit()
        sheet.with_user(self.ops_user).action_ops_approve()
        sheet.with_user(self.mkt_user).write({'marketing_score': '5'})
        sheet.with_user(self.mkt_user).action_marketing_approve()
        vals = Report._build_week(*week, self.env.company)
        self.assertEqual(vals['sheet_total'], before['sheet_total'] + 1)
        self.assertEqual(vals['sheet_approved'], before['sheet_approved'] + 1)
        self.assertIn('Flow Exec', vals['body_html'])

    def test_the_monday_cron_files_the_week_and_tells_the_admins(self):
        self._visit()
        self._sheet().action_submit()
        Report = self.env['lab.weekly.report']
        a_monday = self.today + timedelta(days=(7 - self.today.weekday()) % 7 or 7)
        with patch.object(fields.Date, 'context_today',
                          new=staticmethod(lambda *a, **k: a_monday)):
            self.assertTrue(Report._cron_weekly_report())
            made = Report.search([('date_from', '=', a_monday - timedelta(days=7))])
            self.assertTrue(made)
            # Idempotent: the same Monday never files the week twice.
            self.assertFalse(Report._cron_weekly_report())

    # ------------------------------------------------------------- the menus
    def test_each_desk_sees_its_own_queue_and_not_the_others(self):
        ops_menus = self._visible_menu_ids(self.ops_user)
        mkt_menus = self._visible_menu_ids(self.mkt_user)
        daily = self.env.ref('lab_fieldwork.menu_fw_approvals_daily').id
        activities = self.env.ref('lab_fieldwork.menu_fw_approvals_activities').id
        self.assertIn(daily, ops_menus)
        self.assertNotIn(activities, ops_menus)
        self.assertIn(activities, mkt_menus)
        self.assertNotIn(daily, mkt_menus)

    def test_a_desks_own_menus_do_not_leak_to_the_other_desk(self):
        """The half of the split the "sees its own queue" test cannot catch.

        `menuitem` applies its groups with Command.LINK, so a menu that existed
        before the split KEEPS its old base-manager link and stays visible to
        both desks - which is exactly what happened on the live database:
        Travel, Beats, Targets and Field Cash were all still reaching the
        marketing desk. Only the `-group` prefix removes it. (2026-08-31)
        """
        ops_menus = self._visible_menu_ids(self.ops_user)
        mkt_menus = self._visible_menu_ids(self.mkt_user)
        ops_only = ('menu_fw_ops_approvals', 'menu_fw_ops_cash',
                    'menu_fw_plan_beats', 'menu_fw_plan_targets')
        for xmlid in ops_only:
            menu = self.env.ref('lab_fieldwork.%s' % xmlid)
            self.assertIn(menu.id, ops_menus, "%s is the ops desk's" % xmlid)
            self.assertNotIn(menu.id, mkt_menus,
                             "%s must not reach the marketing desk" % xmlid)
        doors = self.env.ref('lab_fieldwork.menu_fw_plan_new_clinics')
        self.assertIn(doors.id, mkt_menus)
        self.assertNotIn(doors.id, ops_menus,
                         "the company's new doors are marketing's scoreboard")

    def test_the_executive_gets_their_day_sheets_menu(self):
        visible = self._visible_menu_ids(self.exec_user)
        self.assertIn(self.env.ref('lab_fieldwork.menu_fw_my_sheets').id,
                      visible)


@tagged('post_install', '-at_install')
class TestDesks(DailyFlowCase):
    """The three screens that replaced the Control Tower and Field Health.

    Reuses the flow fixture: the desks are READ views over the same records the
    chain writes, so a desk test that built its own parallel fixture would only
    prove the fixture."""

    def test_the_ops_desk_leads_with_its_queue_and_the_sla(self):
        # A difference, not a total: the desk queues every waiting sheet in the
        # database, and this database has real ones.
        before = self.env['lab.desk'].with_user(self.ops_user).get_ops_desk()
        self._visit()
        sheet = self._sheet()
        sheet.with_user(self.exec_user).action_submit()
        desk = self.env['lab.desk'].with_user(self.ops_user).get_ops_desk()
        self.assertIn(sheet.id, [q['id'] for q in desk['queue']])
        row = next(q for q in desk['queue'] if q['id'] == sheet.id)
        self.assertTrue(row['clean'])
        self.assertIn('urgency', row)
        self.assertFalse(row['overdue'], "freshly submitted is inside the SLA")
        self.assertEqual(desk['clean_waiting'], before['clean_waiting'] + 1)
        self.assertEqual(desk['sla_h'],
                         self.env['lab.daily.update']._sla_days() * 24)
        # The live board and the fortnight came over from the tower intact.
        self.assertIn(self.exec_user.id, [r['id'] for r in desk['rows']])
        self.assertEqual(len(desk['trend']), 14)

    def test_a_sheet_past_the_sla_reads_overdue_on_the_desk(self):
        sheet = self._sheet()
        sheet.action_submit()
        sheet.write({'submitted_at': fields.Datetime.now() - timedelta(days=5)})
        queue = self.env['lab.desk'].get_ops_desk()['queue']
        row = next(q for q in queue if q['id'] == sheet.id)
        self.assertTrue(row['overdue'])
        self.assertEqual(row['urgency'], 1.0, "the bar is pinned at full")

    def test_the_marketing_desk_queues_only_what_operations_vouched_for(self):
        waiting = self._sheet()
        waiting.action_submit()
        judged = self.env['lab.daily.update'].create(
            {'user_id': self.exec_user.id, 'date': self.today - timedelta(days=1)})
        judged.action_submit()
        judged.with_user(self.ops_user).action_ops_approve()
        desk = self.env['lab.desk'].with_user(self.mkt_user).get_marketing_desk()
        queued = [q['id'] for q in desk['queue']]
        self.assertIn(judged.id, queued)
        self.assertNotIn(waiting.id, queued,
                         "submitted-but-unvouched days are not marketing's yet")
        for key in ('momentum', 'rescue', 'outcomes', 'months', 'new_clinics',
                    'leaderboard', 'ticker'):
            self.assertIn(key, desk)

    def test_the_command_center_reads_the_whole_machine(self):
        before = self.env['lab.desk'].with_user(
            self.admin_user).get_admin_desk()['funnel']['submitted']
        sheet = self._sheet()
        sheet.action_submit()
        desk = self.env['lab.desk'].with_user(self.admin_user).get_admin_desk()
        self.assertEqual(desk['funnel']['submitted'], before + 1)
        self.assertEqual(len(desk['crons']), 5,
                         "all five flow crons must show their pulse")
        desks = {d['key']: d for d in desk['desks']}
        self.assertIn(self.ops_user.name, desks['ops']['names'])
        self.assertFalse(desks['ops']['warn'])
        for key in ('radius', 'gaps', 'roi', 'ticker'):
            self.assertIn(key, desk)

    def test_the_command_center_warns_when_nobody_holds_a_desk(self):
        # Every direct holder, not only this fixture's: on a database where the
        # role is actually staffed, dropping one person proves nothing.
        # Rolled back with the transaction.
        self.g_ops.sudo().write({'user_ids': [(5, 0, 0)]})
        desks = {d['key']: d for d in
                 self.env['lab.desk'].get_admin_desk()['desks']}
        self.assertTrue(desks['ops']['warn'],
                        "a desk with no named holder runs on administrators "
                        "remembering to do somebody else's job")

    def test_the_ticker_tells_the_flow_in_order(self):
        sheet = self._sheet()
        sheet.with_user(self.exec_user).action_submit()
        sheet.with_user(self.ops_user).action_ops_approve()
        ticker = self.env['lab.desk']._ticker()
        kinds = [t['kind'] for t in ticker if t['sheet_id'] == sheet.id]
        self.assertEqual(kinds, ['ops', 'submitted'],
                         "newest first: the ops signature above the submission")

    def test_each_desk_menu_belongs_to_its_role_alone(self):
        ops_menus = self._visible_menu_ids(self.ops_user)
        mkt_menus = self._visible_menu_ids(self.mkt_user)
        admin_menus = self._visible_menu_ids(self.admin_user)
        ops_desk = self.env.ref('lab_fieldwork.menu_fw_ops_desk').id
        mkt_desk = self.env.ref('lab_fieldwork.menu_fw_marketing_desk').id
        command = self.env.ref('lab_fieldwork.menu_fw_admin_desk').id
        self.assertIn(ops_desk, ops_menus)
        self.assertNotIn(mkt_desk, ops_menus)
        self.assertIn(mkt_desk, mkt_menus)
        self.assertNotIn(ops_desk, mkt_menus)
        self.assertNotIn(command, ops_menus)
        self.assertNotIn(command, mkt_menus)
        # The administrator implies both desks and owns the third screen.
        for menu in (ops_desk, mkt_desk, command):
            self.assertIn(menu, admin_menus)


@tagged('post_install', '-at_install')
class TestDeskBrowser(HttpCase):
    """Load each desk in a real browser.

    An Owl expression error or a broken template is invisible to every
    server-side check and white-screens the client - only a browser sees it.
    (same reasoning as lab_delivery's My Day browser test)
    """

    def _render(self, action_xmlid):
        # base.user_admin implies both desks via the fieldwork Administrator
        # role - granted here so the test does not depend on the database's own
        # role assignments.
        admin = self.env.ref('base.user_admin')
        admin.write({'group_ids': [
            (4, self.env.ref('lab_fieldwork.group_fieldwork_admin').id)]})
        self.browser_js(
            "/odoo/action-%s" % action_xmlid,
            """
            (async () => {
                for (let i = 0; i < 150; i++) {
                    // Odoo 19 counts a browser test as passed only when the
                    // page logs this exact line; a promise that merely resolves
                    // is waited on until the timeout and reported as a failure.
                    if (document.querySelector('.o_fwd_grid')) {
                        console.log('test successful');
                        return;
                    }
                    await new Promise(r => setTimeout(r, 100));
                }
                throw new Error('the desk never rendered past loading');
            })()
            """,
            login="admin")

    def test_the_ops_desk_renders(self):
        self._render("lab_fieldwork.action_ops_desk")

    def test_the_marketing_desk_renders(self):
        self._render("lab_fieldwork.action_marketing_desk")

    def test_the_command_center_renders(self):
        self._render("lab_fieldwork.action_admin_desk")


@tagged('post_install', '-at_install')
class TestDeskCover(DailyFlowCase):
    """Cover: who signs a desk while its manager is away.

    There is one Operational Manager and one Marketing Manager, so a manager on
    leave stops every executive's day sheet. These tests are about the shape of
    the fix as much as the fix: a cover must widen exactly one thing, only
    between its dates, and never become a role somebody keeps.
    """

    def _cover(self, manager, delegate, desk, days=(0, 2), **vals):
        return self.env['lab.desk.cover'].create(dict({
            'manager_id': manager.id, 'delegate_id': delegate.id, 'desk': desk,
            'date_from': self.today + timedelta(days=days[0]),
            'date_to': self.today + timedelta(days=days[1]),
        }, **vals))

    def _submitted(self):
        self._visit()
        sheet = self._sheet()
        sheet.with_user(self.exec_user).action_submit()
        return sheet

    def test_a_stand_in_signs_the_desk_they_cover(self):
        sheet = self._submitted()
        with self.assertRaises(UserError):
            sheet.with_user(self.mkt_user).action_ops_approve()
        self._cover(self.ops_user, self.mkt_user, 'ops')
        sheet.with_user(self.mkt_user).action_ops_approve()
        self.assertEqual(sheet.state, 'ops_approved')
        # Their own name, not the name of the person they stood in for: the
        # audit trail says who actually signed.
        self.assertEqual(sheet.ops_approved_by_id, self.mkt_user)

    def test_a_cover_grants_nothing_before_or_after_its_dates(self):
        self._cover(self.ops_user, self.mkt_user, 'ops', days=(-9, -6))
        sheet = self._submitted()
        with self.assertRaises(UserError):
            sheet.with_user(self.mkt_user).action_ops_approve()
        self._cover(self.ops_user, self.mkt_user, 'ops', days=(3, 6))
        with self.assertRaises(UserError):
            sheet.with_user(self.mkt_user).action_ops_approve()
        self.assertEqual(sheet.state, 'submitted')

    def test_a_cover_widens_signing_and_nothing_else(self):
        """The whole design rests on this: cover is asked about by the approval
        guard, not granted as a group, so it cannot quietly widen reading."""
        self._cover(self.ops_user, self.mkt_user, 'ops')
        self.mkt_user.invalidate_recordset()
        self.assertFalse(self.mkt_user.has_group(
            'lab_fieldwork.group_fieldwork_ops_manager'))
        ops_desk = self.env.ref('lab_fieldwork.menu_fw_ops_desk').id
        self.assertNotIn(ops_desk, self._visible_menu_ids(self.mkt_user))

    def test_a_cover_for_a_desk_its_manager_does_not_hold_is_refused(self):
        with self.assertRaises(ValidationError):
            self._cover(self.exec_user, self.mkt_user, 'ops')

    def test_nobody_covers_their_own_desk(self):
        with self.assertRaises(ValidationError):
            self._cover(self.ops_user, self.ops_user, 'ops')

    def test_a_manager_cannot_arrange_cover_for_somebody_elses_desk(self):
        cover = self._cover(self.ops_user, self.mkt_user, 'ops')
        with self.assertRaises(UserError):
            cover.with_user(self.mkt_user).write({'reason': 'mine now'})

    def test_the_command_center_shows_who_is_standing_in(self):
        cover = self._cover(self.ops_user, self.mkt_user, 'ops', reason='Leave')
        board = self.env['lab.desk'].with_user(
            self.admin_user).get_admin_desk()['covers']
        # This cover among whatever else the database is running.
        mine = next(r for r in board['running'] if r['id'] == cover.id)
        self.assertEqual(mine['delegate'], self.mkt_user.name)
        self.assertEqual(mine['manager'], self.ops_user.name)
        self.assertEqual(mine['reason'], 'Leave')

    def test_the_stand_in_is_told_whose_desk_they_are_on(self):
        self._cover(self.ops_user, self.mkt_user, 'ops', reason='Leave')
        banner = self.env['lab.desk'].with_user(
            self.mkt_user).get_ops_desk()['cover']
        self.assertTrue(banner)
        self.assertEqual(banner['manager'], self.ops_user.name)
        # And nobody else is told they are covering.
        self.assertFalse(self.env['lab.desk'].with_user(
            self.admin_user).get_ops_desk()['cover'])


@tagged('post_install', '-at_install')
class TestNudge(DailyFlowCase):
    """Asking about a day without rejecting it."""

    def _submitted(self):
        self._visit()
        sheet = self._sheet()
        sheet.with_user(self.exec_user).action_submit()
        return sheet

    def test_a_nudge_leaves_the_sheet_on_the_desk(self):
        """The point of the feature: send-back wipes the signature and pushes
        the sheet out of the queue, which is far too much for one missing
        odometer photo."""
        sheet = self._submitted()
        sheet.with_user(self.ops_user).action_nudge(message="Odometer photo?")
        self.assertEqual(sheet.state, 'submitted')
        self.assertEqual(sheet.nudge_count, 1)
        self.assertTrue(sheet.last_nudge_at)

    def test_the_executive_is_actually_told(self):
        sheet = self._submitted()
        before = self.env['mail.message'].search_count(
            [('model', '=', 'lab.daily.update'), ('res_id', '=', sheet.id)])
        sheet.with_user(self.ops_user).action_nudge(message="Check the km.")
        after = self.env['mail.message'].search(
            [('model', '=', 'lab.daily.update'), ('res_id', '=', sheet.id)])
        self.assertGreater(len(after), before)
        self.assertTrue(any('Check the km.' in (m.body or '') for m in after))

    def test_the_desk_card_carries_the_count(self):
        sheet = self._submitted()
        sheet.with_user(self.ops_user).action_nudge()
        sheet.with_user(self.ops_user).action_nudge()
        queue = self.env['lab.desk'].with_user(self.ops_user).get_ops_desk()['queue']
        row = next(q for q in queue if q['id'] == sheet.id)
        self.assertEqual(row['nudges'], 2)

    def test_a_signed_day_cannot_be_nudged(self):
        sheet = self._submitted()
        sheet.with_user(self.ops_user).action_ops_approve()
        sheet.with_user(self.mkt_user).action_marketing_approve()
        with self.assertRaises(UserError):
            sheet.with_user(self.ops_user).action_nudge()


@tagged('post_install', '-at_install')
class TestDeskDayTravel(DailyFlowCase):
    """Any desk can be read for a past day.

    The rule all three share: the WINDOWED figures move to the pinned day, the
    queue stays live (what is waiting to be signed is waiting now), and the
    future is clamped away - a desk for tomorrow would only ever show that
    nobody has worked yet.
    """

    def test_every_desk_accepts_a_day_and_says_which_it_shows(self):
        Desk = self.env['lab.desk']
        past = fields.Date.to_string(self.today - timedelta(days=3))
        for method in ('get_ops_desk', 'get_marketing_desk', 'get_admin_desk'):
            data = getattr(Desk, method)(past)
            self.assertEqual(data['date'], past, method)
            self.assertFalse(data['is_today'], method)
            self.assertEqual(data['today'], fields.Date.to_string(self.today))

    def test_the_future_is_clamped_to_today(self):
        Desk = self.env['lab.desk']
        ahead = fields.Date.to_string(self.today + timedelta(days=5))
        for method in ('get_ops_desk', 'get_marketing_desk', 'get_admin_desk'):
            data = getattr(Desk, method)(ahead)
            self.assertTrue(data['is_today'], method)

    def test_the_queue_stays_live_whatever_day_is_pinned(self):
        self._visit()
        sheet = self._sheet()
        sheet.with_user(self.exec_user).action_submit()
        past = fields.Date.to_string(self.today - timedelta(days=10))
        desk = self.env['lab.desk'].with_user(self.ops_user).get_ops_desk(past)
        self.assertIn(sheet.id, [q['id'] for q in desk['queue']],
                      "pinning a day must never hide the work waiting now")

    def test_the_admin_week_is_the_pinned_week_not_the_current_one(self):
        Desk = self.env['lab.desk'].with_user(self.admin_user)
        pin = fields.Date.to_string(self.today - timedelta(days=10))
        pinned_before = Desk.get_admin_desk(pin)['funnel']['week_total']
        live_before = Desk.get_admin_desk()['funnel']['week_total']
        self._visit()
        self._sheet()                     # dated today, outside the pinned week
        self.assertEqual(Desk.get_admin_desk(pin)['funnel']['week_total'],
                         pinned_before,
                         "a sheet from after the pinned week says nothing about it")
        self.assertEqual(Desk.get_admin_desk()['funnel']['week_total'],
                         live_before + 1)


@tagged('post_install', '-at_install')
class TestDeskSignals(DailyFlowCase):
    """The ten signals added 2026-08-31: each desk gains things to ACT on, and
    every figure here is asserted as a difference or a membership - this
    database has real data under the tests."""

    # ------------------------------------------------------------------- ops
    def test_travel_waits_on_the_desk_and_signs_through_the_guard(self):
        trip = self.env['lab.trip'].create({
            'user_id': self.exec_user.id, 'date': self.today,
            'odo_start': 100.0, 'odo_end': 142.0, 'state': 'closed'})
        rows = self.env['lab.desk'].with_user(self.ops_user)._trip_rows(limit=100)
        mine = next(r for r in rows if r['id'] == trip.id)
        self.assertEqual(mine['km'], 42.0)
        with self.assertRaises(UserError):
            trip.with_user(self.exec_user).action_approve()
        trip.with_user(self.ops_user).action_approve()
        rows = self.env['lab.desk'].with_user(self.ops_user)._trip_rows(limit=100)
        self.assertNotIn(trip.id, [r['id'] for r in rows],
                         "a signed claim leaves the desk")

    def test_the_same_flag_three_times_is_a_pattern(self):
        for offset in range(5):
            self.env['lab.daily.update'].sudo().create({
                'user_id': self.exec_user.id,
                'date': self.today - timedelta(days=offset),
            }).write({'flag_gps': offset < 3, 'is_clean': offset >= 3})
        habits = self.env['lab.desk']._habit_radar()
        mine = [h for h in habits if h['user_id'] == self.exec_user.id]
        self.assertEqual([h['flag'] for h in mine], ['gps'])
        self.assertEqual(mine[0]['count'], 3)
        self.assertEqual(len(mine[0]['sheet_ids']), 3)

    def test_two_of_five_is_still_just_days(self):
        for offset in range(5):
            self.env['lab.daily.update'].sudo().create({
                'user_id': self.exec_user.id,
                'date': self.today - timedelta(days=offset),
            }).write({'flag_km_outlier': offset < 2})
        habits = self.env['lab.desk']._habit_radar()
        self.assertFalse([h for h in habits
                          if h['user_id'] == self.exec_user.id])

    def test_the_forecast_counts_only_the_about_to_breach(self):
        sla = self.env['lab.desk']._sla_hours()
        queue = [
            {'id': 1, 'age_h': sla - 6, 'overdue': False},   # breaches in 12h
            {'id': 2, 'age_h': 1.0, 'overdue': False},        # fresh
            {'id': 3, 'age_h': sla + 5, 'overdue': True},     # already gone
        ]
        forecast = self.env['lab.desk']._sla_forecast(queue, horizon_h=12)
        self.assertEqual(forecast['ids'], [1],
                         "not the fresh one, not the already-overdue one")

    def test_the_heatmap_reads_the_fortnight(self):
        self._visit()
        clean = self._sheet()
        back = self.env['lab.daily.update'].sudo().create({
            'user_id': self.exec_user.id, 'date': self.today - timedelta(days=1)})
        back.write({'state': 'sent_back'})
        heat = self.env['lab.desk']._discipline_heatmap(self.today)
        row = next(r for r in heat['rows'] if r['user_id'] == self.exec_user.id)
        by_day = {c['day']: c for c in row['cells']}
        self.assertEqual(by_day[fields.Date.to_string(self.today)]['code'], 'clean')
        self.assertEqual(by_day[fields.Date.to_string(self.today)]['sheet_id'],
                         clean.id)
        self.assertEqual(by_day[fields.Date.to_string(
            self.today - timedelta(days=1))]['code'], 'back')
        self.assertEqual(by_day[fields.Date.to_string(
            self.today - timedelta(days=5))]['code'], 'off')

    # ------------------------------------------------------------- marketing
    def test_new_doors_conversion_counts_the_second_order(self):
        Desk = self.env['lab.desk']
        before = Desk._new_door_conversion(self.today)
        team = self.env['crm.team'].sudo().search([], limit=1)
        door = self.env['res.partner'].sudo().create({
            'name': 'Conversion Door', 'team_id': team.id})
        conv = Desk._new_door_conversion(self.today)
        self.assertEqual(conv['opened'], before['opened'] + 1)
        self.assertEqual(conv['repeat'], before['repeat'])
        for _n in range(2):
            self.env['sale.order'].sudo().create(
                {'partner_id': door.id}).write({'state': 'sale'})
        conv = Desk._new_door_conversion(self.today)
        self.assertEqual(conv['ordered'], before['ordered'] + 1)
        self.assertEqual(conv['repeat'], before['repeat'] + 1,
                         "the second order is the business")

    def test_score_momentum_reads_the_two_fortnights(self):
        Sheet = self.env['lab.daily.update'].sudo()
        for offset, score in ((0, '4'), (1, '5'), (16, '3')):
            Sheet.create({
                'user_id': self.exec_user.id,
                'date': self.today - timedelta(days=offset),
            }).write({'marketing_score': score})
        rows = self.env['lab.desk']._score_momentum(self.today, limit=100)
        mine = next(r for r in rows if r['user_id'] == self.exec_user.id)
        self.assertEqual(mine['now'], 4.5)
        self.assertEqual(mine['before'], 3.0)
        self.assertEqual(mine['delta'], 1.5)

    def test_a_rescue_visit_is_one_create_away_for_marketing(self):
        visit = self.env['lab.visit'].with_user(self.mkt_user).create({
            'partner_id': self.clinic.id, 'user_id': self.exec_user.id,
            'date': self.today + timedelta(days=1), 'purpose': 'order',
            'note': '[rescue] planned from the marketing desk'})
        self.assertEqual(visit.state, 'planned')
        self.assertEqual(visit.user_id, self.exec_user,
                         "planned FOR the executive, not for the planner")

    # ----------------------------------------------------------------- admin
    def test_velocity_reads_hours_once_a_desk_has_signed(self):
        self._visit()
        sheet = self._sheet()
        sheet.with_user(self.exec_user).action_submit()
        sheet.with_user(self.ops_user).action_ops_approve()
        vel = self.env['lab.desk'].with_user(
            self.admin_user)._approval_velocity(self.today)
        self.assertIsNotNone(vel['ops_now'])
        self.assertGreaterEqual(vel['ops_now'], 0.0)

    def test_nudge_debt_is_twice_asked_and_still_waiting(self):
        self._visit()
        sheet = self._sheet()
        sheet.with_user(self.exec_user).action_submit()
        sheet.with_user(self.ops_user).action_nudge()
        ids = [d['id'] for d in self.env['lab.desk']._nudge_debt(limit=100)]
        self.assertNotIn(sheet.id, ids, "asked once is just a question")
        sheet.with_user(self.ops_user).action_nudge()
        ids = [d['id'] for d in self.env['lab.desk']._nudge_debt(limit=100)]
        self.assertIn(sheet.id, ids)

    def test_the_week_so_far_files_now_and_replaces_itself(self):
        Report = self.env['lab.weekly.report']
        act = Report.with_user(self.admin_user).action_file_week_so_far()
        first = Report.browse(act['res_id'])
        self.assertTrue(first.is_interim)
        self.assertIn('so far', first.name)
        act = Report.with_user(self.admin_user).action_file_week_so_far()
        again = Report.browse(act['res_id'])
        self.assertFalse(first.exists(),
                         "refiling replaces the snapshot, never stacks copies")
        self.assertTrue(again.is_interim)

    def test_only_an_administrator_files_the_week(self):
        with self.assertRaises(UserError):
            self.env['lab.weekly.report'].with_user(
                self.mkt_user).action_file_week_so_far()

    def test_the_monday_cron_ignores_the_interim(self):
        """The snapshot must never eat the real report."""
        Report = self.env['lab.weekly.report']
        a_monday = self.today + timedelta(days=(7 - self.today.weekday()) % 7 or 7)
        with patch.object(fields.Date, 'context_today',
                          new=staticmethod(lambda *a, **k: a_monday)):
            Report.with_user(self.admin_user).action_file_week_so_far()
            self.assertTrue(Report._cron_weekly_report(),
                            "an interim for the same Monday must not satisfy "
                            "the idempotency check")


@tagged('post_install', '-at_install')
class TestSheetVerification(DailyFlowCase):
    """The smart buttons: every figure on a sheet opens the records it was
    added up from, so an approver can check any number they doubt."""

    def test_the_visit_button_opens_exactly_the_days_visits(self):
        visit = self._visit()
        other_day = self.env['lab.visit'].create({
            'partner_id': self.clinic.id, 'user_id': self.exec_user.id,
            'date': self.today - timedelta(days=1), 'purpose': 'round'})
        sheet = self._sheet()
        act = sheet.action_open_visits()
        found = self.env['lab.visit'].search(act['domain'])
        self.assertIn(visit, found)
        self.assertNotIn(other_day, found,
                         "yesterday's visit is yesterday's sheet's business")

    def test_the_orders_button_follows_the_visits(self):
        visit = self._visit()
        order = self.env['sale.order'].sudo().create({
            'partner_id': self.clinic.id, 'visit_id': visit.id})
        sheet = self._sheet()
        act = sheet.action_open_orders()
        self.assertIn(order, self.env['sale.order'].sudo().search(act['domain']))

    def test_the_travel_button_opens_the_days_trip(self):
        trip = self.env['lab.trip'].create({
            'user_id': self.exec_user.id, 'date': self.today,
            'odo_start': 10.0, 'odo_end': 30.0, 'state': 'closed'})
        sheet = self._sheet()
        act = sheet.action_open_trips()
        self.assertIn(trip, self.env['lab.trip'].search(act['domain']))

    def test_the_weekly_report_opens_its_own_sheets(self):
        self._visit()
        sheet = self._sheet()
        report = self.env['lab.weekly.report'].sudo().create(
            self.env['lab.weekly.report']._build_week(
                self.today - timedelta(days=6), self.today, self.env.company))
        act = report.action_open_sheets()
        self.assertIn(sheet,
                      self.env['lab.daily.update'].search(act['domain']))

    def test_the_dispatch_and_cash_buttons_count_what_they_open(self):
        visit = self._visit(collected=1500.0)
        sheet = self._sheet()
        self.assertEqual(sheet.cash_visit_count, 1)
        act = sheet.action_open_cash_visits()
        self.assertIn(visit, self.env['lab.visit'].search(act['domain']))
        # No dispatches today: count zero, and the domain opens nothing.
        self.assertEqual(sheet.dispatch_count,
                         self.env['lab.delivery'].sudo().search_count(
                             sheet._dispatch_domain(*sheet._day_bounds_utc())))

    def test_a_manager_may_read_the_attendance_behind_the_sheet(self):
        self.assertTrue(self.env['hr.attendance'].with_user(
            self.ops_user).has_access('read'),
            "the smart button opens attendance; a manager without read "
            "would crash on the click")


@tagged('post_install', '-at_install')
class TestRoundTwoSignals(DailyFlowCase):
    """Round two (2026-08-31): the health check that leads the Command Center,
    cash held across days, and the month's targets on the marketing desk."""

    def test_the_health_check_answers_in_sentences(self):
        health = self.env['lab.desk'].with_user(self.admin_user)._health()
        self.assertGreaterEqual(len(health['checks']), 6)
        for check in health['checks']:
            self.assertIn('ok', check)
            self.assertTrue(check['text'], "an empty sentence answers nothing")

    def test_a_late_sheet_turns_its_health_line_red(self):
        self._visit()
        sheet = self._sheet()
        sheet.with_user(self.exec_user).action_submit()
        sheet.write({'submitted_at': fields.Datetime.now() - timedelta(days=9)})
        health = self.env['lab.desk'].with_user(self.admin_user)._health()
        late = next(c for c in health['checks'] if sheet.id in c['ids'])
        self.assertFalse(late['ok'])
        self.assertFalse(health['ok'])

    def test_cash_held_counts_across_days_with_the_oldest_day(self):
        for offset in (0, 3):
            self.env['lab.visit'].create({
                'partner_id': self.clinic.id, 'user_id': self.exec_user.id,
                'date': self.today - timedelta(days=offset), 'purpose': 'payment',
            }).write({'state': 'done', 'outcome': 'payment',
                      'collected': 1000.0, 'pay_mode': 'cash'})
        held = self.env['lab.desk']._cash_held(limit=100)
        mine = next(r for r in held if r['user_id'] == self.exec_user.id)
        self.assertEqual(mine['amount'], 2000.0)
        self.assertEqual(mine['days'], 3, "the oldest rupee sets the age")
        self.assertEqual(len(mine['ids']), 2)

    def test_targets_progress_reaches_the_marketing_desk(self):
        target = self.env['lab.target'].sudo().create({
            'user_id': self.exec_user.id,
            'month': self.today.replace(day=1),
            'goal_value': 10000.0, 'state': 'open'})
        rows = self.env['lab.desk'].with_user(
            self.mkt_user)._targets_snapshot(self.today, limit=100)
        mine = next(r for r in rows if r['id'] == target.id)
        self.assertEqual(mine['goal_value'], 10000.0)
        self.assertGreaterEqual(mine['progress'], 0)

    def test_the_ops_board_carries_the_phone(self):
        self.exec_user.partner_id.sudo().write({'phone': '+91 98765 43210'})
        self._visit()
        desk = self.env['lab.desk'].with_user(self.ops_user).get_ops_desk()
        row = next(r for r in desk['rows'] if r['id'] == self.exec_user.id)
        self.assertEqual(row['phone'], '+91 98765 43210')


@tagged('post_install', '-at_install')
class TestWeeklyReportDepth(DailyFlowCase):
    """The weekly report's own judgements (2026-08-31): comparison, stars,
    speed, exceptions - all stored at filing time, because a report whose
    verdicts drift afterwards is not a record."""

    def _week_sheet(self, score=None, approve=True):
        self._visit(collected=2000.0)
        sheet = self._sheet()
        sheet.with_user(self.exec_user).action_submit()
        if approve:
            sheet.with_user(self.ops_user).action_ops_approve()
            if score:
                sheet.with_user(self.mkt_user).write({'marketing_score': score})
            sheet.with_user(self.mkt_user).action_marketing_approve()
        return sheet

    def _build(self):
        Report = self.env['lab.weekly.report']
        return Report._build_week(self.today - timedelta(days=6), self.today,
                                  self.env.company)

    def test_the_report_judges_as_well_as_counts(self):
        self._week_sheet(score='4')
        vals = self._build()
        for key in ('approved_pct', 'clean_pct', 'delta_visits',
                    'delta_collected', 'star_exec', 'exceptions_total',
                    'ops_median_h', 'mkt_median_h', 'summary_text'):
            self.assertIn(key, vals)
        self.assertGreater(vals['approved_pct'], 0)
        self.assertIn('Field Work', vals['summary_text'])
        self.assertIn(self.exec_user.name, vals['summary_text'])

    def test_the_star_is_who_collected_most(self):
        self._week_sheet()
        vals = self._build()
        # Our fixture executive collected 2000 today; whether they beat the
        # database's own people is the database's business - the field must
        # simply name SOMEBODY once money moved.
        self.assertTrue(vals['star_exec'])

    def test_the_medians_read_from_the_stamps(self):
        sheet = self._week_sheet()
        sheet.write({'submitted_at': fields.Datetime.now() - timedelta(hours=10),
                     'ops_approved_at': fields.Datetime.now() - timedelta(hours=4)})
        vals = self._build()
        self.assertGreater(vals['ops_median_h'], 0)

    def test_send_again_is_the_administrators_button(self):
        report = self.env['lab.weekly.report'].sudo().create(self._build())
        with self.assertRaises(UserError):
            report.with_user(self.mkt_user).action_send_again()
        report.with_user(self.admin_user).action_send_again()

    def test_the_pdf_renders(self):
        report = self.env['lab.weekly.report'].sudo().create(self._build())
        html = self.env['ir.actions.report']._render_qweb_html(
            'lab_fieldwork.report_weekly_document', report.ids)[0]
        self.assertIn(report.name.encode(), html)


@tagged('post_install', '-at_install')
class TestCommandCharts(DailyFlowCase):
    """The Command Center's three chart series."""

    def test_the_flow_series_counts_filed_and_approved_per_day(self):
        self._visit()
        sheet = self._sheet()
        sheet.with_user(self.exec_user).action_submit()
        flow = self.env['lab.desk']._flow_series(self.today)
        self.assertEqual(len(flow), 14)
        today_row = next(r for r in flow
                         if r['day'] == fields.Date.to_string(self.today))
        self.assertGreaterEqual(today_row['filed'], 1)
        self.assertGreaterEqual(today_row['filed'], today_row['approved'])

    def test_the_speed_trend_covers_six_weeks(self):
        trend = self.env['lab.desk']._velocity_series(self.today)
        self.assertEqual(len(trend), 6)
        for week in trend:
            self.assertGreaterEqual(week['ops'], 0.0)

    def test_the_exception_mix_counts_flagged_days_by_kind(self):
        self.env['lab.daily.update'].sudo().create({
            'user_id': self.exec_user.id, 'date': self.today,
        }).write({'flag_gps': True, 'is_clean': False})
        mix = self.env['lab.desk']._exception_mix(self.today)
        gps = next(c for c in mix if c['key'] == 'gps')
        self.assertGreaterEqual(gps['count'], 1)
        self.assertLessEqual(sum(c['pct'] for c in mix), 100.5)


@tagged('post_install', '-at_install')
class TestWeeklySalesAnalysis(DailyFlowCase):
    """The report's sales side (2026-08-31): salesperson table, routes, strike
    rates, and the fix-next-week list."""

    def _sold(self, value=5000.0):
        team = self.env['crm.team'].sudo().search([], limit=1)
        order = self.env['sale.order'].sudo().create({
            'partner_id': self.clinic.id, 'user_id': self.exec_user.id,
            'team_id': team.id})
        order.write({'state': 'sale', 'amount_total': value})
        return order

    def _build(self):
        Report = self.env['lab.weekly.report']
        return Report._build_week(self.today - timedelta(days=6), self.today,
                                  self.env.company)

    def test_sales_are_grouped_by_salesperson_with_deltas(self):
        self._sold()
        vals = self._build()
        sales = self.env['lab.weekly.report']._sales_week(
            self.today - timedelta(days=6), self.today, self.env.company)
        mine = [sp for sp in sales['salespeople']
                if sp['name'] == self.exec_user.name]
        self.assertTrue(mine, "the salesperson table must carry the seller")
        self.assertGreaterEqual(mine[0]['orders'], 1)
        self.assertIn('Sales, by salesperson', vals['body_html'])
        self.assertIn('delta', mine[0])

    def test_the_analysis_tab_is_stored_with_the_report(self):
        self._sold()
        self._visit()
        self._sheet()
        vals = self._build()
        self.assertIn('insights_html', vals)
        for probe in ('Strike rate', 'Coverage and rhythm'):
            self.assertIn(probe, vals['insights_html'])

    def test_a_weak_strike_rate_lands_in_fix_next_week(self):
        for _n in range(12):
            self._visit()                    # 12 visits, zero orders
        sheet = self._sheet()
        Report = self.env['lab.weekly.report']
        sales = Report._sales_week(self.today - timedelta(days=6), self.today,
                                   self.env.company)
        points = Report._focus_points(
            {'order_value': 0, 'collected': 0, 'exceptions_total': 0,
             'sheet_total': 1}, sheet, sales)
        self.assertTrue(any('strike rate' in point.lower()
                            for point in points),
                        "12 visits and no orders is the thing to fix")


@tagged('post_install', '-at_install')
class TestBusinessCommandCenter(DailyFlowCase):
    """The Command Center's business turn (2026-08-31): unapproved matrix,
    weekly visits by route/person, neglected doctors, open receivables via
    the countback, and undelivered dispatches on every desk."""

    def test_the_pending_matrix_buckets_by_state_and_person(self):
        self._visit()
        sheet = self._sheet()
        sheet.with_user(self.exec_user).action_submit()
        matrix = self.env['lab.desk']._pending_matrix()
        row = next(r for r in matrix['rows']
                   if r['user_id'] == self.exec_user.id)
        self.assertIn(sheet.id, row['ops'])
        self.assertGreaterEqual(matrix['total'], 1)
        sheet.with_user(self.ops_user).action_ops_approve()
        matrix = self.env['lab.desk']._pending_matrix()
        row = next(r for r in matrix['rows']
                   if r['user_id'] == self.exec_user.id)
        self.assertIn(sheet.id, row['mkt'],
                      "an ops-approved sheet moves to the marketing bucket")

    def test_route_week_counts_both_ways(self):
        team = self.env['crm.team'].sudo().search([], limit=1)
        self.clinic.write({'team_id': team.id})
        before = self.env['lab.desk']._route_week_visits(self.today)
        self._visit()
        after = self.env['lab.desk']._route_week_visits(self.today)
        self.assertEqual(after['total'], before['total'] + 1)
        mine = [p for p in after['people'] if p['name'] == self.exec_user.name]
        self.assertTrue(mine)

    def test_a_valuable_unvisited_doctor_surfaces_until_visited(self):
        team = self.env['crm.team'].sudo().search([], limit=1)
        door = self.env['res.partner'].sudo().create({
            'name': 'Neglected Door', 'team_id': team.id})
        order = self.env['sale.order'].sudo().create({'partner_id': door.id})
        order.write({'state': 'sale', 'amount_total': 99999.0,
                     'date_order': fields.Datetime.now() - timedelta(days=40)})
        quiet = self.env['lab.desk']._unvisited_doctors(self.today, limit=500)
        self.assertIn(door.id, quiet['ids'])
        self.env['lab.visit'].create({
            'partner_id': door.id, 'user_id': self.exec_user.id,
            'date': self.today, 'purpose': 'round'})
        quiet = self.env['lab.desk']._unvisited_doctors(self.today, limit=500)
        self.assertNotIn(door.id, quiet['ids'],
                         "one visit takes the door off the neglect list")

    def test_receivables_come_from_the_countback(self):
        data = self.env['lab.desk']._open_receivables()
        self.assertTrue(data, "lab_collections is installed here")
        summary = self.env['lab.collection.performance'].outstanding_summary()
        self.assertEqual(data['overdue'], summary['overdue'],
                         "one published answer for what is owed - no residuals")

    def test_undelivered_reaches_all_three_desks(self):
        for method in ('get_ops_desk', 'get_marketing_desk', 'get_admin_desk'):
            payload = getattr(self.env['lab.desk'].with_user(
                self.admin_user), method)()
            self.assertIn('undelivered', payload, method)
            self.assertIn('total', payload['undelivered'])

    def test_week_sales_and_targets_rollup_load(self):
        sales = self.env['lab.desk']._week_sales_snapshot(self.today)
        for key in ('orders', 'value', 'collected', 'ratio', 'top'):
            self.assertIn(key, sales)
        rollup = self.env['lab.desk']._targets_rollup(self.today)
        self.assertIn('behind', rollup)


@tagged('post_install', '-at_install')
class TestClinicMoneyFacts(DailyFlowCase):
    """Every clinic row on the desks carries: outstanding now, last SO date,
    last payment date - outstanding through the countback, last payment as
    the last posted receivable CREDIT (receipts here are journal entries,
    account.payment would answer almost never)."""

    def test_the_three_facts_come_back_per_clinic(self):
        order = self.env['sale.order'].sudo().create(
            {'partner_id': self.clinic.id})
        order.write({'state': 'sale'})
        facts = self.env['lab.desk']._clinic_money_facts([self.clinic.id])
        mine = facts[self.clinic.id]
        for key in ('outstanding', 'last_so', 'last_pay'):
            self.assertIn(key, mine)
        self.assertTrue(mine['last_so'], "the order just placed must date it")

    def test_outstanding_agrees_with_the_countback(self):
        Perf = self.env['lab.collection.performance'].sudo()
        debits = Perf._open_debits(self.env.company)
        with_debt = next((d['partner_id'] for d in debits if d['open'] > 0),
                         None)
        if not with_debt:
            self.skipTest("no open debit on this database")
        facts = self.env['lab.desk']._clinic_money_facts([with_debt])
        expected = sum(d['open'] for d in debits
                       if d['partner_id'] == with_debt)
        self.assertAlmostEqual(facts[with_debt]['outstanding'], expected,
                               places=2)

    def test_the_lists_carry_the_facts(self):
        quiet = self.env['lab.desk']._unvisited_doctors(self.today, limit=3)
        for row in quiet['rows']:
            self.assertIn('outstanding', row)
            self.assertIn('last_so', row)
            self.assertIn('last_pay', row)
        rescue = self.env['lab.desk']._rescue_list(limit=3)
        for row in rescue:
            self.assertIn('outstanding', row)
        recv = self.env['lab.desk']._open_receivables()
        for row in recv['debtors']:
            self.assertIn('last_pay', row)


@tagged('post_install', '-at_install')
class TestSheetFormExtras(DailyFlowCase):
    """The day sheet form's tiles and day-flipping (2026-08-31)."""

    def test_the_summary_tiles_render_from_the_snapshot(self):
        self._visit(collected=3000.0)
        sheet = self._sheet()
        html = sheet.summary_html
        for probe in ('visits', 'collected', 'not submitted yet'):
            self.assertIn(probe, html)
        sheet.with_user(self.exec_user).action_submit()
        sheet.with_user(self.ops_user).action_ops_approve()
        html = sheet.summary_html
        self.assertIn('Operations:', html)
        self.assertIn('waiting for Marketing', html)

    def test_the_delta_is_against_their_own_month(self):
        for offset in (3, 4, 5):
            self.env['lab.daily.update'].sudo().create({
                'user_id': self.exec_user.id,
                'date': self.today - timedelta(days=offset),
            }).write({'visit_done_count': 2})
        self._visit()
        sheet = self._sheet()
        sheet.sudo().write({'visit_done_count': 8})
        sheet.invalidate_recordset()
        self.assertIn('vs their usual', sheet.summary_html,
                      "8 visits against a usual 2 must say so")

    def test_day_flipping_stays_on_the_same_executive(self):
        older = self.env['lab.daily.update'].sudo().create({
            'user_id': self.exec_user.id,
            'date': self.today - timedelta(days=2)})
        self.env['lab.daily.update'].sudo().create({
            'user_id': self.ops_user.id,
            'date': self.today - timedelta(days=1)})   # someone else's day
        sheet = self._sheet()
        act = sheet.action_open_prev_day()
        self.assertEqual(act['res_id'], older.id,
                         "previous must skip other people's sheets")
        with self.assertRaises(UserError):
            older.action_open_adjacent(-1)


@tagged('post_install', '-at_install')
class TestReceivableColumns(DailyFlowCase):
    """The three clinic lists as columns (2026-08-31): the sale figure is
    gone, and the unvisited panel totals the open receivable over the WHOLE
    neglected set, not the eight rows shown."""

    def test_unvisited_totals_the_whole_set_and_ranks_by_owed(self):
        quiet = self.env['lab.desk']._unvisited_doctors(self.today, limit=5)
        self.assertIn('outstanding_total', quiet)
        if len(quiet['rows']) >= 2:
            owed = [r['outstanding'] for r in quiet['rows']]
            self.assertEqual(owed, sorted(owed, reverse=True),
                             "the biggest debt floats to the top")
        for row in quiet['rows']:
            self.assertNotIn('value', row,
                             "the sale figure was asked off this panel")

    def test_the_total_is_the_sum_of_every_neglected_clinic(self):
        quiet = self.env['lab.desk']._unvisited_doctors(self.today, limit=1)
        facts = self.env['lab.desk']._clinic_money_facts(quiet['ids'])
        self.assertAlmostEqual(
            quiet['outstanding_total'],
            round(sum(f['outstanding'] for f in facts.values()), 2), places=2)

    def test_debtor_amounts_are_the_overdue_slice_not_total_open(self):
        """Caught by the client: a clinic that paid on 13 Aug showed with its
        TOTAL open under a '30+ days' heading. The membership was right (its
        old balance survived the payment); the figure was not - it included
        fresh invoices. Every shown amount must equal that clinic's own
        sum of >30-day open debits, nothing more."""
        recv = self.env['lab.desk']._open_receivables()
        if not recv or not recv['debtors']:
            self.skipTest("no overdue debtors on this database")
        Perf = self.env['lab.collection.performance'].sudo()
        debits = Perf._open_debits(self.env.company,
                                   partner_ids=[d['id'] for d in
                                                recv['debtors']])
        for debtor in recv['debtors']:
            expected = round(sum(
                d['open'] for d in debits
                if d['partner_id'] == debtor['id']
                and d['days'] > recv['overdue_days']), 2)
            self.assertAlmostEqual(
                debtor['open'], expected, places=2,
                msg="%s must show only money older than %s days"
                    % (debtor['name'], recv['overdue_days']))

    def test_the_marketing_panels_reach_the_command_center(self):
        """The client's screenshots, read back to them: going quiet, the
        outcome donut, and the new-clinic funnel all render on the Command
        Center too."""
        payload = self.env['lab.desk'].with_user(
            self.admin_user).get_admin_desk()
        self.assertIn('rescue', payload)
        self.assertIn('executives', payload,
                      "the rescue planner needs someone to send")
        self.assertIn('outcomes', payload)
        self.assertIn('conversion', payload)


@tagged('post_install', '-at_install')
class TestReceivablesAreAccounting(DailyFlowCase):
    """The client asked whether "Payments not received, 30+ days" was built
    from field-work collections. It never was - but nothing on the screen
    said so, and nothing in the tests forbade it drifting there. Both are
    fixed here. (2026-09-01)"""

    def test_field_collections_cannot_move_the_receivable_figure(self):
        """The proof, as a test: zero every rupee the field says it took and
        the overdue figure must not move by one paisa."""
        desk = self.env['lab.desk']
        before = desk._open_receivables()
        if not before:
            self.skipTest("lab_collections is not installed")
        self.env.cr.execute("UPDATE lab_visit SET collected = 0")
        self.env.invalidate_all()
        after = self.env['lab.desk']._open_receivables()
        self.assertAlmostEqual(after['overdue'], before['overdue'], places=2,
                               msg="the receivable panel must read the ledger, "
                                   "never lab.visit.collected")

    def test_the_week_reports_both_sources_separately(self):
        """Field-collected and accounting-received are different numbers and
        the desk must carry both, so neither can be mistaken for the other."""
        sales = self.env['lab.desk']._week_sales_snapshot(self.today)
        self.assertIn('collected', sales)      # field team
        self.assertIn('banked', sales)         # accounting
        self.assertGreaterEqual(sales['banked'], 0.0)

    def test_the_received_over_sold_ratio_uses_accounting(self):
        sales = self.env['lab.desk']._week_sales_snapshot(self.today)
        expected = round(sales['banked'] * 100.0 / sales['value']) \
            if sales['value'] else 0
        self.assertEqual(sales['ratio'], expected,
                         "the headline ratio must be ledger-based")


@tagged('post_install', '-at_install')
class TestBoardOnEveryDesk(DailyFlowCase):
    """The live field board and the fortnight-per-person grid render on all
    three desks (client, 2026-09-01) - one payload shape, one template each,
    so they can never drift apart."""

    def test_all_three_desks_carry_the_board_and_the_grid(self):
        Desk = self.env['lab.desk']
        for method, user in (('get_ops_desk', self.ops_user),
                             ('get_marketing_desk', self.mkt_user),
                             ('get_admin_desk', self.admin_user)):
            payload = getattr(Desk.with_user(user), method)()
            for key in ('rows', 'totals', 'heatmap'):
                self.assertIn(key, payload, '%s lacks %s' % (method, key))
            self.assertEqual(
                set(payload['totals']),
                {'planned', 'done', 'cases', 'value', 'collected',
                 'unbanked', 'distance'}, method)

    def test_the_board_is_the_same_board_everywhere(self):
        """Same day, same rows - a marketing manager and the administrator
        must never argue about who is on the road."""
        self._visit()
        Desk = self.env['lab.desk']
        ops = Desk.with_user(self.ops_user).get_ops_desk()
        adm = Desk.with_user(self.admin_user).get_admin_desk()
        self.assertEqual([r['id'] for r in ops['rows']],
                         [r['id'] for r in adm['rows']])
        self.assertEqual(ops['totals'], adm['totals'])


@tagged('post_install', '-at_install')
class TestSeeAllAndWithdraw(DailyFlowCase):
    """The 2026-09-02 batch: see-all behind every list tile, the clinics-going-
    quiet panel on all three desks, geolocation on the sheet, the undelivered
    smart button, and taking back an operational approval."""

    # -------------------------------------------------------------- see all
    def test_every_offered_kind_opens_a_real_list(self):
        Desk = self.env['lab.desk'].with_user(self.admin_user)
        kinds = list(Desk._see_all_specs(self.today))
        self.assertIn('rescue', kinds)
        for kind in kinds:
            action = Desk.action_see_all(kind)
            self.assertEqual(action['type'], 'ir.actions.act_window', kind)
            self.assertIn(action['res_model'], self.env, kind)
            # view_mode alone crashes doAction ("reading 'map'") — the same
            # trap the Collections drill carries a comment about.
            self.assertEqual([m for _v, m in action['views']],
                             ['list', 'form'], kind)
            # The domain must actually run for the reader who clicked it.
            self.env[action['res_model']].with_user(
                self.admin_user).search(action['domain'], limit=1)

    def test_an_unknown_tile_is_refused_not_guessed(self):
        with self.assertRaises(UserError):
            self.env['lab.desk'].action_see_all('not_a_panel')

    def test_the_count_in_the_title_is_the_readers_own(self):
        visit = self._visit()
        action = self.env['lab.desk'].with_user(
            self.admin_user).action_see_all('visits_today')
        listed = self.env['lab.visit'].with_user(
            self.admin_user).search(action['domain'])
        self.assertIn(visit, listed)
        self.assertIn('(%s)' % len(listed), action['name'])

    def test_undelivered_see_all_is_the_whole_riding_set(self):
        if 'lab.delivery' not in self.env:
            self.skipTest('deliveries are not installed here')
        action = self.env['lab.desk'].action_see_all('undelivered')
        payload = self.env['lab.desk']._undelivered()
        self.assertEqual(
            self.env['lab.delivery'].sudo().search_count(action['domain']),
            payload['total'], "the button opens exactly what the panel counted")

    def test_a_dispatch_whose_order_already_shipped_is_not_still_riding(self):
        # 17,524 of this database's 17,898 draft dispatches sit on orders that
        # were delivered before the module existed. Counting them put the lab's
        # whole history under "not yet delivered". (2026-09-02)
        if 'lab.delivery' not in self.env:
            self.skipTest('deliveries are not installed here')
        Delivery = self.env['lab.delivery'].sudo()
        product = self.env['product.product'].create(
            {'name': 'Ghost Appliance', 'type': 'consu'})
        order = self.env['sale.order'].create({
            'partner_id': self.clinic.id,
            'order_line': [(0, 0, {'product_id': product.id,
                                   'product_uom_qty': 1})]})
        order.action_confirm()
        dispatch = Delivery.create({
            'direction': 'out', 'sale_order_id': order.id,
            'partner_id': self.clinic.id,
            'executive_id': self.exec_user.id})
        self.assertIn(dispatch.id, Delivery._live_ids(),
                      "nothing has shipped yet, so it is genuinely out")
        picking = order.picking_ids.filtered(
            lambda p: p.picking_type_code == 'outgoing')
        if not picking:
            self.skipTest('no outgoing picking on this configuration')
        for move in picking.move_ids:
            move.quantity = move.product_uom_qty
        picking.with_context(skip_backorder=True).button_validate()
        self.assertEqual(picking.mapped('state'), ['done'])
        Delivery.invalidate_model()
        self.assertNotIn(dispatch.id, Delivery._live_ids(),
                         "the work went out; the draft row is paperwork")

    def test_receivables_see_all_goes_through_the_countback(self):
        if 'lab.collection.performance' not in self.env:
            self.skipTest('collections is not installed here')
        action = self.env['lab.desk'].sudo().action_see_all_receivables()
        self.assertEqual(action['res_model'], 'lab.collection.open.item',
                         "open money has exactly one definition")

    # ------------------------------------------- clinics going quiet, on ops
    def test_the_going_quiet_panel_rides_on_all_three_desks(self):
        ops = self.env['lab.desk'].with_user(self.ops_user).get_ops_desk()
        self.assertIn('rescue', ops)
        self.assertIn('executives', ops,
                      "the rescue planner needs somebody to send")
        mkt = self.env['lab.desk'].with_user(
            self.mkt_user).get_marketing_desk()
        adm = self.env['lab.desk'].with_user(self.admin_user).get_admin_desk()
        self.assertEqual([r['id'] for r in ops['rescue']],
                         [r['id'] for r in mkt['rescue']],
                         "one panel, one answer, on every desk")
        self.assertEqual([r['id'] for r in ops['rescue']],
                         [r['id'] for r in adm['rescue']])

    # ------------------------------------------------------ the day sheet
    def test_a_located_visit_gets_a_map_link_and_a_button(self):
        self._visit()
        sheet = self._sheet()
        sheet._collect_facts()
        self.assertGreaterEqual(sheet.located_visit_count, 1)
        self.assertIn('google.com/maps', sheet.timeline_html)
        action = sheet.action_open_visit_map()
        found = self.env['lab.visit'].search(action['domain'])
        self.assertTrue(found, "the button opens the visits it counted")
        self.assertTrue(all(v.gps_lat and v.gps_lon for v in found))

    def test_a_visit_with_no_fix_gets_no_pin(self):
        sheet = self._sheet()
        visit = self._visit(gps=False)
        self.assertEqual(sheet._map_link(visit), '',
                         "a pin nobody can follow is worse than no pin")

    def test_the_undelivered_button_counts_what_never_came_back(self):
        if 'lab.delivery' not in self.env:
            self.skipTest('deliveries are not installed here')
        sheet = self._sheet()
        before = sheet.undelivered_count
        # A pickup, not a delivery: an outbound one needs its sale order
        # ("that is what says which box this is"), and the panel counts both.
        delivery = self.env['lab.delivery'].sudo().create({
            'direction': 'in',
            'partner_id': self.clinic.id,
            'executive_id': self.exec_user.id})
        sheet.invalidate_recordset()
        self.assertEqual(sheet.undelivered_count, before + 1)
        action = sheet.action_open_undelivered()
        self.assertIn(delivery, self.env['lab.delivery'].sudo().search(
            action['domain']))

    # ------------------------------------------------- withdrawing approval
    def test_operations_can_take_back_its_own_signature(self):
        sheet = self._sheet()
        sheet.action_submit()
        sheet.with_user(self.ops_user).action_ops_approve()
        self.assertEqual(sheet.state, 'ops_approved')
        sheet.with_user(self.ops_user).action_ops_unapprove()
        self.assertEqual(sheet.state, 'submitted')
        self.assertFalse(sheet.ops_approved_by_id)
        self.assertFalse(sheet.ops_approved_at)
        self.assertTrue(any('withdrawn' in (m.body or '')
                            for m in sheet.message_ids),
                        "the chatter says who took it back")

    def test_it_can_be_approved_again_afterwards(self):
        sheet = self._sheet()
        sheet.action_submit()
        sheet.with_user(self.ops_user).action_ops_approve()
        sheet.with_user(self.ops_user).action_ops_unapprove()
        sheet.with_user(self.ops_user).action_ops_approve()
        self.assertEqual(sheet.state, 'ops_approved')

    def test_once_marketing_has_judged_the_day_it_is_too_late(self):
        sheet = self._sheet()
        sheet.action_submit()
        sheet.with_user(self.ops_user).action_ops_approve()
        sheet.with_user(self.mkt_user).action_marketing_approve()
        with self.assertRaises(UserError):
            sheet.with_user(self.ops_user).action_ops_unapprove()
        self.assertEqual(sheet.state, 'approved')

    def test_a_marketing_manager_cannot_undo_the_operational_desk(self):
        sheet = self._sheet()
        sheet.action_submit()
        sheet.with_user(self.ops_user).action_ops_approve()
        with self.assertRaises(UserError):
            sheet.with_user(self.mkt_user).action_ops_unapprove()

    def test_a_sheet_that_was_never_approved_cannot_be_unapproved(self):
        sheet = self._sheet()
        sheet.action_submit()
        with self.assertRaises(UserError):
            sheet.with_user(self.ops_user).action_ops_unapprove()


@tagged('post_install', '-at_install')
class TestSheetFitsOnAScreen(TransactionCase):
    """The day sheet stopped growing with the size of the round.

    A thirty-clinic day rendered thirty stacked lines, then two tall empty
    textareas, then the figures — so an approver scrolled past the whole day to
    reach the buttons that judge it. (client, 2026-09-05)
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = cls.env['res.users'].create({
            'name': 'Scroll Exec', 'login': 'scroll_exec',
            'group_ids': [(6, 0, [
                cls.env.ref('base.group_user').id,
                cls.env.ref('lab_fieldwork.group_fieldwork_executive').id])]})
        cls.sheet = cls.env['lab.daily.update'].create({
            'user_id': cls.user.id,
            'date': fields.Date.context_today(cls.env.user)})

    def test_the_round_is_capped_and_scrolls_inside_itself(self):
        html = self.sheet.timeline_html or ''
        if not html:
            self.skipTest('this fixture day has no movement to render')
        self.assertIn('o_fw_tl', html,
                      "the rows need the class the stylesheet caps")

    def test_the_cap_never_drops_a_visit(self):
        """Capped in CSS, not in the data: a hidden row is a lost row."""
        clinic = self.env['res.partner'].create(
            {'name': 'Scroll Clinic', 'is_clinic': True})
        for _i in range(12):
            self.env['lab.visit'].create({
                'partner_id': clinic.id, 'user_id': self.user.id,
                'date': self.sheet.date, 'state': 'done',
                'check_in': fields.Datetime.now(),
                'check_out': fields.Datetime.now(), 'outcome': 'absent'})
        self.sheet.invalidate_recordset(['timeline_html'])
        html = self.sheet.timeline_html or ''
        self.assertEqual(html.count('Scroll Clinic'), 12,
                         "every visit is rendered; only the box is bounded")
        self.assertIn('12 visits', html,
                      "and the header says how many are in the box")

    def _arch(self):
        """The form as a MANAGER sees it.

        Send Back and the marketing verdict are group-gated, so an ordinary
        reader's arch has them stripped and a test reading that would be
        asserting against the wrong document.
        """
        from lxml import etree
        boss = self.env['res.users'].create({
            'name': 'Scroll Boss', 'login': 'scroll_boss',
            'group_ids': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref('lab_fieldwork.group_fieldwork_manager').id,
                self.env.ref(
                    'lab_fieldwork.group_fieldwork_marketing_manager').id])]})
        arch = self.env['lab.daily.update'].with_user(boss).get_view(
            self.env.ref('lab_fieldwork.view_daily_update_form').id,
            'form')['arch']
        return etree.fromstring(arch)

    def test_the_write_in_blocks_are_tabs_now(self):
        """Three tall sections that were usually empty used to stack under the
        timeline on every sheet."""
        root = self._arch()
        pages = {p.get('name') for p in root.iter('page')}
        self.assertIn('notes', pages)
        self.assertIn('figures', pages)
        for field in ('note', 'send_back_reason', 'marketing_remark'):
            nodes = root.xpath("//page[@name='notes']//field[@name='%s']" % field)
            self.assertTrue(nodes, "%s must survive the move into the tab" % field)

    def test_nothing_was_removed_from_the_sheet(self):
        """The last repack of a screen in this module dropped columns and had
        to be reverted; this one moves things, it does not delete them."""
        names = {f.get('name') for f in self._arch().iter('field')}
        for field in ('note', 'send_back_reason', 'marketing_score',
                      'marketing_remark', 'timeline_html', 'summary_html',
                      'attendance_in', 'attendance_out', 'km',
                      'minutes_at_clinics', 'visit_done_count', 'visit_count',
                      'gps_ok_count', 'gps_far_count', 'gps_nopin_count',
                      'case_count', 'order_count', 'order_value', 'collected',
                      'submitted_at', 'ops_approved_by_id', 'approved_by_id'):
            self.assertIn(field, names, "%s vanished from the sheet" % field)


@tagged('post_install', '-at_install')
class TestRaisedToTheAdministrator(TransactionCase):
    """A desk can send a day UP, not only back. (client, 2026-09-05)

    A manager had two verbs — sign it, or return it to the executive — and
    neither says "this day has a problem somebody above me needs to see". The
    administrator learned about those only by reading a chatter nobody opens.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.exec_user = cls.env['res.users'].create({
            'name': 'Flag Exec', 'login': 'flag_exec',
            'group_ids': [(6, 0, [
                cls.env.ref('base.group_user').id,
                cls.env.ref('lab_fieldwork.group_fieldwork_executive').id])]})
        cls.ops = cls.env['res.users'].create({
            'name': 'Flag Ops', 'login': 'flag_ops',
            'group_ids': [(6, 0, [
                cls.env.ref('base.group_user').id,
                cls.env.ref(
                    'lab_fieldwork.group_fieldwork_ops_manager').id])]})
        cls.mkt = cls.env['res.users'].create({
            'name': 'Flag Mkt', 'login': 'flag_mkt',
            'group_ids': [(6, 0, [
                cls.env.ref('base.group_user').id,
                cls.env.ref(
                    'lab_fieldwork.group_fieldwork_marketing_manager').id])]})
        cls.boss = cls.env['res.users'].create({
            'name': 'Flag Admin', 'login': 'flag_admin',
            'group_ids': [(6, 0, [
                cls.env.ref('base.group_user').id,
                cls.env.ref('lab_fieldwork.group_fieldwork_admin').id])]})

    # One sheet per person per day is a database constraint, so each fixture
    # sheet gets its own day rather than colliding with the last one.
    _day_offset = 0

    def _sheet(self, state='submitted'):
        type(self)._day_offset += 1
        sheet = self.env['lab.daily.update'].create({
            'user_id': self.exec_user.id,
            'date': fields.Date.subtract(
                fields.Date.context_today(self.env.user),
                days=self._day_offset)})
        sheet.state = state
        return sheet

    # ------------------------------------------------------------ raising it
    def test_the_ops_desk_can_raise_a_submitted_day(self):
        sheet = self._sheet('submitted').with_user(self.ops)
        sheet.write({'flag_kind': 'timing', 'flag_reason': 'All stamped 14:14.'})
        sheet.action_flag()
        self.assertEqual(sheet.state, 'flagged')
        self.assertEqual(sheet.flagged_desk, 'ops')
        self.assertEqual(sheet.flagged_by_id, self.ops)
        self.assertTrue(sheet.flagged_at)

    def test_the_marketing_desk_can_raise_one_it_holds(self):
        sheet = self._sheet('ops_approved').with_user(self.mkt)
        sheet.write({'flag_kind': 'activity', 'flag_reason': 'No real calls.'})
        sheet.action_flag()
        self.assertEqual(sheet.state, 'flagged')
        self.assertEqual(sheet.flagged_desk, 'marketing')

    def test_a_desk_cannot_raise_a_day_it_does_not_hold(self):
        """The ops desk does not get to raise a day the marketing desk is on,
        and vice versa — that is what the two-desk order means."""
        sheet = self._sheet('ops_approved').with_user(self.ops)
        sheet.write({'flag_kind': 'timing', 'flag_reason': 'x'})
        with self.assertRaises(UserError):
            sheet.action_flag()

    def test_a_flag_needs_a_reason_and_a_kind(self):
        sheet = self._sheet('submitted').with_user(self.ops)
        sheet.flag_kind = 'timing'
        with self.assertRaises(UserError):
            sheet.action_flag()                       # no reason
        sheet.write({'flag_reason': 'Something', 'flag_kind': False})
        with self.assertRaises(UserError):
            sheet.action_flag()                       # no kind

    def test_raising_is_not_sending_back(self):
        """The executive is not the audience: the day stays where it is and
        the approval trail is kept."""
        sheet = self._sheet('ops_approved')
        sheet.write({'ops_approved_by_id': self.ops.id,
                     'ops_approved_at': fields.Datetime.now()})
        sheet = sheet.with_user(self.mkt)
        sheet.write({'flag_kind': 'money', 'flag_reason': 'Cash short.'})
        sheet.action_flag()
        self.assertNotEqual(sheet.state, 'sent_back')
        self.assertTrue(sheet.ops_approved_by_id,
                        "the operational signature is not thrown away")

    # ---------------------------------------------------------- answering it
    def test_only_the_administrator_answers(self):
        sheet = self._sheet('submitted').with_user(self.ops)
        sheet.write({'flag_kind': 'timing', 'flag_reason': 'x'})
        sheet.action_flag()
        sheet.flag_answer = 'Looked at it.'
        with self.assertRaises(UserError):
            sheet.action_flag_return()

    def test_the_answer_returns_it_to_the_desk_that_raised_it(self):
        sheet = self._sheet('ops_approved').with_user(self.mkt)
        sheet.write({'flag_kind': 'activity', 'flag_reason': 'thin day'})
        sheet.action_flag()
        boss_view = sheet.with_user(self.boss)
        boss_view.flag_answer = 'Spoke to them; sign it.'
        boss_view.action_flag_return()
        self.assertEqual(sheet.state, 'ops_approved',
                         "it goes back to the desk that could not sign it")
        self.assertEqual(sheet.flag_closed_by_id, self.boss)

    def test_an_answer_is_required(self):
        sheet = self._sheet('submitted').with_user(self.ops)
        sheet.write({'flag_kind': 'timing', 'flag_reason': 'x'})
        sheet.action_flag()
        with self.assertRaises(UserError):
            sheet.with_user(self.boss).action_flag_return()

    def test_the_administrator_can_agree_and_send_it_to_the_executive(self):
        sheet = self._sheet('submitted').with_user(self.ops)
        sheet.write({'flag_kind': 'location', 'flag_reason': 'No GPS at all.'})
        sheet.action_flag()
        boss_view = sheet.with_user(self.boss)
        boss_view.flag_answer = 'Agreed, redo the day.'
        boss_view.action_flag_to_executive()
        self.assertEqual(sheet.state, 'sent_back')
        self.assertTrue(sheet.send_back_reason,
                        "and it carries a reason the executive can act on")

    # -------------------------------------------------------- what it tells
    def test_the_kind_is_guessed_from_the_sheets_own_numbers(self):
        """The desk is looking at a screen that already noticed something;
        making them classify it from scratch asks the same question twice."""
        sheet = self._sheet('submitted')
        sheet.write({'visit_done_count': 6, 'gps_ok_count': 0})
        self.assertEqual(sheet._suggested_flag_kind(), 'location')
        sheet.write({'gps_ok_count': 6, 'km': 0})
        self.assertEqual(sheet._suggested_flag_kind(), 'travel')

    def test_a_repeat_is_visible_on_the_sheet_being_judged(self):
        """A first flag and a fourth are different conversations."""
        for _i in range(2):
            sheet = self._sheet('submitted').with_user(self.ops)
            sheet.write({'flag_kind': 'timing', 'flag_reason': 'bulk'})
            sheet.action_flag()
        fresh = self._sheet('submitted')
        fresh.invalidate_recordset(['flag_history_count'])
        self.assertGreaterEqual(fresh.flag_history_count, 2,
                                "the approver sees this is a habit")

    def test_the_command_center_counts_them_by_kind_and_by_person(self):
        sheet = self._sheet('submitted').with_user(self.ops)
        sheet.write({'flag_kind': 'money', 'flag_reason': 'cash short'})
        sheet.action_flag()
        board = self.env['lab.desk'].sudo()._flag_board(
            fields.Date.context_today(self.env.user))
        self.assertIn(sheet.id, [r['id'] for r in board['rows']])
        self.assertGreaterEqual(board['open'], 1)
        self.assertIn('money', [k['key'] for k in board['kinds']])
        # Labels, not raw keys: the panel read them off a module constant
        # through the model, which silently resolved to nothing and printed a
        # blank Issue on every row. (2026-09-05)
        row = next(r for r in board['rows'] if r['id'] == sheet.id)
        self.assertEqual(row['kind_label'],
                         dict(self.env['lab.daily.update']._fields['flag_kind']
                              ._description_selection(self.env))['money'])
        self.assertTrue(all(k['label'] != k['key'] for k in board['kinds']),
                        "a breakdown that prints keys is a broken lookup")
        self.assertIn(self.exec_user.id, [p['id'] for p in board['people']])
        self.assertEqual(sum(k['count'] for k in board['kinds']), board['total'])

    def test_the_drill_opens_exactly_the_raised_sheets(self):
        action = self.env['lab.desk'].sudo().action_open_flags()
        got = self.env['lab.daily.update'].search(action['domain'])
        self.assertEqual(
            set(got.ids),
            set(self.env['lab.daily.update'].search(
                [('state', '=', 'flagged')]).ids))


@tagged('post_install', '-at_install')
class TestTheDayIsWrittenTheWayTheDeskSaysIt(TransactionCase):
    """A day sheet is read by people who write the day first: "11th Sept 2026",
    never "2026-09-10" and never an American "Sep 11, 2026".
    (client, 2026-09-11)"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.exec_user = cls.env['res.users'].create({
            'name': 'Label Exec', 'login': 'label_exec',
            'group_ids': [(4, cls.env.ref('base.group_user').id)]})

    def _sheet(self, day):
        return self.env['lab.daily.update'].create({
            'user_id': self.exec_user.id, 'date': day})

    def test_the_label_is_the_day_then_the_month_then_the_year(self):
        from odoo.addons.lab_fieldwork.models.daily_update import day_label
        self.assertEqual(day_label(fields.Date.to_date('2026-09-11')), '11th Sept 2026')
        self.assertEqual(day_label(fields.Date.to_date('2026-01-31')), '31st Jan 2026')
        self.assertEqual(day_label(fields.Date.to_date('2026-12-25')), '25th Dec 2026')
        self.assertEqual(day_label(False), '', "a sheet with no day yet says nothing")

    def test_the_ordinals_the_naive_rule_gets_wrong(self):
        from odoo.addons.lab_fieldwork.models.daily_update import day_label
        for day, expected in (('2026-09-01', '1st'), ('2026-09-02', '2nd'),
                              ('2026-09-03', '3rd'), ('2026-09-11', '11th'),
                              ('2026-09-12', '12th'), ('2026-09-13', '13th'),
                              ('2026-09-21', '21st'), ('2026-09-22', '22nd'),
                              ('2026-09-23', '23rd')):
            self.assertTrue(day_label(fields.Date.to_date(day)).startswith(expected),
                            '%s should read %s' % (day, expected))

    def test_the_heading_carries_it(self):
        sheet = self._sheet('2026-09-11')
        self.assertEqual(sheet.display_name, 'Label Exec — 11th Sept 2026')
        self.assertNotIn('2026-09-11', sheet.display_name, "no ISO date on the form")

    def test_the_field_beside_the_figures_carries_it(self):
        sheet = self._sheet('2026-09-11')
        self.assertEqual(sheet.date_label, '11th Sept 2026')
        sheet.date = fields.Date.to_date('2026-09-01')
        self.assertEqual(sheet.date_label, '1st Sept 2026',
                         "it follows the day it is set to")

    def test_the_form_reads_the_label_and_still_edits_the_date(self):
        arch = self.env.ref('lab_fieldwork.view_daily_update_form').arch_db
        self.assertIn('name="date_label"', arch)
        self.assertIn('name="date"', arch, "a draft sheet can still be dated")


@tagged('post_install', '-at_install')
class TestSheetWriteGuard(DailyFlowCase):
    """An executive writes the note on their own sheet, and nothing else.

    The access list grants write for the note, and before the guard that same
    right let a day approve itself over RPC: the state, both signatures, the
    score and every snapshot figure. (2026-09-15)
    """

    def test_an_executive_cannot_sign_or_score_their_own_day(self):
        sheet = self._sheet()
        as_exec = sheet.with_user(self.exec_user)
        for vals in ({'state': 'approved'}, {'marketing_score': '5'},
                     {'ops_approved_by_id': self.exec_user.id},
                     {'approved_by_id': self.exec_user.id},
                     {'is_clean': True}, {'visit_done_count': 30}):
            with self.assertRaises(UserError):
                as_exec.write(vals)
        self.assertEqual(sheet.state, 'draft')
        self.assertFalse(sheet.marketing_score)
        self.assertFalse(sheet.approved_by_id)

    def test_an_executive_cannot_create_a_sheet_already_signed(self):
        Sheet = self.env['lab.daily.update'].with_user(self.exec_user)
        with self.assertRaises(UserError):
            Sheet.create({'user_id': self.exec_user.id, 'date': self.today,
                          'state': 'approved', 'marketing_score': '5'})
        sheet = Sheet.create({'user_id': self.exec_user.id, 'date': self.today,
                              'note': 'Started my own sheet.'})
        self.assertEqual(sheet.state, 'draft')

    def test_the_note_is_theirs_while_the_sheet_is(self):
        sheet = self._sheet()
        sheet.with_user(self.exec_user).write({'note': 'Two doctors away.'})
        self.assertEqual(sheet.note, 'Two doctors away.')
        sheet.with_user(self.exec_user).action_submit()
        with self.assertRaises(UserError):
            sheet.with_user(self.exec_user).write({'note': 'changed on the desk'})
        sheet.with_user(self.ops_user).write(
            {'send_back_reason': 'The odometer photo is missing.'})
        sheet.with_user(self.ops_user).action_send_back()
        sheet.with_user(self.exec_user).write({'note': 'Photo added.'})
        self.assertEqual(sheet.note, 'Photo added.')

    def test_the_desks_still_sign_through_the_guard(self):
        self._visit()
        sheet = self._sheet()
        sheet.with_user(self.exec_user).action_submit()
        sheet.with_user(self.ops_user).action_ops_approve()
        sheet.with_user(self.mkt_user).write(
            {'marketing_score': '4', 'marketing_remark': 'A good round.'})
        sheet.with_user(self.mkt_user).action_marketing_approve()
        self.assertEqual(sheet.state, 'approved')
        self.assertEqual(sheet.ops_approved_by_id, self.ops_user)
        self.assertEqual(sheet.approved_by_id, self.mkt_user)
        self.assertEqual(sheet.marketing_score, '4')

    def test_an_automatic_check_out_is_read_from_out_mode(self):
        """hr.attendance has no auto_check_out field; the day closer stamps
        out_mode, and the sheet read the missing field as never."""
        employee = self.exec_user.sudo()._fw_employee() or \
            self.env['hr.employee'].create({'name': self.exec_user.name,
                                            'user_id': self.exec_user.id})
        sheet = self._sheet()
        start = sheet._day_bounds_utc()[0]
        self.env['hr.attendance'].create({
            'employee_id': employee.id,
            'check_in': start + timedelta(hours=3),
            'check_out': start + timedelta(hours=5),
            'out_mode': 'auto_check_out'})
        sheet.action_refresh()
        self.assertTrue(sheet.attendance_auto_closed)
        self.assertTrue(sheet.flag_attendance)

    def test_a_cancelled_trip_is_not_distance_driven(self):
        self.env['lab.trip'].create({
            'user_id': self.exec_user.id, 'date': self.today,
            'odo_start': 1000.0, 'odo_end': 1040.0, 'state': 'cancel'})
        sheet = self._sheet()
        self.assertEqual(sheet.km, 0.0)

    def test_a_day_with_no_timezone_anywhere_is_the_labs_day(self):
        """Most of the field force has no tz, and neither does the cron user.
        The fallback was UTC, which started every day 5h30 early here."""
        nobody = self.env['res.users'].create({
            'name': 'Flow No Tz', 'login': 'flow.notz@test',
            'group_ids': [(4, self.g_exec.id),
                          (4, self.env.ref('base.group_user').id)]})
        nobody.tz = False
        self.env.company.partner_id.tz = False
        sheet = self._sheet(user=nobody)
        start, end = sheet.with_env(
            self.env(user=nobody, context={}))._day_bounds_utc()
        self.assertEqual(start, datetime.combine(self.today, time.min)
                         - timedelta(hours=5, minutes=30))
        self.assertEqual(end - start, timedelta(days=1))

    def test_the_raised_sheets_panel_opens_over_rpc(self):
        """The Command Center calls this with no ids; without @api.model the
        call died with an IndexError."""
        from odoo.service.model import call_kw
        action = call_kw(self.env['lab.desk'].with_user(self.admin_user),
                         'action_open_flags', [], {})
        self.assertEqual(action['res_model'], 'lab.daily.update')


@tagged('post_install', '-at_install')
class TestCoverArrangement(DailyFlowCase):
    """Who may be named on a cover, and what state it is in today."""

    def _cover_as(self, user, manager, delegate, desk, days=(0, 2)):
        return self.env['lab.desk.cover'].with_user(user).create({
            'manager_id': manager.id, 'delegate_id': delegate.id, 'desk': desk,
            'date_from': self.today + timedelta(days=days[0]),
            'date_to': self.today + timedelta(days=days[1]),
        })

    def test_a_manager_cannot_create_cover_that_signs_both_desks(self):
        """Only write() asked, so a create naming the other desk's manager with
        yourself as stand-in gave one person both signatures. (2026-09-15)"""
        with self.assertRaises(UserError):
            self._cover_as(self.ops_user, self.mkt_user, self.ops_user, 'marketing')
        with self.assertRaises(UserError):
            self._cover_as(self.ops_user, self.mkt_user, self.exec_user, 'marketing')
        self.assertFalse(self.env['lab.desk.cover']._covers(
            self.ops_user, 'marketing'))

    def test_a_manager_still_arranges_their_own_cover(self):
        cover = self._cover_as(self.ops_user, self.ops_user, self.mkt_user, 'ops')
        self.assertTrue(self.env['lab.desk.cover']._covers(self.mkt_user, 'ops'))
        with self.assertRaises(UserError):
            cover.with_user(self.ops_user).write(
                {'manager_id': self.mkt_user.id, 'desk': 'marketing'})
        with self.assertRaises(UserError):
            cover.with_user(self.ops_user).write({'delegate_id': self.ops_user.id})

    def test_the_state_follows_the_calendar(self):
        """Stored, it froze at Scheduled: nothing on the record changes when
        the day arrives."""
        Cover = self.env['lab.desk.cover']

        def cover(days):
            return Cover.create({
                'manager_id': self.ops_user.id, 'delegate_id': self.mkt_user.id,
                'desk': 'ops',
                'date_from': self.today + timedelta(days=days[0]),
                'date_to': self.today + timedelta(days=days[1])})
        running, scheduled, over = cover((0, 2)), cover((3, 6)), cover((-9, -6))
        self.assertEqual((running.state, scheduled.state, over.state),
                         ('running', 'scheduled', 'over'))
        mine = [('id', 'in', (running | scheduled | over).ids)]
        self.assertEqual(Cover.search(mine + [('state', '=', 'running')]), running)
        self.assertEqual(
            Cover.search(mine + [('state', 'in', ('scheduled', 'over'))]),
            scheduled | over)
        self.assertEqual(Cover.search(mine + [('state', '!=', 'running')]),
                         scheduled | over)


@tagged('post_install', '-at_install')
class TestCheckedWithIssues(DailyFlowCase):
    """A day with issues is marked checked with a note instead of approved, so
    the next desk and the administrator can see it was read.
    (client, 2026-09-15)"""

    def _with_issues(self):
        sheet = self._sheet()
        sheet.write({'state': 'submitted', 'submitted_at': fields.Datetime.now(),
                     'flag_gps': True, 'is_clean': False})
        return sheet

    def test_a_check_needs_no_note(self):
        """No warning: pressing the button is the check, a note is optional."""
        sheet = self._with_issues()
        sheet.with_user(self.ops_user).action_ops_check()
        self.assertEqual(sheet.state, 'ops_checked')
        self.assertTrue(sheet.ops_check_note, "the day still says it was checked")
        sheet.with_user(self.mkt_user).action_marketing_check()
        self.assertEqual(sheet.state, 'checked')

    def test_operations_checks_and_the_day_moves_to_marketing(self):
        sheet = self._with_issues()
        sheet.with_user(self.ops_user).action_ops_check(note='GPS far - phoned the clinic')
        self.assertEqual(sheet.state, 'ops_checked')
        self.assertEqual(sheet.ops_check_note, 'GPS far - phoned the clinic')
        self.assertEqual(sheet.ops_approved_by_id, self.ops_user)
        Desk = self.env['lab.desk']
        self.assertNotIn(sheet.id, [q['id'] for q in
                                    Desk.with_user(self.ops_user).get_ops_desk()['queue']])
        mkt = {q['id']: q for q in
               Desk.with_user(self.mkt_user).get_marketing_desk()['queue']}
        self.assertIn(sheet.id, mkt)
        self.assertTrue(mkt[sheet.id]['ops_checked'])
        self.assertEqual(mkt[sheet.id]['ops_note'], 'GPS far - phoned the clinic')

    def test_marketing_checks_to_the_final_status(self):
        sheet = self._with_issues()
        sheet.with_user(self.ops_user).action_ops_check(note='GPS far')
        sheet.with_user(self.mkt_user).action_marketing_check(note='Visits genuine')
        self.assertEqual(sheet.state, 'checked')
        self.assertEqual(sheet.approved_by_id, self.mkt_user)
        board = self.env['lab.desk'].with_user(self.admin_user).get_admin_desk()['checked']
        row = next(r for r in board['rows'] if r['id'] == sheet.id)
        self.assertEqual((row['ops_note'], row['mkt_note']), ('GPS far', 'Visits genuine'))

    def test_only_the_desk_checks(self):
        sheet = self._with_issues()
        with self.assertRaises(UserError):
            sheet.with_user(self.exec_user).action_ops_check(note='mine')
        with self.assertRaises(UserError):
            sheet.with_user(self.mkt_user).action_ops_check(note='not my desk')
        with self.assertRaises(UserError):
            sheet.with_user(self.mkt_user).action_marketing_check(note='too early')

    def test_sending_back_or_unapproving_clears_the_check(self):
        sheet = self._with_issues()
        sheet.with_user(self.ops_user).action_ops_check(note='GPS far')
        sheet.with_user(self.ops_user).action_ops_unapprove()
        self.assertEqual((sheet.state, sheet.ops_check_note), ('submitted', False))
        sheet.with_user(self.ops_user).action_ops_check(note='GPS far again')
        sheet.with_user(self.mkt_user).write({'send_back_reason': 'Fix the visits'})
        sheet.with_user(self.mkt_user).action_send_back()
        self.assertEqual((sheet.state, sheet.ops_check_note), ('sent_back', False))


@tagged('post_install', '-at_install')
class TestMyDayCashInHand(DailyFlowCase):
    """The hero shows the cash in the executive's pocket: collected AS CASH and not
    yet sent to the float, from every day so far. (client, 2026-09-15)"""

    def test_cash_in_hand_is_unbanked_cash_from_every_day(self):
        self._visit(user=self.exec_user, pay_mode='cash', collected=500.0)
        self._visit(user=self.exec_user, pay_mode='online', collected=300.0)
        self._visit(user=self.exec_user, pay_mode='cash', collected=200.0,
                    date=self.today - timedelta(days=1))
        summary = self.env['lab.my.day'].with_user(self.exec_user).get_day()['summary']
        self.assertEqual(summary['cash_in_hand'], 700.0,
                         "cash from today and yesterday, not the UPI")
