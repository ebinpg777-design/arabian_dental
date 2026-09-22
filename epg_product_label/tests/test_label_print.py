# -*- coding: utf-8 -*-
from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import LabelCase


@tagged('post_install', '-at_install')
class TestLabelPrint(LabelCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.sheet = cls.make_template(
            name='Print Sheet', layout_mode='sheet', page_format='A4',
            auto_grid=False, columns=2, rows=3,
            label_width=90.0, label_height=50.0,
            margin_left=5.0, margin_right=5.0, margin_top=5.0, margin_bottom=5.0,
            column_gap=5.0, row_gap=5.0, output_type='both')
        cls.make_element(cls.sheet, name='Name', element_type='field',
                         field_path='name')
        cls.make_element(cls.sheet, name='Barcode', element_type='barcode',
                         pos_y=20.0, width=50.0, height=15.0)
        cls.report = cls.env.ref('epg_product_label.action_report_epg_label')

    def wizard(self, **values):
        base = {
            'template_id': self.sheet.id,
            'label_type': 'product',
            'product_ids': [(6, 0, self.product.ids)],
        }
        base.update(values)
        return self.Print.create(base)

    def render_html(self, wizard):
        html, _report_type = self.report._render_qweb_html(
            'epg_product_label.report_label', wizard.ids,
            data={'wizard_id': wizard.id})
        return html.decode() if isinstance(html, bytes) else html

    # ------------------------------------------------------------------
    # counting
    # ------------------------------------------------------------------
    def test_copies_and_page_count(self):
        wizard = self.wizard(quantity=7)
        self.assertEqual(wizard.record_count, 1)
        self.assertEqual(wizard.label_count, 7)
        self.assertEqual(wizard.page_count, 2)   # 6 per page

    def test_skip_pushes_the_run_onto_another_page(self):
        wizard = self.wizard(quantity=6, skip_labels=3)
        self.assertEqual(wizard.label_count, 6)
        self.assertEqual(wizard.page_count, 2)

    def test_per_record_quantities(self):
        other = self.env['product.product'].create({
            'name': 'Second Widget', 'barcode': '4006381333931'})
        wizard = self.wizard(product_ids=[(6, 0, (self.product | other).ids)],
                             quantity_source='per_record')
        wizard._onchange_quantity_source()
        self.assertEqual(len(wizard.line_ids), 2)
        wizard.line_ids[0].quantity = 4
        wizard.line_ids[1].quantity = 2
        self.assertEqual(wizard.label_count, 6)
        self.assertEqual(len(wizard._collect_slots()), 6)

    def test_zero_copies_is_refused(self):
        wizard = self.wizard(quantity=0)
        with self.assertRaises(UserError):
            wizard._collect_slots()

    def test_empty_selection_is_refused(self):
        wizard = self.wizard(product_ids=[(5, 0, 0)], quantity=1)
        with self.assertRaises(UserError):
            wizard._collect_slots()

    def test_a_template_without_elements_is_refused(self):
        empty = self.make_template(name='Empty')
        wizard = self.wizard(template_id=empty.id)
        with self.assertRaises(UserError):
            wizard.action_print()

    # ------------------------------------------------------------------
    # warnings
    # ------------------------------------------------------------------
    def test_wrong_template_type_warns(self):
        partner_template = self.make_template(name='Partner', label_type='partner')
        wizard = self.wizard(template_id=partner_template.id)
        self.assertIn('not made for this kind of record', wizard.warning)

    def test_zpl_on_a_pdf_only_template_warns_and_refuses(self):
        pdf_only = self.make_template(name='PDF only', output_type='pdf')
        self.make_element(pdf_only, element_type='field', field_path='name')
        wizard = self.wizard(template_id=pdf_only.id, output_format='zpl')
        self.assertIn('PDF output only', wizard.warning)
        with self.assertRaises(UserError):
            wizard.action_print()

    def test_skip_on_a_roll_template_warns(self):
        roll = self.make_template(name='Roll', layout_mode='roll')
        self.make_element(roll, element_type='field', field_path='name')
        wizard = self.wizard(template_id=roll.id, skip_labels=2)
        self.assertIn('no effect', wizard.warning)

    # ------------------------------------------------------------------
    # selection pick-up
    # ------------------------------------------------------------------
    def test_default_get_picks_up_the_list_selection(self):
        wizard = self.Print.with_context(
            active_model='product.product',
            active_ids=self.product.ids).create({'template_id': self.sheet.id})
        self.assertEqual(wizard.product_ids, self.product)
        self.assertEqual(wizard.label_type, 'product')

    def test_partner_selection_switches_the_label_type(self):
        wizard = self.Print.with_context(
            active_model='res.partner',
            active_ids=self.partner.ids).create({})
        self.assertEqual(wizard.label_type, 'partner')
        self.assertEqual(wizard.partner_ids, self.partner)

    def test_product_action_opens_the_wizard(self):
        action = self.product.action_epg_print_labels()
        self.assertEqual(action['res_model'], 'epg.label.print')
        self.assertEqual(action['context']['default_product_ids'], self.product.ids)

    def test_standard_wizard_is_replaced_only_when_configured(self):
        parameters = self.env['ir.config_parameter'].sudo()
        parameters.set_param('epg_product_label.replace_standard', 'False')
        self.assertEqual(
            self.product.action_open_label_layout()['res_model'],
            'product.label.layout')
        parameters.set_param('epg_product_label.replace_standard', 'True')
        self.assertEqual(
            self.product.action_open_label_layout()['res_model'], 'epg.label.print')

    # ------------------------------------------------------------------
    # rendering through the report
    # ------------------------------------------------------------------
    def test_report_html_contains_every_label(self):
        wizard = self.wizard(quantity=4)
        html = self.render_html(wizard)
        self.assertEqual(html.count('class="epg_label"'), 4)
        self.assertEqual(html.count('Label Test Widget'), 4)

    def test_report_html_lays_out_the_grid(self):
        wizard = self.wizard(quantity=4)
        html = self.render_html(wizard)
        self.assertIn('left:5mm', html)
        self.assertIn('left:100mm', html)   # 5 + 90 + 5
        self.assertIn('top:60mm', html)     # 5 + 50 + 5

    def test_report_refuses_a_stale_wizard(self):
        wizard = self.wizard(quantity=1)
        wizard_id = wizard.id
        wizard.unlink()
        with self.assertRaises(UserError):
            self.report._render_qweb_html(
                'epg_product_label.report_label', [wizard_id],
                data={'wizard_id': wizard_id})

    def test_pdf_action_carries_the_paperformat(self):
        wizard = self.wizard(quantity=1)
        action = wizard.action_print()
        self.assertEqual(action['type'], 'ir.actions.report')
        self.assertEqual(action['context']['epg_label_paperformat_id'],
                         self.sheet.paperformat_id.id)
        self.assertTrue(action['close_on_report_download'])

    # ------------------------------------------------------------------
    # ZPL output
    # ------------------------------------------------------------------
    def test_build_zpl_produces_one_document_per_label(self):
        wizard = self.wizard(quantity=3, output_format='zpl')
        wizard.action_build_zpl()
        self.assertEqual(wizard.zpl_text.count('^XA'), 3)
        self.assertEqual(wizard.zpl_text.count('^XZ'), 3)
        self.assertTrue(wizard.zpl_data)
        self.assertTrue(wizard.zpl_filename.endswith('.zpl'))

    def test_download_url_points_at_the_wizard(self):
        wizard = self.wizard(quantity=1, output_format='zpl')
        action = wizard.action_download_zpl()
        self.assertEqual(action['type'], 'ir.actions.act_url')
        self.assertIn('/web/content/epg.label.print/%s/zpl_data/' % wizard.id,
                      action['url'])

    def test_direct_print_without_an_endpoint_is_refused(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'epg_product_label.zpl_printer_url', '')
        wizard = self.wizard(quantity=1, output_format='zpl', printer_url=False)
        with self.assertRaises(UserError):
            wizard.action_direct_print()

    # ------------------------------------------------------------------
    # partner and lot labels
    # ------------------------------------------------------------------
    def test_partner_label_renders(self):
        template = self.make_template(name='Addresses', label_type='partner',
                                      label_width=100.0, label_height=35.0)
        self.make_element(template, element_type='address', width=60.0, height=25.0)
        self.make_element(template, element_type='vcard', pos_x=75.0,
                          width=20.0, height=20.0)
        wizard = self.Print.create({
            'template_id': template.id,
            'label_type': 'partner',
            'partner_ids': [(6, 0, self.partner.ids)],
        })
        html = self.render_html(wizard)
        self.assertIn('Rivera Tooling', html)
        self.assertIn('data:image/png;base64,', html)
        self.assertNotIn('BEGIN:VCARD', html)

    def test_lot_label_renders(self):
        lot = self.env['stock.lot'].create({
            'name': 'LOT-0001', 'product_id': self.product.id})
        template = self.make_template(name='Lots', label_type='lot',
                                      label_width=50.0, label_height=25.0)
        self.make_element(template, element_type='field', field_path='name')
        self.make_element(template, element_type='field',
                          field_path='product_id.name', pos_y=8.0)
        self.make_element(template, element_type='barcode', barcode_source='name',
                          pos_y=14.0, width=45.0, height=10.0)
        wizard = self.Print.create({
            'template_id': template.id,
            'label_type': 'lot',
            'lot_ids': [(6, 0, lot.ids)],
        })
        html = self.render_html(wizard)
        self.assertIn('LOT-0001', html)
        self.assertIn('Label Test Widget', html)
