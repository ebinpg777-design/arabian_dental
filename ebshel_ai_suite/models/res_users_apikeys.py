# Part of Ebshel AI Suite. See LICENSE file for full copyright and licensing details.
from odoo import models
from odoo.http import request
from odoo.addons.base.models.res_users import check_identity

from ..services.mcp_server import API_KEY_SCOPE


class ResUsersApikeysDescription(models.TransientModel):
    _inherit = 'res.users.apikeys.description'

    def make_key(self):
        if not self.env.context.get('cai_mcp_scope'):
            return super().make_key()
        return self._cai_make_mcp_key()

    @check_identity
    def _cai_make_mcp_key(self):
        """Create a key limited to the Ebshel AI MCP server."""
        self.check_access_make_key()
        description = self.sudo()
        key = self.env['res.users.apikeys']._generate(API_KEY_SCOPE, description.name, self.expiration_date)
        description.unlink()
        return {
            'type': 'ir.actions.act_window', 'res_model': 'res.users.apikeys.show',
            'name': self.env._('MCP Key Ready'), 'views': [(False, 'form')], 'target': 'new',
            # the 20.0 dialog also shows the scope and a connection snippet built on the base URL
            'context': {
                'default_key': key,
                'default_scope': API_KEY_SCOPE,
                'default_base_url': ((request and request.httprequest.url_root) or '<Your URL>').rstrip('/'),
            },
        }
