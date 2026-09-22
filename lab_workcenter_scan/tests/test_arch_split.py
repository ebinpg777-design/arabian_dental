# -*- coding: utf-8 -*-
"""A case sold as Upper & Lower is made as two pieces.

One appliance, one price, one delivery - but the upper and the lower are bent,
acrylised, trimmed and polished separately, often days apart. The routing is
therefore doubled into two chains, and everything the floor uses has to follow:
the queue, the hand-over, the job cards and the scan. (client, 2026-09-12)
"""
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestArchSplit(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.bend = cls.env['mrp.workcenter'].create({'name': 'Arch Bending'})
        cls.polish = cls.env['mrp.workcenter'].create({'name': 'Arch Polishing'})
        cls.product = cls.env['product.product'].create(
            {'name': 'Arch Appliance', 'is_storable': True})
        cls.bom = cls.env['mrp.bom'].create({
            'product_tmpl_id': cls.product.product_tmpl_id.id, 'product_qty': 1.0,
            'operation_ids': [
                (0, 0, {'name': 'Bend', 'workcenter_id': cls.bend.id, 'sequence': 1}),
                (0, 0, {'name': 'Polish', 'workcenter_id': cls.polish.id, 'sequence': 2}),
            ]})
        cls.clinic = cls.env['res.partner'].create({'name': 'Arch Clinic'})

    def _case(self, ul='ul'):
        """An order line with an arch, and the job made from it."""
        order = self.env['sale.order'].create({
            'partner_id': self.clinic.id, 'patient': 'ARCH PATIENT',
            'order_line': [(0, 0, {'product_id': self.product.id,
                                   'product_uom_qty': 1, 'ul': ul})]})
        mo = self.env['mrp.production'].create({
            'product_id': self.product.id, 'bom_id': self.bom.id, 'product_qty': 1.0})
        # `ul` and `sale_id` are stored relateds through sale_line_id, so a
        # fixture writes the link by SQL rather than inventing a whole
        # sale-to-manufacture procurement chain.
        self.env.cr.execute(
            "UPDATE mrp_production SET sale_line_id = %s, sale_id = %s, ul = %s "
            "WHERE id = %s",
            [order.order_line.id, order.id, ul, mo.id])
        mo.invalidate_recordset()
        return order, mo

    # ------------------------------------------------------------ the split
    def test_a_two_arch_case_becomes_two_chains(self):
        _order, mo = self._case('ul')
        mo.action_confirm()
        steps = mo.workorder_ids
        self.assertEqual(len(steps), 4, "two operations, once per arch")
        self.assertEqual(steps.filtered(lambda w: w.arch == 'upper').mapped('name'),
                         ['Bend', 'Polish'])
        self.assertEqual(steps.filtered(lambda w: w.arch == 'lower').mapped('name'),
                         ['Bend', 'Polish'])

    def test_the_lower_chain_is_banded_clear_of_the_upper(self):
        _order, mo = self._case('ul')
        mo.action_confirm()
        upper = mo.workorder_ids.filtered(lambda w: w.arch == 'upper')
        lower = mo.workorder_ids.filtered(lambda w: w.arch == 'lower')
        self.assertLess(max(upper.mapped('sequence')), min(lower.mapped('sequence')),
                        "walking (sequence, id) never steps from one arch into the other")

    def test_a_single_arch_case_is_left_alone(self):
        for arch in ('upper', 'lower'):
            _order, mo = self._case(arch)
            mo.action_confirm()
            self.assertEqual(len(mo.workorder_ids), 2, "one chain, as before")
            # `any`, not the list itself: mapped() on an unset Selection gives
            # [False, False], which is truthy as a list and would pass nothing.
            self.assertFalse(any(mo.workorder_ids.mapped('arch')),
                             "a single-arch case needs no arch on its steps")

    def test_splitting_twice_changes_nothing(self):
        _order, mo = self._case('ul')
        mo.action_confirm()
        before = mo.workorder_ids.ids
        self.assertFalse(mo._lab_split_arches(), "nothing left to split")
        self.assertEqual(mo.workorder_ids.ids, before)

    # ------------------------------------------------------------ the floor
    def test_hand_over_stays_inside_its_own_arch(self):
        _order, mo = self._case('ul')
        mo.action_confirm()
        for arch in ('upper', 'lower'):
            first = mo.workorder_ids.filtered(
                lambda w, a=arch: w.arch == a).sorted('sequence')[0]
            self.assertEqual(first.next_workorder_id.arch, arch,
                             "the piece goes to the next bench of its OWN arch")
            self.assertEqual(first.next_workcenter_id, self.polish)

    def test_the_bench_sees_both_pieces_in_its_queue(self):
        _order, mo = self._case('ul')
        mo.action_confirm()
        rows = self.env['report.lab.floor']._open_steps_by_arch(mo)
        self.assertEqual(len(rows), 2, "two pieces waiting, not one case")
        self.assertEqual({row['arch'] for row in rows}, {'upper', 'lower'})
        self.assertEqual({(row['workcenter_id'] or [0])[0] for row in rows},
                         {self.bend.id}, "both start at the first bench")

    def test_a_case_is_still_one_case_where_a_case_is_counted(self):
        _order, mo = self._case('ul')
        mo.action_confirm()
        per_case = self.env['report.lab.floor']._first_open_step(mo)
        self.assertEqual(len(per_case), 1, "'where is this case' has one answer")

    def test_the_step_after_stays_inside_its_own_arch(self):
        """The undo guard reads the next step whatever its state; walking (sequence,
        id) alone would read the lower chain's first bench as the upper's successor.
        (review, 2026-09-15)"""
        _order, mo = self._case('ul')
        mo.action_confirm()
        for arch in ('upper', 'lower'):
            first, last = mo.workorder_ids.filtered(
                lambda w, a=arch: w.arch == a).sorted('sequence')
            self.assertEqual(first._step_after(), last)
            self.assertFalse(last._step_after(),
                             "the last step of an arch hands to nobody")

    # ------------------------------------- one piece at a time at a bench
    def test_a_bench_holding_both_pieces_finishes_one_before_starting_the_next(self):
        """Scanning the card at a bench that holds two pieces of the same case
        means "I have finished this one", not "start the other one".

        The first piece was left accepted and never handed on while the second
        was opened underneath it, so the technician had two jobs open at once.
        (client, 2026-09-16)
        """
        head = self.env['res.users'].create({
            'name': 'Arch Bench Lead', 'login': 'arch_bench_lead',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id,
                                  self.env.ref('mrp.group_mrp_user').id])]})
        self.bend.head_user_ids = [(6, 0, head.ids)]
        _order, mo = self._case('ul')
        mo.action_confirm()
        # confirmed=True throughout: this bench asks the operator to confirm a
        # scan, and the question is not what is being tested here.
        station = self.env['lab.station'].with_user(head)
        at_bench = mo.workorder_ids.filtered(
            lambda w: w.workcenter_id == self.bend).sorted('sequence')
        self.assertEqual(len(at_bench), 2, "both pieces are bent at the same bench")
        first, second = at_bench[0], at_bench[1]

        # A scan that accepts a piece answers with the "who is doing it" question,
        # so the piece itself is what says which one was taken.
        self.assertIn(station.scan(mo.name, self.bend.id, confirmed=True)['action'],
                      ('accepted', 'needs_person'))
        first.invalidate_recordset()
        self.assertTrue(first.accepted_at, "the first piece is the one taken")
        self.assertFalse(second.accepted_at)

        first.with_user(head).action_assign_bench_user(head.id)
        self.assertEqual(
            station.scan(mo.name, self.bend.id, confirmed=True)['action'], 'handed',
            "the next scan finishes the piece in hand")
        first.invalidate_recordset()
        second.invalidate_recordset()
        self.assertIn(first.state, ('done', 'cancel'))
        self.assertFalse(second.accepted_at, "the second piece has not been opened")

        self.assertIn(station.scan(mo.name, self.bend.id, confirmed=True)['action'],
                      ('accepted', 'needs_person'))
        second.invalidate_recordset()
        self.assertTrue(second.accepted_at, "and now the second piece starts")

    # ------------------------------------- telling the two pieces apart
    def test_a_message_about_a_scan_names_the_piece(self):
        _order, mo = self._case('ul')
        mo.action_confirm()
        Station = self.env['lab.station']
        labels = {Station._job_label(wo) for wo in mo.workorder_ids}
        self.assertTrue(any(label.endswith('(Upper)') for label in labels))
        self.assertTrue(any(label.endswith('(Lower)') for label in labels))

    def test_a_single_arch_scan_message_carries_no_suffix(self):
        _order, mo = self._case('upper')
        mo.action_confirm()
        label = self.env['lab.station']._job_label(mo.workorder_ids[0])
        self.assertNotIn('(', label, "nothing to disambiguate on a one-piece case")

    # ------------------------------------------------------------ the paper
    def test_a_job_card_is_printed_for_each_piece(self):
        from odoo.addons.sale_custom.report.production_slip import _slips_from_mo
        _order, mo = self._case('ul')
        mo.action_confirm()
        slips = _slips_from_mo(mo)
        self.assertEqual(len(slips), 2)
        self.assertEqual(sorted(s['ul'] for s in slips), ['L', 'U'])
        self.assertEqual({s['code'] for s in slips}, {mo.name},
                         "one number on both cards; the board works out the piece")

    def test_one_card_for_a_single_arch_case(self):
        from odoo.addons.sale_custom.report.production_slip import _slips_from_mo
        _order, mo = self._case('upper')
        mo.action_confirm()
        self.assertEqual(len(_slips_from_mo(mo)), 1)
