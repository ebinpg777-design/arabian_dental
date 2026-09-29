# Part of Ebshel AI Suite. See LICENSE file for full copyright and licensing details.
import base64

from odoo import api, models
from odoo.exceptions import AccessError, UserError

from ..services.engines.errors import EngineError
from ..services.llm_gateway import LLMGateway
from ..services.nl_search import NaturalSearchService, SearchInterpretationError
from ..services.text_tools import OPERATIONS, TONES, TextTransformer


MAX_AUDIO_BYTES = 25 * 1024 * 1024


class CommunityAITextService(models.AbstractModel):
    """RPC facade used by the web client for stateless AI helpers."""
    _name = 'community.ai.text.service'
    _description = 'AI Writing & Search Helpers'

    def _cai_check_user(self):
        if not (self.env.su or self.env.user.has_group('ebshel_ai_suite.group_cai_user')):
            raise AccessError(self.env._('You are not allowed to use AI features.'))

    @api.model
    def cai_writing_options(self, res_model=None):
        self._cai_check_user()
        preset = self.env['community.ai.context.preset']._cai_find('write', res_model or None, has_record=True)
        lang = self.env.lang
        return {
            'shortcuts': preset._cai_card()['buttons'] if preset else [],
            'operations': [{'key': key, 'label': label._translate(lang)} for key, (label, _t) in OPERATIONS.items()],
            'tones': [{'key': key, 'label': label._translate(lang)} for key, label in TONES],
            'languages': [{'key': code, 'label': name} for code, name in self.env['res.lang'].get_installed()],
        }

    @api.model
    def cai_transform_text(self, operation, text, options=None):
        """Return ``{'text': result}``; never writes anything."""
        self._cai_check_user()
        options = options or {}
        record = None
        if options.get('res_model') and options.get('res_id') and options['res_model'] in self.env:
            record = self.env[options['res_model']].browse(int(options['res_id'])).exists()
            if record and not record.has_access('read'):
                record = None
        assistant = self.env['community.ai.assistant']
        if options.get('assistant_id'):
            assistant = assistant.browse(int(options['assistant_id']))
            assistant._cai_check_usable()
        try:
            result = TextTransformer(self.env).transform(
                operation, text or '', language=options.get('language'), tone=options.get('tone'),
                instruction=options.get('instruction'), assistant=assistant or None,
                output_html=bool(options.get('html')),
                context_record=record if operation in ('reply', 'follow_up', 'describe') else None)
        except EngineError as exc:
            raise UserError(exc.public_message_for(self.env)) from exc
        return {'text': result}

    @api.model
    def cai_transcribe(self, data_b64, mimetype='audio/webm', summarize=False):
        """Transcribe a recording (kept in memory only) and optionally summarize it."""
        self._cai_check_user()
        try:
            audio = base64.b64decode(data_b64 or '', validate=True)
        except (ValueError, TypeError) as exc:
            raise UserError(self.env._('The recording could not be read.')) from exc
        if not audio:
            raise UserError(self.env._('The recording is empty.'))
        if len(audio) > MAX_AUDIO_BYTES:
            raise UserError(self.env._('Recordings are limited to 25 MB.'))
        try:
            text = LLMGateway(self.env).transcribe(audio, mimetype or 'audio/webm')
            summary = ''
            if summarize and text.strip():
                summary = TextTransformer(self.env).transform('meeting_summary', text)
        except EngineError as exc:
            raise UserError(exc.public_message_for(self.env)) from exc
        return {'text': text, 'summary': summary}

    @api.model
    def cai_natural_search(self, query, model_hint=None):
        """Interpret ``query`` and return a validated window action."""
        self._cai_check_user()
        service = NaturalSearchService(self.env)
        try:
            plan = service.interpret(query, model_hint=model_hint)
        except SearchInterpretationError as exc:
            raise UserError(str(exc)) from exc
        except EngineError as exc:
            raise UserError(exc.public_message_for(self.env)) from exc
        return {
            'action': service.plan_to_action(plan),
            'count': plan.count,
            'explanation': plan.explanation,
            'preview': service.preview(plan),
        }
