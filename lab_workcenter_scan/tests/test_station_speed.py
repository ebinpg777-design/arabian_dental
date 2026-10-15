# -*- coding: utf-8 -*-
"""The manufacturing boards read the live set once, and the stamps they count are indexed.

(client, 2026-10-14: "fix any performance issues in the manufacturing module")
Only the loading work of Ortho 22b6d02 is here: the quicker scan beside it (4b6eff0)
was reverted at the client's request and never came to the lab.
"""
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestStationSpeed(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.head = cls.env['res.users'].create({
            'name': 'Speed Head', 'login': 'wc_speed_head',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id,
                                  cls.env.ref('mrp.group_mrp_user').id])]})
        cls.tech = cls.env['res.users'].create({
            'name': 'Speed Tech', 'login': 'wc_speed_tech',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id,
                                  cls.env.ref('mrp.group_mrp_user').id])]})
        cls.wc = cls.env['mrp.workcenter'].create({
            'name': 'Speed Trim', 'head_user_ids': [(6, 0, cls.head.ids)],
            'users': [(6, 0, cls.tech.ids)], 'scan_confirm': True})
        cls.next_wc = cls.env['mrp.workcenter'].create({'name': 'Speed Polish'})
        product = cls.env['product.product'].create({'name': 'Speed Retainer', 'type': 'consu', 'is_storable': True})
        cls.mo = cls.env['mrp.production'].create({'product_id': product.id, 'product_qty': 1.0})
        cls.wo = cls.env['mrp.workorder'].create({
            'name': 'Trimming', 'sequence': 10, 'production_id': cls.mo.id,
            'workcenter_id': cls.wc.id})
        cls.env['mrp.workorder'].create({
            'name': 'Polishing', 'sequence': 20, 'production_id': cls.mo.id,
            'workcenter_id': cls.next_wc.id})
        cls.Station = cls.env['lab.station'].with_user(cls.head)

    # ------------------------------------------------------------ the live set, once a board
    # (client, 2026-10-14: "fix any performance issues in the manufacturing module")
    def test_the_live_set_read_once_is_the_live_set(self):
        Floor = self.env['report.lab.floor']
        Production = self.env['mrp.production'].sudo()
        remembered = Floor._remember_live(Floor)
        self.assertEqual(sorted(remembered.env.context['lab_live_ids']),
                         sorted(Production.search(Floor._live_domain()).ids))
        self.assertIn(self.mo.id, remembered.env.context['lab_live_ids'])
        self.assertEqual(remembered._closeout_count(), Production.search_count(Floor._closeout_domain()))
        # asked again inside the same read, it is the same set and not a second query
        self.assertEqual(sorted(Production.search(remembered._live_domain()).ids),
                         sorted(remembered.env.context['lab_live_ids']))

    def test_the_flow_board_counts_from_the_one_read(self):
        Floor = self.env['report.lab.floor']
        board = self.env['lab.flow'].get_flow()
        self.assertEqual(board['totals']['jobs'], self.env['mrp.production'].sudo().search_count(Floor._live_domain()))
        self.assertEqual(board['closeout'], self.env['mrp.production'].sudo().search_count(Floor._closeout_domain()))

    def test_the_stamps_are_indexed(self):
        for column in ('accepted_at', 'handed_over_at', 'bench_assigned_at'):
            self.env.cr.execute("SELECT 1 FROM pg_indexes WHERE tablename = 'mrp_workorder' AND indexdef LIKE %s",
                                ('%%(%s)%%' % column,))
            self.assertTrue(self.env.cr.fetchone(), "%s has an index" % column)

