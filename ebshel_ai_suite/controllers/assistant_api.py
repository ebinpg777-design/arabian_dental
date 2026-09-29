# Part of Ebshel AI Suite. See LICENSE file for full copyright and licensing details.
"""HTTP entry points of the AI console.

Most interactions go through ORM methods (``community.ai.session.cai_*``),
which the web client calls with the standard ``orm`` service. This
controller adds what plain RPC cannot do:

* ``/ebshel_ai/bootstrap`` — one round-trip to initialise the console;
* ``/ebshel_ai/stream`` — chunked NDJSON streaming of an assistant turn.

Streaming note: Odoo commits the request transaction only when the handler
returns, and bus notifications are only delivered after that commit, so the
bus cannot carry token-by-token output. The streaming route therefore returns
a generator that opens its *own* cursor, runs the conversation turn and
flushes each event as one JSON line. The client falls back to the regular
RPC call when streaming is disabled or unavailable.
"""
import json
import logging

from odoo import api, http
from odoo.exceptions import AccessError, MissingError, UserError
from odoo.http import request
from odoo.modules.registry import Registry

from ..services.conversation_runner import ConversationRunner

_logger = logging.getLogger(__name__)


def _line(event: dict) -> bytes:
    return (json.dumps(event, default=str, ensure_ascii=False) + '\n').encode()


class CommunityAIConsoleController(http.Controller):

    @staticmethod
    def _cai_owned_session(env, session_id):
        session = env['community.ai.session'].browse(int(session_id)).exists()
        if not session:
            raise MissingError(env._('This conversation no longer exists.'))
        session._cai_check_owner()
        session.assistant_id._cai_check_usable()
        return session

    @http.route('/ebshel_ai/bootstrap', type='jsonrpc', auth='user', readonly=True)
    def cai_bootstrap(self):
        env = request.env
        user = env.user
        if not user.has_group('ebshel_ai_suite.group_cai_user'):
            return {'enabled': False}
        assistants = env['community.ai.assistant']._cai_usable_assistants()
        default = env.company.cai_assistant_id
        params = env['ir.config_parameter'].sudo()
        return {
            'enabled': True,
            'assistants': [assistant._cai_card() for assistant in assistants],
            'default_assistant_id': default.id if default in assistants else (assistants[:1].id or False),
            'streaming': params.get_param('ebshel_ai_suite.streaming', 'True') != 'False',
            'recent': env['community.ai.session'].cai_recent(),
            'can_configure': user.has_group('ebshel_ai_suite.group_cai_manager'),
        }

    @http.route('/ebshel_ai/stream', type='http', auth='user', methods=['POST'], csrf=True)
    def cai_stream(self, session_id, prompt=None, mode='send', **_kwargs):
        env = request.env
        try:
            session = self._cai_owned_session(env, session_id)
        except (ValueError, UserError, AccessError, MissingError) as exc:
            message = exc.args[0] if exc.args else str(exc)
            return request.make_response(_line({'type': 'error', 'message': message}),
                                         headers=[('Content-Type', 'application/x-ndjson')])

        dbname, uid = request.db, env.uid
        context = dict(env.context)
        user_text = prompt if mode == 'send' else None
        target_id = session.id

        def generate():
            try:
                with Registry(dbname).cursor() as cr:
                    stream_env = api.Environment(cr, uid, context)
                    stream_session = stream_env['community.ai.session'].browse(target_id)
                    runner = ConversationRunner(stream_env, stream_session)
                    for event in runner.iter_events(user_text, stream=True):
                        yield _line(event)
            except (UserError, AccessError, MissingError) as exc:
                yield _line({'type': 'error', 'message': exc.args[0] if exc.args else str(exc)})
            except Exception:  # noqa: BLE001 - the client must always receive a final line
                _logger.exception('AI streaming turn failed')
                yield _line({'type': 'error', 'message': 'The AI request failed unexpectedly. Please retry.'})

        response = request.make_response(generate(), headers=[
            ('Content-Type', 'application/x-ndjson; charset=utf-8'),
            ('Cache-Control', 'no-cache'),
            ('X-Accel-Buffering', 'no'),
        ])
        response.direct_passthrough = True
        return response
