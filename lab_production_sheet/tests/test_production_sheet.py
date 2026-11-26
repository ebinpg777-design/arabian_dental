# -*- coding: utf-8 -*-
"""What the two sheets say, read off `_lab_sheet` rather than off a PDF."""
from datetime import datetime

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestProductionSheet(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        G = cls.env.ref
        cls.officer = cls.env['res.users'].create({
            'name': 'Sheet Officer', 'login': 'sheet_officer',
            'group_ids': [(6, 0, [G('base.group_user').id])]})
        cls.bench_user = cls.env['res.users'].create({
            'name': 'Sheet Technician', 'login': 'sheet_technician',
            'group_ids': [(6, 0, [G('base.group_user').id, G('mrp.group_mrp_user').id])]})
        cls.route = cls.env['crm.team'].create({'name': 'Sheet Route', 'user_id': cls.officer.id})
        cls.clinic = cls.env['res.partner'].create({'name': 'Sheet Clinic', 'team_id': cls.route.id})
        cls.shade = cls.env['product.colour'].create({'name': 'ZZ-SHEET-A2'})

    def _order(self, code, **line):
        bom = self.env['mrp.bom'].search([('code', '=', code)], limit=1)
        self.assertTrue(bom, "no bill of materials coded %s" % code)
        product = bom.product_tmpl_id.product_variant_id
        order = self.env['sale.order'].create({
            'partner_id': self.clinic.id, 'team_id': self.route.id, 'patient': 'Sheet Patient',
            'impression_type': 'Cast', 'wax_bite': 'Yes', 'opposing_model': 'No',
            'pre_cutting_model': 'Yes', 'impression_tray': 'Both',
            'order_line': [(0, 0, dict({'product_id': product.id, 'product_uom_qty': 2,
                                        'ul': 'upper', 'color_scheme': self.shade.id}, **line))],
        })
        production = self.env['mrp.production'].create({
            'product_id': product.id, 'bom_id': bom.id, 'product_qty': 2,
            'sale_line_id': order.order_line.id,
        })
        production.action_confirm()
        return order, production

    # ------------------------------------------------------------- the heading
    def test_the_heading_is_the_orders(self):
        order, production = self._order('MC-BSC', teeth='14, 13, 46, 21')
        sheet = production._lab_sheet()
        self.assertEqual(sheet['file_no'], production.name, "the file number is the MO's")
        self.assertEqual(sheet['order_no'], order.name)
        self.assertEqual((sheet['clinic'], sheet['patient']), ('Sheet Clinic', 'Sheet Patient'))
        self.assertEqual((sheet['impression_type'], sheet['wax_bite'], sheet['opposing_model'],
                          sheet['pre_cutting_model'], sheet['impression_tray']),
                         ('Cast', 'Yes', 'No', 'Yes', 'Both'))
        self.assertIn(('Cast', 'Cast'), sheet['impression_types'])
        self.assertEqual(sheet['shade'], 'ZZ-SHEET-A2')
        self.assertEqual(sheet['quantity'], '2')
        self.assertEqual(sheet['officer'], 'Sheet Officer', "the route's executive")
        self.assertEqual(sheet['teeth']['upper_right'], '4 3', "17-style numbers lose the quadrant")
        self.assertEqual(sheet['prepared_by'], order.create_uid.name
                         if not order.create_uid._is_superuser() else '',
                         "never the system's own name")

    def test_the_teeth_go_in_the_cross(self):
        """Palmer, as the invoice draws it: the cell is the quadrant, so 14 is a 4."""
        cross = self.env['mrp.production']._lab_sheet_teeth('14, 13, 46, 21, 22, 31 and 55')
        self.assertEqual(cross, {'upper_right': 'E 4 3', 'upper_left': '1 2',
                                 'lower_right': '6', 'lower_left': '1', 'rest': 'and'})
        self.assertEqual(self.env['mrp.production']._lab_sheet_teeth(False)['upper_left'], '')
        self.assertEqual(self.env['mrp.production']._lab_sheet_teeth('upper arch')['rest'],
                         'upper arch', "no tooth at all: the text is printed as written")

    def test_the_priority_and_the_day_the_work_is_owed(self):
        order, production = self._order('MC-BSC')
        order.date_due = '2026-11-26'
        sheet = production._lab_sheet()
        self.assertEqual((sheet['due'], sheet['priority'], sheet['rush']),
                         ('26-11-2026', 'Normal', ''))
        order.order_line.is_urgent = True
        self.assertEqual(production._lab_sheet()['rush'], 'URGENT', "a line ticked urgent")
        order.order_line.is_urgent = False
        order.priority = 'urgent'
        self.assertEqual(production._lab_sheet()['rush'], 'URGENT')
        if 'emergency' in dict(order._fields['priority']._description_selection(self.env)):
            order.priority = 'emergency'
            sheet = production._lab_sheet()
            self.assertEqual((sheet['priority'], sheet['rush']), ('Emergency', 'EMERGENCY'))

    def test_an_answer_changed_on_the_job_is_changed_on_the_order(self):
        """By somebody with no right to edit a sale order."""
        order, production = self._order('MC-BSC')
        production.with_user(self.bench_user).lab_opposing_model = 'Yes'
        self.assertEqual(order.opposing_model, 'Yes')
        self.assertEqual(order.wax_bite, 'Yes', "the answers not touched are left alone")

    # -------------------------------------------------------------- the tables
    def test_a_metal_ceramic_case_has_the_white_sheets_four_tables(self):
        _order, production = self._order('MC-BSC')
        sheet = production._lab_sheet()
        self.assertEqual([s['title'] for s in sheet['sections']],
                         ['CAD/CAM', 'WAX UP', 'METAL', 'CERAMIC'])
        rows = {s['title']: [r['label'] for r in s['rows']] for s in sheet['sections']}
        self.assertEqual(rows['CAD/CAM'], ['Scanning', 'Designing', 'Printing', 'Light Curing'])
        self.assertEqual(rows['WAX UP'], ['Spruing', 'Investing'])
        self.assertEqual(rows['METAL'], ['Casting', 'Trimming', 'Polishing'])
        self.assertEqual(rows['CERAMIC'], ['Opaque', 'Buildup', 'Trimming', 'Finishing'])
        subtitles = {s['title']: s['subtitle'] for s in sheet['sections']}
        self.assertEqual((subtitles['CAD/CAM'], subtitles['CERAMIC']), ('3D', 'Metal Ceramic'))
        self.assertTrue(sheet['model'], "model preparation has its own line")
        self.assertEqual(sheet['model']['label'], 'Model Preparation')

    def test_a_zirconia_case_has_the_blue_sheets_two(self):
        _order, production = self._order('ZR-BSC')
        sheet = production._lab_sheet()
        self.assertEqual([(s['title'], s['subtitle']) for s in sheet['sections']],
                         [('CAD/CAM', 'Zirconia'), ('CERAMIC', 'Metal free Ceramic')])
        self.assertEqual([r['label'] for r in sheet['sections'][0]['rows']],
                         ['Scanning', 'Designing', 'Milling', 'Sintering'])

    def test_the_other_two_sheets(self):
        _order, hawley = self._order('OR-HAW')
        ortho = hawley._lab_sheet()['sections']
        self.assertEqual([s['title'] for s in ortho], ['ORTHODONTIC'])
        self.assertEqual([r['label'] for r in ortho[0]['rows']],
                         ['Wire Bending', 'Acrylization', 'Trimming'])
        _order, denture = self._order('AC-RPD')
        acrylic = denture._lab_sheet()['sections']
        self.assertEqual([r['label'] for r in acrylic[0]['rows']],
                         ['Special Tray', 'Wax Bite', 'Teeth Setting', 'Trail Carving', 'Wax Up',
                          'Flasking', 'De Waxing',
                          'Packing', 'Curing', 'Trimming', 'Polishing'])

    def test_a_bench_named_for_its_department_prints_as_its_row(self):
        label = self.env['mrp.production']._lab_sheet_label
        metal = self.env.ref('lab_masters.dept_metal')
        ortho = self.env.ref('lab_masters.dept_ortho')
        self.assertEqual(label('Metal/Trimming', metal), 'Trimming')
        self.assertEqual(label('%s/Model Preparation' % ortho.name, ortho), 'Model Preparation')
        self.assertEqual(label('Metal Trimming', metal), 'Trimming', "the older spelling too")
        self.assertEqual(label('Casting', metal), 'Casting')
        cadcam = self.env.ref('lab_masters.dept_cadcam')
        self.assertEqual(label('Scanning', cadcam), 'Scanning')

    def test_every_sheet_has_its_own_model_preparation_line(self):
        for code in ('MC-BSC', 'OR-HAW', 'AC-RPD'):
            _order, production = self._order(code)
            sheet = production._lab_sheet()
            self.assertTrue(sheet['model'], code)
            self.assertEqual(sheet['model']['label'], 'Model Preparation', code)
            self.assertNotIn('Model Preparation',
                             [r['label'] for s in sheet['sections'] for r in s['rows']], code)

    def test_a_step_says_who_did_it_and_when(self):
        _order, production = self._order('MC-BSC')
        casting = production.workorder_ids.filtered(lambda w: w.name == 'Casting')
        casting.write({'bench_user_id': self.bench_user.id,
                       'handed_over_at': datetime(2026, 11, 24, 6, 30)})   # 12:00 in the lab
        checked = production.workorder_ids.filtered(lambda w: w.name == 'Final Check')
        checked.bench_user_id = self.bench_user
        sheet = production.with_context(tz='Asia/Kolkata')._lab_sheet()
        metal = next(s for s in sheet['sections'] if s['title'] == 'METAL')
        row = metal['rows'][0]
        self.assertEqual((row['technician'], row['date'], row['time']),
                         ('Sheet Technician', '24-11-2026', '12:00'))
        self.assertEqual((metal['rows'][1]['technician'], metal['rows'][1]['date']), ('', ''),
                         "a step nobody has done yet is left for a pen")
        self.assertEqual(sheet['checked']['technician'], 'Sheet Technician')
        self.assertFalse(sheet['packed']['technician'])

    def test_a_cancelled_step_is_not_on_the_sheet(self):
        _order, production = self._order('AC-RPD')
        production.workorder_ids.filtered(lambda w: w.name == 'Special Tray').action_cancel()
        rows = [r['label'] for r in production._lab_sheet()['sections'][0]['rows']]
        self.assertNotIn('Special Tray', rows)

    def test_material_is_listed_under_the_department_that_keeps_it(self):
        _order, production = self._order('MC-BSC')
        sheet = production._lab_sheet()
        materials = {s['title']: s['materials'] for s in sheet['sections']}
        self.assertIn('Glaze Powder', materials['CERAMIC'])
        self.assertIn('Casting Alloy', materials['METAL'])
        self.assertNotIn('Glaze Powder', materials['METAL'])

    # --------------------------------------------------------------- printing
    def test_both_sheets_render(self):
        _order, production = self._order('MC-BSC', teeth='14, 13')
        Report = self.env['ir.actions.report']
        html, _kind = Report._render_qweb_html(
            'lab_production_sheet.report_production_report', production.ids)
        text = html.decode()
        self.assertIn('DAILY PRODUCTION REPORT', text)
        self.assertIn(production.name, text)
        self.assertIn('Buildup', text)
        self.assertIn('Marketing Officer', text)

        html, _kind = Report._render_qweb_html(
            'lab_production_sheet.report_job_sheet', production.ids)
        text = html.decode()
        self.assertIn('TECHNICIAN JOB SHEET', text)
        self.assertIn(production.name, text)
        self.assertNotIn('Particulars', text, "no stage table on the technician's sheet")
        self.assertNotIn('Casting', text)
        self.assertEqual(text.count('alt="Barcode %s"' % production.name), 1,
                         "the barcode the station board reads, and no QR code beside it")
        for gone in ('Came with the case', 'Modifications', '>Teeth<'):
            self.assertNotIn(gone, text)
        for there in ('Expected Date', 'Priority', 'Special instructions'):
            self.assertIn(there, text)
        self.assertNotIn('class="ps-rush"', text, "a normal order carries no rush label")

    def test_two_job_sheets_to_a_page(self):
        productions = self.env['mrp.production']
        for _i in range(3):
            productions |= self._order('MC-BSC')[1]
        productions[1].sale_id.priority = 'urgent'
        html, _kind = self.env['ir.actions.report']._render_qweb_html(
            'lab_production_sheet.report_job_sheet', productions.ids)
        text = html.decode()
        self.assertEqual(text.count('<div class="article"'), 2, "one page body per pair")
        self.assertEqual(text.count('class="ps-half"'), 2)
        self.assertEqual(text.count('class="ps-half ps-half-second"'), 1)
        self.assertEqual(text.count('class="ps-rush"'), 1)
        self.assertIn('>URGENT<', text)

    def test_the_order_prints_the_sheets_of_all_its_jobs(self):
        order, production = self._order('MC-BSC')
        action = order.action_print_job_sheets()
        self.assertEqual(action['report_name'], 'lab_production_sheet.report_job_sheet')
        self.assertEqual(action['context']['active_ids'], production.ids)
        action = order.action_print_production_reports()
        self.assertEqual(action['report_name'], 'lab_production_sheet.report_production_report')
        empty = self.env['sale.order'].create({'partner_id': self.clinic.id})
        with self.assertRaises(UserError):
            empty.action_print_job_sheets()

    def test_the_job_list_prints_the_jobs_ticked(self):
        jobs = self._order('MC-BSC')[1] | self._order('MC-BSC')[1]
        dead = self._order('MC-BSC')[1]
        dead.action_cancel()
        action = (jobs | dead).action_print_job_sheet()
        self.assertEqual(action['report_name'], 'lab_production_sheet.report_job_sheet')
        self.assertEqual(sorted(action['context']['active_ids']), sorted(jobs.ids),
                         "a cancelled job's sheet is not printed")
        with self.assertRaises(UserError):
            dead.action_print_job_sheet()
        arch = self.env['mrp.production'].get_view(
            self.env.ref('mrp.mrp_production_tree_view').id, 'list')['arch']
        self.assertIn('name="action_print_job_sheet"', arch, "the button in the list's header")

    # ------------------------------------------- the job card: printed, or not yet
    def test_printing_the_job_card_says_so_on_the_job(self):
        """(client, 2026-11-26: "able to filter confirmed orders which are not printed
        job card")"""
        order, production = self._order('MC-BSC')
        self.assertFalse(production.job_sheet_printed_at)
        self.assertEqual((order.job_card_state, order.job_card_pending), ('pending', True))
        waiting = self.env['sale.order'].search(
            [('job_card_pending', '=', True), ('id', '=', order.id)])
        self.assertEqual(waiting, order)
        # the Daily Production Report is not the job card
        self.env['ir.actions.report']._render_qweb_html(
            'lab_production_sheet.report_production_report', production.ids)
        self.assertFalse(production.job_sheet_printed_at)
        self.env['ir.actions.report']._render_qweb_html(
            'lab_production_sheet.report_job_sheet', production.ids)
        self.assertTrue(production.job_sheet_printed_at)
        self.assertEqual((production.job_sheet_printed_by_id, production.job_sheet_print_count),
                         (self.env.user, 1))
        order.invalidate_recordset(['job_card_state', 'job_card_pending'])
        self.assertEqual((order.job_card_state, order.job_card_pending), ('printed', False))
        self.assertFalse(self.env['sale.order'].search(
            [('job_card_pending', '=', True), ('id', '=', order.id)]))
        self.assertEqual(self.env['sale.order'].search(
            [('job_card_pending', '=', False), ('id', '=', order.id)]), order)

    def test_an_order_with_one_card_of_two_is_partly_printed(self):
        order, first = self._order('MC-BSC')
        second = first.copy({'sale_line_id': order.order_line.id})
        second.action_confirm()
        first._lab_stamp_job_sheet()
        self.assertEqual((order.job_card_state, order.job_card_pending), ('partial', True))

    def test_the_job_card_names_the_category(self):
        """(client, 2026-11-26: "category name should show in the job card")"""
        _order, production = self._order('MC-BSC')
        category = production.product_id.categ_id.name
        self.assertTrue(category)
        self.assertEqual(production._lab_sheet()['category'], category)
        self.assertEqual(production.product_categ_id, production.product_id.categ_id)
        html, _kind = self.env['ir.actions.report']._render_qweb_html(
            'lab_production_sheet.report_job_sheet', production.ids)
        self.assertIn('Type of work : <b>%s</b>' % category, html.decode())

    def test_somebody_without_manufacturing_rights_can_open_an_order(self):
        """A field executive opened an order from a case and the screen crashed: the
        form named a field only manufacturing users may read. Every field the form
        names must be one the reader is given. (client, 2026-11-26)"""
        from lxml import etree
        G = self.env.ref
        executive = self.env['res.users'].create({
            'name': 'Sheet Executive', 'login': 'sheet_executive',
            'group_ids': [(6, 0, [G('base.group_user').id,
                                  G('sales_team.group_sale_salesman').id])]})
        self.assertFalse(executive.has_group('mrp.group_mrp_user'))
        order, _production = self._order('MC-BSC')
        views = self.env['sale.order'].with_user(executive).get_views([(False, 'form')])
        known = set(views['models']['sale.order']['fields'])
        arch = etree.fromstring(views['views']['form']['arch'])
        named = set()

        def walk(node):
            for child in node:
                if child.tag == 'field':
                    named.add(child.get('name'))
                    continue            # a sub-view is another model's
                walk(child)
        walk(arch)
        self.assertFalse(named - known, "the form names fields this user is not given")
        self.assertEqual(order.with_user(executive).job_card_state, 'pending',
                         "and the buttons still know the order has a job")

