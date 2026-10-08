from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestBackendTheme(TransactionCase):

    def _css(self, bundle):
        assets = self.env['ir.qweb']._get_asset_bundle(bundle, js=False)
        css = assets.preprocess_css()
        self.assertFalse(assets.css_errors, f"{bundle} does not compile: {assets.css_errors}")
        return ''.join(css.lower().split())

    def test_backend_styles_compile_with_the_brand_colours(self):
        css = self._css('web.assets_backend')
        for selector in ('.o_home_greeting', '.o_arrow_button_current', '.o_form_sheet_bg'):
            self.assertIn(selector, css)
        # the lab's red is an accent: the navbar line and the open tab, never a button
        self.assertIn('inset0-2px0#ed1c24', css)
        self.assertIn('inset0-3px0#ed1c24', css)
        primary = css[css.index('.o_web_client.btn-primary{'):][:900]
        self.assertNotIn('#ed1c24', primary)
        self.assertIn('linear-gradient(135deg', primary)
        # WhatsApp buttons, found by their icon, in WhatsApp's gradient
        self.assertIn('[data-icon="oi_whatsapp"]', css)
        self.assertIn('>i.fa-whatsapp', css)
        self.assertIn('linear-gradient(135deg,#2fd8750%,#17a86250%,#0b7f68100%)', css)

    def test_dark_mode_compiles_its_own_cards(self):
        """backend.scss is written in Odoo's colour variables, so the dark bundle draws
        the form canvas dark rather than carrying the light one over."""
        light = self._css('web.assets_web')
        dark = self._css('web.assets_web_dark')

        def canvas(css):
            rule = css[css.rindex('.o_form_view.o_form_sheet_bg{'):]
            return rule[:rule.index('}')]
        self.assertNotEqual(canvas(light), canvas(dark))

    def test_no_reference_to_the_old_module_name(self):
        for bundle in ('web.assets_backend', 'web.assets_web_dark'):
            self.assertNotIn('zxs_entp_theme', self._css(bundle))
        assets = self.env['ir.qweb']._get_asset_bundle('web.assets_backend', css=False)
        self.assertFalse([f for f in assets.files if 'zxs_entp_theme' in f['url']])
