# -*- coding: utf-8 -*-
"""A job passed from one technician to another, and a job that is waiting.

(client, 2026-11-26: "if any technician working on work order but he couldn't
finish it properly then lead can reassign to another technician ... show the
technician initially worked and reassigned person" - and - "option to make a work
to Pending status by production team ... add note for reason")
"""
from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestReassignPending(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        groups = [(6, 0, [cls.env.ref('base.group_user').id, cls.env.ref('mrp.group_mrp_user').id])]

        def user(name, login):
            return cls.env['res.users'].create({'name': name, 'login': login, 'group_ids': groups})

        cls.head = user('Pass Head', 'pass_head')
        cls.asha = user('Pass Asha', 'pass_asha')
        cls.ben = user('Pass Ben', 'pass_ben')
        cls.cara = user('Pass Cara', 'pass_cara')
        cls.outsider = user('Pass Out', 'pass_out')
        cls.bench = cls.env['mrp.workcenter'].create({
            'name': 'Pass Ceramic', 'scan_confirm': False,
            'head_user_ids': [(6, 0, cls.head.ids)],
            'users': [(6, 0, (cls.asha | cls.ben | cls.cara).ids)]})
        cls.pack = cls.env['mrp.workcenter'].create({'name': 'Pass Pack'})
        cls.product = cls.env['product.product'].create(
            {'name': 'Pass Crown', 'type': 'consu', 'is_storable': True})
        cls.Station = cls.env['lab.station'].with_user(cls.head)

    def _job(self, technician=None):
        """A job at the bench, taken on - and, with `technician`, in their hands."""
        mo = self.env['mrp.production'].create({'product_id': self.product.id, 'product_qty': 1.0})
        wo = self.env['mrp.workorder'].create({
            'name': 'Build up', 'sequence': 10, 'production_id': mo.id,
            'workcenter_id': self.bench.id})
        self.env['mrp.workorder'].create({
            'name': 'Packing', 'sequence': 20, 'production_id': mo.id,
            'workcenter_id': self.pack.id})
        if technician:
            wo.with_user(self.head).action_station_accept()
            wo.with_user(self.head).action_assign_bench_team(technician.ids)
        return mo, wo

    def _card(self, board, wo):
        return next(c for c in board['incoming'] + board['working'] if c['id'] == wo.id)

    # ------------------------------------------------------------- reassigned
    def test_a_lead_reassigns_and_both_names_stay(self):
        mo, wo = self._job(self.asha)
        result = self.Station.reassign(wo.id, [self.ben.id], 'quality', 'margins open')
        self.assertEqual(wo.bench_user_id, self.ben, "it is his now")
        self.assertTrue(wo.reassigned)
        self.assertEqual(wo.initial_user_ids, self.asha, "and she worked on it first")
        card = self._card(result['board'], wo)
        self.assertTrue(card['reassigned'])
        self.assertEqual(card['first_team'], 'Pass Asha')
        self.assertEqual(card['reassign_note'], 'Work not up to standard - margins open')
        self.assertEqual(wo._bench_people_label(), 'Pass Ben (first: Pass Asha)')
        row = wo.reassign_ids
        self.assertEqual((row.from_user_ids, row.to_user_ids, row.user_id, row.workcenter_id),
                         (self.asha, self.ben, self.head, self.bench))
        self.assertIn('reassigned from Pass Asha to Pass Ben by Pass Head', mo.message_ids[0].body)

    def test_a_second_reassignment_keeps_who_was_first(self):
        mo, wo = self._job(self.asha)
        self.Station.reassign(wo.id, [self.ben.id], 'unavailable')
        self.Station.reassign(wo.id, [self.cara.id, self.asha.id], 'load')
        self.assertEqual(wo.initial_user_ids, self.asha, "first is first, however often it moves")
        self.assertEqual(wo._bench_people(), self.cara | self.asha)
        self.assertEqual(len(wo.reassign_ids), 2)
        oldest, newest = wo.reassign_ids.sorted(lambda r: (r.date, r.id))
        self.assertEqual(newest.from_user_ids, self.ben)
        self.assertEqual(newest.since, oldest.date, "Ben had it since it was given to him")

    def test_only_a_lead_with_a_reason_and_somebody_to_take_it_from(self):
        mo, wo = self._job(self.asha)
        with self.assertRaises(UserError, msg="a technician does not pass work on"):
            self.env['lab.station'].with_user(self.asha).reassign(wo.id, [self.ben.id], 'quality')
        with self.assertRaises(UserError, msg="'other' says nothing by itself"):
            self.Station.reassign(wo.id, [self.ben.id], 'other', ' ')
        with self.assertRaises(UserError, msg="already hers"):
            self.Station.reassign(wo.id, [self.asha.id], 'quality')
        with self.assertRaises(UserError, msg="not posted here"):
            self.Station.reassign(wo.id, [self.outsider.id], 'quality')
        unnamed_mo, unnamed = self._job()
        with self.assertRaises(UserError, msg="nobody to take it from"):
            self.Station.reassign(unnamed.id, [self.ben.id], 'quality')
        self.assertFalse(wo.reassigned or wo.reassign_ids)

    def test_a_scan_finds_the_job_to_reassign(self):
        mo, wo = self._job(self.asha)
        found = self.Station.scan_to_reassign(mo.name, self.bench.id)
        self.assertEqual(found['job']['id'], wo.id)
        with self.assertRaises(UserError):
            self.env['lab.station'].with_user(self.asha).scan_to_reassign(mo.name, self.bench.id)

    def test_a_job_that_starts_over_starts_clean(self):
        mo, wo = self._job(self.asha)
        self.Station.reassign(wo.id, [self.ben.id], 'quality')
        wo.write({'bench_user_id': False})
        self.assertFalse(wo.reassigned or wo.initial_user_ids)
        self.assertTrue(wo.reassign_ids, "the record of it stays")

    def test_the_day_draws_each_their_own_part(self):
        """The one who took it over from when they took it; the one who worked on
        it first until it was taken - a bar, not a finish."""
        mo, wo = self._job(self.asha)
        self.Station.reassign(wo.id, [self.ben.id], 'quality')
        moved = wo.reassign_ids.date
        began = moved - timedelta(hours=2)
        wo.reassign_ids.since = began
        self.env.flush_all()
        start = fields.Datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
        end = start + timedelta(days=3)
        row = {'id': wo.id, 'uid': self.ben.id, 'tech': self.ben.id, 'finisher': self.ben.id,
               'started': began, 'finished': None, 'due': None}
        rows = self.env['lab.tech.day']._reassigned_slices([row], start, end)
        his = next(r for r in rows if r['uid'] == self.ben.id)
        hers = next(r for r in rows if r['uid'] == self.asha.id)
        self.assertEqual((his['started'], his['took_over_from']), (moved, 'Pass Asha'))
        self.assertEqual((hers['started'], hers['finished'], hers['passed_to']),
                         (began, moved, 'Pass Ben'))
        self.assertFalse(hers['finisher'], "taken from her, not handed on by her")

    # ---------------------------------------------------------------- pending
    def test_the_bench_marks_a_job_pending_with_the_reason(self):
        mo, wo = self._job(self.asha)
        Station = self.env['lab.station'].with_user(self.asha)
        found = Station.scan_to_pend(mo.name, self.bench.id)
        self.assertEqual((found['production_id'], found['pending']), (mo.id, False))
        result = Station.mark_pending(mo.id, "waiting for the doctor's reply", self.bench.id)
        self.assertTrue(mo.lab_pending)
        self.assertEqual(mo.lab_pending_by_id, self.asha)
        card = self._card(result['board'], wo)
        self.assertTrue(card['pending'])
        self.assertEqual(card['pending_reason'], "waiting for the doctor's reply")
        again = Station.pending_of_job(wo.id, self.bench.id)
        self.assertEqual((again['pending'], again['marked_by']), (True, 'Pass Asha'))
        result = Station.resume_pending(mo.id, self.bench.id)
        self.assertFalse(mo.lab_pending or mo.lab_pending_reason)
        self.assertFalse(self._card(result['board'], wo)['pending'])

    def test_pending_needs_a_reason_and_a_place_at_the_bench(self):
        mo, wo = self._job(self.asha)
        with self.assertRaises(UserError):
            self.Station.mark_pending(mo.id, '  ', self.bench.id)
        with self.assertRaises(UserError):
            self.env['lab.station'].with_user(self.outsider).mark_pending(mo.id, 'x', self.bench.id)
        self.assertFalse(mo.lab_pending)

    def test_work_going_on_ends_the_wait(self):
        """Taken at a bench, or handed to the next: the bench saying work went on."""
        mo, wo = self._job()
        self.Station.mark_pending(mo.id, 'try-in not back', self.bench.id)
        wo.with_user(self.head).action_station_accept()
        self.assertFalse(mo.lab_pending, "taking it on resumes it")
        self.assertIn('no longer pending', mo.message_ids[0].body + mo.message_ids[1].body)
        wo.with_user(self.head).action_assign_bench_team(self.asha.ids)
        self.Station.mark_pending(mo.id, 'shade to be confirmed', self.bench.id)
        self.assertTrue(mo.lab_pending)
        wo.with_user(self.head).action_station_handover()
        self.assertFalse(mo.lab_pending, "handing it on resumes it")

    def test_the_tracking_screen_says_both(self):
        if 'lab.track' not in self.env:
            self.skipTest("lab_track is not installed")
        mo, wo = self._job(self.asha)
        self.Station.reassign(wo.id, [self.ben.id], 'quality')
        self.Station.mark_pending(mo.id, 'material not available', self.bench.id)
        Track = self.env['lab.track']
        self.assertEqual(Track._lab_step_first(wo), 'Pass Asha')
        pending = Track._lab_pending(mo)
        self.assertEqual((pending['reason'], pending['by']), ('material not available', 'Pass Head'))
        self.Station.resume_pending(mo.id, self.bench.id)
        self.assertFalse(Track._lab_pending(mo))
