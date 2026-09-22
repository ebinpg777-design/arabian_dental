# -*- coding: utf-8 -*-
"""Deliveries on My Day, and the link back to the visit.

The browser test here is not decoration. OWL template inheritance is resolved in the
*browser*, not on the server, so a mistyped xpath passes every server-side check and
then white-screens the entire web client on load. Nothing short of loading the page
catches that.
"""
from datetime import date, datetime, timedelta

from odoo.tests.common import HttpCase, TransactionCase, tagged
from odoo import fields
from odoo.exceptions import AccessError, UserError

READY = "document.querySelector('.o_fw_day') !== null"


@tagged('post_install', '-at_install')
class TestMyDayDelivery(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.executive = cls.env['res.users'].create({
            'name': 'Delivery Exec', 'login': 'deliv_exec',
            'group_ids': [(6, 0, [
                cls.env.ref('base.group_user').id,
                cls.env.ref('lab_fieldwork.group_fieldwork_executive').id,
                cls.env.ref('sales_team.group_sale_salesman').id])]})
        # The clinic sits on a route the executive works: since 2026-08-24 an
        # executive only reads their own route's partners, and a routeless test
        # clinic is - correctly - invisible to them.
        cls.route = cls.env['crm.team'].create({
            'name': 'Test Route MD', 'user_id': cls.executive.id})
        cls.clinic = cls.env['res.partner'].create({
            'name': 'My Day Clinic', 'is_clinic': True, 'phone': '9961577735',
            'street': 'MG Road', 'city': 'Kochi', 'team_id': cls.route.id})
        cls.product = cls.env['product.product'].create({
            'name': 'My Day Appliance', 'type': 'consu', 'list_price': 900.0,
            'invoice_policy': 'order'})

    def _order(self, patient='Ravi Menon'):
        order = self.env['sale.order'].create({
            'partner_id': self.clinic.id, 'patient': patient,
            'order_line': [(0, 0, {'product_id': self.product.id,
                                   'product_uom_qty': 1})]})
        order.action_confirm()
        return order

    def _visit(self, state='open'):
        visit = self.env['lab.visit'].create({
            'partner_id': self.clinic.id, 'user_id': self.executive.id})
        if state != 'planned':
            visit.state = state
        return visit

    # ---------------------------------------------------------------- the link
    def test_a_delivery_records_the_visit_it_was_handed_over_on(self):
        order = self._order()
        visit = self._visit()
        wizard = self.env['lab.delivery.new.wizard'].create({
            'visit_id': visit.id, 'partner_id': self.clinic.id,
            'executive_id': self.executive.id,
            'order_ids': [(6, 0, order.ids)]})
        wizard.action_create()
        delivery = self.env['lab.delivery'].search([('sale_order_id', '=', order.id)])
        self.assertEqual(delivery.visit_id, visit)
        self.assertEqual(visit.delivery_ids, delivery)
        self.assertEqual(visit.delivery_count, 1)

    def test_a_handover_is_assigned_to_whoever_raised_it(self):
        """Raised by the person about to carry it — making them press Assign next
        would be theatre."""
        order = self._order()
        wizard = self.env['lab.delivery.new.wizard'].create({
            'partner_id': self.clinic.id, 'executive_id': self.executive.id,
            'order_ids': [(6, 0, order.ids)]})
        wizard.action_create()
        delivery = self.env['lab.delivery'].search([('sale_order_id', '=', order.id)])
        self.assertEqual(delivery.state, 'assigned')
        self.assertEqual(delivery.executive_id, self.executive)

    # ------------------------------------------------------- the door count
    def _delivered(self, direction='out', by=None, when=None):
        """A box handed over: the plain state write is the stamping path."""
        order = self._order(patient='Box %s' % direction)
        # Raised as the carrier's own box: the rules let nobody else touch it.
        wizard = self.env['lab.delivery.new.wizard'].create({
            'partner_id': self.clinic.id, 'executive_id': (by or self.executive).id,
            'order_ids': [(6, 0, order.ids)]})
        wizard.action_create()
        delivery = self.env['lab.delivery'].search([('sale_order_id', '=', order.id)])
        if direction != 'out':
            delivery.sudo().write({'direction': direction})
        delivery.with_user(by or self.executive).action_start()
        delivery.with_user(by or self.executive).write({'state': 'delivered'})
        if when:
            delivery.sudo().write({'delivered_datetime': when})
        return delivery

    def test_my_day_counts_the_boxes_i_handed_over_today(self):
        """The strip's third door count. (client, 2026-09-08)"""
        me = self.env['lab.my.day'].with_user(self.executive)
        self.assertEqual(me.get_day()['summary']['dispatched'], 0)
        box = self._delivered()
        self.assertEqual(box.delivered_by_id, self.executive, "the stamp scopes the count")
        self.assertEqual(me.get_day()['summary']['dispatched'], 1)

    def test_pickups_yesterday_and_other_people_do_not_count(self):
        me = self.env['lab.my.day'].with_user(self.executive)
        self._delivered()
        self._delivered(direction='in')
        self._delivered(when=fields.Datetime.now() - timedelta(days=1))
        other = self.env['res.users'].create({
            'name': 'Other Exec', 'login': 'other_deliv_exec',
            'group_ids': [(6, 0, self.executive.group_ids.ids)]})
        self._delivered(by=other)
        self.assertEqual(me.get_day()['summary']['dispatched'], 1,
                         "one outbound box, today, by me")

    def test_a_box_handed_over_after_midnight_counts_on_the_lab_s_day(self):
        """`user.tz or 'UTC'` started an executive's day at 05:30 - and most of them
        have no timezone - so a box handed over at 01:30 counted as yesterday's.
        (2026-09-15)"""
        self.executive.tz = False
        self.env.company.partner_id.tz = False
        # 01:30 on 13 September in Kochi.
        self._delivered(when=datetime(2026, 9, 12, 20, 0))
        me = self.env['lab.my.day'].with_user(self.executive)
        self.assertEqual(me._dispatched_on(date(2026, 9, 13)), 1)
        self.assertEqual(me._dispatched_on(date(2026, 9, 12)), 0)

    def test_work_already_being_carried_is_not_offered_again(self):
        order = self._order()
        self.env['lab.delivery'].create({'sale_order_id': order.id})
        wizard = self.env['lab.delivery.new.wizard'].create({
            'partner_id': self.clinic.id})
        self.assertNotIn(order, wizard.candidate_order_ids)

    def test_two_phones_cannot_raise_the_same_delivery_twice(self):
        """The list is drawn once but pressed later, possibly by someone else."""
        order = self._order()
        wizard = self.env['lab.delivery.new.wizard'].create({
            'partner_id': self.clinic.id, 'order_ids': [(6, 0, order.ids)]})
        self.env['lab.delivery'].create({'sale_order_id': order.id})
        with self.assertRaises(UserError):
            wizard.action_create()

    def test_handing_over_nothing_is_refused_with_an_explanation(self):
        wizard = self.env['lab.delivery.new.wizard'].create({
            'partner_id': self.clinic.id})
        with self.assertRaises(UserError):
            wizard.action_create()

    def test_marking_delivered_stamps_the_visit_that_was_happening(self):
        """The link is knowable at handover time and only then."""
        order = self._order()
        visit = self._visit()
        delivery = self.env['lab.delivery'].create({
            'sale_order_id': order.id, 'executive_id': self.executive.id})
        self.assertFalse(delivery.visit_id)
        self.env['lab.delivery.done.wizard'].create({
            'delivery_id': delivery.id, 'delivery_outcome': 'clinic',
            'received_by': 'Reception'}).action_confirm()
        self.assertEqual(delivery.visit_id, visit)

    def test_a_delivery_is_never_attached_to_someone_else_s_trip(self):
        order = self._order()
        other = self.env['res.users'].create({
            'name': 'Other Exec', 'login': 'deliv_other',
            'group_ids': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref('lab_fieldwork.group_fieldwork_executive').id])]})
        self.env['lab.visit'].create({
            'partner_id': self.clinic.id, 'user_id': other.id, 'state': 'open'})
        delivery = self.env['lab.delivery'].create({
            'sale_order_id': order.id, 'executive_id': self.executive.id})
        delivery._attach_visit()
        self.assertFalse(delivery.visit_id)

    def test_an_executive_can_hand_over_the_office_s_own_orders(self):
        """The only scenario this feature has in real life.

        The office raises and confirms the order; the executive standing in the clinic
        is the one handing it over. Run as admin this passes trivially — run as the
        executive it did not work at all, because Sales/User is "Own Documents Only":
        the candidate list came back empty and ticking one raised AccessError.
        """
        order = self._order()
        order.user_id = self.env.ref('base.user_admin')      # the office's order
        self.env.flush_all()
        self.env.invalidate_all()

        wizard = self.env['lab.delivery.new.wizard'].with_user(self.executive).create(
            {'partner_id': self.clinic.id, 'executive_id': self.executive.id})
        self.assertIn(order, wizard.candidate_order_ids,
                      "the executive cannot see the work they are meant to carry")
        wizard.order_ids = [(6, 0, order.ids)]
        wizard.action_create()
        delivery = self.env['lab.delivery'].sudo().search(
            [('sale_order_id', '=', order.id)])
        self.assertEqual(len(delivery), 1)
        self.assertEqual(delivery.executive_id, self.executive)

    def test_an_executive_still_cannot_change_the_office_s_orders(self):
        """Reading confirmed work is not permission to edit it.

        This executive also holds Sales / "Own Documents Only", which is what grants any
        write access to sale.order at all — a real field executive on this database holds
        no such role and cannot write to an order under any circumstances. The narrowing
        to their OWN orders is core's `sale.sale_order_personal_rule`, and that rule is
        one of the 270 ir.rule records switched off on this database on 2026-08-14. The
        test asserts our contract, so it makes sure that rule is on for its own
        transaction rather than reporting the environment's damage as a code failure.
        (client, 2026-08-27)
        """
        personal = self.env.ref('sale.sale_order_personal_rule',
                                raise_if_not_found=False)
        if personal and not personal.active:
            personal.sudo().active = True
            self.env.registry.clear_cache()
            self.addCleanup(self.env.registry.clear_cache)

        order = self._order()
        order.user_id = self.env.ref('base.user_admin')
        self.env.flush_all()
        self.env.invalidate_all()
        with self.assertRaises(AccessError):
            order.with_user(self.executive).write({'patient': 'Tampered'})

    def test_an_executive_cannot_read_a_draft_quotation(self):
        """The read rule stops at confirmed work — pricing under negotiation is not
        the delivery round's business."""
        draft = self.env['sale.order'].create({
            'partner_id': self.clinic.id, 'patient': 'Draft Patient',
            'order_line': [(0, 0, {'product_id': self.product.id,
                                   'product_uom_qty': 1})]})
        draft.user_id = self.env.ref('base.user_admin')
        self.env.flush_all()
        self.env.invalidate_all()
        self.assertEqual(draft.state, 'draft')
        with self.assertRaises(AccessError):
            draft.with_user(self.executive).read(['patient'])

    # ---------------------------------------------------------------- payload
    def test_my_day_carries_what_is_in_the_bag(self):
        order = self._order()
        delivery = self.env['lab.delivery'].create({
            'sale_order_id': order.id, 'executive_id': self.executive.id,
            'state': 'assigned'})
        data = self.env['lab.my.day'].with_user(self.executive).get_day()
        self.assertEqual([d['id'] for d in data['deliveries']], [delivery.id])
        self.assertEqual(data['delivery_summary']['open'], 1)
        row = data['deliveries'][0]
        self.assertEqual(row['clinic'], self.clinic.display_name)
        self.assertTrue(row['map_url'], "no directions offered for the clinic")
        self.assertTrue(row['map_url'].startswith('geo:'),
                        "the field app is a WebView wrapper, not a browser - a "
                        "google.com/maps link fails there (client, 2026-08-29)")
        self.assertTrue(row['map_address'],
                        "text fallback for a wrapper that can't open geo: either")
        self.assertEqual(row['call'], '9961577735')

    def test_my_day_carries_the_invoice_number_once_the_order_is_billed(self):
        """The courier and the doctor's receptionist both ask for it by number -
        it has to be on the card the executive is already looking at, not one
        more screen away."""
        order = self._order()
        order._create_invoices()
        invoice = order.invoice_ids
        invoice.action_post()
        delivery = self.env['lab.delivery'].create({
            'sale_order_id': order.id, 'executive_id': self.executive.id,
            'state': 'assigned'})
        data = self.env['lab.my.day'].with_user(self.executive).get_day()
        row = next(d for d in data['deliveries'] if d['id'] == delivery.id)
        self.assertEqual(row['invoice'], invoice.name)

    def test_a_visit_card_says_what_is_waiting_for_that_clinic(self):
        order = self._order()
        visit = self._visit(state='planned')
        self.env['lab.delivery'].create({
            'sale_order_id': order.id, 'executive_id': self.executive.id,
            'state': 'assigned'})
        data = self.env['lab.my.day'].with_user(self.executive).get_day()
        card = next(v for v in data['visits'] if v['id'] == visit.id)
        self.assertEqual(card['deliveries_pending'], 1)

    def test_my_day_survives_a_delivery_for_someone_else_s_order(self):
        """The normal case, not an edge case.

        The office raises and confirms the order; the executive only carries the box.
        With Sales/User "Own Documents Only" the executive cannot read that order, so
        anything in the payload that reaches through to it takes their whole home
        screen down.
        """
        order = self._order()
        order.user_id = self.env.ref('base.user_admin')   # somebody else's order
        self.env['lab.delivery'].create({
            'sale_order_id': order.id, 'executive_id': self.executive.id,
            'state': 'assigned'})
        # Cold, like every real request. With the order's name still warm in the ORM
        # cache the read never reaches the ACL and this passes while production
        # crashes — which is exactly what happened the first time this was written.
        self.env.flush_all()
        self.env.invalidate_all()
        data = self.env['lab.my.day'].with_user(self.executive).get_day()
        self.assertEqual(len(data['deliveries']), 1)
        self.assertEqual(data['deliveries'][0]['order'], order.name)

    def test_my_day_still_works_for_an_executive_with_an_empty_bag(self):
        self._visit(state='planned')
        data = self.env['lab.my.day'].with_user(self.executive).get_day()
        self.assertEqual(data['deliveries'], [])
        self.assertEqual(data['delivery_summary']['open'], 0)


@tagged('post_install', '-at_install')
class TestMyDayDeliveryBrowser(HttpCase):
    """Load the real screen in a real browser.

    A broken t-inherit xpath is invisible to every server-side check and fatal in the
    client — this is the only test that can see it.
    """

    def test_my_day_renders_with_the_delivery_section(self):
        clinic = self.env['res.partner'].create({
            'name': 'Browser Clinic', 'is_clinic': True, 'phone': '9961577735'})
        product = self.env['product.product'].create({
            'name': 'Browser Appliance', 'type': 'consu', 'list_price': 500.0})
        order = self.env['sale.order'].create({
            'partner_id': clinic.id, 'patient': 'Browser Patient',
            'order_line': [(0, 0, {'product_id': product.id, 'product_uom_qty': 1})]})
        order.action_confirm()
        admin = self.env.ref('base.user_admin')
        self.env['lab.visit'].create({
            'partner_id': clinic.id, 'user_id': admin.id})
        # 'out', so the card's single button reads Delivered — the other route into
        # the action service, and the one that opens the handover wizard.
        self.env['lab.delivery'].create({
            'sale_order_id': order.id, 'executive_id': admin.id,
            'state': 'out'})

        self.browser_js(
            "/odoo/action-lab_fieldwork.action_my_day",
            """
            (async () => {
                const until = async (fn, what) => {
                    for (let i = 0; i < 100; i++) {
                        if (fn()) { return true; }
                        await new Promise(r => setTimeout(r, 100));
                    }
                    throw new Error("never appeared: " + what);
                };
                const text = () => document.querySelector('.o_fw_day').innerText;
                await until(() => document.querySelector('.o_fw_day'), 'My Day');
                await until(() => text().includes('In your bag'),
                            'the delivery section');
                await until(() => document.querySelector('.o_fw_card_deliv'),
                            'a delivery card');

                // Press it, do not merely find it. An action dict that reaches the JS
                // action service without `views` throws inside _preprocessAction, and
                // asserting the button exists never touches that path.
                // On the visit card since 7c3f2f3: the Do-grid tile became
                // Deliver (a worklist), and the hand-over wizard is reached
                // from the clinic being visited.
                const handOver = [...document.querySelectorAll('.o_fw_quick_btn')]
                    .find(b => b.innerText.includes('Hand over'));
                if (!handOver) { throw new Error('no Hand over button on the visit card'); }
                handOver.click();
                await until(() => document.querySelector('.modal .o_form_view'),
                            'the hand-over wizard');
                // textContent, not innerText: the modal is still fading in and
                // innerText is empty for anything not yet rendered.
                await until(() => document.querySelector('.modal').textContent
                                    .includes('What are you handing over?'),
                            'the wizard question');
                // Leave the screen as it was found.
                document.querySelector('.modal .btn-secondary[special="cancel"], ' +
                                       '.modal .o_form_button_cancel, ' +
                                       '.modal footer button:last-child').click();
                await until(() => !document.querySelector('.modal .o_form_view'),
                            'the wizard to close');

                // The card's own button takes the same route into the action service.
                const deliver = document.querySelector(
                    '.o_fw_card_deliv .o_fw_action');
                if (!deliver.textContent.includes('Delivered')) {
                    throw new Error('an out-for-delivery card should offer Delivered, '
                                    + 'got: ' + deliver.textContent);
                }
                deliver.click();
                await until(() => document.querySelector('.modal .o_form_view'),
                            'the Delivered wizard');
                document.querySelector('.modal .btn-secondary[special="cancel"], ' +
                                       '.modal .o_form_button_cancel, ' +
                                       '.modal footer button:last-child').click();
                await until(() => !document.querySelector('.modal .o_form_view'),
                            'the Delivered wizard to close');
                console.log('test successful');
            })();
            """,
            login="admin", ready=READY, timeout=120)

    def test_the_mobile_save_bar_appears_once_a_form_is_dirty(self):
        """The bar must track the stock indicator exactly — it is the same control."""
        clinic = self.env['res.partner'].create({
            'name': 'Savebar Clinic', 'is_clinic': True})
        # 'open', not the default 'planned': the notebook holding the free-text field
        # is hidden until the executive has checked in.
        visit = self.env['lab.visit'].create({
            'partner_id': clinic.id, 'user_id': self.env.ref('base.user_admin').id,
            'state': 'open'})

        self.browser_js(
            "/odoo/action-lab_fieldwork.action_visit_my/%s" % visit.id,
            """
            (async () => {
                const until = async (fn, what) => {
                    for (let i = 0; i < 100; i++) {
                        if (fn()) { return true; }
                        await new Promise(r => setTimeout(r, 100));
                    }
                    throw new Error("never appeared: " + what);
                };
                await until(() => document.querySelector('.o_form_view'), 'the form');
                const bar = () => document.querySelector('.o_fw_savebar');
                await until(bar, 'the save bar in the DOM');
                if (bar().classList.contains('o_fw_savebar_shown')) {
                    throw new Error('the bar is up on a clean form');
                }
                const input = document.querySelector(
                    ".o_field_widget[name='note'] textarea, " +
                    ".o_field_widget[name='note'] input");
                if (!input) { throw new Error('no note field to dirty'); }
                input.focus();
                input.value = 'dirtied by the test';
                input.dispatchEvent(new Event('input', { bubbles: true }));
                input.dispatchEvent(new Event('change', { bubbles: true }));
                await until(() => bar().classList.contains('o_fw_savebar_shown'),
                            'the save bar to come up when dirty');
                const save = bar().querySelector('.o_fw_savebar_save');
                if (!save.innerText.trim()) {
                    throw new Error('the save button carries no words');
                }
                // Discard through the bar's own button: it puts the form back to clean
                // (which the test harness insists on) and proves the second button
                // works at the same time.
                bar().querySelector('.o_fw_savebar_discard').click();
                await until(() => !bar().classList.contains('o_fw_savebar_shown'),
                            'the save bar to go away after discarding');
                console.log('test successful');
            })();
            """,
            login="admin",
            ready="document.querySelector('.o_form_view') !== null",
            timeout=120)
