# Part of Ebshel AI Suite. See LICENSE file for full copyright and licensing details.
"""MCP (Model Context Protocol) endpoint: ``POST /ebshel_ai/mcp``.

Authentication: ``Authorization: Bearer <API key>``. Keys created with the
"Generate MCP key" button carry the ``ebshel_ai_mcp`` scope; unscoped
(global) API keys are accepted too. In multi-database setups, clients add the
``X-Odoo-Database`` header.
"""
import json
import logging

from odoo import http
from odoo.http import request

from ..services.mcp_server import API_KEY_SCOPE, McpServer

_logger = logging.getLogger(__name__)


def _json_response(payload, status=200, headers=None):
    return request.make_response(
        json.dumps(payload, default=str, ensure_ascii=False), status=status,
        headers=[('Content-Type', 'application/json'), ('Cache-Control', 'no-store'), *(headers or [])])


class CommunityAIMcpController(http.Controller):

    # auth='none' routes default to a read-only cursor; tools and audit logs write
    @http.route('/ebshel_ai/mcp', type='http', auth='none', methods=['GET', 'POST', 'DELETE'], csrf=False,
                readonly=False, save_session=False)
    def cai_mcp(self, **_kwargs):
        if request.httprequest.method != 'POST':
            # no server-initiated stream and no session to close
            return request.make_response('', status=405, headers=[('Allow', 'POST')])
        header = request.httprequest.headers.get('Authorization') or ''
        token = header[7:].strip() if header.lower().startswith('bearer ') else ''
        unauthorized = [('WWW-Authenticate', 'Bearer realm="odoo-ebshel-ai"')]
        if not token or not request.db:
            return _json_response({'error': 'unauthorized'}, 401, unauthorized)
        uid = request.env['res.users.apikeys']._check_credentials(scope=API_KEY_SCOPE, key=token)
        if not uid:
            return _json_response({'error': 'unauthorized'}, 401, unauthorized)
        request.update_env(user=uid)
        env = request.env
        params = env['ir.config_parameter'].sudo()
        if params.get_param('ebshel_ai_suite.mcp_enabled') != 'True':
            return _json_response({'error': 'The MCP server is disabled.'}, 403)
        if not env.user.has_group('ebshel_ai_suite.group_cai_user'):
            return _json_response({'error': 'This user is not allowed to use AI features.'}, 403)
        if 'json' not in (request.httprequest.mimetype or ''):
            return _json_response({'jsonrpc': '2.0', 'id': None, 'error': {
                'code': -32700, 'message': 'Content-Type must be application/json'}}, 415)
        try:
            message = json.loads(request.httprequest.get_data(as_text=True) or 'null')
        except ValueError:
            return _json_response({'jsonrpc': '2.0', 'id': None,
                                   'error': {'code': -32700, 'message': 'Parse error'}})
        response = McpServer(env).handle(message)
        if response is None:
            return request.make_response('', status=202)
        return _json_response(response)
