# Part of ebshel_whatsapp_password_reset. See LICENSE file for full copyright and licensing details.
"""A reset link opened in a browser that keeps no cookie. (client, 2026-11-02)

A sales executive opened the WhatsApp link in his phone's browser, typed a new
password and got "400: Bad Request - Session expired (invalid CSRF token)" - three
links in a row: his browser came back with a different session on every request.
"""
import re
from urllib.parse import urlsplit

from odoo.tests import HttpCase, new_test_user, tagged

TOKEN = re.compile(r'name="csrf_token" value="([^"]+)"')
LOGIN, OLD, CHOSEN = 'link_user', 'link-user-pw-1', 'link-user-pw-2'


@tagged('post_install', '-at_install')
class TestLinkWithoutCookies(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(cls.env, login=LOGIN, password=OLD, groups='base.group_user')

    def _link(self):
        link = urlsplit(self.user.sudo().get_reset_password_link())
        token = dict(part.split('=', 1) for part in link.query.split('&'))['token']
        return '%s?%s' % (link.path, link.query), token

    def _signs_in_with(self, password):
        try:
            self.authenticate(LOGIN, password)
        except Exception:  # noqa: BLE001 - any refusal is a "no"
            return False
        return True

    def test_a_new_password_is_set_without_the_session_the_page_was_given(self):
        path, token = self._link()
        page = self.url_open(path)
        self.assertEqual(page.status_code, 200)
        form_token = TOKEN.search(page.text).group(1)
        self.opener.cookies.clear()  # the browser kept nothing
        answer = self.url_open(path, data={
            'csrf_token': form_token, 'token': token, 'login': LOGIN,
            'password': CHOSEN, 'confirm_password': CHOSEN}, allow_redirects=False)
        self.assertEqual(answer.status_code, 200, answer.text[:300])
        self.assertIn('reset successfully', answer.text)
        self.assertTrue(self._signs_in_with(CHOSEN))

    def test_the_link_alone_is_not_enough_without_its_token(self):
        path, token = self._link()
        answer = self.url_open(path.replace(token, 'not-the-token'), data={
            'token': 'not-the-token', 'login': LOGIN,
            'password': CHOSEN, 'confirm_password': CHOSEN}, allow_redirects=False)
        self.assertNotIn('reset successfully', answer.text)
        self.assertTrue(self._signs_in_with(OLD), "the password did not change")

    def test_asking_for_a_reset_by_login_still_needs_the_form_token(self):
        self.env['ir.config_parameter'].sudo().set_bool('auth_signup.reset_password', True)
        answer = self.url_open('/web/reset_password', data={'login': LOGIN}, allow_redirects=False)
        self.assertEqual(answer.status_code, 400)
