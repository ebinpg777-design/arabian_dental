# -*- coding: utf-8 -*-
from unittest.mock import patch
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestStickerPrint(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.team_a = cls.env['crm.team'].create({'name': 'KLM', 'address': 'Kollam depot\nNear bus stand', 'phone': '0474 1234'})
        cls.team_b = cls.env['crm.team'].create({'name': 'EKM'})
        cls.p1 = cls.env['res.partner'].create({'name': 'DR ONE', 'street': '1 Main Rd', 'city': 'Kochi', 'phone': '111'})
        cls.p2 = cls.env['res.partner'].create({'name': 'DR TWO', 'street': '2 Side Rd', 'city': 'Kollam', 'phone': '222'})
        cls.product = cls.env['product.product'].create({'name': 'Twin Block', 'type': 'consu', 'list_price': 500})
        cls.o1 = cls._order(cls.p1, cls.team_a); cls.o2 = cls._order(cls.p1, cls.team_a); cls.o3 = cls._order(cls.p2, cls.team_b)
        cls.Engine = cls.env['epg.sticker.engine']

    @classmethod
    def _order(cls, partner, team):
        return cls.env['sale.order'].create({
            'partner_id': partner.id, 'team_id': team.id,
            'order_line': [(0, 0, {'product_id': cls.product.id, 'product_uom_qty': 1}),
                           (0, 0, {'product_id': cls.product.id, 'product_uom_qty': 2})]})

    def test_customer_mode_groups_orders(self):
        st = self.Engine.build(self.o1 | self.o2 | self.o3, {'mode': 'customer'})
        self.assertEqual([s['name'] for s in st], ['DR ONE', 'DR TWO'])
        one = st[0]
        self.assertEqual(one['count'], 2)
        self.assertIn(self.o1.name, one['refs']); self.assertIn(self.o2.name, one['refs'])
        self.assertTrue(one['barcode'].startswith('data:image/png;base64,'), "barcode is inline, never a URL")
        self.assertEqual(one['route'], 'KLM'); self.assertEqual(one['phone'], '111')

    def test_order_mode_lines_and_toggles(self):
        st = self.Engine.build(self.o1, {'mode': 'order', 'barcode_type': 'qr', 'show_phone': False})
        self.assertEqual(len(st), 1)
        s = st[0]
        self.assertEqual(s['title'], self.o1.name)
        self.assertEqual(len(s['lines']), 2); self.assertEqual(s['lines'][1]['qty'], 2)
        self.assertEqual(s['phone'], '', "phone hidden by toggle")
        self.assertTrue(s['barcode'])

    def test_route_mode_and_no_route(self):
        o4 = self._order(self.p2, self.env['crm.team']); o4.team_id = False
        st = self.Engine.build(self.o1 | self.o3 | o4, {'mode': 'route', 'barcode_type': 'none'})
        names = [s['name'] for s in st]
        self.assertEqual(names[:2], ['EKM', 'KLM']); self.assertIn('(no route)', names)
        klm = next(s for s in st if s['name'] == 'KLM')
        self.assertEqual(klm['address'], ['Kollam depot', 'Near bus stand']); self.assertFalse(klm['barcode'])

    def test_copies_and_pages(self):
        st = self.Engine.build(self.o1 | self.o3, {'mode': 'order', 'copies': 3})
        self.assertEqual(len(st), 6)
        sheet = self.env.ref('epg_sticker_print.format_a4_2x5')
        layout = self.Engine.layout_vars(sheet)
        self.assertEqual(layout['per_page'], 10)
        self.assertEqual(sheet.paperformat_id.page_width, 210, "sheet paperformat follows the grid")

    def test_format_paperformat_sync(self):
        fmt = self.env['epg.sticker.format'].create({'name': 'Test 80x40', 'width_mm': 80, 'height_mm': 40})
        self.assertEqual((fmt.paperformat_id.page_width, fmt.paperformat_id.page_height, fmt.paperformat_id.orientation), (80, 40, 'Portrait'))
        fmt.write({'columns': 2, 'gap_mm': 2})
        self.assertEqual(fmt.paperformat_id.page_width, 162)

    def test_wizard_defaults_preview_and_print(self):
        wiz = self.env['epg.sticker.print.wizard'].with_context(active_model='sale.order', active_ids=(self.o1 | self.o3).ids).create({})
        self.assertEqual(wiz.order_ids, self.o1 | self.o3)
        self.assertEqual(wiz.sticker_count, 2)
        self.assertIn('DR ONE', str(wiz.preview_html))
        wiz.write({'mode': 'order', 'copies': 2}); self.assertEqual(wiz.sticker_count, 4)
        action = wiz.action_print()
        self.assertEqual(action['report_name'], 'epg_sticker_print.report_sticker')
        self.assertEqual(action['data']['ids'], (self.o1 | self.o3).ids)
        self.assertEqual(action['context']['epg_sticker_paperformat_id'], wiz.format_id.paperformat_id.id)
        # download path without ids in the URL (what the browser does with data actions)
        report = self.env['ir.actions.report']
        html = report.with_context(action['context'])._render_qweb_html('epg_sticker_print.report_sticker', None, data=dict(action['data'], context=action['context']))[0]
        self.assertIn(b'DR ONE', html); self.assertIn(b'DR TWO', html)
        self.assertEqual(html.count(b'epg-sticker"'), 4, "2 orders x 2 copies")

    def test_wizard_from_picking(self):
        self.o1.action_confirm()
        picking = self.o1.picking_ids[:1]
        if picking:
            action = picking.action_print_sticker()
            self.assertEqual(action['context']['default_order_ids'], [(6, 0, self.o1.ids)])
            wiz = self.env['epg.sticker.print.wizard'].with_context(active_model='stock.picking', active_ids=picking.ids).create({})
            self.assertEqual(wiz.order_ids, self.o1)

    def test_the_65x35_roll_prints_on_its_own_page_size(self):
        fmt = self.env.ref('epg_sticker_print.format_roll_65x35')
        paper = fmt.paperformat_id
        self.assertEqual((paper.page_width, paper.page_height), (65, 35))
        self.assertFalse(fmt.is_sheet, "a roll: one sticker per page")

    def test_paperformat_from_context(self):
        report = self.env.ref('epg_sticker_print.action_report_sticker')
        fmt = self.env.ref('epg_sticker_print.format_roll_50x30')
        self.assertEqual(report.with_context(epg_sticker_paperformat_id=fmt.paperformat_id.id).get_paperformat(), fmt.paperformat_id)


@tagged('post_install', '-at_install')
class TestStickerWizardPartners(TransactionCase):
    """The counter works doctor-first.

    It knows whose parcels are going out today long before it knows which order
    numbers they are, so the wizard takes the clinics first and narrows the order
    picker to them - rather than making somebody hunt order numbers out of a list of
    24,000. (client, 2026-08-28)
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.product = cls.env['product.product'].create({
            'name': 'Wizard Appliance', 'type': 'consu', 'list_price': 100.0})
        cls.dr_one = cls.env['res.partner'].create({'name': 'WIZ DR ONE'})
        cls.dr_two = cls.env['res.partner'].create({'name': 'WIZ DR TWO'})
        # A clinic that books under a contact of its own: the counter thinks of these
        # as one clinic, so the picker has to as well.
        cls.branch = cls.env['res.partner'].create({
            'name': 'WIZ DR ONE (branch)', 'parent_id': cls.dr_one.id, 'type': 'delivery'})
        cls.o_one = cls._order(cls.dr_one)
        cls.o_branch = cls._order(cls.branch)
        cls.o_two = cls._order(cls.dr_two)

    @classmethod
    def _order(cls, partner):
        return cls.env['sale.order'].create({
            'partner_id': partner.id,
            'order_line': [(0, 0, {'product_id': cls.product.id, 'product_uom_qty': 1})]})

    def _wizard(self, **ctx):
        return self.env['epg.sticker.print.wizard'].with_context(**ctx).create({})

    def test_partners_default_from_the_orders_it_was_opened_on(self):
        wiz = self._wizard(active_model='sale.order',
                           active_ids=(self.o_one | self.o_two).ids)
        self.assertEqual(wiz.partner_ids, self.dr_one | self.dr_two)
        self.assertEqual(wiz.order_ids, self.o_one | self.o_two,
                         "opening on orders must not lose them")

    def test_opened_on_partners_it_starts_with_no_orders(self):
        wiz = self._wizard(active_model='res.partner', active_ids=self.dr_one.ids)
        self.assertEqual(wiz.partner_ids, self.dr_one)
        self.assertFalse(wiz.order_ids,
                         "a doctor can have hundreds of orders - the counter picks")

    def test_the_order_picker_narrows_to_the_chosen_clinics(self):
        wiz = self._wizard()
        self.assertNotIn('child_of', wiz.order_domain,
                         "with nobody chosen, every order is on offer")
        wiz.partner_ids = self.dr_one
        offered = self.env['sale.order'].search(eval(wiz.order_domain))  # noqa: S307
        self.assertIn(self.o_one, offered)
        self.assertIn(self.o_branch, offered, "a clinic's contacts are the same clinic")
        self.assertNotIn(self.o_two, offered)

    def test_adding_a_clinics_orders_in_one_press(self):
        wiz = self._wizard()
        wiz.partner_ids = self.dr_one
        self.assertGreaterEqual(wiz.available_count, 2)
        wiz.action_add_partner_orders()
        self.assertIn(self.o_one, wiz.order_ids)
        self.assertIn(self.o_branch, wiz.order_ids)
        self.assertNotIn(self.o_two, wiz.order_ids)
        self.assertEqual(wiz.available_count, 0, "nothing left to add")

    def test_dropping_a_clinic_drops_its_parcels(self):
        wiz = self._wizard()
        wiz.partner_ids = self.dr_one | self.dr_two
        wiz.action_add_partner_orders()
        self.assertIn(self.o_two, wiz.order_ids)
        wiz.partner_ids = self.dr_one
        wiz._onchange_partner_ids()
        self.assertNotIn(self.o_two, wiz.order_ids,
                         "a sticker for a clinic just taken off the list is a parcel "
                         "on the wrong van")
        self.assertIn(self.o_one, wiz.order_ids)

    def test_a_button_that_keeps_the_wizard_open(self):
        wiz = self._wizard()
        wiz.partner_ids = self.dr_one
        action = wiz.action_add_partner_orders()
        self.assertEqual(action['res_model'], 'epg.sticker.print.wizard')
        self.assertEqual(action['res_id'], wiz.id)
        self.assertTrue(action['views'], "an action without views cannot be opened")

    def test_the_menu_reaches_the_wizard(self):
        menu = self.env.ref('epg_sticker_print.menu_sticker_print')
        self.assertEqual(menu.action.res_model, 'epg.sticker.print.wizard')
        self.assertEqual(menu.parent_id.complete_name, 'Sales/Orders')


@tagged('post_install', '-at_install')
class TestStickerFromPartnersOnly(TransactionCase):
    """Customer / route labels straight from the clinics - no orders needed.

    The counter sometimes wants labels for a batch of clinics, or for a whole
    route, with nothing booked yet to point at. Address, phone and route all
    live on the partner - only 'One sticker per order' genuinely needs an
    order (its number, product, patient). (client, 2026-08-29)
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Engine = cls.env['epg.sticker.engine']
        cls.Wizard = cls.env['epg.sticker.print.wizard']
        cls.route_a = cls.env['crm.team'].create({
            'name': 'FROMPART A', 'address': 'Route A depot\nNear the junction',
            'phone': '0470 1111'})
        cls.route_b = cls.env['crm.team'].create({'name': 'FROMPART B'})
        cls.p1 = cls.env['res.partner'].create({
            'name': 'FP DR ONE', 'street': '1 Clinic Rd', 'city': 'Kochi',
            'phone': '1000', 'team_id': cls.route_a.id})
        cls.p2 = cls.env['res.partner'].create({
            'name': 'FP DR TWO', 'street': '2 Clinic Rd', 'city': 'Kollam',
            'phone': '2000', 'team_id': cls.route_a.id})
        cls.p3 = cls.env['res.partner'].create({
            'name': 'FP DR THREE', 'street': '3 Clinic Rd', 'city': 'Thrissur',
            'phone': '3000', 'team_id': cls.route_b.id})
        # a contact booked under its parent's own route, and a clinic with none
        cls.p_no_route = cls.env['res.partner'].create({'name': 'FP DR NO ROUTE'})
        cls.p_contact = cls.env['res.partner'].create({
            'name': 'FP DR ONE (contact)', 'parent_id': cls.p1.id, 'type': 'delivery'})

    # ------------------------------------------------------------------ engine
    def test_customer_mode_prints_the_partners_own_address(self):
        st = self.Engine.build_from_partners(self.p1 | self.p2, {'mode': 'customer'})
        self.assertEqual([s['name'] for s in st], ['FP DR ONE', 'FP DR TWO'])
        one = st[0]
        self.assertIn('1 Clinic Rd', ', '.join(one['address']))
        self.assertEqual(one['phone'], '1000')
        self.assertEqual(one['route'], 'FROMPART A')
        self.assertFalse(one['barcode'], "no orders behind it - nothing real to encode")
        self.assertEqual(one['refs'], '')
        self.assertEqual(one['count'], 0, "never claims a number of orders it has none of")

    def test_route_mode_groups_partners_by_their_own_route(self):
        st = self.Engine.build_from_partners(
            self.p1 | self.p2 | self.p3, {'mode': 'route'})
        names = [s['name'] for s in st]
        self.assertEqual(names, ['FROMPART A', 'FROMPART B'])
        route_a = st[0]
        self.assertIn('Route A depot', ', '.join(route_a['address']))
        self.assertEqual(route_a['phone'], '0470 1111')

    def test_a_partner_with_no_route_lands_in_its_own_bucket(self):
        st = self.Engine.build_from_partners(
            self.p1 | self.p_no_route, {'mode': 'route'})
        names = [s['name'] for s in st]
        self.assertIn('FROMPART A', names)
        self.assertIn('(no route)', names)

    def test_a_contact_inherits_its_parent_clinics_route(self):
        st = self.Engine.build_from_partners(self.p_contact, {'mode': 'route'})
        self.assertEqual([s['name'] for s in st], ['FROMPART A'],
                         "a contact booked under a clinic is that clinic's route")

    def test_a_long_dirty_address_never_overlaps_the_meta_line(self):
        """A comma-heavy address wrapping to five lines, with no barcode column to
        narrow it, cleared the fit estimate by a fraction of a millimetre while
        overflowing the real render - the address's last line printed on top of
        the phone/route line beneath it. The estimate now keeps a 3% cushion so a
        knife-edge case drops to the next size down, which has real headroom.
        (client, 2026-08-29)"""
        partner = self.env['res.partner'].create({
            'name': 'FP LONG ADDRESS CLINIC',
            'street': 'EDAVA RAILWAY STATION ROAD , VETTAKADA, EDAVA, KERALA 695311, '
                     'THIRUVANANTHAPURAM',
            'street2': 'PH : 081293 86293', 'phone': '8129386293',
            'team_id': self.route_a.id})
        fmt = self.env['epg.sticker.format'].get_default()
        st = self.Engine.build_from_partners(partner, {'mode': 'customer'}, fmt=fmt)[0]
        z = self.Engine.zones(fmt, has_code=False, is_qr=False)
        fit = st['fit']
        address_text = ', '.join(st['address'])
        addr_lines = self.Engine._lines_for(address_text, fit['address'], z['inner_w'])
        name_lines = self.Engine._lines_for(st['name'], fit['name'], z['inner_w'], bold=True)
        body_needed = (name_lines * fit['name'] * self.Engine.PT_MM * 1.08
                       + addr_lines * fit['address'] * self.Engine.PT_MM * 1.12)
        self.assertLessEqual(body_needed, fit['body'] + 0.05,
                             "the address must fit in the band it was given "
                             "%s lines needed, %.1fmm allotted for %.1fmm of text"
                             % (addr_lines, fit['body'], body_needed))

    def test_order_mode_builds_nothing_from_partners_alone(self):
        st = self.Engine.build_from_partners(self.p1 | self.p2, {'mode': 'order'})
        self.assertEqual(st, [],
                         "an order sticker has no number, product or patient without an order")

    # ------------------------------------------------------------------ wizard
    def test_the_wizard_prints_customer_labels_with_no_orders_selected(self):
        wiz = self.Wizard.create({
            'partner_ids': [(6, 0, (self.p1 | self.p2).ids)], 'mode': 'customer'})
        self.assertFalse(wiz.order_ids)
        self.assertTrue(wiz._can_print_from_partners())
        action = wiz.action_print()  # must not raise
        self.assertEqual(action['type'], 'ir.actions.report')
        self.assertEqual(wiz.sticker_count, 2)

    def test_the_wizard_prints_route_labels_with_no_orders_selected(self):
        wiz = self.Wizard.create({
            'partner_ids': [(6, 0, (self.p1 | self.p3).ids)], 'mode': 'route'})
        action = wiz.action_print()
        self.assertEqual(action['type'], 'ir.actions.report')
        self.assertEqual(wiz.sticker_count, 2, "one label per distinct route")

    def test_order_mode_still_requires_real_orders(self):
        """Picking clinics is not enough for 'One sticker per order' - there is
        nothing to print an order number, product or patient from."""
        wiz = self.Wizard.create({
            'partner_ids': [(6, 0, self.p1.ids)], 'mode': 'order'})
        self.assertFalse(wiz._can_print_from_partners())
        with self.assertRaises(UserError) as caught:
            wiz.action_print()
        self.assertIn('order', str(caught.exception).lower())

    def test_report_data_carries_the_partners_for_the_pdf_renderer(self):
        wiz = self.Wizard.create({
            'partner_ids': [(6, 0, self.p1.ids)], 'mode': 'customer'})
        data = wiz._report_data()
        self.assertEqual(data['ids'], [])
        self.assertEqual(data['partner_ids'], self.p1.ids)

    def test_choosing_an_order_wins_over_the_partner_only_path(self):
        """Pick some orders on top of the clinics, and those specific orders -
        not a blanket clinic label - are what gets printed."""
        product = self.env['product.product'].create({
            'name': 'FP Product', 'type': 'consu', 'list_price': 50})
        order = self.env['sale.order'].create({
            'partner_id': self.p1.id,
            'order_line': [(0, 0, {'product_id': product.id, 'product_uom_qty': 1})]})
        wiz = self.Wizard.create({
            'partner_ids': [(6, 0, self.p1.ids)], 'order_ids': [(6, 0, order.ids)],
            'mode': 'customer'})
        stickers = wiz._build_now()
        self.assertEqual(stickers[0]['count'], 1, "built from the order, not the bare clinic")

    def test_the_report_renders_a_real_pdf_from_partners_alone(self):
        wiz = self.Wizard.create({
            'partner_ids': [(6, 0, self.p1.ids)], 'mode': 'customer'})
        report = self.env.ref('epg_sticker_print.action_report_sticker')
        pdf, kind = report.with_context(force_report_rendering=True)._render_qweb_pdf(
            report.report_name, [], data=wiz._report_data())
        self.assertEqual(kind, 'pdf')
        self.assertTrue(pdf.startswith(b'%PDF'))


@tagged('post_install', '-at_install')
class TestStickerMenuTarget(TransactionCase):
    """The menu-launched wizard must actually BE the full page its own comment
    describes - not a modal masquerading as one.

    `_reopen()` decides target='current' whenever the `epg_sticker_fullpage`
    context flag is set, which only this menu action sets. If the action itself
    were still target='new' (a modal), "Add all open orders" would tear the
    small dialog open into a full page mid-click: the data updates correctly,
    but the wizard the person was looking at visibly vanishes and is replaced
    by something that looks like a different, broken page. Both sides of that
    contract are pinned here so they cannot drift apart again.
    (client, 2026-08-29)
    """

    def test_the_menu_action_is_a_real_full_page(self):
        action = self.env.ref('epg_sticker_print.action_sticker_print_menu')
        self.assertEqual(action.target, 'current')

    def test_reopen_agrees_with_the_menu_actions_own_target(self):
        wiz = self.env['epg.sticker.print.wizard'].with_context(
            epg_sticker_fullpage=True).create({})
        action = wiz._reopen()
        menu_action = self.env.ref('epg_sticker_print.action_sticker_print_menu')
        self.assertEqual(action['target'], menu_action.target,
                         "reopening must match how the wizard is actually shown")

    def test_reopen_stays_a_modal_when_opened_as_one(self):
        """The list/kanban and partner bindings open as target='new' (a modal)
        and carry no fullpage flag - reopening them must stay a modal too."""
        wiz = self.env['epg.sticker.print.wizard'].create({})
        self.assertEqual(wiz._reopen()['target'], 'new')


@tagged('post_install', '-at_install')
class TestStickerFormatAccess(TransactionCase):

    def test_a_sales_manager_can_resize_a_format(self):
        """report.paperformat is admin-only; editing a sticker format must not need that."""
        from odoo.tests import new_test_user
        manager = new_test_user(self.env, login='sticker_mgr_x',
                                groups='base.group_user,sales_team.group_sale_manager')
        self.assertFalse(manager.has_group('base.group_system'))
        fmt = self.env['epg.sticker.format'].create({'name': 'Access 80x40', 'width_mm': 80, 'height_mm': 40})
        fmt.with_user(manager).write({'width_mm': 90})
        self.assertEqual(fmt.paperformat_id.page_width, 90)
        created = self.env['epg.sticker.format'].with_user(manager).create(
            {'name': 'Access 60x30', 'width_mm': 60, 'height_mm': 30})
        self.assertEqual(created.paperformat_id.page_width, 60)


@tagged('post_install', '-at_install')
class TestStickerRunPreviewAndInvoices(TransactionCase):
    """Every sticker of the run in the preview, and the wizard from an invoice.
    (client, 2026-10-14: "print sticker should show the previews of multiple selected
    orders. Provide the print sticker in the invoices' action also in accounting")"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.team = cls.env['crm.team'].create({'name': 'KLM'})
        cls.p1 = cls.env['res.partner'].create({'name': 'DR RUN ONE', 'street': '1 Main Rd', 'city': 'Kochi', 'phone': '111'})
        cls.p2 = cls.env['res.partner'].create({'name': 'DR RUN TWO', 'street': '2 Side Rd', 'city': 'Kollam', 'phone': '222'})
        cls.product = cls.env['product.product'].create({'name': 'Twin Block', 'type': 'consu', 'list_price': 500})
        cls.orders = cls.env['sale.order']
        for partner in (cls.p1, cls.p1, cls.p2):
            cls.orders |= cls.env['sale.order'].create({
                'partner_id': partner.id, 'team_id': cls.team.id,
                'order_line': [(0, 0, {'product_id': cls.product.id, 'product_uom_qty': 1})]})
        cls.Wizard = cls.env['epg.sticker.print.wizard']

    def _wizard(self, records, **vals):
        return self.Wizard.with_context(active_model=records._name, active_ids=records.ids).create(vals)

    def test_the_preview_shows_every_sticker_of_the_run(self):
        wiz = self._wizard(self.orders, mode='order')
        html = str(wiz.preview_html)
        self.assertIn('all 3 stickers', html)
        for i, order in enumerate(self.orders.sorted('name')):
            self.assertIn('%s · %s' % (i + 1, order.name), html, "each label says whose parcel it is")

    def test_a_customer_run_previews_one_label_a_clinic(self):
        wiz = self._wizard(self.orders, mode='customer')
        html = str(wiz.preview_html)
        self.assertIn('all 2 stickers', html)
        self.assertIn('DR RUN ONE · 2 orders', html)
        self.assertIn('DR RUN TWO', html)

    def test_a_long_run_previews_the_first_and_says_so(self):
        from odoo.addons.epg_sticker_print.wizard import sticker_wizard
        with patch.object(sticker_wizard, 'PREVIEW_MAX', 2):
            html = str(self._wizard(self.orders, mode='order').preview_html)
        last = self.orders.sorted('name')[-1]
        self.assertIn('first 2 of 3', html)
        self.assertNotIn(last.name, html)

    def test_the_report_can_be_cut_to_the_first_stickers(self):
        values = self.env['report.epg_sticker_print.report_sticker']._get_report_values(
            self.orders.ids, data={'mode': 'order', 'limit': 1})
        self.assertEqual(len(values['stickers']), 1)
        self.assertEqual(len(self.env['report.epg_sticker_print.report_sticker']._get_report_values(
            self.orders.ids, data={'mode': 'order'})['stickers']), 3, "no limit, the whole run")

    def test_the_body_wkhtmltopdf_gets_says_it_is_utf8(self):
        """The page has no div.article, so wkhtmltopdf is handed the body bare - without
        the layout's <meta charset> - and read it as latin-1: "Destination · 2 orders"
        printed "Destination Â· 2 orders". The body itself must say utf-8."""
        report = self.env.ref('epg_sticker_print.action_report_sticker')
        html = report._render_qweb_html(report.report_name, self.orders.ids,
                                        data={'mode': 'customer', 'ids': self.orders.ids})[0]
        bodies = report._prepare_wkhtmltopdf_html(html)[0]
        self.assertEqual(len(bodies), 1, "one body: the labels are not wrapped in the padded layout")
        self.assertIn('charset="utf-8"', bodies[0][:2000])
        self.assertIn('· 2 orders', bodies[0], "and it carries the middle dot as text")

    def test_each_label_is_cut_out_of_its_sheet(self):
        import shutil
        # Odoo's own finding of wkhtmltopdf (PATH + bin_path), not shutil's; Odoo 20 asks the engine by name
        if not shutil.which('pdftoppm') or self.env['ir.actions.report'].get_pdf_engine_state('wkhtmltopdf') != 'ok':
            self.skipTest("needs wkhtmltopdf and pdftoppm")
        from PIL import Image
        import io
        fmt = self.env.ref('epg_sticker_print.format_a4_2x5')
        report = self.env.ref('epg_sticker_print.action_report_sticker').with_context(
            epg_sticker_paperformat_id=fmt.paperformat_id.id, force_report_rendering=True)
        pdf = report._render_qweb_pdf(report.report_name, self.orders.ids,
                                      data={'mode': 'order', 'ids': self.orders.ids, 'format_id': fmt.id, 'copies': 1})[0]
        self.assertTrue(pdf.startswith(b'%PDF'))
        pngs = self.Wizard._label_pngs(pdf, fmt, 3)
        self.assertEqual(len(pngs), 3, "three labels off one sheet")
        with Image.open(io.BytesIO(pngs[1])) as label:
            self.assertAlmostEqual(label.width, fmt.width_mm * 150 / 25.4, delta=2)
            self.assertAlmostEqual(label.height, fmt.height_mm * 150 / 25.4, delta=2)
            # the second label sits in the second column: not blank
            self.assertLess(min(label.convert('L').getdata()), 128)

    # ------------------------------------------------------------ from an invoice
    def test_from_an_invoice_the_wizard_takes_the_orders_it_bills(self):
        order = self.orders[0]
        order.action_confirm()
        invoice = order._create_invoices()
        wiz = self._wizard(invoice)
        self.assertEqual(wiz.order_ids, order)
        self.assertEqual(wiz.partner_ids, self.p1)
        self.assertEqual(wiz.sticker_count, 1)

    def test_an_invoice_without_an_order_prints_for_its_clinic(self):
        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': self.p2.id,
            'invoice_line_ids': [(0, 0, {'product_id': self.product.id, 'quantity': 1, 'price_unit': 10})]})
        wiz = self._wizard(invoice)
        self.assertFalse(wiz.order_ids)
        self.assertEqual(wiz.partner_ids, self.p2)
        self.assertEqual(wiz.sticker_count, 1, "a customer label from the clinic itself")
        self.assertEqual(wiz.action_print()['data']['partner_ids'], self.p2.ids)

    def test_the_invoices_action_menu_and_accounting_offer_it(self):
        action = self.env.ref('epg_sticker_print.action_sticker_print_invoice')
        self.assertEqual(action.binding_model_id.model, 'account.move')
        menu = self.env.ref('epg_sticker_print.menu_sticker_print_accounting')
        self.assertEqual(menu.parent_id, self.env.ref('account.menu_finance_receivables'))


@tagged('post_install', '-at_install')
class TestStickerTraySheet(TransactionCase):
    """An A4 of 3 x 8 labels in an ordinary printer, mostly peeled off: the wizard shows
    the sheet, the run lands on the labels still there, and the next run remembers.
    (client, 2026-10-19)"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.fmt = cls.env.ref('epg_sticker_print.format_a4_3x8_65x35')
        cls.Engine = cls.env['epg.sticker.engine']
        cls.Sheet = cls.env['epg.sticker.sheet']
        cls.product = cls.env['product.product'].create({'name': 'Tray Appliance', 'type': 'consu', 'list_price': 100.0})
        cls.clinic = cls.env['res.partner'].create({'name': 'DR TRAY', 'street': '1 Tray Rd', 'city': 'Kochi', 'phone': '111'})
        cls.orders = cls.env['sale.order']
        for _i in range(2):
            cls.orders |= cls.env['sale.order'].create({
                'partner_id': cls.clinic.id,
                'order_line': [(0, 0, {'product_id': cls.product.id, 'product_uom_qty': 1})]})

    def _wizard(self, **vals):
        return self.env['epg.sticker.print.wizard'].with_context(
            active_model='sale.order', active_ids=self.orders.ids).create(dict({'mode': 'order', 'format_id': self.fmt.id}, **vals))

    def test_the_sheet_is_an_a4_page_with_the_grid_centred(self):
        self.assertTrue(self.fmt.is_sheet)
        self.assertEqual((self.fmt.columns * self.fmt.rows, self.fmt.grid_width_mm, self.fmt.grid_height_mm), (24, 195, 280))
        self.assertEqual((self.fmt.page_width_mm, self.fmt.page_height_mm), (210, 297), "the page is the sheet")
        self.assertEqual(self.fmt._grid_offsets(), (7.5, 8.5), "centred until measured offsets are typed in")
        self.assertEqual((self.fmt.paperformat_id.page_width, self.fmt.paperformat_id.page_height), (210, 297))
        layout = self.Engine.layout_vars(self.fmt)
        self.assertEqual((layout['per_page'], layout['offset_left'], layout['offset_top']), (24, 7.5, 8.5))
        self.fmt.write({'offset_left_mm': 6.0})
        self.assertEqual(self.fmt._grid_offsets(), (6.0, 8.5))
        with self.assertRaises(ValidationError):
            self.fmt.write({'offset_left_mm': 40.0})
        self.fmt.write({'offset_left_mm': 0.0})
        roll = self.env.ref('epg_sticker_print.format_roll_65x35')
        self.assertEqual(roll._grid_offsets(), (0.0, 0.0), "a roll's page is its label")

    def test_the_run_fills_the_free_cells_then_fresh_sheets(self):
        stickers = ['s%s' % i for i in range(7)]
        pages = self.Engine.paginate(stickers, 6, skip=[0, 1, 4])
        self.assertEqual(pages, [[None, None, 's0', 's1', None, 's2'], ['s3', 's4', 's5', 's6']])
        self.assertEqual(self.Engine.paginate(stickers[:2], 6, skip=[0, 4]), [[None, 's0', 's1']],
                         "cells after the last sticker are not drawn")
        self.assertEqual(self.Engine.paginate(stickers, 6, skip=range(6)), [stickers[:6], stickers[6:]],
                         "a sheet with every label gone is no sheet: the run starts fresh")
        self.assertEqual(self.Engine.paginate(stickers[:3], 1, skip=[0]), [['s0'], ['s1'], ['s2']], "rolls ignore it")
        self.assertEqual(self.Engine.paginate([], 6, skip=[0]), [[]])

    def test_what_the_print_took_is_what_the_next_run_starts_from(self):
        after = self.Sheet.after_print
        self.assertEqual(after(24, range(21), 2), list(range(23)), "two of the three left")
        self.assertEqual(after(24, range(21), 3), [], "the sheet is finished: a fresh one next")
        self.assertEqual(after(24, range(21), 5), [0, 1], "two on the fresh sheet after it")
        self.assertEqual(after(24, range(21), 27), [], "3 here + 24 on the next: both finished")
        self.assertEqual(after(24, [], 24), [])
        self.assertEqual(after(24, [3, 5], 1), [0, 3, 5], "reading order, around the gaps")

    def test_the_wizard_starts_from_the_tray_sheet_and_the_print_moves_it_on(self):
        import json
        self.Sheet.create({'format_id': self.fmt.id, 'used_cells': json.dumps(list(range(21)))})
        wiz = self._wizard()
        self.assertEqual(json.loads(wiz.sheet_cells), list(range(21)), "the sheet as the last print left it")
        self.assertEqual(wiz.sticker_count, 2)
        self.assertEqual(wiz._report_data()['skip_cells'], list(range(21)))
        action = wiz.action_print()
        self.assertEqual(action['data']['skip_cells'], list(range(21)))
        self.assertEqual(self.Sheet.cells_for(self.fmt), list(range(23)), "two more gone")
        values = self.env['report.epg_sticker_print.report_sticker']._get_report_values(
            self.orders.ids, data=action['data'])
        first = values['pages'][0]
        self.assertEqual(len(values['pages']), 1)
        self.assertEqual(first[:21], [None] * 21, "the peeled-off cells stay blank")
        self.assertEqual([s['title'] for s in first[21:]], self.orders.sorted('name').mapped('name'))
        # the counter corrects the grid by hand: that is what the print takes from
        wiz.sheet_cells = json.dumps([0, 1])
        wiz.action_print()
        self.assertEqual(self.Sheet.cells_for(self.fmt), [0, 1, 2, 3])
        # a roll has no sheet in the tray
        wiz.format_id = self.env.ref('epg_sticker_print.format_roll_65x35')
        self.assertEqual(wiz.sheet_cells, '[]')
        self.assertNotIn('skip_cells', wiz._report_data())

    def test_the_page_blanks_the_peeled_cells(self):
        report = self.env.ref('epg_sticker_print.action_report_sticker')
        html = report._render_qweb_html(report.report_name, self.orders.ids, data={
            'mode': 'order', 'ids': self.orders.ids, 'format_id': self.fmt.id, 'copies': 1, 'skip_cells': [0, 2]})[0]
        self.assertEqual(html.count(b'epg-sticker-blank'), 2)
        self.assertEqual(html.count(b'class="epg-sticker"'), 2)
        self.assertIn(b'padding:8.5mm 0 0 7.5mm', html, "the grid is pushed to where the labels are")
        self.assertLess(html.index(b'epg-sticker-blank'), html.index(b'class="epg-sticker"'), "first cell blank")

    def test_the_order_number_prints_at_the_details_size(self):
        """(client, 2026-10-19: "increase the order number font ... same font size as
        patient name and others")"""
        order = self.orders[0]
        by_order = self.Engine.build(order, {'mode': 'order', 'show_patient': True}, fmt=self.fmt)[0]
        self.assertEqual(by_order['fit']['title'], by_order['fit']['meta'], "the order number as big as the details")
        self.assertGreaterEqual(by_order['fit']['head'], by_order['fit']['title'] * self.Engine.PT_MM * 1.25 - 0.1,
                                "and the head band holds it")
        z = self.Engine.zones(self.fmt, has_code=True)
        fit = by_order['fit']
        self.assertLessEqual(fit['head'] + fit['body'] + fit['meta_h'], z['inner_h'] + 0.1,
                             "the three bands still share the label's height")
        by_clinic = self.Engine.build(order, {'mode': 'customer'}, fmt=self.fmt)[0]
        self.assertEqual(by_clinic['fit']['title'], by_clinic['fit']['small'], "'Destination' stays a small caption")
        report = self.env.ref('epg_sticker_print.action_report_sticker')
        html = report._render_qweb_html(report.report_name, order.ids,
                                        data={'mode': 'order', 'ids': order.ids, 'format_id': self.fmt.id})[0].decode()
        self.assertIn('font-size:%spt;font-weight:bold;color:#000' % fit['title'], html)

    def test_the_test_grid_prints_the_formats_outlines_on_its_page(self):
        action = self.fmt.action_print_test_grid()
        self.assertEqual(action['report_name'], 'epg_sticker_print.report_sticker_grid')
        report = self.env.ref('epg_sticker_print.action_report_sticker_grid')
        self.assertEqual(report.with_context(action['context']).get_paperformat(), self.fmt.paperformat_id,
                         "on the format's own page size")
        html = report.with_context(action['context'])._render_qweb_html(report.report_name, self.fmt.ids)[0].decode()
        self.assertEqual(html.count('epg-grid-cell'), 24)
        self.assertIn('padding:8.5mm 0 0 7.5mm', html)
        self.assertIn('50 mm', html)
        self.assertIn('row 8, col 3', html)
