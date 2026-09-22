# -*- coding: utf-8 -*-
"""Track Order on the Station Board: read-only, and open to the bench.

A bench asks about the case in its hand, and almost nobody posted to one holds a
Sales role. So the floor door answers manufacturing users the order would refuse,
keeps the money off anyone without a Sales or Invoicing role, and the screen it feeds names records without ever
opening one. (client, 2026-09-17)
"""
from pathlib import Path

from lxml import etree

from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestFloorTrack(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Track = cls.env['lab.track']
        clinic = cls.env['res.partner'].create({'name': 'Floor Track Clinic'})
        product = cls.env['product.product'].create({
            'name': 'Floor Track Appliance', 'type': 'consu', 'list_price': 900.0,
            'invoice_policy': 'order'})
        cls.order = cls.env['sale.order'].create({
            'partner_id': clinic.id, 'patient': 'Floor Patient Anjali',
            'order_line': [(0, 0, {'product_id': product.id, 'product_uom_qty': 1})]})
        cls.order.action_confirm()
        invoice = cls.order._create_invoices()
        invoice.action_post()

        user = cls.env.ref('base.group_user')
        cls.bench = cls.env['res.users'].create({
            'name': 'Floor Bench', 'login': 'floor_track_bench',
            'group_ids': [(6, 0, [user.id, cls.env.ref('mrp.group_mrp_user').id])]})
        cls.office = cls.env['res.users'].create({
            'name': 'Floor Office', 'login': 'floor_track_office',
            'group_ids': [(6, 0, [user.id])]})
        cls.head = cls.env['res.users'].create({
            'name': 'Floor Head', 'login': 'floor_track_head',
            'group_ids': [(6, 0, [user.id, cls.env.ref('mrp.group_mrp_user').id,
                                  cls.env.ref('sales_team.group_sale_salesman').id])]})

    def test_a_bench_without_sales_rights_tracks_the_order(self):
        data = self.Track.with_user(self.bench).floor_search_work(self.order.name)
        self.assertTrue(data['found'])
        self.assertEqual(data['order']['id'], self.order.id)
        self.assertEqual(data['order']['patient'], 'Floor Patient Anjali')
        self.assertIn('productions', data)
        self.assertIn('deliveries', data)

    def test_the_money_stays_off_the_bench(self):
        data = self.Track.with_user(self.bench).floor_get_work(self.order.id)
        self.assertFalse(data['money'])
        self.assertEqual(data['order']['amount'], 0)
        self.assertEqual(data['invoices'], [])
        self.assertEqual(data['payments'], [])

    def test_a_sales_user_at_the_board_still_sees_the_money(self):
        data = self.Track.with_user(self.head).floor_get_work(self.order.id)
        self.assertTrue(data['money'])
        self.assertEqual(data['order']['amount'], self.order.amount_total)
        self.assertEqual(len(data['invoices']), 1)

    def test_the_bench_can_search_by_patient(self):
        rows = self.Track.with_user(self.bench).floor_live_search('Floor Patient Anj')
        self.assertIn(self.order.id, [r['id'] for r in rows])
        self.assertEqual(self.Track.with_user(self.bench).floor_live_search('F'), [])

    def test_the_floor_door_is_for_manufacturing_users_only(self):
        Track = self.Track.with_user(self.office)
        with self.assertRaises(AccessError):
            Track.floor_get_work(self.order.id)
        with self.assertRaises(AccessError):
            Track.floor_search_work(self.order.name)
        with self.assertRaises(AccessError):
            Track.floor_live_search('Floor Patient')

    def test_a_dead_id_is_not_found(self):
        self.assertFalse(self.Track.with_user(self.bench).floor_get_work(0)['found'])

    def test_the_station_board_opens_the_read_only_tracker(self):
        action = self.env.ref('lab_track.action_track_floor')
        self.assertEqual(action.tag, 'lab_track_readonly')

    def test_no_record_opens_from_the_read_only_screen(self):
        """Every drill-down on the tracker is withheld when it is read-only.

        A new link added without the guard would hand the bench a form it cannot
        read - and the client asked for none at all.
        """
        tree = etree.parse(str(Path(__file__).parents[1] / 'static/src/xml/track.xml'))
        opening = [el for el in tree.iter('button', 'a')
                   if 'openRecord' in (el.get('t-on-click') or '')
                   or el.get('href') or el.get('t-att-href')]
        self.assertTrue(opening)
        for el in opening:
            self.assertIn('!readonly', el.get('t-if') or '', etree.tostring(el)[:120])
