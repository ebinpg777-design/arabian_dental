# -*- coding: utf-8 -*-
"""Reading the consignment number off a courier receipt.

The promise: what the browser decoded from the barcode and what the vision pass read
off the paper are merged into a ranked list with a reason for each entry; the
courier's own number shape and the postal check digit do the ranking; a number
already on another parcel is flagged; and none of it needs the vision pass to be
configured. The vision pass itself is exercised with a fake client — no network.
(client, 2026-08-28)
"""
import base64
import io
import json
from types import SimpleNamespace
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase, tagged

from ..models.courier_receipt import (
    LabCourierReceipt, PARAM_KEY, s10_check_digit, s10_status, normalize)


def _png_b64():
    from PIL import Image
    out = io.BytesIO()
    Image.new('RGB', (40, 20), 'white').save(out, format='PNG')
    return base64.b64encode(out.getvalue()).decode()


@tagged('post_install', '-at_install')
class TestS10(TransactionCase):
    """The UPU S10 check digit, from the standard's own worked example."""

    def test_the_standards_worked_example(self):
        self.assertEqual(s10_check_digit('47312482'), 9)

    def test_the_two_special_cases(self):
        # 11 - (S mod 11) of 10 becomes 0, of 11 becomes 5.
        # Search a serial for each rather than hard-code one that might be wrong.
        seen = set()
        for n in range(0, 100000):
            serial = '%08d' % n
            total = sum(w * int(d) for w, d in zip((8, 6, 4, 2, 3, 5, 9, 7), serial))
            remainder = 11 - (total % 11)
            if remainder == 10 and 10 not in seen:
                self.assertEqual(s10_check_digit(serial), 0); seen.add(10)
            if remainder == 11 and 11 not in seen:
                self.assertEqual(s10_check_digit(serial), 5); seen.add(11)
            if seen == {10, 11}:
                break
        self.assertEqual(seen, {10, 11})

    def test_status_reads_the_ninth_digit(self):
        self.assertTrue(s10_status('EK473124829IN'))
        self.assertFalse(s10_status('EK473124828IN'))     # one digit off
        self.assertIsNone(s10_status('12345678901'))      # not postal-shaped

    def test_normalize_strips_the_grouping_people_type(self):
        self.assertEqual(normalize(' ek 4731 2482 9in '), 'EK473124829IN')


@tagged('post_install', '-at_install')
class TestCourierPatterns(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Courier = cls.env['lab.courier']
        cls.bluedart = cls.Courier.create({
            'name': 'Blue Dart T', 'code': 'BDT', 'awb_pattern': '^[0-9]{8,11}$',
            'tracking_url': 'https://example.com/bd?no={awb}'})
        cls.post = cls.Courier.create({
            'name': 'Post T', 'code': 'PT', 'awb_pattern': '^[A-Z]{2}[0-9]{9}IN$',
            'awb_s10': True, 'tracking_url': 'https://example.com/post?no={awb}'})
        cls.plain = cls.Courier.create({
            'name': 'Plain T', 'code': 'PL', 'tracking_url': 'https://example.com/p?no={awb}'})

    def test_a_pattern_recognises_its_own_numbers(self):
        self.assertTrue(self.bluedart.matches_awb('12345678901'))
        self.assertFalse(self.bluedart.matches_awb('EK123456789IN'))
        self.assertTrue(self.post.matches_awb('ek 1234 5678 9 in'))

    def test_no_pattern_means_nothing_to_say(self):
        self.assertIsNone(self.plain.matches_awb('anything'))

    def test_a_broken_pattern_is_refused(self):
        with self.assertRaises(ValidationError):
            self.plain.awb_pattern = '^[0-9'

    def test_the_wizard_warns_but_does_not_block(self):
        delivery = self._delivery()
        wizard = self.env['lab.courier.dispatch.wizard'].create({
            'delivery_id': delivery.id, 'courier_id': self.bluedart.id,
            'courier_awb': 'EK123456789IN', 'notify_doctor': False})
        self.assertTrue(wizard.awb_looks_wrong)
        wizard.action_confirm()                     # still goes through
        self.assertEqual(delivery.courier_awb, 'EK123456789IN')

    def _delivery(self):
        clinic = self.env['res.partner'].create({'name': 'Pattern Clinic', 'is_clinic': True})
        product = self.env['product.product'].create({
            'name': 'Pattern Appliance', 'type': 'consu', 'list_price': 10.0})
        order = self.env['sale.order'].create({
            'partner_id': clinic.id, 'patient': 'Pattern Patient',
            'order_line': [(0, 0, {'product_id': product.id, 'product_uom_qty': 1})]})
        order.action_confirm()
        return self.env['lab.delivery'].create({'sale_order_id': order.id})


@tagged('post_install', '-at_install')
class TestReceiptReader(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Reader = cls.env['lab.courier.receipt']
        cls.env['ir.config_parameter'].sudo().set_param(PARAM_KEY, False)
        cls.bluedart = cls.env['lab.courier'].create({
            'name': 'Blue Dart R', 'code': 'BDR', 'awb_pattern': '^[0-9]{8,11}$',
            'tracking_url': 'https://example.com/bd?no={awb}'})
        cls.post = cls.env['lab.courier'].create({
            'name': 'Post R', 'code': 'PR', 'awb_pattern': '^[A-Z]{2}[0-9]{9}IN$',
            'awb_s10': True, 'tracking_url': 'https://example.com/post?no={awb}'})
        cls.clinic = cls.env['res.partner'].create({'name': 'Reader Clinic', 'is_clinic': True})
        cls.product = cls.env['product.product'].create({
            'name': 'Reader Appliance', 'type': 'consu', 'list_price': 10.0})

    def _delivery(self):
        order = self.env['sale.order'].create({
            'partner_id': self.clinic.id, 'patient': 'Reader Patient',
            'order_line': [(0, 0, {'product_id': self.product.id, 'product_uom_qty': 1})]})
        order.action_confirm()
        return self.env['lab.delivery'].create({'sale_order_id': order.id})

    # ------------------------------------------------------------ barcode only
    def test_without_a_key_only_the_barcode_engine_runs(self):
        result = self.Reader.read_receipt(_png_b64(), ['12345678901'])
        self.assertEqual(result['engines'], ['barcode'])
        self.assertFalse(result['vision_enabled'])
        self.assertEqual(result['candidates'][0]['token'], '12345678901')
        self.assertTrue(result['auto'])

    def test_a_barcode_that_fits_one_courier_suggests_that_courier(self):
        only_mine = self.env['lab.courier'].create({
            # A hyphen: no seeded courier's pattern allows one, so this shape is
            # this courier's alone - which is the case under test.
            'name': 'Only Mine R', 'code': 'OMR', 'awb_pattern': '^OMR-[0-9]{4}$',
            'tracking_url': 'https://example.com/om?no={awb}'})
        result = self.Reader.read_receipt(None, ['OMR-1234'])
        self.assertEqual(result['courier']['id'], only_mine.id)
        self.assertEqual(result['courier']['why'], "from the number's shape")
        self.assertIn('Only Mine R', result['candidates'][0]['reason'])

    def test_a_shape_two_couriers_share_suggests_neither(self):
        # The seeded Blue Dart and the test one both accept eleven digits: guessing
        # between them would be wrong half the time, so the widget leaves it.
        result = self.Reader.read_receipt(None, ['12345678901'])
        self.assertIsNone(result['courier'])
        self.assertGreaterEqual(len(result['candidates'][0]['courier_ids']), 2)

    def test_a_valid_postal_number_outranks_a_phone_number(self):
        result = self.Reader.read_receipt(None, ['9876543210', 'EK473124829IN'])
        tokens = [c['token'] for c in result['candidates']]
        self.assertEqual(tokens[0], 'EK473124829IN')
        self.assertIn('check digit is valid', result['candidates'][0]['reason'])
        self.assertIn('mobile number', result['candidates'][1]['reason'])

    def test_a_misread_postal_digit_is_called_out(self):
        result = self.Reader.read_receipt(None, ['EK473124828IN'])
        self.assertIn('FAILS', result['candidates'][0]['reason'])
        self.assertFalse(result['auto'])

    def test_the_chosen_courier_ranks_its_own_shape_up_and_others_down(self):
        result = self.Reader.read_receipt(
            None, ['12345678901', 'EK473124829IN'], courier_id=self.bluedart.id)
        self.assertEqual(result['candidates'][0]['token'], '12345678901')
        self.assertIn('does not look like', result['candidates'][1]['reason'])
        self.assertIsNone(result['courier'])          # they chose already

    def test_a_number_already_on_another_parcel_is_flagged_and_not_auto_applied(self):
        other = self._delivery()
        self.env['lab.courier.dispatch.wizard'].create({
            'delivery_id': other.id, 'courier_id': self.bluedart.id,
            'courier_awb': '12345678901', 'notify_doctor': False}).action_confirm()
        result = self.Reader.read_receipt(None, ['12345678901'])
        self.assertEqual(result['candidates'][0]['clash'], other.name)
        self.assertFalse(result['auto'])

    def test_rubbish_is_dropped_and_grouping_is_normalised(self):
        result = self.Reader.read_receipt(None, ['ab', '1234 5678 901', 'x' * 40])
        self.assertEqual([c['token'] for c in result['candidates']], ['12345678901'])

    # ------------------------------------------------------------ vision pass
    def _fake_client(self, payload, stop_reason='end_turn'):
        text = json.dumps(payload) if isinstance(payload, dict) else payload
        response = SimpleNamespace(
            stop_reason=stop_reason,
            content=[SimpleNamespace(type='text', text=text)])
        create = lambda **kw: response                                   # noqa: E731
        return SimpleNamespace(beta=SimpleNamespace(
            messages=SimpleNamespace(create=create)))

    def test_with_a_key_the_receipt_is_read_and_agreement_scores_highest(self):
        self.env['ir.config_parameter'].sudo().set_param(PARAM_KEY, 'test-key')
        answer = {'consignment_number': '1234 5678 901', 'courier_name': 'BLUE DART R',
                  'booking_date': '2026-08-27', 'receiver': 'Dr X', 'destination': 'Kochi',
                  'other_numbers': ['9876543210', '682001'], 'legible': True}
        with patch.object(LabCourierReceipt, '_vision_client',
                          return_value=self._fake_client(answer)):
            result = self.Reader.read_receipt(_png_b64(), ['12345678901'])
        self.assertEqual(result['engines'], ['barcode', 'vision'])
        top = result['candidates'][0]
        self.assertEqual(top['token'], '12345678901')
        self.assertIn('barcode', top['reason'])
        self.assertIn('read from the receipt', top['reason'])
        self.assertEqual(top['score'], 100)
        self.assertEqual(result['booking_date'], '2026-08-27')
        # the phone and the pin code are listed, but at the bottom, scored to nothing
        tail = result['candidates'][1:]
        self.assertEqual({c['token'] for c in tail}, {'9876543210', '682001'})
        self.assertTrue(all(c['score'] == 0 for c in tail))

    def test_the_courier_printed_on_the_receipt_is_matched_by_name(self):
        self.env['ir.config_parameter'].sudo().set_param(PARAM_KEY, 'test-key')
        answer = {'consignment_number': 'XYZ99', 'courier_name': 'post r speed',
                  'booking_date': '', 'receiver': '', 'destination': '',
                  'other_numbers': [], 'legible': True}
        with patch.object(LabCourierReceipt, '_vision_client',
                          return_value=self._fake_client(answer)):
            result = self.Reader.read_receipt(_png_b64(), [])
        self.assertEqual(result['courier']['id'], self.post.id)
        self.assertEqual(result['courier']['why'], 'printed on the receipt')

    def test_a_refusal_or_bad_answer_degrades_to_barcode_only(self):
        self.env['ir.config_parameter'].sudo().set_param(PARAM_KEY, 'test-key')
        with patch.object(LabCourierReceipt, '_vision_client',
                          return_value=self._fake_client('{}', stop_reason='refusal')):
            result = self.Reader.read_receipt(_png_b64(), ['12345678901'])
        self.assertTrue(result['vision_error'])
        self.assertEqual(result['candidates'][0]['token'], '12345678901')
        with patch.object(LabCourierReceipt, '_vision_client',
                          return_value=self._fake_client('not json at all')):
            result = self.Reader.read_receipt(_png_b64(), ['12345678901'])
        self.assertIn('could not be understood', result['vision_error'])

    def test_the_photo_is_shrunk_before_it_is_sent(self):
        from PIL import Image
        out = io.BytesIO()
        Image.new('RGB', (4000, 3000), 'white').save(out, format='PNG')
        big = base64.b64encode(out.getvalue()).decode()
        small = Image.open(io.BytesIO(base64.b64decode(self.Reader._vision_prepare(big))))
        self.assertEqual(small.format, 'JPEG')
        self.assertLessEqual(max(small.size), 1600)

    # ------------------------------------------------------------ provenance + sync
    def test_dispatch_records_where_the_number_came_from_and_mirrors_it_to_the_transfer(self):
        delivery = self._delivery()
        wizard = self.env['lab.courier.dispatch.wizard'].create({
            'delivery_id': delivery.id, 'courier_id': self.bluedart.id,
            'courier_awb': '12345678901', 'awb_source': 'barcode',
            'receipt_image': _png_b64(), 'notify_doctor': False})
        wizard.action_confirm()
        self.assertEqual(delivery.courier_awb_source, 'barcode')
        self.assertEqual(delivery.courier_awb_captured_by_id, self.env.user)
        self.assertTrue(delivery.courier_awb_captured_at)
        self.assertTrue(delivery.courier_receipt_image)
        picking = delivery.sale_order_id.picking_ids.filtered(
            lambda p: p.picking_type_code == 'outgoing')
        self.assertTrue(picking)
        self.assertEqual(set(picking.mapped('consignment_number')), {'12345678901'})
        self.assertEqual(set(picking.mapped('courier_company')), {'Blue Dart R'})
        self.assertEqual(set(picking.mapped('consignment_source')), {'barcode'})
        self.assertTrue(all(picking.mapped('courier_receipt_image')))
        # the receipt is on the parcel's thread, not the doctor's
        self.assertTrue(delivery.message_ids.filtered('attachment_ids'))

    def test_a_typed_edit_on_the_form_is_recorded_as_typed(self):
        delivery = self._delivery()
        delivery.write({'delivery_mode': 'courier', 'courier_id': self.bluedart.id,
                        'courier_awb': '11111111'})
        self.assertEqual(delivery.courier_awb_source, 'typed')
        picking = delivery.sale_order_id.picking_ids.filtered(
            lambda p: p.picking_type_code == 'outgoing')
        self.assertEqual(set(picking.mapped('consignment_number')), {'11111111'})

    def test_a_pickup_never_touches_a_transfer(self):
        delivery = self._delivery()
        delivery.write({'direction': 'in', 'delivery_mode': 'courier',
                        'courier_id': self.bluedart.id, 'courier_awb': '22222222'})
        picking = delivery.sale_order_id.picking_ids.filtered(
            lambda p: p.picking_type_code == 'outgoing')
        self.assertFalse(any(picking.mapped('consignment_number')))


@tagged('post_install', '-at_install')
class TestWhereItWasHandedOver(TransactionCase):
    """The executive's location, captured when Delivered is pressed.

    Not at creation: a record raised in the morning and delivered in the afternoon
    would otherwise carry the office's coordinates. A manager's question is "was this
    really handed over at the clinic", and only the handover moment answers it.
    (client, 2026-08-28)
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # A clinic with a pin: Kochi.
        cls.clinic = cls.env['res.partner'].create({
            'name': 'Pinned Clinic', 'is_clinic': True,
            'partner_latitude': 9.9312, 'partner_longitude': 76.2673})
        cls.unpinned = cls.env['res.partner'].create({
            'name': 'Unpinned Clinic', 'is_clinic': True})
        cls.product = cls.env['product.product'].create({
            'name': 'Located Appliance', 'type': 'consu', 'list_price': 10.0})

    def _delivery(self, partner=None):
        order = self.env['sale.order'].create({
            'partner_id': (partner or self.clinic).id, 'patient': 'Located Patient',
            'order_line': [(0, 0, {'product_id': self.product.id,
                                   'product_uom_qty': 1})]})
        order.action_confirm()
        return self.env['lab.delivery'].create({'sale_order_id': order.id})

    def _hand_over(self, delivery, lat=False, lon=False, accuracy=False, **kw):
        """The whole path: the button's action context into the done wizard."""
        action = delivery.action_mark_delivered(lat, lon, accuracy)
        wizard = self.env['lab.delivery.done.wizard'].with_context(
            **action['context']).create(dict({
                'delivery_id': delivery.id, 'delivery_outcome': 'clinic'}, **kw))
        wizard.action_confirm()
        return wizard

    # ------------------------------------------------------------ the moment
    def test_creating_a_delivery_captures_no_location_at_all(self):
        delivery = self._delivery()
        self.assertEqual(delivery.delivered_gps_state, 'nofix')
        self.assertFalse(delivery.delivered_lat)
        self.assertFalse(delivery.delivered_by_id)

    def test_the_fix_is_taken_when_delivered_is_pressed(self):
        delivery = self._delivery()
        self._hand_over(delivery, 9.9312, 76.2673, 12.0)
        self.assertEqual(delivery.state, 'delivered')
        self.assertEqual(delivery.delivered_gps_state, 'ok')
        self.assertLess(delivery.delivered_distance_m, 50)
        self.assertEqual(delivery.delivered_accuracy_m, 12.0)
        self.assertEqual(delivery.delivered_by_id, self.env.user)
        self.assertTrue(delivery.delivered_datetime)

    def test_a_handover_far_from_the_clinic_says_so_with_the_distance(self):
        # Alappuzha, ~50 km down the coast.
        delivery = self._delivery()
        self._hand_over(delivery, 9.4981, 76.3388)
        self.assertEqual(delivery.delivered_gps_state, 'far')
        self.assertGreater(delivery.delivered_distance_m, 40000)

    def test_confirming_from_a_desk_carries_no_location_rather_than_a_fake_one(self):
        delivery = self._delivery()
        self._hand_over(delivery)
        self.assertEqual(delivery.delivered_gps_state, 'nofix')
        self.assertEqual(delivery.delivered_distance_m, 0.0)
        self.assertFalse(delivery.delivered_map_url)
        self.assertEqual(delivery.delivered_by_id, self.env.user,
                         "who pressed it is known even when where is not")

    def test_a_clinic_with_no_pin_cannot_be_measured_against(self):
        delivery = self._delivery(partner=self.unpinned)
        self._hand_over(delivery, 9.9312, 76.2673)
        self.assertEqual(delivery.delivered_gps_state, 'nopin')
        self.assertEqual(delivery.delivered_distance_m, 0.0)

    def test_the_pin_is_written_once_and_a_re_save_never_moves_it(self):
        delivery = self._delivery()
        self._hand_over(delivery, 9.9312, 76.2673)
        before = (delivery.delivered_lat, delivery.delivered_lon)
        delivery.with_context(lab_delivered_lat=1.0,
                              lab_delivered_lon=1.0).write({'state': 'delivered'})
        self.assertEqual((delivery.delivered_lat, delivery.delivered_lon), before)

    def test_the_courier_feed_closing_a_parcel_leaves_no_location(self):
        delivery = self._delivery()
        delivery.write({'state': 'delivered'})
        self.assertEqual(delivery.delivered_gps_state, 'nofix')

    # ------------------------------------------------------------ inbound
    def test_receiving_at_the_lab_captures_it_the_same_way(self):
        delivery = self.env['lab.delivery'].create({
            'direction': 'in', 'partner_id': self.clinic.id})
        action = delivery.action_receive_at_lab(9.9312, 76.2673, 9.0)
        self.assertEqual(action['context']['lab_delivered_lat'], 9.9312)
        delivery.with_context(**action['context']).write({'state': 'delivered'})
        self.assertEqual(delivery.delivered_gps_state, 'ok')

    def test_a_button_pressed_with_no_fix_adds_no_keys_at_all(self):
        delivery = self._delivery()
        context = delivery.action_mark_delivered()['context']
        self.assertNotIn('lab_delivered_lat', context,
                         "no fix means no stamp, not a stamp of zero")

    # ------------------------------------------------------------ the map
    def test_the_map_link_points_at_the_fix(self):
        delivery = self._delivery()
        self._hand_over(delivery, 9.9312, 76.2673)
        self.assertIn('9.9312', delivery.delivered_map_url)
        self.assertIn('76.2673', delivery.delivered_map_url)
        self.assertEqual(delivery.action_open_delivered_map()['type'],
                         'ir.actions.act_url')

    def test_a_delivery_with_no_fix_refuses_to_open_a_map(self):
        delivery = self._delivery()
        self._hand_over(delivery)
        with self.assertRaises(UserError):
            delivery.action_open_delivered_map()

    # ------------------------------------------------------------ who may see it
    def test_an_executive_cannot_read_the_location(self):
        """It is a check on the round, not something the round sees."""
        executive = self.env['res.users'].create({
            'name': 'Located Exec', 'login': 'located_exec',
            'group_ids': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref('lab_fieldwork.group_fieldwork_executive').id])]})
        seen = self.env['lab.delivery'].with_user(executive).fields_get()
        self.assertNotIn('delivered_lat', seen)
        self.assertNotIn('delivered_gps_state', seen)

    def test_a_field_manager_can_read_it(self):
        delivery = self._delivery()
        self._hand_over(delivery, 9.9312, 76.2673)
        manager = self.env['res.users'].create({
            'name': 'Located Manager', 'login': 'located_mgr',
            'group_ids': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref('lab_fieldwork.group_fieldwork_manager').id])]})
        seen = delivery.with_user(manager).read(
            ['delivered_gps_state', 'delivered_distance_m'])
        self.assertEqual(seen[0]['delivered_gps_state'], 'ok')

    def test_an_administrator_can_read_it_too(self):
        delivery = self._delivery()
        self._hand_over(delivery, 9.9312, 76.2673)
        admin = self.env['res.users'].create({
            'name': 'Located Admin', 'login': 'located_admin',
            'group_ids': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref('base.group_system').id])]})
        seen = delivery.with_user(admin).read(['delivered_gps_state'])
        self.assertEqual(seen[0]['delivered_gps_state'], 'ok')


@tagged('post_install', '-at_install')
class TestHandOverWorklist(TransactionCase):
    """Raising a hand-over from the work rather than from a list of clinics.

    Three complaints, one shape: the clinic dropdown offered every doctor on the
    route including the ones with nothing waiting; the order checkboxes showed the
    case number when the doctor quotes the invoice number; and there was no way to
    see everything waiting or to raise a delivery straight from it.
    (client, 2026-08-28)
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.busy = cls.env['res.partner'].create({
            'name': 'Busy Clinic', 'is_clinic': True})
        cls.quiet = cls.env['res.partner'].create({
            'name': 'Quiet Clinic', 'is_clinic': True})
        cls.product = cls.env['product.product'].create({
            'name': 'Worklist Appliance', 'type': 'consu', 'list_price': 100.0})

    def _order(self, partner=None, confirm=True):
        order = self.env['sale.order'].create({
            'partner_id': (partner or self.busy).id, 'patient': 'Worklist Patient',
            'order_line': [(0, 0, {'product_id': self.product.id,
                                   'product_uom_qty': 1})]})
        if confirm:
            order.action_confirm()
        return order

    # ------------------------------------------------------------ the flag
    def test_a_confirmed_order_nobody_carries_is_waiting_to_be_delivered(self):
        order = self._order()
        self.assertTrue(order.lab_awaiting_delivery)

    def test_a_quotation_is_not_waiting_for_anything(self):
        self.assertFalse(self._order(confirm=False).lab_awaiting_delivery)

    def test_raising_a_delivery_takes_the_order_off_the_list(self):
        order = self._order()
        order.action_lab_raise_delivery()
        self.assertFalse(order.lab_awaiting_delivery)

    def test_a_cancelled_delivery_puts_the_order_back(self):
        order = self._order()
        action = order.action_lab_raise_delivery()
        delivery = self.env['lab.delivery'].browse(action['res_id'])
        delivery.action_cancel()
        self.assertTrue(order.lab_awaiting_delivery,
                        "that case genuinely needs raising again")

    def test_a_pickup_does_not_count_as_carrying_the_work(self):
        order = self._order()
        self.env['lab.delivery'].create({
            'direction': 'in', 'partner_id': self.busy.id,
            'sale_order_id': order.id})
        self.assertTrue(order.lab_awaiting_delivery)

    # ------------------------------------------------------------ the clinic list
    def test_only_clinics_with_work_waiting_are_offered_for_a_hand_over(self):
        self._order(partner=self.busy)
        wizard = self.env['lab.delivery.new.wizard'].new({'direction': 'out'})
        wizard._compute_waiting_partners()
        offered = wizard.waiting_partner_ids.ids
        self.assertIn(self.busy.id, offered)
        self.assertNotIn(self.quiet.id, offered)

    def test_collecting_is_not_narrowed_at_all(self):
        wizard = self.env['lab.delivery.new.wizard'].new({'direction': 'in'})
        wizard._compute_waiting_partners()
        self.assertFalse(wizard.waiting_partner_ids,
                         "an impression can be collected from any clinic")

    # ------------------------------------------------------------ the label
    def test_the_order_label_carries_the_invoice_number_where_asked(self):
        order = self._order()
        invoice = order._create_invoices()
        invoice.action_post()
        order.invalidate_recordset()
        self.assertEqual(order.lab_invoice_ref, invoice.name)
        self.assertIn(invoice.name,
                      order.with_context(lab_with_invoice=1).display_name)
        self.assertIn(order.name,
                      order.with_context(lab_with_invoice=1).display_name)

    def test_nothing_else_in_the_database_gets_a_longer_name(self):
        order = self._order()
        invoice = order._create_invoices()
        invoice.action_post()
        order.invalidate_recordset()
        self.assertNotIn(invoice.name, order.display_name)

    def test_an_uninvoiced_order_simply_shows_its_own_number(self):
        order = self._order()
        self.assertFalse(order.lab_invoice_ref)
        self.assertEqual(order.with_context(lab_with_invoice=1).display_name,
                         order.display_name)

    def test_a_draft_invoice_is_not_quoted_as_the_invoice_number(self):
        order = self._order()
        order._create_invoices()                       # left in draft
        order.invalidate_recordset()
        self.assertFalse(order.lab_invoice_ref)

    # ------------------------------------------------------------ the worklist
    def test_the_worklist_is_a_list_with_no_form_behind_it(self):
        """Opening an order is not what anybody came to this screen to do."""
        action = self.env['sale.order'].action_lab_delivery_worklist()
        self.assertEqual(action['view_mode'], 'list')
        self.assertEqual([v[1] for v in action['views']], ['list'])

    def test_the_raise_button_is_the_first_column(self):
        view = self.env.ref('lab_delivery.view_order_list_awaiting_delivery')
        arch = view.arch
        button = arch.index('action_lab_raise_delivery')
        first_field = arch.index('<field')
        self.assertLess(button, first_field,
                        "an action at the far right is one a thumb never reaches")

    def test_the_worklist_opens_on_everything_waiting(self):
        self._order()
        action = self.env['sale.order'].action_lab_delivery_worklist()
        self.assertEqual(action['res_model'], 'sale.order')
        self.assertIn(('lab_awaiting_delivery', '=', True), action['domain'])
        self.assertTrue(action['views'], "handed to doAction, so views is required")

    def test_the_wizard_can_open_it_too(self):
        wizard = self.env['lab.delivery.new.wizard'].new({'direction': 'out'})
        self.assertEqual(wizard.action_open_worklist()['res_model'], 'sale.order')

    def test_a_row_raises_its_own_delivery_with_the_clinic_from_the_order(self):
        order = self._order()
        action = order.action_lab_raise_delivery()
        delivery = self.env['lab.delivery'].browse(action['res_id'])
        self.assertEqual(delivery.sale_order_id, order)
        self.assertEqual(delivery.partner_id, self.busy,
                         "the clinic comes from the order, not from a picker")
        self.assertEqual(delivery.direction, 'out')
        self.assertEqual(delivery.state, 'assigned')

    def test_raising_twice_is_refused_rather_than_making_a_second_box(self):
        order = self._order()
        order.action_lab_raise_delivery()
        with self.assertRaises(UserError):
            order.action_lab_raise_delivery()

    def test_an_unconfirmed_order_cannot_be_raised(self):
        with self.assertRaises(UserError):
            self._order(confirm=False).action_lab_raise_delivery()


@tagged('post_install', '-at_install')
class TestScanToDeliver(TransactionCase):
    """One camera for every code the lab prints.

    The invoice carries a barcode of its own number, the job card the order number,
    a dispatch note the delivery number. An executive at a door should not have to
    know which of the three is in their hand. A scan never changes state on its own:
    it opens the handover popup, carrying the fix taken at the scan.
    (client, 2026-08-28)
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.clinic = cls.env['res.partner'].create({
            'name': 'Scan Clinic', 'is_clinic': True,
            'partner_latitude': 9.9312, 'partner_longitude': 76.2673})
        cls.product = cls.env['product.product'].create({
            'name': 'Scan Appliance', 'type': 'consu', 'list_price': 100.0})
        cls.Delivery = cls.env['lab.delivery']

    def _order(self):
        order = self.env['sale.order'].create({
            'partner_id': self.clinic.id, 'patient': 'Scan Patient',
            'order_line': [(0, 0, {'product_id': self.product.id,
                                   'product_uom_qty': 1})]})
        order.action_confirm()
        return order

    def _invoice(self, order):
        invoice = order._create_invoices()
        invoice.action_post()
        return invoice

    # ------------------------------------------------------------ what it reads
    def test_the_first_scan_stages_the_box_and_asks(self):
        """Scan one, at the lab: the box is raised and the choice is offered -
        onto the run, or deliver now. Nothing is delivered by a first scan."""
        order = self._order()
        action = self.Delivery.scan(order.name, 9.9312, 76.2673, 10.0)
        delivery = self.Delivery.search([('sale_order_id', '=', order.id)])
        self.assertTrue(delivery, "no delivery existed, so one is raised")
        self.assertEqual(delivery.state, 'assigned',
                         "raised, not silently delivered - a camera can misread")
        found = action['context']['lab_scan_summary']
        self.assertEqual(found['stage'], 'first')
        self.assertEqual(found['id'], delivery.id)
        self.assertEqual(action['type'], 'ir.actions.act_window_close',
                         'no wizard yet - the choice belongs to the executive')

    def test_the_second_scan_delivers(self):
        """Scan two, at the door: the box is out on the run, so the same code now
        goes straight to the hand-over wizard, fix and all."""
        order = self._order()
        first = self.Delivery.scan(order.name)
        delivery = self.Delivery.browse(first['context']['lab_scan_summary']['id'])
        delivery.action_scan_load()
        self.assertEqual(delivery.state, 'out', 'loaded onto the run')
        action = self.Delivery.scan(order.name, 9.9312, 76.2673, 10.0)
        found = action['context']['lab_scan_summary']
        self.assertEqual(found['stage'], 'deliver')
        self.assertEqual(action['res_model'], 'lab.delivery.done.wizard')
        self.assertEqual(action['context']['default_delivery_id'], delivery.id)
        self.assertEqual(action['context']['lab_delivered_lat'], 9.9312)

    def test_loading_is_idempotent_and_only_forward(self):
        order = self._order()
        delivery = self.Delivery.create({'sale_order_id': order.id})
        delivery.write({'state': 'delivered'})
        delivery.action_scan_load()
        self.assertEqual(delivery.state, 'delivered',
                         'a finished box cannot be loaded back onto a run')

    def test_scanning_an_invoice_number_finds_the_same_case(self):
        order = self._order()
        invoice = self._invoice(order)
        action = self.Delivery.scan(invoice.name)
        delivery = self.Delivery.search([('sale_order_id', '=', order.id)])
        self.assertEqual(action['context']['lab_scan_summary']['id'], delivery.id)

    def test_a_code_is_read_regardless_of_case(self):
        """The sheet says OC243101; a phone keyboard types oc243101. Same box."""
        order = self._order()
        action = self.Delivery.scan(order.name.lower())
        delivery = self.Delivery.search([('sale_order_id', '=', order.id)])
        self.assertEqual(action['context']['lab_scan_summary']['id'], delivery.id)

    def test_scanning_a_delivery_number_opens_that_delivery(self):
        order = self._order()
        delivery = self.Delivery.create({'sale_order_id': order.id})
        action = self.Delivery.scan(delivery.name)
        self.assertEqual(action['context']['lab_scan_summary']['id'], delivery.id)

    def test_an_existing_delivery_is_reused_rather_than_doubled(self):
        order = self._order()
        first = self.Delivery.create({'sale_order_id': order.id})
        self.Delivery.scan(order.name)
        self.assertEqual(
            self.Delivery.search_count([('sale_order_id', '=', order.id)]), 1,
            "scanning twice must not manufacture a second box")
        self.assertEqual(
            self.Delivery.scan(order.name)['context']['lab_scan_summary']['id'],
            first.id)

    # ------------------------------------------------------------ what it refuses
    def test_an_unknown_code_names_itself_rather_than_opening_the_wrong_box(self):
        with self.assertRaises(UserError) as caught:
            self.Delivery.scan('NOT-A-REAL-CODE')
        self.assertIn('NOT-A-REAL-CODE', str(caught.exception))

    def test_an_empty_scan_is_refused(self):
        with self.assertRaises(UserError):
            self.Delivery.scan('   ')

    def test_a_cancelled_delivery_is_not_revived_by_a_scan(self):
        order = self._order()
        delivery = self.Delivery.create({'sale_order_id': order.id})
        delivery.action_cancel()
        # cancelled ones are skipped, so the scan raises a fresh hand-over
        action = self.Delivery.scan(order.name)
        new = self.Delivery.browse(action['context']['lab_scan_summary']['id'])
        self.assertNotEqual(new, delivery)
        self.assertEqual(new.state, 'assigned')

    def test_a_delivered_case_says_so_instead_of_reopening_it(self):
        order = self._order()
        delivery = self.Delivery.create({'sale_order_id': order.id})
        delivery.write({'state': 'delivered'})
        action = self.Delivery.scan(order.name)
        # The finished box opens read-only, and the summary rides the context so
        # the phone raises a sticky warning it controls - a bare notification
        # action could be swallowed mid-navigation and read as a dead scanner.
        self.assertEqual(action['res_model'], 'lab.delivery')
        self.assertEqual(action['res_id'], delivery.id)
        found = action['context']['lab_scan_summary']
        self.assertEqual(found['state'], 'delivered')
        self.assertEqual(found['delivery'], delivery.name)
        self.assertTrue(found['when'], "when it was handed over is the whole answer")
        self.assertFalse(found['created'])

    def test_an_invoice_covering_several_cases_asks_which_one(self):
        first, second = self._order(), self._order()
        invoice = (first + second)._create_invoices()
        invoice.action_post()
        action = self.Delivery.scan(invoice.name)
        self.assertEqual(action['res_model'], 'sale.order')
        self.assertEqual(sorted(action['domain'][0][2]), sorted((first + second).ids))
        found = action['context']['lab_scan_summary']
        self.assertEqual((found['state'], found['count']), ('several', 2),
                         "the phone says why a list just opened")

    # ------------------------------------------------------------ the fix
    def test_the_scan_carries_the_location_into_the_handover(self):
        order = self._order()
        delivery = self.Delivery.create({'sale_order_id': order.id, 'state': 'out'})
        action = self.Delivery.scan(order.name, 9.9312, 76.2673, 6.0)
        wizard = self.env['lab.delivery.done.wizard'].with_context(
            **action['context']).create({
                'delivery_id': action['context']['default_delivery_id'],
                'delivery_outcome': 'clinic'})
        wizard.action_confirm()
        self.assertEqual(delivery.state, 'delivered')
        self.assertEqual(delivery.delivered_gps_state, 'ok')
        self.assertEqual(delivery.delivered_accuracy_m, 6.0)

    def test_a_scan_with_no_fix_still_hands_the_case_over(self):
        order = self._order()
        self.Delivery.create({'sale_order_id': order.id, 'state': 'out'})
        action = self.Delivery.scan(order.name)
        self.assertNotIn('lab_delivered_lat', action['context'])

    def test_the_payment_qr_is_recognised_and_named(self):
        """The invoice also carries a UPI QR at its foot; a camera held back far
        enough to see the whole sheet reads that instead. (client, 2026-08-28)"""
        upi = ('upi://pay?pa=124378187005338@cnrb&pn=LAB CREATION&mc=5047'
               '&tr=1234567887654321&am=0&cu=INR')
        with self.assertRaises(UserError) as caught:
            self.Delivery.scan(upi)
        message = str(caught.exception)
        self.assertIn('payment QR', message)
        self.assertIn('under the doctor', message)

    def test_any_url_is_treated_the_same_way(self):
        with self.assertRaises(UserError) as caught:
            self.Delivery.scan('https://example.com/whatever')
        self.assertIn('payment QR', str(caught.exception))

    def test_the_scan_says_what_it_found_before_anything_is_confirmed(self):
        order = self._order()
        action = self.Delivery.scan(order.name)
        found = action['context']['lab_scan_summary']
        self.assertEqual(found['clinic'], self.clinic.display_name)
        self.assertEqual(found['patient'], 'Scan Patient')
        self.assertEqual(found['order'], order.name)
        self.assertTrue(found['delivery'].startswith('DLV'))
        self.assertTrue(found['created'], "this one was raised by the scan")
        self.assertEqual(found['state'], 'raised')

    def test_an_existing_delivery_is_not_reported_as_newly_raised(self):
        order = self._order()
        self.Delivery.create({'sale_order_id': order.id})
        action = self.Delivery.scan(order.name)
        found = action['context']['lab_scan_summary']
        self.assertFalse(found['created'])
        self.assertEqual(found['state'], 'ready')
        self.assertEqual(found['stage'], 'first',
                         'still at the lab: staged, not delivered')

    def test_an_inbound_bag_opens_the_receive_wizard_instead(self):
        delivery = self.Delivery.create({
            'direction': 'in', 'partner_id': self.clinic.id})
        action = self.Delivery.scan(delivery.name, 9.9312, 76.2673)
        self.assertEqual(action['res_model'], 'lab.delivery.receive.wizard')

    # ------------------------------------------------------------ the printout
    def test_the_invoice_carries_a_scannable_barcode_of_its_own_number(self):
        order = self._order()
        invoice = self._invoice(order)
        html = self.env['ir.actions.report']._render_qweb_html(
            'sale_custom.report_dental_invoice', invoice.ids)[0].decode()
        # Odoo 19 embeds the barcode as an inline PNG rather than linking
        # /report/barcode - better for a PDF, which fetches nothing.
        self.assertIn('alt="Barcode %s"' % invoice.name, html,
                      "the barcode must carry THIS invoice's number")
        self.assertIn('src="data:image/png;base64,', html,
                      "and be embedded, not fetched")
        # Bare on purpose (client, 2026-08-28): no caption, no instruction, and no
        # number under the bars - the invoice number is already printed twice.
        self.assertNotIn('Scan to deliver', html)
        self.assertIn('oi-scanstrip', html)
        # and the code it carries is the one the scanner resolves
        found = self.Delivery.scan(invoice.name)['context']['lab_scan_summary']
        self.assertEqual(found['order'], order.name)

    def test_the_invoice_pdf_is_never_stored_as_an_attachment(self):
        """A stored copy outlives the layout: the barcode reached new invoices
        only, and 9,999 already-printed ones kept reprinting without it.
        (client, 2026-08-28)"""
        for xmlid in ('sale_custom.action_report_dental_invoice',
                      'account.account_invoices',
                      'account.account_invoices_without_payment'):
            report = self.env.ref(xmlid, raise_if_not_found=False)
            if not report:
                continue
            self.assertFalse(report.attachment, xmlid)
            self.assertFalse(report.attachment_use, xmlid)


@tagged('post_install', '-at_install')
class TestPhotoDecode(TransactionCase):
    """The server as the stronger pair of eyes behind the phone.

    The page's own ZXing build gives up on photographs zxing-cpp still reads - a
    sheet a few degrees off square, dim, slightly out of focus. When the phone
    finds nothing it hands the downscaled JPEG to ``decode_photo``, which reads it
    and drops it. Nothing is stored, and a server without zxing-cpp just finds
    nothing rather than erroring. (client, 2026-08-28)
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Delivery = cls.env['lab.delivery']
        try:
            import zxingcpp  # noqa: F401
            cls.has_decoder = True
        except ImportError:
            cls.has_decoder = False

    def _photo_of(self, code, rotate_deg=0):
        """A JPEG photograph of one Code 128, as base64 - tilted if asked."""
        import base64
        import io
        from PIL import Image
        png = self.env['ir.actions.report'].barcode(
            'Code128', code, width=900, height=220, humanreadable=0, quiet=1)
        bars = Image.open(io.BytesIO(png)).convert('RGB')
        sheet = Image.new('RGB', (1400, 900), 'white')
        sheet.paste(bars.resize((900, 220)), (250, 320))
        if rotate_deg:
            sheet = sheet.rotate(rotate_deg, expand=True, fillcolor=(200, 196, 190))
        out = io.BytesIO()
        sheet.save(out, format='JPEG', quality=80)
        return base64.b64encode(out.getvalue())

    def test_a_straight_photo_is_read(self):
        if not self.has_decoder:
            self.skipTest('zxing-cpp is not installed on this server')
        self.assertEqual(self.Delivery.decode_photo(self._photo_of('OC999001')),
                         ['OC999001'])

    def test_a_tilted_photo_is_read(self):
        if not self.has_decoder:
            self.skipTest('zxing-cpp is not installed on this server')
        self.assertEqual(
            self.Delivery.decode_photo(self._photo_of('OC999002', rotate_deg=7)),
            ['OC999002'],
            'the whole point of the server fallback is the photo the page gave up on')

    def test_a_transparent_png_is_read_on_a_white_ground(self):
        """A screenshot or a barcode saved off a PDF arrives as a PNG with
        transparency, and transparency flattens to BLACK unless somebody puts
        white underneath - black bars on a black ground read as nothing at all.
        The field lost two scans to this before it was understood."""
        if not self.has_decoder:
            self.skipTest('zxing-cpp is not installed on this server')
        import base64
        import io
        from PIL import Image
        png = self.env['ir.actions.report'].barcode(
            'Code128', 'OC999010', width=900, height=220, humanreadable=0, quiet=1)
        bars = Image.open(io.BytesIO(png)).convert('RGBA')
        # bars stay opaque, the white ground becomes fully transparent
        pixels = bars.getdata()
        bars.putdata([(r, g, b, 0) if r > 200 else (r, g, b, 255)
                      for (r, g, b, a) in pixels])
        out = io.BytesIO()
        bars.save(out, format='PNG')
        self.assertEqual(
            self.Delivery.decode_photo(base64.b64encode(out.getvalue())),
            ['OC999010'])

    def test_a_ragged_print_is_read_through_the_ladder(self):
        """A cheap printer lays bars down ragged and a handheld photo blurs
        them; the raw frame reads as nothing while the upscaled Otsu frame reads
        cleanly. This fixture fails zxing-cpp raw and must pass the ladder."""
        if not self.has_decoder:
            self.skipTest('zxing-cpp is not installed on this server')
        import base64
        import io
        from PIL import Image, ImageEnhance, ImageFilter
        png = self.env['ir.actions.report'].barcode(
            'Code128', 'OC999020', width=900, height=220, humanreadable=0, quiet=1)
        bars = Image.open(io.BytesIO(png)).convert('RGB').resize((900, 220))
        sheet = Image.new('RGB', (1100, 700), 'white')
        sheet.paste(bars, (100, 240))
        pixelated = sheet.resize((sheet.width // 2, sheet.height // 2),
                                 Image.NEAREST)
        rough = pixelated.resize((1100, 700), Image.NEAREST)
        rough = rough.filter(ImageFilter.GaussianBlur(0.8))
        rough = ImageEnhance.Contrast(rough).enhance(0.75)
        out = io.BytesIO()
        rough.save(out, format='JPEG', quality=75)
        self.assertEqual(
            self.Delivery.decode_photo(base64.b64encode(out.getvalue())),
            ['OC999020'])

    def test_an_unreadable_sheet_is_kept_and_a_face_is_not(self):
        """'The scanner found nothing' is unanswerable without the picture it
        found nothing in - staging keeps the evidence. But only frames that
        plausibly held a sheet: ten face frames rotating every ten seconds
        buried the one that mattered."""
        import base64
        import io
        import os
        from odoo.tools import config
        from PIL import Image, ImageDraw
        folder = os.path.join(config['data_dir'], 'lab_scan_failures')

        def photo(image):
            out = io.BytesIO()
            image.save(out, format='JPEG', quality=80)
            return base64.b64encode(out.getvalue())

        # a bright sheet with stripes that are not a barcode: unreadable, sheet-like
        sheet = Image.new('RGB', (800, 400), (245, 243, 240))
        draw = ImageDraw.Draw(sheet)
        for x in range(60, 740, 24):
            draw.rectangle([x, 150, x + 9, 250], fill=(30, 30, 30))
        before = set(os.listdir(folder)) if os.path.isdir(folder) else set()
        self.assertEqual(self.Delivery.decode_photo(photo(sheet)), [])
        after = set(os.listdir(folder))
        self.assertTrue(after - before, 'the unreadable sheet frame is kept')

        # a dark, flat frame - a face in a room - is nothing worth keeping
        face = Image.new('RGB', (800, 400), (70, 60, 55))
        before = set(os.listdir(folder))
        self.assertEqual(self.Delivery.decode_photo(photo(face)), [])
        self.assertEqual(set(os.listdir(folder)), before,
                         'a frame with no sheet in it is not evidence')

    def test_garbage_is_nothing_not_an_error(self):
        self.assertEqual(self.Delivery.decode_photo(b'not-base64!!'), [])
        self.assertEqual(self.Delivery.decode_photo(False), [])
        import base64
        self.assertEqual(self.Delivery.decode_photo(base64.b64encode(b'plain text')), [])

    def test_an_executive_can_call_it(self):
        """The caller is a field executive, not an admin - the method must not
        require anything their everyday rights do not already give them."""
        if not self.has_decoder:
            self.skipTest('zxing-cpp is not installed on this server')
        group = self.env.ref('lab_fieldwork.group_fieldwork_executive',
                             raise_if_not_found=False)
        if not group:
            self.skipTest('fieldwork groups not installed')
        executive = self.env['res.users'].create({
            'name': 'Photo Executive', 'login': 'photo.exec@test',
            'group_ids': [(4, group.id)],
        })
        self.assertEqual(
            self.Delivery.with_user(executive).decode_photo(self._photo_of('OC999003')),
            ['OC999003'])


@tagged('post_install', '-at_install')
class TestScanAsExecutive(TransactionCase):
    """The scan through the eyes it was built for.

    Everything here runs as a plain field executive under the "own deliveries"
    record rule - because that is who actually presses Scan, and it is exactly
    where the feature kept dying while every admin test passed: a delivery raised
    for the order's salesperson could not be read back by its own scanner, and
    confirming a hand-over crashed on the manager-only location fields.
    (client, 2026-08-28)
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.product = cls.env['product.product'].create({
            'name': 'Exec Scan Appliance', 'type': 'consu', 'list_price': 100.0})
        # base.group_user too, as every real login has: an executive without it
        # cannot even read the sequence that numbers the delivery.
        groups = [(4, cls.env.ref('lab_fieldwork.group_fieldwork_executive').id),
                  (4, cls.env.ref('base.group_user').id)]
        cls.exec_a, cls.exec_b = cls.env['res.users'].create([
            {'name': 'Exec A', 'login': 'exec.a@scan', 'group_ids': groups},
            {'name': 'Exec B', 'login': 'exec.b@scan', 'group_ids': groups},
        ])
        cls.salesperson = cls.env['res.users'].create({
            'name': 'Desk Salesperson', 'login': 'desk@scan'})
        # fw_route_ids is a COMPUTE (crm.team.user_id / member_ids), not a plain
        # field - membership is what actually puts someone "on" a route (learned
        # the hard way: writing fw_route_ids directly is a silent no-op).
        cls.route = cls.env['crm.team'].create({
            'name': 'Exec Scan Route',
            'member_ids': [(4, cls.exec_a.id), (4, cls.exec_b.id)]})
        cls.clinic = cls.env['res.partner'].create({
            'name': 'Exec Scan Clinic', 'team_id': cls.route.id})

    def _order(self, clinic=None):
        return self.env['sale.order'].create({
            'partner_id': (clinic or self.clinic).id, 'patient': 'Exec Patient',
            'user_id': self.salesperson.id,
            'order_line': [(0, 0, {'product_id': self.product.id,
                                   'product_uom_qty': 1})]})

    def _scan(self, user, token, **kw):
        return self.env['lab.delivery'].with_user(user).scan(token, **kw)

    def test_the_scanner_becomes_the_executive(self):
        order = self._order()
        action = self._scan(self.exec_a, order.name)
        delivery = self.env['lab.delivery'].browse(
            action['context']['lab_scan_summary']['id'])
        self.assertEqual(delivery.executive_id, self.exec_a,
                         'the box is in the scanner\'s hand, not the salesperson\'s')
        # and the scanner can read what they just raised, under their own rule
        self.assertTrue(delivery.with_user(self.exec_a).name)

    def test_a_colleagues_undelivered_box_changes_hands_on_scan(self):
        order = self._order()
        theirs = self.env['lab.delivery'].create({
            'sale_order_id': order.id, 'executive_id': self.exec_b.id,
            'state': 'assigned'})
        action = self._scan(self.exec_a, order.name)
        self.assertEqual(action['context']['lab_scan_summary']['id'], theirs.id,
                         'the existing delivery is reused, never doubled')
        self.assertEqual(theirs.executive_id, self.exec_a,
                         'whoever is at the door with the box delivers it')
        message = theirs.message_ids.filtered(
            lambda m: 'Taken over' in (m.body or ''))
        self.assertTrue(message, 'the handover of the handover is on the record')
        self.assertIn(self.exec_b.partner_id, message.partner_ids,
                      'the colleague who lost the box hears about it in their '
                      'inbox, not only in a chatter they never open')
        found = action['context']['lab_scan_summary']
        self.assertEqual(found['claimed_from'], self.exec_b.display_name,
                         'the phone says whose round the box left, in amber - '
                         'a silent reassignment is information lost')

    def test_an_untouched_scan_reports_no_takeover(self):
        order = self._order()
        mine = self.env['lab.delivery'].create({
            'sale_order_id': order.id, 'executive_id': self.exec_a.id,
            'state': 'assigned'})
        action = self._scan(self.exec_a, order.name)
        self.assertEqual(action['context']['lab_scan_summary']['id'], mine.id)
        self.assertEqual(action['context']['lab_scan_summary']['claimed_from'], '',
                         'scanning your own box must not cry takeover')

    def test_a_colleagues_delivered_box_only_reports_itself(self):
        order = self._order()
        theirs = self.env['lab.delivery'].create({
            'sale_order_id': order.id, 'executive_id': self.exec_b.id,
            'state': 'assigned'})
        theirs.write({'state': 'delivered'})
        action = self._scan(self.exec_a, order.name)
        self.assertEqual(action['tag'], 'display_notification',
                         'somebody else\'s history is not the scanner\'s to open')
        self.assertIn('already delivered', action['params']['message'])
        self.assertEqual(theirs.executive_id, self.exec_b,
                         'a finished box never changes hands')

    def test_an_executive_can_confirm_with_a_fix(self):
        """The location fields are manager-only, and the delivered stamp both
        reads and writes them - which crashed every real confirmation until the
        stamp ran as superuser."""
        order = self._order()
        # first scan stages it; "Deliver now" (or the second scan at the door)
        # is what opens the wizard - as the station drives it.
        staged = self._scan(self.exec_a, order.name)
        delivery = self.env['lab.delivery'].browse(
            staged['context']['lab_scan_summary']['id'])
        action = delivery.with_user(self.exec_a).action_mark_delivered(
            9.9312, 76.2673, 8.0)
        context = {key: value for key, value in action['context'].items()
                   if key.startswith(('default_', 'lab_'))}
        wizard = self.env['lab.delivery.done.wizard'].with_user(
            self.exec_a).with_context(**context).create({})
        wizard.action_confirm()
        self.assertEqual(delivery.state, 'delivered')
        self.assertEqual(delivery.sudo().delivered_by_id, self.exec_a)
        self.assertAlmostEqual(delivery.sudo().delivered_lat, 9.9312, places=4)
        self.assertTrue(delivery.sudo().delivered_datetime)


@tagged('post_install', '-at_install')
class TestScanStaysOnRoute(TransactionCase):
    """An executive scans only what is theirs to carry.

    Every case above runs inside one route, on purpose - it never exercised the
    route boundary at all. _scan_resolve is fully sudo'd (a plain executive
    cannot read account.move/sale.order to begin with), so without an explicit
    check here nothing stops a code from another round being raised, or a
    colleague on a different route having their box taken over, by whoever
    happens to scan it. (client, 2026-08-29)
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.product = cls.env['product.product'].create({
            'name': 'Route Scan Appliance', 'type': 'consu', 'list_price': 100.0})
        groups = [(4, cls.env.ref('lab_fieldwork.group_fieldwork_executive').id),
                  (4, cls.env.ref('base.group_user').id)]
        cls.exec_home, cls.exec_away = cls.env['res.users'].create([
            {'name': 'Route Exec Home', 'login': 'exec.home@scan', 'group_ids': groups},
            {'name': 'Route Exec Away', 'login': 'exec.away@scan', 'group_ids': groups},
        ])
        cls.manager = cls.env['res.users'].create({
            'name': 'Route Scan Manager', 'login': 'manager@scan',
            'group_ids': [(4, cls.env.ref(
                'lab_fieldwork.group_fieldwork_manager').id),
                          (4, cls.env.ref('base.group_user').id)]})
        cls.salesperson = cls.env['res.users'].create({
            'name': 'Route Desk Salesperson', 'login': 'desk@route-scan'})
        cls.home_route, cls.away_route = cls.env['crm.team'].create([
            {'name': 'Home Route', 'member_ids': [(4, cls.exec_home.id)]},
            {'name': 'Away Route', 'member_ids': [(4, cls.exec_away.id)]},
        ])
        cls.home_clinic = cls.env['res.partner'].create({
            'name': 'Home Route Clinic', 'team_id': cls.home_route.id})
        cls.away_clinic = cls.env['res.partner'].create({
            'name': 'Away Route Clinic', 'team_id': cls.away_route.id})

    def _order(self, clinic):
        return self.env['sale.order'].create({
            'partner_id': clinic.id, 'patient': 'Route Patient',
            'user_id': self.salesperson.id,
            'order_line': [(0, 0, {'product_id': self.product.id,
                                   'product_uom_qty': 1})]})

    def _scan(self, user, token, **kw):
        return self.env['lab.delivery'].with_user(user).scan(token, **kw)

    def test_an_executive_scans_a_new_code_on_their_own_route_fine(self):
        order = self._order(self.home_clinic)
        action = self._scan(self.exec_home, order.name)
        self.assertEqual(action['context']['lab_scan_summary']['clinic'],
                         self.home_clinic.display_name)

    def test_an_executive_is_refused_a_code_off_their_route(self):
        order = self._order(self.away_clinic)
        with self.assertRaises(UserError) as ctx:
            self._scan(self.exec_home, order.name)
        self.assertIn('not on your Sales Route', str(ctx.exception))
        # and nothing was raised on the strength of the refused scan
        self.assertFalse(self.env['lab.delivery'].sudo().search(
            [('sale_order_id', '=', order.id)]))

    def test_an_executive_cannot_take_over_a_colleagues_box_off_their_route(self):
        """The existing box belongs to exec_away, on exec_away's own route - the
        route check runs before the handover branch, so exec_home never even
        reaches the "whose box is this" logic."""
        order = self._order(self.away_clinic)
        theirs = self.env['lab.delivery'].create({
            'sale_order_id': order.id, 'executive_id': self.exec_away.id,
            'state': 'assigned'})
        with self.assertRaises(UserError):
            self._scan(self.exec_home, order.name)
        self.assertEqual(theirs.executive_id, self.exec_away,
                         'a refused scan must not move the box')

    def test_a_manager_scans_across_any_route(self):
        """Managers: every clinic - the route check must be a no-op for them."""
        order = self._order(self.away_clinic)
        action = self._scan(self.manager, order.name)
        self.assertEqual(action['context']['lab_scan_summary']['clinic'],
                         self.away_clinic.display_name)
