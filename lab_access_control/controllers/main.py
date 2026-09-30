# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request
from odoo.addons.web.controllers.home import Home


class SignIn(Home):

    @http.route()
    def web_login(self, *args, **kw):
        """Say why they are looking at the sign-in page again - see models/ir_http.py."""
        response = super().web_login(*args, **kw)
        context = getattr(response, 'qcontext', None)
        if request.httprequest.method == 'GET' and kw.get('expired') and context is not None and not context.get('error'):
            context.setdefault('message', request.env._("That sign-in page had expired. Please sign in again."))
        return response
