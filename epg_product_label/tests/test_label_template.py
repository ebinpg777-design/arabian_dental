# -*- coding: utf-8 -*-
import base64

from odoo.exceptions import ValidationError
from odoo.tests import tagged

from .common import LabelCase


@tagged('post_install', '-at_install')
class TestLabelTemplate(LabelCase):

    # ------------------------------------------------------------------
    # geometry
    # ------------------------------------------------------------------
    def test_roll_layout_is_one_label_per_page(self):
        template = self.make_template(layout_mode='roll',
                                      label_width=57.0, label_height=38.0)
        self.assertEqual((template.columns, template.rows), (1, 1))
        self.assertEqual(template.labels_per_page, 1)
        self.assertEqual((template.page_width, template.page_height), (57.0, 38.0))

    def test_roll_page_grows_with_margins(self):
        template = self.make_template(
            layout_mode='roll', label_width=50.0, label_height=25.0,
            margin_left=2.0, margin_right=2.0, margin_top=1.0, margin_bottom=1.0)
        self.assertEqual(template.page_width, 54.0)
        self.assertEqual(template.page_height, 27.0)

    def test_auto_grid_fits_the_page(self):
        template = self.make_template(
            layout_mode='sheet', page_format='A4', auto_grid=True,
            label_width=70.0, label_height=37.0,
            margin_top=0.0, margin_bottom=0.0, margin_left=0.0, margin_right=0.0,
            column_gap=0.0, row_gap=0.0)
        # A4 is 210 x 297: three 70mm columns exactly, eight 37mm rows (296mm).
        self.assertEqual((template.columns, template.rows), (3, 8))
        self.assertEqual(template.labels_per_page, 24)

    def test_auto_grid_accounts_for_gaps(self):
        template = self.make_template(
            layout_mode='sheet', page_format='A4', auto_grid=True,
            label_width=70.0, label_height=37.0,
            margin_top=0.0, margin_bottom=0.0, margin_left=0.0, margin_right=0.0,
            column_gap=5.0, row_gap=0.0)
        # 2 x 70 + 5 = 145 fits, 3 x 70 + 10 = 220 does not.
        self.assertEqual(template.columns, 2)

    def test_manual_grid_is_left_alone(self):
        template = self.make_template(
            layout_mode='sheet', page_format='Letter', auto_grid=False,
            label_width=101.0, label_height=50.0, columns=2, rows=5,
            margin_top=14.7, margin_bottom=14.7,
            margin_left=4.95, margin_right=4.95, column_gap=4.0)
        self.assertEqual((template.columns, template.rows), (2, 5))
        self.assertEqual(template.labels_per_page, 10)

    def test_label_larger_than_the_page_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.make_template(layout_mode='sheet', page_format='A4',
                               label_width=250.0, label_height=40.0)

    def test_inch_template_converts_to_millimetres(self):
        template = self.make_template(unit='in', label_width=4.0, label_height=2.0)
        self.assertAlmostEqual(template.paperformat_id.page_width, 102, delta=1)
        self.assertAlmostEqual(template.paperformat_id.page_height, 51, delta=1)

    # ------------------------------------------------------------------
    # paper format
    # ------------------------------------------------------------------
    def test_paperformat_is_created_and_kept_in_step(self):
        template = self.make_template(label_width=100.0, label_height=100.0)
        paperformat = template.paperformat_id
        self.assertTrue(paperformat)
        self.assertEqual((paperformat.page_width, paperformat.page_height), (100, 100))
        # Margins live in CSS; letting wkhtmltopdf add its own would double them.
        self.assertEqual(paperformat.margin_top, 0.0)
        self.assertTrue(paperformat.disable_shrinking)

        template.label_height = 150.0
        self.assertEqual(template.paperformat_id.page_height, 150)
        self.assertEqual(template.paperformat_id, paperformat,
                         "the template should reuse its paper format, not pile up new ones")

    def test_paperformat_is_removed_with_the_template(self):
        template = self.make_template()
        paperformat = template.paperformat_id
        template.unlink()
        self.assertFalse(paperformat.exists())

    def test_report_honours_the_template_paperformat(self):
        template = self.make_template(label_width=80.0, label_height=60.0)
        report = self.env.ref('epg_product_label.action_report_epg_label')
        resolved = report.with_context(
            epg_label_paperformat_id=template.paperformat_id.id).get_paperformat()
        self.assertEqual(resolved, template.paperformat_id)
        self.assertNotEqual(report.get_paperformat(), template.paperformat_id)

    # ------------------------------------------------------------------
    # rendering
    # ------------------------------------------------------------------
    def test_render_label_html_places_every_element(self):
        template = self.make_template()
        self.make_element(template, name='Name', element_type='field',
                          field_path='name', pos_x=2.0, pos_y=3.0)
        self.make_element(template, name='Code', element_type='field',
                          field_path='default_code', pos_x=2.0, pos_y=12.0)
        html = str(template._render_label_html(
            self.product, template._build_render_context()))
        self.assertIn('Label Test Widget', html)
        self.assertIn('LTW-001', html)
        self.assertIn('left:2mm', html)
        self.assertIn('top:3mm', html)
        self.assertIn('top:12mm', html)

    def test_pages_respect_the_grid_and_the_skip(self):
        template = self.make_template(
            layout_mode='sheet', page_format='A4', auto_grid=False,
            columns=2, rows=2, label_width=90.0, label_height=50.0,
            margin_left=5.0, margin_right=5.0, margin_top=5.0, margin_bottom=5.0,
            column_gap=5.0, row_gap=5.0)
        self.make_element(template, element_type='field', field_path='name')
        context = template._build_render_context()
        slots = [(self.product, context)] * 6

        pages = template._render_pages_html(slots, context, skip=0)
        self.assertEqual(len(pages), 2)
        self.assertEqual(pages[0].count('class="epg_label"'), 4)
        self.assertEqual(pages[1].count('class="epg_label"'), 2)

        # Skipping two positions pushes two labels onto a third page.
        pages = template._render_pages_html(slots, context, skip=2)
        self.assertEqual(len(pages), 2)
        self.assertEqual(pages[0].count('class="epg_label"'), 2)
        self.assertEqual(pages[1].count('class="epg_label"'), 4)

    def test_second_column_is_offset_by_width_and_gap(self):
        template = self.make_template(
            layout_mode='sheet', page_format='A4', auto_grid=False,
            columns=2, rows=1, label_width=90.0, label_height=50.0,
            margin_left=5.0, margin_top=5.0, column_gap=5.0, row_gap=0.0,
            margin_right=5.0, margin_bottom=5.0)
        self.make_element(template, element_type='field', field_path='name')
        context = template._build_render_context()
        page = template._render_pages_html([(self.product, context)] * 2, context)[0]
        self.assertIn('left:5mm', page)
        self.assertIn('left:100mm', page)  # 5 margin + 90 label + 5 gap

    def test_preview_survives_a_broken_element(self):
        template = self.make_template()
        self.make_element(template, element_type='field',
                          field_path='no_such_field_at_all')
        template.preview_product_id = self.product
        # A renamed or removed field must blank the element, not break the form.
        self.assertIn('epg_label', template.preview_html)
        self.assertNotIn('alert-warning', template.preview_html)

    # ------------------------------------------------------------------
    # ZPL
    # ------------------------------------------------------------------
    def test_zpl_document_structure(self):
        template = self.make_template(zpl_use_global=False, zpl_density='8')
        self.make_element(template, element_type='field', field_path='name')
        zpl = template._render_label_zpl(
            self.product, template._build_render_context())
        self.assertTrue(zpl.startswith('^XA'))
        self.assertTrue(zpl.rstrip().endswith('^XZ'))
        self.assertIn('^CI28', zpl)
        self.assertIn('^PW480', zpl)   # 60 mm at 8 dots/mm
        self.assertIn('^LL320', zpl)   # 40 mm at 8 dots/mm

    def test_zpl_density_scales_the_coordinates(self):
        template = self.make_template(zpl_use_global=False, zpl_density='12')
        self.make_element(template, element_type='field', field_path='name')
        zpl = template._render_label_zpl(
            self.product, template._build_render_context())
        # 60 mm at 11.8 dots/mm
        self.assertIn('^PW708', zpl)

    def test_zpl_prefix_and_suffix_are_included(self):
        template = self.make_template(zpl_prefix='^MMT', zpl_suffix='^PQ2')
        self.make_element(template, element_type='field', field_path='name')
        zpl = template._render_label_zpl(
            self.product, template._build_render_context())
        self.assertIn('^MMT', zpl)
        self.assertIn('^PQ2', zpl)

    def test_zpl_falls_back_to_the_global_settings(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'epg_product_label.zpl_density', '24')
        template = self.make_template(zpl_use_global=True)
        self.assertEqual(template._zpl_settings()['density'], '24')
        template.zpl_use_global = False
        template.zpl_density = '6'
        self.assertEqual(template._zpl_settings()['density'], '6')

    # ------------------------------------------------------------------
    # import / export
    # ------------------------------------------------------------------
    def test_xml_round_trip(self):
        template = self.make_template(name='Round Trip', code='rt-1',
                                      label_width=70.0, label_height=45.0)
        self.make_element(template, name='Title', element_type='text',
                          text_content='{{ name }}', font_size=11.0,
                          font_bold=True, text_align='center')
        self.make_element(template, name='Code', element_type='barcode',
                          barcode_type='EAN13', barcode_humanreadable=True)

        payload = self._export(template)
        imported = self.Template._import_from_xml(payload)

        self.assertNotEqual(imported, template)
        self.assertEqual(imported.label_width, 70.0)
        self.assertEqual(imported.label_height, 45.0)
        self.assertEqual(len(imported.element_ids), 2)
        title = imported.element_ids.filtered(lambda e: e.name == 'Title')
        self.assertEqual(title.text_content, '{{ name }}')
        self.assertEqual(title.font_size, 11.0)
        self.assertTrue(title.font_bold)
        self.assertEqual(title.text_align, 'center')
        barcode = imported.element_ids.filtered(lambda e: e.name == 'Code')
        self.assertEqual(barcode.barcode_type, 'EAN13')
        self.assertTrue(barcode.barcode_humanreadable)
        # An imported copy is never predefined, so it can be edited freely.
        self.assertFalse(imported.is_predefined)

    def test_importing_twice_does_not_clash_on_the_reference(self):
        template = self.make_template(name='Clash', code='clash-1')
        self.make_element(template, element_type='field', field_path='name')
        payload = self._export(template)
        first = self.Template._import_from_xml(payload)
        second = self.Template._import_from_xml(payload)
        self.assertNotEqual(first.code, second.code)
        self.assertNotEqual(first, second)

    def test_import_with_overwrite_replaces_the_elements(self):
        template = self.make_template(name='Overwrite', code='ow-1')
        self.make_element(template, name='One', element_type='field', field_path='name')
        payload = self._export(template)
        self.make_element(template, name='Two', element_type='field',
                          field_path='default_code')
        self.assertEqual(len(template.element_ids), 2)

        imported = self.Template._import_from_xml(payload, overwrite=True)
        self.assertEqual(imported, template)
        self.assertEqual(len(template.element_ids), 1)
        self.assertEqual(template.element_ids.name, 'One')

    def test_import_rejects_a_foreign_file(self):
        from odoo.exceptions import UserError
        with self.assertRaises(UserError):
            self.Template._import_from_xml(b'<odoo><record/></odoo>')
        with self.assertRaises(UserError):
            self.Template._import_from_xml(b'not xml at all')

    def _export(self, template):
        action = template.action_export_xml()
        attachment_id = int(action['url'].split('/web/content/')[1].split('?')[0])
        return base64.b64decode(self.env['ir.attachment'].browse(attachment_id).datas)

    # ------------------------------------------------------------------
    # copies and access
    # ------------------------------------------------------------------
    def test_copy_is_editable_and_loses_the_reference(self):
        predefined = self.env.ref('epg_product_label.label_product_57x38')
        copy = predefined.copy()
        self.assertFalse(copy.is_predefined)
        self.assertFalse(copy.code)
        self.assertEqual(len(copy.element_ids), len(predefined.element_ids))
        self.assertNotEqual(copy.paperformat_id, predefined.paperformat_id)

    def test_default_template_prefers_the_user_setting(self):
        chosen = self.make_template(name='My Default', label_type='product')
        self.env.user.epg_default_label_template_id = chosen
        self.assertEqual(self.Template._default_template('product'), chosen)
        # A default of the wrong type must not be handed to a partner wizard.
        self.assertNotEqual(self.Template._default_template('partner'), chosen)

    def test_nine_templates_ship_with_the_module(self):
        predefined = self.Template.search([('is_predefined', '=', True)])
        self.assertEqual(len(predefined), 9)
        self.assertEqual(
            len(predefined.filtered(lambda t: t.label_type == 'product')), 7)
        self.assertEqual(
            len(predefined.filtered(lambda t: t.label_type == 'partner')), 2)
        for template in predefined:
            self.assertTrue(template.element_ids,
                            "%s ships without any element" % template.name)
            self.assertTrue(template.paperformat_id,
                            "%s ships without a paper format" % template.name)

    # ------------------------------------------------------------------
    # review fixes 2026-09-15
    # ------------------------------------------------------------------
    def _user(self, login, group):
        from odoo.tests import new_test_user
        return new_test_user(self.env, login=login, groups='base.group_user,%s' % group)

    def test_a_designer_who_is_not_an_admin_can_resize_and_delete(self):
        designer = self._user('label_designer_x', 'epg_product_label.group_label_designer')
        template = self.make_template(label_width=50.0, label_height=30.0)
        paperformat = template.paperformat_id
        template.with_user(designer).write({'label_width': 70.0})
        self.assertEqual(paperformat.page_width, 70)
        template.with_user(designer).unlink()
        self.assertFalse(paperformat.exists())

    def test_designer_canvas_requires_the_designer_group(self):
        from odoo.exceptions import AccessError
        printer = self._user('label_printer_x', 'epg_product_label.group_label_user')
        template = self.make_template()
        with self.assertRaises(AccessError):
            self.Template.with_user(printer).render_designer(template.id)

    def test_designer_canvas_ignores_an_unsaved_condition(self):
        designer = self._user('label_designer_y', 'epg_product_label.group_label_designer')
        template = self.make_template()
        element = self.make_element(template, element_type='field', field_path='name')
        result = self.Template.with_user(designer).render_designer(
            template.id, None, {str(element.id): {
                'condition': "False", 'pos_x': 9.0}})
        html = result['elements'][element.id]
        self.assertTrue(html, "the unsaved condition must not be applied")
        self.assertIn('left:9', html, "a whitelisted unsaved value is still previewed")
