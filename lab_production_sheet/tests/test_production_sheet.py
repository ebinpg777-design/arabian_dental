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
        self.assertEqual(sheet['prepared_by'], order.create_uid.name
                         if not order.create_uid._is_superuser() else '',
                         "never the system's own name")

    def test_the_teeth_go_in_the_cross(self):
        cross = self.env['mrp.production']._lab_sheet_teeth('14, 13, 46, 21, 22, 31 and 55')
        self.assertEqual(cross, {'upper_right': '55 14 13', 'upper_left': '21 22',
                                 'lower_right': '46', 'lower_left': '31'})
        self.assertEqual(self.env['mrp.production']._lab_sheet_teeth(False)['upper_left'], '')

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
        self.assertEqual(rows['CERAMIC'], ['Opaque & Buildup', 'Trimming', 'Finishing'])
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
                         ['Special Tray', 'Wax Bite', 'Teeth Setting', 'Flasking', 'De Waxing',
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
        self.assertIn('Opaque &amp; Buildup', text)
        self.assertIn('Marketing Officer', text)

        html, _kind = Report._render_qweb_html(
            'lab_production_sheet.report_job_sheet', production.ids)
        text = html.decode()
        self.assertIn('TECHNICIAN JOB SHEET', text)
        self.assertIn(production.name, text)
        self.assertNotIn('Particulars', text, "no stage table on the technician's sheet")
        self.assertNotIn('Casting', text)
        self.assertIn('alt="Barcode %s"' % production.name, text,
                      "the barcode the station board reads")

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
