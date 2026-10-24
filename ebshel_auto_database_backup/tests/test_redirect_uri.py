from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestGoogleRedirectUri(TransactionCase):
    """Google refused Setup Token with "Error 400: redirect_uri_mismatch": the
    redirect URI was built on an http base URL (Cloudflare reaches Odoo over
    plain http), which Google never accepts outside localhost."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with patch('odoo.addons.ebshel_auto_database_backup.models.db_backup_configure.odoo_db.verify_admin_password'):
            cls.config = cls.env['db.backup.configure'].create({
                'name': 'Drive', 'db_name': cls.env.cr.dbname, 'master_pwd': 'x',
                'backup_destination': 'google_drive', 'gdrive_client_key': '1234567890-abc123.apps.googleusercontent.com',
                'gdrive_client_secret': 'secret', 'google_drive_folder_key': 'folder',
            })

    def _with_base_url(self, url):
        self.env['ir.config_parameter'].sudo().set_str('web.base.url', url)
        self.config.invalidate_recordset(['gdrive_redirect_uri', 'is_redirect_uri_insecure'])

    def test_an_http_base_url_is_flagged(self):
        self._with_base_url('http://erp.example.com')
        self.assertTrue(self.config.is_redirect_uri_insecure)
        self._with_base_url('https://erp.example.com')
        self.assertFalse(self.config.is_redirect_uri_insecure)
        self.assertEqual(self.config.gdrive_redirect_uri, 'https://erp.example.com/google_drive/authentication')
        self._with_base_url('http://localhost:8069')
        self.assertFalse(self.config.is_redirect_uri_insecure, "Google accepts http for localhost")

    def test_the_consent_screen_returns_a_refresh_token(self):
        self._with_base_url('https://erp.example.com')
        query = parse_qs(urlparse(self.config.action_get_gdrive_auth_code()['url']).query)
        self.assertEqual(query['redirect_uri'], ['https://erp.example.com/google_drive/authentication'])
        self.assertEqual(query['prompt'], ['consent'])
        self.assertEqual(query['access_type'], ['offline'])
        self.assertNotIn('approval_prompt', query)
