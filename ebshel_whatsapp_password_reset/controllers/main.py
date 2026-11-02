# Part of ebshel_whatsapp_password_reset. See LICENSE file for full copyright and licensing details.
"""The reset link works in a browser that keeps no cookie.

Odoo's reset page carries a form token computed from the browser's session, and
refuses the new password with "400: Bad Request - Session expired (invalid CSRF
token)" when the browser comes back with another session - or with none, which is
what a phone whose browser does not keep this site's cookies does on every request.
A link opened from WhatsApp is often opened in exactly such a browser. (client,
2026-11-02: a sales executive could not set his password, three links in a row.)

Setting a password with a reset link needs the link's own token: one-time, signed,
bound to the user and expiring. Whoever holds it can set the password anyway, so
the session's form token adds no protection to that form and only gets in the way.
It is still required where the form has no reset token - asking for a reset email
by login.
"""
from werkzeug.exceptions import BadRequest

from odoo import http
from odoo.http import request

from odoo.addons.auth_signup.controllers.main import AuthSignupHome


class ResetLink(AuthSignupHome):

    @http.route(csrf=False)
    def web_auth_reset_password(self, *args, **kw):
        if request.httprequest.method == 'POST' and not kw.get('token'):
            if not request.validate_csrf(kw.get('csrf_token')):
                raise BadRequest("Session expired (invalid CSRF token)")
        kw.pop('csrf_token', None)
        return super().web_auth_reset_password(*args, **kw)
