# -*- coding: utf-8 -*-
"""Signing in after a session has gone stale.

A session goes stale whenever what it was signed against changes underneath it: an
administrator resets the person's password, the login is renamed, or the database
is replaced by a fresh copy. The next request carrying that session is logged out
by core, which also marks it for a NEW session id - and hands that id out only when
the response is saved, after the page has been rendered.

The sign-in form carries a token computed from the session id. So a stale session
that opens `/web/login` directly gets a form whose token belongs to the id being
thrown away, and a cookie for the new one: the very first attempt to sign in is
refused with "400: Bad Request - Session expired (invalid CSRF token)". On a phone
the natural move is Back and try again, which brings the same dead form out of the
browser's memory, so it fails every time. An administrator "fixing" it by resetting
the password makes the session stale again and re-arms it. One sales executive lost
most of a morning to this. (client, 2026-09-30)

Two things, both small:

* the new id is given the moment the session is logged out, so whatever is rendered
  afterwards in that request - the sign-in form included - agrees with the cookie;
* a sign-in form that still arrives with a dead token (Back, a tab left open
  overnight) is answered with a fresh sign-in page and a plain sentence, not a 400.
  The refused request signs nobody in; every other form keeps its 400.
"""
import logging
from urllib.parse import urlencode

import werkzeug.exceptions

from odoo import http, models
from odoo.http import request

_logger = logging.getLogger(__name__)


class IrHttp(models.AbstractModel):
    _inherit = 'ir.http'

    @classmethod
    def _authenticate_explicit(cls, auth):
        had_user = request.session.uid is not None
        try:
            super()._authenticate_explicit(auth)
        finally:
            session = request.session
            if had_user and session.uid is None and session.should_rotate and session.can_save:
                # exactly what `_save_session` would do at the end of the request,
                # done before anything is rendered with the old id
                http.root.session_store.rotate(session, request.env)

    @classmethod
    def _is_dead_sign_in_form(cls, exception):
        return (isinstance(exception, werkzeug.exceptions.BadRequest)
                and 'CSRF' in str(exception.description or '')
                and request.httprequest.method == 'POST'
                and request.httprequest.path == '/web/login')

    @classmethod
    def _handle_error(cls, exception):
        if cls._is_dead_sign_in_form(exception):
            _logger.info("sign-in form with a dead token: answered with a fresh sign-in page")
            params = {'expired': 1}
            # where they were going, when it is a page of this site
            target = request.params.get('redirect') or ''
            if target.startswith('/') and not target.startswith('//'):
                params['redirect'] = target
            return request.redirect('/web/login?%s' % urlencode(params))
        return super()._handle_error(exception)
