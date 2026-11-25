# -*- coding: utf-8 -*-
"""Work done together: several people on one job, and a tray scanned without being
asked "which ONE of you" for every card in it.

(client, 2026-11-25: "In work stations the multiple technicians are doing multiple
works together. It is difficult to assign a single work to a single person.")
"""
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestBenchTeam(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        groups = [(6, 0, [cls.env.ref('base.group_user').id, cls.env.ref('mrp.group_mrp_user').id])]

        def user(name, login):
            return cls.env['res.users'].create({'name': name, 'login': login, 'group_ids': groups})

        cls.head = user('Team Head', 'team_head')
        cls.asha = user('Team Asha', 'team_asha')
        cls.ben = user('Team Ben', 'team_ben')
        cls.cara = user('Team Cara', 'team_cara')
        cls.outsider = user('Team Out', 'team_out')
        cls.bench = cls.env['mrp.workcenter'].create({
            'name': 'Team Ceramic', 'scan_confirm': False,
            'head_user_ids': [(6, 0, cls.head.ids)],
            'users': [(6, 0, (cls.asha | cls.ben | cls.cara).ids)]})
        cls.pack = cls.env['mrp.workcenter'].create({'name': 'Team Pack'})
        cls.product = cls.env['product.product'].create(
            {'name': 'Team Crown', 'type': 'consu', 'is_storable': True})
        cls.Station = cls.env['lab.station'].with_user(cls.head)

    def _job(self):
        mo = self.env['mrp.production'].create({'product_id': self.product.id, 'product_qty': 1.0})
        wo = self.env['mrp.workorder'].create({
            'name': 'Build up', 'sequence': 10, 'production_id': mo.id,
            'workcenter_id': self.bench.id})
        self.env['mrp.workorder'].create({
            'name': 'Packing', 'sequence': 20, 'production_id': mo.id,
            'workcenter_id': self.pack.id})
        return mo, wo

    def _chip(self, board, user):
        return next(p for p in board['bench_people'] if p['id'] == user.id)

    # ------------------------------------------------------------ the team itself
    def test_a_job_is_given_to_several_people(self):
        mo, wo = self._job()
        wo.with_user(self.head).action_station_accept()
        wo.with_user(self.head).action_assign_bench_team([self.asha.id, self.ben.id, self.cara.id])
        self.assertEqual(wo.bench_user_id, self.asha, "the first is the job's technician")
        self.assertEqual(wo.bench_helper_ids, self.ben | self.cara)
        self.assertEqual(wo._bench_people(), self.asha | self.ben | self.cara)
        self.assertIn('Team Asha, Team Ben, Team Cara', mo.message_ids[0].body,
                      "the order's chatter names all of them")

    def test_one_name_means_one_person(self):
        mo, wo = self._job()
        wo.with_user(self.head).action_station_accept()
        wo.with_user(self.head).action_assign_bench_team([self.asha.id, self.ben.id])
        wo.with_user(self.head).action_assign_bench_user(self.cara.id)
        self.assertEqual(wo.bench_user_id, self.cara)
        self.assertFalse(wo.bench_helper_ids, "given to one person, the others come off")

    def test_only_a_lead_gives_work_and_only_to_people_at_the_bench(self):
        mo, wo = self._job()
        wo.with_user(self.head).action_station_accept()
        with self.assertRaises(UserError, msg="a technician cannot name colleagues"):
            wo.with_user(self.asha).action_assign_bench_team([self.asha.id, self.ben.id])
        with self.assertRaises(UserError, msg="somebody who does not work here"):
            wo.with_user(self.head).action_assign_bench_team([self.asha.id, self.outsider.id])
        self.assertFalse(wo.bench_user_id | wo.bench_helper_ids)

    def test_the_technician_is_not_their_own_helper(self):
        mo, wo = self._job()
        wo.write({'bench_user_id': self.asha.id,
                  'bench_helper_ids': [(6, 0, (self.asha | self.ben).ids)]})
        self.assertEqual(wo.bench_helper_ids, self.ben)

    def test_a_job_that_leaves_the_bench_leaves_the_team_too(self):
        mo, wo = self._job()
        wo.with_user(self.head).action_station_accept()
        wo.with_user(self.head).action_assign_bench_team([self.asha.id, self.ben.id])
        wo.write({'bench_user_id': False})
        self.assertFalse(wo.bench_helper_ids, "nobody works WITH nobody")

    # ------------------------------------------------------------------- scanning
    def test_a_scan_asks_who_with_the_recent_teams(self):
        mo, wo = self._job()
        answer = self.Station.scan(mo.name, self.bench.id)
        self.assertEqual(answer['action'], 'needs_person')
        self.assertEqual({p['id'] for p in answer['people']},
                         set((self.head | self.asha | self.ben | self.cara).ids))
        self.assertEqual(answer['teams'], [], "no team has worked here yet")
        # the answer: two people, the job stays on the bench under both names
        board = self.Station.assign(wo.id, [self.asha.id, self.ben.id])
        card = next(c for c in board['working'] if c['id'] == wo.id)
        self.assertEqual([p['name'] for p in card['bench_team']], ['Team Asha', 'Team Ben'])
        # ... and the next question offers that pair as one tap
        mo2, wo2 = self._job()
        again = self.Station.scan(mo2.name, self.bench.id)
        self.assertEqual(again['teams'], [{'ids': sorted((self.asha | self.ben).ids),
                                           'names': ['Team Asha', 'Team Ben']}])

    def test_a_tray_is_scanned_for_a_team_without_asking(self):
        team = [self.ben.id, self.cara.id]
        for _n in range(3):
            mo, wo = self._job()
            answer = self.Station.scan(mo.name, self.bench.id, team=team)
            self.assertEqual(answer['action'], 'accepted', "no question, card after card")
            self.assertIn('Team Ben, Team Cara', answer['message'], "but it says to whom")
            self.assertEqual((wo.bench_user_id, wo.bench_helper_ids), (self.ben, self.cara))
        board = self.Station.get_station(self.bench.id)
        self.assertEqual(self._chip(board, self.ben)['load'], 3)
        self.assertEqual(self._chip(board, self.cara)['load'], 3, "in hand for each of them")
        self.assertEqual(self._chip(board, self.asha)['load'], 0)

    def test_a_team_does_not_take_a_job_that_already_has_a_name(self):
        mo, wo = self._job()
        self.Station.scan(mo.name, self.bench.id, team=[self.asha.id])
        # the second scan hands it on: the team in force is not put over Asha's name
        answer = self.Station.scan(mo.name, self.bench.id, team=[self.ben.id, self.cara.id])
        self.assertEqual(answer['action'], 'handed')
        self.assertEqual(wo.bench_user_id, self.asha)
        self.assertFalse(wo.bench_helper_ids)

    def test_a_technicians_scan_carries_no_team(self):
        """Who does which job is the lead's call: a team sent by somebody who is not
        one is not applied, and the scan asks as it always did."""
        mo, wo = self._job()
        answer = self.env['lab.station'].with_user(self.asha).scan(
            mo.name, self.bench.id, team=[self.asha.id, self.ben.id])
        self.assertEqual(answer['action'], 'needs_person')
        self.assertFalse(answer['can_assign'])
        self.assertFalse(wo.bench_user_id)

    def test_somebody_moved_off_the_bench_is_dropped_from_the_team(self):
        mo, wo = self._job()
        answer = self.Station.scan(mo.name, self.bench.id, team=[self.outsider.id, self.cara.id])
        self.assertEqual(answer['action'], 'accepted')
        self.assertEqual(wo.bench_user_id, self.cara)
        mo2, wo2 = self._job()
        asked = self.Station.scan(mo2.name, self.bench.id, team=[self.outsider.id])
        self.assertEqual(asked['action'], 'needs_person', "nobody left of it: ask")

    def test_the_confirmation_says_who_the_scan_is_for(self):
        self.bench.scan_confirm = True
        mo, wo = self._job()
        asked = self.Station.scan(mo.name, self.bench.id, team=[self.asha.id, self.ben.id])
        self.assertEqual(asked['action'], 'confirm')
        self.assertIn('for Team Asha, Team Ben', asked['message'])
        self.assertFalse(wo.accepted_at, "nothing is written before the answer")
        done = self.Station.scan(asked['code'], self.bench.id, confirmed=True,
                                 team=[self.asha.id, self.ben.id])
        self.assertEqual(done['action'], 'accepted')
        self.assertEqual(wo._bench_people(), self.asha | self.ben)

    def test_handed_on_by_a_team_in_one_answer(self):
        mo, wo = self._job()
        wo.with_user(self.head).action_station_accept()
        self.Station.assign_and_handover(wo.id, [self.asha.id, self.ben.id])
        self.assertEqual(wo.state, 'done')
        self.assertEqual(wo._bench_people(), self.asha | self.ben)

    # ------------------------------------------------------------------- counting
    def test_everybody_on_the_job_counts_it(self):
        mo, wo = self._job()
        self.Station.scan(mo.name, self.bench.id, team=[self.asha.id, self.ben.id])
        self.Station.scan(mo.name, self.bench.id)       # hand it on
        self.assertEqual(wo.state, 'done')
        board = self.Station.get_station(self.bench.id)
        for person in (self.asha, self.ben):
            chip = self._chip(board, person)
            self.assertEqual((chip['load'], chip['done_count'], chip['day_count']), (0, 1, 1),
                             "%s finished it too" % person.name)
        self.assertEqual(self._chip(board, self.cara)['done_count'], 0)
        # the chip and the list under it agree: Ben's filter shows the job
        mine = self.Station.get_station(self.bench.id, self.ben.id)
        self.assertEqual([c['id'] for c in mine['done_today']], [wo.id])
        # one job, however many hands: the bench's own count is not inflated
        self.assertEqual(board['done_total'], 1)

    def test_targets_count_the_people_working_with_the_technician(self):
        mo, wo = self._job()
        self.Station.scan(mo.name, self.bench.id, team=[self.asha.id, self.ben.id])
        day = self.env['lab.station']._lab_time(wo.accepted_at).date()
        Target = self.env['lab.work.target']
        ben = Target.create({'date': day, 'user_id': self.ben.id, 'workcenter_id': self.bench.id, 'target': 5})
        cara = Target.create({'date': day, 'user_id': self.cara.id, 'workcenter_id': self.bench.id, 'target': 5})
        (ben | cara).action_refresh()
        self.assertEqual((ben.done_count, cara.done_count), (1, 0))
        # Cara joins the job later: her day is counted again at once
        self.Station.apply_change(wo.id, helper_ids=[self.ben.id, self.cara.id])
        self.assertEqual(wo.bench_helper_ids, self.ben | self.cara)
        self.assertEqual((ben.done_count, cara.done_count), (1, 1))

    def test_a_finisher_who_also_worked_on_it_counts_it_once(self):
        self.bench.is_finisher = True
        mo, wo = self._job()
        self.Station.scan(mo.name, self.bench.id, team=[self.asha.id, self.ben.id])
        wo.with_user(self.head).action_assign_finisher(self.ben.id)
        wo.with_user(self.head).action_station_handover()
        start, end = self.env['lab.station']._day_window(self.env['lab.station']._lab_today())
        counts = self.env['mrp.workorder']._day_counts_by_person(
            start, end, (self.asha | self.ben).ids, self.bench)
        self.assertEqual(counts, {self.asha.id: 1, self.ben.id: 1})

    # ----------------------------------------------------------------- correcting
    def test_correct_changes_who_it_was_worked_with(self):
        mo, wo = self._job()
        self.Station.scan(mo.name, self.bench.id, team=[self.asha.id, self.ben.id])
        found = self.Station.scan_to_change(mo.name, self.bench.id)
        self.assertEqual((found['bench_user_id'], found['helper_ids']), (self.asha.id, self.ben.ids))
        result = self.Station.apply_change(wo.id, helper_ids=[self.cara.id])
        self.assertIn('working with', result['message'])
        self.assertEqual((wo.bench_user_id, wo.bench_helper_ids), (self.asha, self.cara))
        # taking the technician off takes everybody off
        self.Station.apply_change(wo.id, bench_user_id=False)
        self.assertFalse(wo.bench_user_id | wo.bench_helper_ids)

    def test_a_finished_job_can_have_its_team_put_right(self):
        mo, wo = self._job()
        self.Station.scan(mo.name, self.bench.id, team=[self.asha.id])
        self.Station.scan(mo.name, self.bench.id)
        self.assertEqual(wo.state, 'done')
        self.Station.apply_change(wo.id, helper_ids=[self.ben.id])
        self.assertEqual((wo.bench_user_id, wo.bench_helper_ids), (self.asha, self.ben))
        with self.assertRaises(UserError, msg="somebody who does not work here"):
            self.Station.apply_change(wo.id, helper_ids=[self.outsider.id])

    # -------------------------------------------------------------------- reports
    def test_the_production_summary_names_everybody(self):
        mo, wo = self._job()
        self.Station.scan(mo.name, self.bench.id, team=[self.asha.id, self.ben.id])
        self.Station.scan(mo.name, self.bench.id)
        self.env.flush_all()
        Perf = self.env['lab.production.performance']
        summary = Perf.station_summary(workcenter_ids=[self.bench.id])
        jobs = {row['name']: row['jobs'] for row in summary['people']}
        self.assertEqual((jobs.get('Team Asha'), jobs.get('Team Ben')), (1, 1))
        self.assertEqual(summary['totals']['jobs'], 1, "one job on the floor")
        only_ben = Perf.station_summary(workcenter_ids=[self.bench.id], user_ids=[self.ben.id])
        self.assertEqual([row['name'] for row in only_ben['people']], ['Team Ben'])
