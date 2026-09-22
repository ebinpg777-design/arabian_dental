# -*- coding: utf-8 -*-
"""Courier tracking.

The promise: a consignment number becomes a page somebody can open, the parcel's state
is whatever its latest checkpoint says, a parcel the courier delivered is delivered
without anybody retyping it, and one that stops moving gets chased.
"""
from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestCourierTracking(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.courier = cls.env['lab.courier'].create({
            'name': 'Test Courier', 'code': 'TC', 'transit_days': 2,
            'tracking_url': 'https://example.com/track?no={awb}'})
        cls.clinic = cls.env['res.partner'].create({
            'name': 'Courier Clinic', 'is_clinic': True})
        cls.product = cls.env['product.product'].create({
            'name': 'Courier Appliance', 'type': 'consu', 'list_price': 750.0})

    def _delivery(self):
        order = self.env['sale.order'].create({
            'partner_id': self.clinic.id, 'patient': 'Courier Patient',
            'order_line': [(0, 0, {'product_id': self.product.id,
                                   'product_uom_qty': 1})]})
        order.action_confirm()
        return self.env['lab.delivery'].create({'sale_order_id': order.id})

    def _dispatch(self, delivery, awb='AWB123456', **kw):
        wizard = self.env['lab.courier.dispatch.wizard'].create(dict({
            'delivery_id': delivery.id, 'courier_id': self.courier.id,
            'courier_awb': awb, 'notify_doctor': False,
        }, **kw))
        wizard.action_confirm()
        return wizard

    # ---------------------------------------------------------------- the link
    def test_a_consignment_number_becomes_an_openable_page(self):
        delivery = self._delivery()
        self._dispatch(delivery, awb='AWB 99/1')
        self.assertEqual(delivery.courier_tracking_url,
                         'https://example.com/track?no=AWB%2099%2F1')

    def test_a_courier_with_no_tracking_page_still_records_the_number(self):
        plain = self.env['lab.courier'].create({'name': 'No Website Courier'})
        delivery = self._delivery()
        self.env['lab.courier.dispatch.wizard'].create({
            'delivery_id': delivery.id, 'courier_id': plain.id,
            'courier_awb': 'X1', 'notify_doctor': False}).action_confirm()
        self.assertEqual(delivery.courier_awb, 'X1')
        self.assertFalse(delivery.courier_tracking_url)
        with self.assertRaises(UserError):
            delivery.action_open_tracking()

    def test_a_tracking_url_without_a_place_for_the_number_is_refused(self):
        with self.assertRaises(ValidationError):
            self.env['lab.courier'].create({
                'name': 'Broken', 'tracking_url': 'https://example.com/track'})

    def test_a_tracking_url_must_be_a_url(self):
        with self.assertRaises(ValidationError):
            self.env['lab.courier'].create({
                'name': 'Broken2', 'tracking_url': 'example.com/track?no={awb}'})

    # ---------------------------------------------------------------- dispatch
    def test_dispatch_sends_the_parcel_out_and_books_it(self):
        delivery = self._delivery()
        self._dispatch(delivery)
        self.assertEqual(delivery.delivery_mode, 'courier')
        self.assertEqual(delivery.state, 'out')
        self.assertTrue(delivery.courier_dispatched_at)
        self.assertEqual(delivery.courier_status, 'booked')
        self.assertEqual(len(delivery.courier_event_ids), 1)

    def test_the_same_consignment_number_cannot_be_on_two_parcels(self):
        first, second = self._delivery(), self._delivery()
        self._dispatch(first, awb='DUP-1')
        with self.assertRaises(UserError):
            self._dispatch(second, awb='dup-1')  # same number, different case

    def test_an_empty_consignment_number_is_refused(self):
        delivery = self._delivery()
        with self.assertRaises(UserError):
            self._dispatch(delivery, awb='   ')

    def test_the_expected_date_is_suggested_from_the_courier(self):
        delivery = self._delivery()
        wizard = self.env['lab.courier.dispatch.wizard'].new({
            'delivery_id': delivery.id, 'courier_id': self.courier.id,
            'dispatched_at': fields.Datetime.now()})
        wizard._onchange_expected()
        self.assertEqual(
            wizard.expected_date,
            fields.Date.add(fields.Datetime.now().date(), days=2))

    def test_telling_the_doctor_puts_the_link_on_their_order(self):
        delivery = self._delivery()
        self._dispatch(delivery, notify_doctor=True)
        body = delivery.sale_order_id.message_ids[0].body
        self.assertIn('AWB123456', body)
        self.assertIn('https://example.com/track?no=AWB123456', body)

    # ---------------------------------------------------------------- checkpoints
    def test_the_parcel_state_is_its_latest_checkpoint(self):
        delivery = self._delivery()
        self._dispatch(delivery)
        delivery.record_event('in_transit', location='Kochi hub')
        self.assertEqual(delivery.courier_status, 'in_transit')
        delivery.record_event('out_for_delivery', location='Kottayam')
        self.assertEqual(delivery.courier_status, 'out_for_delivery')

    def test_a_backdated_checkpoint_does_not_move_the_parcel_backwards(self):
        """The office types this morning's scan, then goes back and fills in
        yesterday's. The parcel must not reverse because of typing order."""
        delivery = self._delivery()
        self._dispatch(delivery)
        now = fields.Datetime.now()
        delivery.record_event('out_for_delivery', when=now)
        delivery.record_event('in_transit', when=now - timedelta(days=1))
        self.assertEqual(delivery.courier_status, 'out_for_delivery')

    def test_the_courier_reporting_delivery_closes_the_delivery(self):
        """Asking the office to also walk the handover wizard is asking them to say
        the same thing twice — and the second saying is the one that gets skipped."""
        delivery = self._delivery()
        self._dispatch(delivery)
        delivery.record_event('delivered', location='Clinic front desk')
        self.assertEqual(delivery.state, 'delivered')
        self.assertEqual(delivery.delivery_outcome, 'clinic')
        self.assertTrue(delivery.delivered_datetime)
        self.assertFalse(delivery.is_delayed)

    def test_an_exception_raises_a_job_for_a_person(self):
        delivery = self._delivery()
        self._dispatch(delivery)
        delivery.record_event('exception', note='Address not found')
        self.assertEqual(delivery.courier_status, 'exception')
        self.assertTrue(self.env['mail.activity'].search_count([
            ('res_model', '=', 'lab.delivery'), ('res_id', '=', delivery.id)]))
        self.assertNotEqual(delivery.state, 'delivered')

    def test_a_returned_parcel_does_not_count_as_delivered(self):
        delivery = self._delivery()
        self._dispatch(delivery)
        delivery.record_event('returned', note='Clinic shut')
        self.assertEqual(delivery.courier_status, 'returned')
        self.assertEqual(delivery.state, 'out')

    def test_the_wizard_records_the_same_checkpoint_as_the_api(self):
        delivery = self._delivery()
        self._dispatch(delivery)
        self.env['lab.courier.event.wizard'].create({
            'delivery_id': delivery.id, 'status': 'picked_up',
            'location': 'Lab'}).action_confirm()
        self.assertEqual(delivery.courier_status, 'picked_up')

    # ---------------------------------------------------------------- stalled
    def test_a_silent_consignment_is_chased_once(self):
        delivery = self._delivery()
        self._dispatch(delivery)
        old = fields.Datetime.now() - timedelta(days=5)
        delivery.courier_event_ids.event_datetime = old
        delivery.courier_dispatched_at = old
        delivery.invalidate_recordset()

        self.assertEqual(self.env['lab.delivery']._cron_courier_stale_check(), 1)
        self.assertTrue(delivery.courier_is_stale)
        # Chased once, not every night — a channel that repeats gets muted.
        self.assertEqual(self.env['lab.delivery']._cron_courier_stale_check(), 0)

    def test_a_moving_consignment_is_left_alone(self):
        delivery = self._delivery()
        self._dispatch(delivery)
        self.assertEqual(self.env['lab.delivery']._cron_courier_stale_check(), 0)
        self.assertFalse(delivery.courier_is_stale)

    def test_a_new_scan_clears_the_stalled_flag(self):
        delivery = self._delivery()
        self._dispatch(delivery)
        old = fields.Datetime.now() - timedelta(days=5)
        delivery.courier_event_ids.event_datetime = old
        delivery.courier_dispatched_at = old
        delivery.invalidate_recordset()
        self.env['lab.delivery']._cron_courier_stale_check()
        self.assertTrue(delivery.courier_is_stale)

        delivery.record_event('in_transit', location='Moved at last')
        self.assertFalse(delivery.courier_is_stale)

    def test_a_delivered_consignment_is_never_chased(self):
        delivery = self._delivery()
        self._dispatch(delivery)
        old = fields.Datetime.now() - timedelta(days=9)
        delivery.courier_dispatched_at = old
        delivery.record_event('delivered', when=old)
        delivery.invalidate_recordset()
        self.assertEqual(self.env['lab.delivery']._cron_courier_stale_check(), 0)

    def test_hand_carried_deliveries_are_not_courier_business(self):
        delivery = self._delivery()
        delivery.write({'state': 'out'})
        self.assertEqual(self.env['lab.delivery']._cron_courier_stale_check(), 0)
        self.assertFalse(delivery.courier_is_stale)

    # ---------------------------------------------------------------- surfaces
    def test_the_tracking_window_shows_the_consignment(self):
        delivery = self._delivery()
        self._dispatch(delivery)
        delivery.record_event('in_transit', location='Kochi hub')
        data = self.env['lab.track'].get_work(delivery.sale_order_id.id)
        row = data['deliveries'][0]
        self.assertEqual(row['mode'], 'courier')
        self.assertEqual(row['courier'], 'Test Courier')
        self.assertEqual(row['awb'], 'AWB123456')
        self.assertTrue(row['tracking_url'])
        self.assertEqual(row['courier_status'], 'In Transit')

    def test_my_day_offers_the_tracking_page_instead_of_directions(self):
        delivery = self._delivery()
        delivery.executive_id = self.env.user
        self._dispatch(delivery)
        data = self.env['lab.my.day'].get_day()
        row = next(d for d in data['deliveries'] if d['id'] == delivery.id)
        self.assertTrue(row['is_courier'])
        self.assertEqual(row['awb'], 'AWB123456')
        self.assertTrue(row['tracking_url'])


@tagged('post_install', '-at_install')
class TestDeliveryButtons(TransactionCase):
    """The dispatch button has to be findable next to the native one."""

    def _sale_order_buttons(self):
        from lxml import etree
        arch = self.env.ref('sale.view_order_form')._get_combined_arch()
        if isinstance(arch, (str, bytes)):
            arch = etree.fromstring(arch)
        return arch.xpath("//div[@name='button_box']//button")

    def test_the_dispatch_button_does_not_wear_another_button_s_icon(self):
        """sale_stock already puts a truck labelled "Delivery" in this box. Two
        trucks a word apart is a button nobody can aim at."""
        buttons = self._sale_order_buttons()
        mine = [b for b in buttons if b.get('name') == 'action_view_deliveries']
        self.assertEqual(len(mine), 1, "the dispatch button is missing")
        my_icon = mine[0].get('icon')
        self.assertTrue(my_icon, "the dispatch button has no icon at all")
        others = [b.get('icon') for b in buttons
                  if b.get('name') != 'action_view_deliveries']
        self.assertNotIn(my_icon, others,
                         "the dispatch button shares icon %s with another button"
                         % my_icon)

    def test_the_dispatch_button_does_not_wear_another_button_s_words(self):
        buttons = self._sale_order_buttons()

        def label(btn):
            if btn.get('string'):
                return btn.get('string').strip().lower()
            info = btn.xpath(".//field[@widget='statinfo']")
            return (info[0].get('string') or '').strip().lower() if info else ''

        mine = [b for b in buttons if b.get('name') == 'action_view_deliveries'][0]
        others = [label(b) for b in buttons
                  if b.get('name') != 'action_view_deliveries']
        self.assertNotIn(label(mine), others)

