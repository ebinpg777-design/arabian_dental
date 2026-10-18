# -*- coding: utf-8 -*-
import gzip

from odoo.tests import HttpCase, tagged


@tagged('post_install', '-at_install')
class TestAssetGzip(HttpCase):

    def _bundle_url(self, ext):
        """The address the web client asks for its main bundle."""
        bundle = self.env['ir.qweb']._get_asset_bundle('web.assets_web', css=ext == 'css', js=ext == 'js')
        (bundle.js() if ext == 'js' else bundle.css())
        return bundle.get_link(ext)

    def _raw(self, url, **headers):
        """The bytes as sent, not decoded on the way in."""
        return self.opener.get(self.base_url() + url, headers=headers, stream=True, timeout=60)

    def test_the_bundles_come_compressed_and_unchanged(self):
        for ext in ('js', 'css'):
            with self.subTest(ext=ext):
                url = self._bundle_url(ext)
                plain = self.url_open(url, headers={'Accept-Encoding': 'identity'})
                self.assertEqual(plain.status_code, 200)
                self.assertNotIn('Content-Encoding', plain.headers, "a browser that says no gets it plain")
                packed = self._raw(url, **{'Accept-Encoding': 'gzip, deflate, br'})
                self.assertEqual(packed.status_code, 200)
                self.assertEqual(packed.headers.get('Content-Encoding'), 'gzip')
                self.assertEqual(packed.headers.get('Vary'), 'Accept-Encoding')
                body = packed.raw.read()
                self.assertLess(len(body), len(plain.content) / 2, "at least halved")
                self.assertEqual(gzip.decompress(body), plain.content, "the same bytes once unpacked")
                self.assertIn('immutable', packed.headers.get('Cache-Control', ''), "cached as before")
                self.assertEqual(packed.headers.get('Content-Type'), plain.headers.get('Content-Type'))

    def test_a_revalidation_is_still_not_modified(self):
        url = self._bundle_url('js')
        first = self.url_open(url, headers={'Accept-Encoding': 'gzip'})
        etag = first.headers.get('ETag')
        self.assertTrue(etag)
        again = self.url_open(url, headers={'Accept-Encoding': 'gzip', 'If-None-Match': etag})
        self.assertEqual(again.status_code, 304)
