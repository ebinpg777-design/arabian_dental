# -*- coding: utf-8 -*-
"""Signing in after a session has gone stale. (client, 2026-09-30)

An administrator reset a sales executive's password; his phone then answered every
attempt to sign in with "400: Bad Request - Session expired (invalid CSRF token)".
The reset had made his open session stale, and the sign-in form rendered while that
session was being thrown away carried a token for the id being thrown away.
"""
import re

from odoo.tests import HttpCase, new_test_user, tagged

TOKEN = re.compile(r'name="csrf_token" value="([^"]+)"')
LOGIN, OLD, NEW = 'stale_session_user', 'stale-session-pw-1', 'stale-session-pw-2'


@tagged('post_install', '-at_install')
class TestSignInAfterStaleSession(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(cls.env, login=LOGIN, password=OLD, groups='base.group_user')

    def _token(self, page):
        found = TOKEN.search(page.text)
        self.assertTrue(found, "the sign-in page carries a token")
        return found.group(1)

    def _sign_in(self, page, password, **extra):
        return self.url_open('/web/login', data=dict(
            {'csrf_token': self._token(page), 'login': LOGIN, 'password': password}, **extra),
            allow_redirects=False)

    def test_a_password_reset_does_not_break_the_next_sign_in(self):
        self.authenticate(LOGIN, OLD)
        # what an administrator resetting the password does to every open session
        self.user.password = NEW
        self.env.flush_all()
        page = self.url_open('/web/login')
        self.assertEqual(page.status_code, 200)
        answer = self._sign_in(page, NEW)
        self.assertEqual(answer.status_code, 303, "signed in, not refused: %s" % answer.text[:200])
        self.assertNotIn('/web/login', answer.headers.get('Location', ''),
                         "and on to the app, not back to the sign-in page")

    def test_a_form_with_a_dead_token_is_answered_with_a_fresh_one(self):
        """Back on a phone, or a tab left open overnight: the form in hand is dead."""
        page = self.url_open('/web/login')
        dead = self.url_open('/web/login', data={
            'csrf_token': 'dead' + self._token(page)[4:], 'login': LOGIN, 'password': OLD,
            'redirect': '/odoo/action-1'}, allow_redirects=False)
        self.assertEqual(dead.status_code, 303, "not a 400 page")
        where = dead.headers.get('Location', '')
        self.assertIn('/web/login?', where)
        self.assertIn('expired=1', where)
        self.assertIn('redirect=%2Fodoo%2Faction-1', where, "and they still end up where they were going")
        fresh = self.url_open(where)
        self.assertIn('had expired', fresh.text, "told why, in a sentence")
        # nobody was signed in by the refused form; the fresh one works
        answer = self._sign_in(fresh, OLD)
        self.assertEqual(answer.status_code, 303)
        self.assertNotIn('/web/login', answer.headers.get('Location', ''))

    def test_an_outside_address_is_not_carried_back(self):
        page = self.url_open('/web/login')
        dead = self.url_open('/web/login', data={
            'csrf_token': 'dead', 'login': LOGIN, 'password': OLD,
            'redirect': 'https://elsewhere.example/x'}, allow_redirects=False)
        self.assertEqual(dead.status_code, 303)
        self.assertNotIn('elsewhere', dead.headers.get('Location', ''))

    def test_every_other_form_still_refuses_a_dead_token(self):
        """Only the sign-in form is sent back; nothing else is let through."""
        self.authenticate(LOGIN, OLD)
        answer = self.url_open('/web/binary/upload_attachment', data={
            'csrf_token': 'dead', 'model': 'res.partner', 'id': str(self.user.partner_id.id)},
            allow_redirects=False)
        self.assertEqual(answer.status_code, 400)
