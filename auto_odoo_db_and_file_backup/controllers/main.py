# -*- coding: utf-8 -*-
"""Where Google sends the browser back after Drive access is approved.

The auth link used to name the first redirect address in client_secrets.json:
http://localhost, or the site root - neither of which does anything with the code
Google hands back, so the browser landed on "This site can't be reached" and the
code had to be copied out of the address bar by hand. This route takes the code,
saves it on the rule it was asked for (which swaps it for the refresh token) and
returns to that rule. (client, 2026-09-15)
"""
from markupsafe import escape

from odoo import _, http
from odoo.exceptions import UserError
from odoo.http import request

from ..models.models import GDRIVE_CALLBACK


class GoogleDriveCallback(http.Controller):

    def _page(self, message, rule=None, status=400):
        back = ('<p><a href="/web#id=%s&amp;model=database.backup&amp;view_type=form">%s</a></p>'
                % (rule.id, escape(_("Back to the backup rule")))) if rule else ''
        body = '<html><body style="font-family: sans-serif; margin: 3rem;"><p>%s</p>%s</body></html>' % (
            escape(message), back)
        return request.make_response(body, headers=[('Content-Type', 'text/html; charset=utf-8')],
                                     status=status)

    @http.route(GDRIVE_CALLBACK, type='http', auth='user', methods=['GET'])
    def gdrive_callback(self, code=None, state=None, error=None, **kwargs):
        if not request.env.user.has_group('base.group_system'):
            return self._page(_("Only a Settings administrator can connect Google Drive."), status=403)
        rule = request.env['database.backup']._gdrive_rule_from_state(state)
        if not rule:
            return self._page(_("This Google sign-in link does not belong to a backup rule. "
                                "Open the rule and press Get Credentials again."))
        if error or not code:
            return self._page(_("Google did not grant access (%s). Nothing was changed.")
                              % (error or _("no code returned")), rule)
        try:
            rule.write({'google_drive_authorization_code': code})
        except UserError as exc:
            return self._page(str(exc), rule)
        return request.redirect('/web#id=%s&model=database.backup&view_type=form' % rule.id)
