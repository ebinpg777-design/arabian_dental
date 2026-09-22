# -*- coding: utf-8 -*-
from odoo.exceptions import ValidationError
from odoo.tests import tagged

from ..models.label_zpl import ZplBuilder
from .common import LabelCase


@tagged('post_install', '-at_install')
class TestLabelElement(LabelCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.template = cls.make_template()
        cls.context = cls.template._build_render_context()

    def value(self, **values):
        return self.make_element(self.template, **values)._get_text_value(
            self.product, self.context)

    # ------------------------------------------------------------------
    # value resolution
    # ------------------------------------------------------------------
    def test_field_path_walks_relations(self):
        self.assertEqual(self.value(element_type='field', field_path='name'),
                         'Label Test Widget')
        self.assertEqual(self.value(element_type='field', field_path='uom_id.name'),
                         self.product.uom_id.name)

    def test_unknown_field_path_is_blank_not_an_error(self):
        self.assertEqual(self.value(element_type='field', field_path='nope'), '')
        self.assertEqual(self.value(element_type='field', field_path='categ_id.nope'), '')

    def test_fallback_is_used_when_the_value_is_empty(self):
        self.assertEqual(
            self.value(element_type='field', field_path='nope', default_value='n/a'),
            'n/a')

    def test_placeholders_are_substituted(self):
        self.assertEqual(
            self.value(element_type='text',
                       text_content='{{ default_code }} - {{ name }}'),
            'LTW-001 - Label Test Widget')

    def test_unknown_placeholder_collapses_to_nothing(self):
        self.assertEqual(
            self.value(element_type='text', text_content='[{{ missing }}]'), '[]')

    def test_prefix_suffix_and_truncation(self):
        self.assertEqual(
            self.value(element_type='field', field_path='name',
                       prefix='> ', suffix=' <'),
            '> Label Test Widget <')
        # The ellipsis counts towards the limit, so the result is never longer
        # than the box the designer sized for.
        truncated = self.value(element_type='field', field_path='name', max_length=8)
        self.assertEqual(truncated, 'Label T…')
        self.assertEqual(len(truncated), 8)

    def test_number_and_case_formats(self):
        self.assertEqual(
            self.value(element_type='field', field_path='list_price',
                       value_format='float', digits=1), '12.5')
        self.assertEqual(
            self.value(element_type='field', field_path='list_price',
                       value_format='integer'), '12')
        self.assertEqual(
            self.value(element_type='field', field_path='name',
                       value_format='upper'), 'LABEL TEST WIDGET')

    def test_date_element_defaults_to_the_print_date(self):
        from odoo import fields
        today = fields.Date.context_today(self.env['epg.label.element'])
        self.assertEqual(
            self.value(element_type='date', date_format='%Y-%m-%d'),
            today.strftime('%Y-%m-%d'))

    def test_address_puts_city_state_and_zip_on_one_line(self):
        element = self.make_element(
            self.template, element_type='address',
            address_parts='name,street,street2,city,zip,country')
        lines = element._get_text_value(self.partner, self.context).split('\n')
        self.assertEqual(lines[0], 'Rivera Tooling')
        self.assertEqual(lines[1], '12 Foundry Row')
        self.assertEqual(lines[2], 'Unit B')
        self.assertEqual(lines[3], 'Sheffield S1 2AB')

    def test_address_renders_line_breaks_in_html(self):
        element = self.make_element(self.template, element_type='address')
        html = str(element._render_html(self.partner, self.context))
        self.assertIn('<br/>', html)

    # ------------------------------------------------------------------
    # prices
    # ------------------------------------------------------------------
    def test_price_falls_back_to_the_sales_price(self):
        self.assertIn('12.50', self.value(element_type='price', price_base='pricelist'))

    def test_cost_price_source(self):
        self.assertIn('7.00', self.value(element_type='price',
                                         price_base='standard_price'))

    def test_currency_position_and_visibility(self):
        symbol = self.env.company.currency_id.symbol
        after = self.value(element_type='price', price_currency_position='after')
        self.assertTrue(after.endswith(symbol), after)
        bare = self.value(element_type='price', price_show_currency=False)
        self.assertEqual(bare, '12.50')

    def test_price_uses_the_pricelist_from_the_context(self):
        pricelist = self.env['product.pricelist'].create({
            'name': 'Label Test List',
            'item_ids': [(0, 0, {
                'applied_on': '3_global',
                'compute_price': 'percentage',
                'percent_price': 10.0,
            })],
        })
        context = self.template._build_render_context(pricelist=pricelist)
        element = self.make_element(self.template, element_type='price')
        self.assertIn('11.25', element._get_text_value(self.product, context))

    def test_promotional_price_and_difference(self):
        promo = self.env['product.pricelist'].create({
            'name': 'Label Test Promo',
            'item_ids': [(0, 0, {
                'applied_on': '3_global',
                'compute_price': 'percentage',
                'percent_price': 20.0,
            })],
        })
        context = self.template._build_render_context(promo_pricelist=promo)
        promo_element = self.make_element(self.template, element_type='price_promo')
        self.assertIn('10.00', promo_element._get_text_value(self.product, context))

        diff = self.make_element(self.template, element_type='price_diff',
                                 price_diff_mode='amount')
        self.assertIn('2.50', diff._get_text_value(self.product, context))

        percent = self.make_element(self.template, element_type='price_diff',
                                    price_diff_mode='percent', digits=0)
        self.assertEqual(percent._get_text_value(self.product, context), '-20%')

    def test_price_difference_is_blank_when_there_is_no_saving(self):
        element = self.make_element(self.template, element_type='price_diff')
        self.assertEqual(element._get_text_value(self.product, self.context), '')

    def test_price_per_unit_multiplies_by_the_quantity(self):
        element = self.make_element(self.template, element_type='price_uom',
                                    price_uom_qty=10.0, price_uom_label='/ 10 pcs')
        self.assertIn('125.00', element._get_text_value(self.product, self.context))

    def test_tax_included_price(self):
        if 'account.tax' not in self.env:
            self.skipTest("accounting is not installed")
        tax = self.env['account.tax'].create({
            'name': 'Label Test 10%',
            'amount': 10.0,
            'amount_type': 'percent',
            'type_tax_use': 'sale',
        })
        self.product.taxes_id = tax
        element = self.make_element(self.template, element_type='price',
                                    price_tax='included')
        self.assertIn('13.75', element._get_text_value(self.product, self.context))

    # ------------------------------------------------------------------
    # barcodes
    # ------------------------------------------------------------------
    def test_barcode_sources(self):
        element = self.make_element(self.template, element_type='barcode')
        self.assertEqual(
            element._get_barcode_value(self.product, self.context), '5901234123457')
        element.barcode_source = 'default_code'
        self.assertEqual(
            element._get_barcode_value(self.product, self.context), 'LTW-001')
        element.barcode_source = 'static'
        element.barcode_value = 'FIXED-1'
        self.assertEqual(
            element._get_barcode_value(self.product, self.context), 'FIXED-1')
        element.barcode_source = 'text'
        element.text_content = 'https://shop.example.com/{{ default_code }}'
        self.assertEqual(element._get_barcode_value(self.product, self.context),
                         'https://shop.example.com/LTW-001')

    def test_barcode_renders_as_an_embedded_png(self):
        element = self.make_element(self.template, element_type='barcode',
                                    barcode_type='EAN13', width=40.0, height=15.0)
        html = str(element._render_html(self.product, self.context))
        self.assertIn('data:image/png;base64,', html)
        # The payload belongs in the image, not in the alt text.
        self.assertNotIn('alt="5901234123457"', html)

    def test_transparent_barcode_produces_an_rgba_png(self):
        from PIL import Image
        import base64
        import io
        element = self.make_element(self.template, element_type='barcode',
                                    barcode_transparent=True)
        html = str(element._render_html(self.product, self.context))
        payload = html.split('base64,')[1].split('"')[0]
        image = Image.open(io.BytesIO(base64.b64decode(payload)))
        self.assertEqual(image.mode, 'RGBA')
        self.assertEqual(image.getpixel((0, 0))[3], 0, "corner should be transparent")

    def test_vcard_payload_is_well_formed(self):
        element = self.make_element(self.template, element_type='vcard')
        vcard = element._get_vcard_value(self.partner, self.context)
        self.assertTrue(vcard.startswith('BEGIN:VCARD'))
        self.assertTrue(vcard.endswith('END:VCARD'))
        self.assertIn('FN:Rivera Tooling', vcard)
        self.assertIn('EMAIL;TYPE=INTERNET:orders@example.com', vcard)
        self.assertIn('12 Foundry Row Unit B', vcard)

    def test_vcard_never_leaks_into_the_html(self):
        element = self.make_element(self.template, element_type='vcard',
                                    width=20.0, height=20.0)
        html = str(element._render_html(self.partner, self.context))
        self.assertNotIn('BEGIN:VCARD', html)
        self.assertIn('data:image/png;base64,', html)

    # ------------------------------------------------------------------
    # visibility
    # ------------------------------------------------------------------
    def test_condition_controls_visibility(self):
        element = self.make_element(self.template, element_type='field',
                                    field_path='name', condition='object.barcode')
        self.assertTrue(element._is_visible(self.product, self.context))
        element.condition = 'not object.barcode'
        self.assertFalse(element._is_visible(self.product, self.context))

    def test_a_failing_condition_hides_the_element(self):
        element = self.make_element(self.template, element_type='field',
                                    field_path='name',
                                    condition='object.does_not_exist.at_all')
        self.assertFalse(element._is_visible(self.product, self.context))

    def test_invalid_condition_syntax_is_refused_up_front(self):
        with self.assertRaises(ValidationError):
            self.make_element(self.template, condition='object.name ==')

    def test_inactive_element_is_not_rendered(self):
        element = self.make_element(self.template, element_type='field',
                                    field_path='name', active=False)
        self.assertEqual(str(element._render_html(self.product, self.context)), '')

    # ------------------------------------------------------------------
    # styling
    # ------------------------------------------------------------------
    def test_style_reaches_the_html(self):
        element = self.make_element(
            self.template, element_type='text', text_content='Hi',
            font_size=12.0, font_bold=True, font_italic=True, text_align='center',
            text_color='#FF0000', bg_transparent=False, bg_color='#EEEEEE',
            border_width=0.5, rotation='90', letter_spacing=0.3)
        html = str(element._render_html(self.product, self.context))
        self.assertIn('font-size:12pt', html)
        self.assertIn('font-weight:bold', html)
        self.assertIn('font-style:italic', html)
        self.assertIn('text-align:center', html)
        self.assertIn('color:#FF0000', html)
        self.assertIn('background-color:#EEEEEE', html)
        self.assertIn('border:0.5mm solid', html)
        self.assertIn('rotate(90deg)', html)
        self.assertIn('letter-spacing:0.3mm', html)

    def test_strikethrough_price(self):
        element = self.make_element(self.template, element_type='price',
                                    price_strikethrough=True)
        self.assertIn('line-through',
                      str(element._render_html(self.product, self.context)))

    def test_inch_template_emits_inch_units(self):
        template = self.make_template(unit='in', label_width=4.0, label_height=2.0)
        element = self.make_element(template, element_type='text',
                                    text_content='x', pos_x=0.25, width=1.5)
        html = str(element._render_html(self.product,
                                        template._build_render_context()))
        self.assertIn('left:0.25in', html)
        self.assertIn('width:1.5in', html)

    # ------------------------------------------------------------------
    # ZPL emission
    # ------------------------------------------------------------------
    def test_zpl_escapes_control_characters(self):
        builder = ZplBuilder(50, 30)
        self.assertEqual(builder.escape('a^b~c\\d'), 'a_5Eb_7Ec_5Cd')

    def test_zpl_hex_escapes_non_ascii(self):
        builder = ZplBuilder(50, 30, encoding='utf8')
        # é is C3 A9 in UTF-8.
        self.assertEqual(builder.escape('café'), 'caf_C3_A9')

    def test_zpl_ascii_encoding_degrades_gracefully(self):
        builder = ZplBuilder(50, 30, encoding='ascii')
        self.assertEqual(builder.escape('café'), 'caf_3F')

    def test_zpl_symbology_guessing_matches_odoo(self):
        self.assertEqual(ZplBuilder._resolve_symbology('auto', '5901234123457'), 'EAN13')
        self.assertEqual(ZplBuilder._resolve_symbology('auto', '12345678'), 'EAN8')
        self.assertEqual(ZplBuilder._resolve_symbology('auto', 'ABC-123'), 'Code128')
        self.assertEqual(ZplBuilder._resolve_symbology('Code39', '5901234123457'),
                         'Code39')

    def test_zpl_text_element(self):
        template = self.make_template(zpl_use_global=False, zpl_density='8')
        self.make_element(template, element_type='field', field_path='name',
                          pos_x=2.0, pos_y=3.0, font_size=10.0)
        zpl = template._render_label_zpl(self.product,
                                         template._build_render_context())
        self.assertIn('^FO16,24', zpl)          # 2 mm and 3 mm at 8 dots/mm
        self.assertIn('Label Test Widget', zpl)

    def test_zpl_barcode_element(self):
        template = self.make_template()
        self.make_element(template, element_type='barcode', barcode_type='EAN13',
                          pos_x=5.0, pos_y=10.0, width=40.0, height=15.0)
        zpl = template._render_label_zpl(self.product,
                                         template._build_render_context())
        self.assertIn('^BE', zpl)
        self.assertIn('5901234123457', zpl)

    def test_zpl_qr_element(self):
        template = self.make_template()
        self.make_element(template, element_type='qrcode', width=20.0, height=20.0,
                          qr_error_correction='H')
        zpl = template._render_label_zpl(self.product,
                                         template._build_render_context())
        self.assertIn('^BQ', zpl)
        # ^FD<ecc>A,<data> - the H is the requested error-correction level.
        self.assertIn('^FDHA,', zpl)

    def test_zpl_shapes(self):
        template = self.make_template()
        self.make_element(template, element_type='box', pos_x=1.0, pos_y=1.0,
                          width=20.0, height=10.0, border_width=0.5)
        self.make_element(template, element_type='line', pos_x=1.0, pos_y=15.0,
                          width=30.0, height=0.3)
        self.make_element(template, element_type='ellipse', pos_x=40.0, pos_y=1.0,
                          width=10.0, height=10.0)
        zpl = template._render_label_zpl(self.product,
                                         template._build_render_context())
        self.assertIn('^GB', zpl)
        self.assertIn('^GE', zpl)

    def test_zpl_skips_a_hidden_element(self):
        template = self.make_template()
        self.make_element(template, element_type='field', field_path='name',
                          condition='not object.barcode')
        zpl = template._render_label_zpl(self.product,
                                         template._build_render_context())
        self.assertNotIn('Label Test Widget', zpl)

    # ------------------------------------------------------------------
    # review fixes 2026-09-15
    # ------------------------------------------------------------------
    def test_zpl_escapes_the_hex_indicator_itself(self):
        builder = ZplBuilder(50, 30)
        # ^FH makes '_' the hex indicator: "LOT_42" must not become LOT + byte 0x42.
        self.assertEqual(builder.escape('LOT_42'), 'LOT_5F42')

    def test_zpl_keeps_control_characters_as_hex(self):
        builder = ZplBuilder(50, 30)
        self.assertEqual(builder.escape('a\nb\tc'), 'a_0Ab_09c')

    def test_zpl_vcard_keeps_its_line_breaks(self):
        template = self.make_template(label_type='partner')
        self.make_element(template, element_type='vcard', width=20.0, height=20.0)
        zpl = template._render_label_zpl(self.partner, template._build_render_context())
        self.assertIn('BEGIN:VCARD_0AVERSION:3.0', zpl)

    def test_zpl_text_breaks_lines_with_the_field_block_marker(self):
        builder = ZplBuilder(50, 30)
        builder.text(0, 0, '12 Foundry Row\nSheffield', width_mm=40)
        self.assertIn('^FD12 Foundry Row\\&Sheffield^FS', builder.render())
        single = ZplBuilder(50, 30)
        single.text(0, 0, 'one\ntwo')
        self.assertIn('^FDone two^FS', single.render())

    def test_zpl_filled_box_keeps_its_shape(self):
        builder = ZplBuilder(50, 30, density='8')
        builder.box(0, 0, 20, 10, fill=True)
        # 160 x 80 dots, filled by an 80-dot border - not grown into a 160 x 160 square
        self.assertIn('^GB160,80,80,B,0^FS', builder.render())

    def test_condition_cannot_reach_the_orm(self):
        attempts = [
            "env['res.users'].sudo().browse(1).write({'login': 'pwned'})",
            "object.env['res.users'].sudo().browse(1).write({'login': 'pwned'})",
            "user.sudo()",
            "object.sudo()",
            "object._RecordView__record.env",
            "company.partner_id.env",
            "ctx['template'].env",
        ]
        for condition in attempts:
            element = self.make_element(self.template, element_type='field',
                                        field_path='name', condition=condition)
            with self.assertLogs('odoo.addons.epg_product_label.models.label_element', 'WARNING'):
                self.assertFalse(element._is_visible(self.product, self.context), condition)
        self.assertNotEqual(self.env['res.users'].browse(1).login, 'pwned')

    def test_condition_cannot_format_its_way_past_the_view(self):
        sneaky = "'{0._RecordView__record.env}'.format(object)"
        with self.assertRaises(ValidationError):
            self.make_element(self.template, condition=sneaky)
        # a row stored before the constraint existed is refused at print time
        element = self.make_element(self.template, element_type='field', field_path='name')
        self.env.cr.execute("UPDATE epg_label_element SET condition = %s WHERE id = %s",
                            [sneaky, element.id])
        element.invalidate_recordset(['condition'])
        with self.assertLogs('odoo.addons.epg_product_label.models.label_element', 'WARNING'):
            self.assertFalse(element._is_visible(self.product, self.context))

    def test_condition_still_reads_fields_and_relations(self):
        self.product.categ_id.name = 'Widgets'
        for condition, expected in [
                ("object.default_code == 'LTW-001'", True),
                ("object.categ_id.name == 'Widgets'", True),
                ("not object.company_id", True),
                ("len(object.product_tag_ids) == 0", True),
                ("object.list_price > 100", False),
                ("company.name", True)]:
            element = self.make_element(self.template, element_type='field',
                                        field_path='name', condition=condition)
            self.assertEqual(element._is_visible(self.product, self.context), expected, condition)

    def test_html_element_escapes_record_values(self):
        self.product.name = '<img src=x onerror=alert(1)>'
        element = self.make_element(self.template, element_type='html',
                                    html_content='<b>{{ name }}</b>')
        html = str(element._render_html(self.product, self.context))
        self.assertIn('<b>&lt;img src=x onerror=alert(1)&gt;</b>', html)
