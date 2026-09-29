# Part of Ebshel AI Suite. See LICENSE file for full copyright and licensing details.
import logging
import os
import re

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

from ..services.engines import EngineSettings, get_engine_class
from ..services.engines.errors import EngineError
from ..services.guardrails import redact_secrets

_logger = logging.getLogger(__name__)

ENV_VAR_PATTERN = re.compile(r'^(CAI|ODOO_AI)_[A-Z0-9_]{1,60}$')


class CommunityAIConnection(models.Model):
    """Credentials and tuning of one language-model engine."""
    _name = 'community.ai.connection'
    _description = 'AI Engine Connection'
    _order = 'sequence, id'

    name = fields.Char('Label', required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    engine_type = fields.Selection(
        [('openai_compatible', 'OpenAI-compatible API'),
         ('gemini', 'Google Gemini'),
         ('mock', 'Offline mock engine (testing)')],
        string='Engine', required=True, default='openai_compatible')
    endpoint_url = fields.Char(
        'Endpoint URL',
        help='Base URL of the API. Leave empty for the vendor default. Examples: '
             'https://api.openai.com/v1, http://localhost:11434/v1 (Ollama), '
             'https://generativelanguage.googleapis.com/v1beta.')
    company_id = fields.Many2one('res.company', string='Company', index=True,
                                 help='Leave empty to share the connection between companies.')

    # --- secret handling -------------------------------------------------
    # The raw key is only readable by Settings administrators through RPC and
    # is never placed in a view. Administrators type a new key in the
    # write-only ``secret_input`` field.
    secret_key = fields.Char('API Key', groups='base.group_system', copy=False)
    secret_input = fields.Char('New API Key', compute='_compute_secret_input', inverse='_inverse_secret_input',
                               help='Type a key to replace the stored one. The stored key is never displayed.')
    secret_source = fields.Selection(
        [('stored', 'Stored in the database'), ('environment', 'Server environment variable')],
        default='stored', required=True,
        help='Environment variables keep the key out of the database and backups.')
    secret_env_var = fields.Char(
        'Variable Name', help='Name of the environment variable; must start with CAI_ or ODOO_AI_.')
    has_secret = fields.Boolean(compute='_compute_secret_status')
    secret_hint = fields.Char('Key', compute='_compute_secret_status')

    organization_reference = fields.Char('Organization / Project',
                                         help='Optional organization identifier sent to OpenAI-compatible APIs.')
    default_model = fields.Char('Chat Model', help='e.g. gpt-4o-mini, gemini-2.0-flash, llama3.1')
    embedding_model = fields.Char('Embedding Model', help='e.g. text-embedding-3-small, text-embedding-004')
    image_model = fields.Char('Image Model', help='e.g. gpt-image-1, dall-e-3')
    transcription_model = fields.Char('Transcription Model',
                                      help='e.g. whisper-1, gpt-4o-mini-transcribe. Gemini uses the chat model when empty.')
    timeout_seconds = fields.Integer('Timeout (s)', default=60)
    temperature = fields.Float(default=0.3, digits=(3, 2))
    send_sampling = fields.Boolean(
        'Send Temperature', default=True,
        help='Disable for reasoning models that reject the temperature parameter.')
    maximum_tokens = fields.Integer('Max Output Tokens', default=0, help='0 lets the engine decide.')
    token_parameter = fields.Selection(
        [('max_tokens', 'max_tokens'), ('max_completion_tokens', 'max_completion_tokens')],
        default='max_tokens', help='Name of the output-limit parameter (OpenAI-compatible engines).')
    json_mode = fields.Boolean('Native JSON Mode', default=True,
                               help='Ask the engine for strict JSON output when a feature needs structured data.')
    stream_usage = fields.Boolean('Token Usage in Streams', default=True,
                                  help='Request token counts at the end of streamed answers.')
    retry_count = fields.Integer('Retries', default=1, help='Automatic retries on rate limits or outages.')
    supports_streaming = fields.Boolean(compute='_compute_engine_features')
    supports_embeddings = fields.Boolean(compute='_compute_engine_features')

    currency_id = fields.Many2one('res.currency', compute='_compute_currency_id')
    cost_input_per_million = fields.Float('Input Cost / 1M tokens', digits=(12, 4))
    cost_output_per_million = fields.Float('Output Cost / 1M tokens', digits=(12, 4))

    available_models = fields.Json('Known Models', readonly=True, copy=False)
    models_refreshed_at = fields.Datetime(readonly=True, copy=False)
    last_check_state = fields.Selection([('unknown', 'Not tested'), ('ok', 'Working'), ('failed', 'Failed')],
                                        default='unknown', readonly=True, copy=False)
    last_check_message = fields.Char(readonly=True, copy=False)
    last_check_at = fields.Datetime(readonly=True, copy=False)

    def _compute_secret_input(self):
        for connection in self:
            connection.secret_input = False

    def _inverse_secret_input(self):
        if not self.env.user.has_group('ebshel_ai_suite.group_cai_admin') and not self.env.su:
            raise UserError(self.env._('Only AI administrators can change API keys.'))
        for connection in self:
            if connection.secret_input:
                connection.sudo().secret_key = connection.secret_input.strip()

    @api.depends('secret_source', 'secret_env_var')
    def _compute_secret_status(self):
        for connection in self:
            secret = connection._cai_secret()
            connection.has_secret = bool(secret)
            connection.secret_hint = f'•••• {secret[-4:]}' if secret and len(secret) > 8 else (
                '••••' if secret else False)

    @api.depends('engine_type')
    def _compute_engine_features(self):
        for connection in self:
            try:
                engine = get_engine_class(connection.engine_type)
            except EngineError:
                connection.supports_streaming = connection.supports_embeddings = False
                continue
            connection.supports_streaming = engine.supports_streaming
            connection.supports_embeddings = engine.supports_embeddings

    @api.depends('company_id')
    def _compute_currency_id(self):
        for connection in self:
            connection.currency_id = (connection.company_id or self.env.company).currency_id

    @api.constrains('secret_source', 'secret_env_var')
    def _check_env_var(self):
        for connection in self:
            if connection.secret_source == 'environment' and not ENV_VAR_PATTERN.match(connection.secret_env_var or ''):
                raise ValidationError(self.env._(
                    'The environment variable name must be uppercase and start with CAI_ or ODOO_AI_.'))

    @api.constrains('timeout_seconds', 'retry_count', 'temperature', 'maximum_tokens')
    def _check_numbers(self):
        for connection in self:
            if not 1 <= connection.timeout_seconds <= 600:
                raise ValidationError(self.env._('The timeout must be between 1 and 600 seconds.'))
            if not 0 <= connection.retry_count <= 5:
                raise ValidationError(self.env._('Retries must be between 0 and 5.'))
            if not 0 <= connection.temperature <= 2:
                raise ValidationError(self.env._('The temperature must be between 0 and 2.'))
            if connection.maximum_tokens < 0:
                raise ValidationError(self.env._('The output token limit cannot be negative.'))

    @api.constrains('endpoint_url', 'engine_type')
    def _check_endpoint(self):
        for connection in self:
            url = connection.endpoint_url
            if url and connection.engine_type != 'mock' and not re.match(r'^https?://', url):
                raise ValidationError(self.env._('The endpoint URL must start with http:// or https://.'))

    # ------------------------------------------------------------------
    # Adapter
    # ------------------------------------------------------------------
    def _cai_secret(self) -> str:
        self.ensure_one()
        if self.secret_source == 'environment':
            name = self.secret_env_var or ''
            return os.environ.get(name, '') if ENV_VAR_PATTERN.match(name) else ''
        return self.sudo().secret_key or ''

    def _cai_engine_settings(self) -> EngineSettings:
        self.ensure_one()
        return EngineSettings(
            engine_type=self.engine_type,
            endpoint_url=self.endpoint_url or '',
            secret=self._cai_secret(),
            model=self.default_model or '',
            embedding_model=self.embedding_model or '',
            image_model=self.image_model or '',
            transcription_model=self.transcription_model or '',
            timeout=float(self.timeout_seconds or 60),
            temperature=self.temperature if self.send_sampling else None,
            max_tokens=self.maximum_tokens or None,
            organization=self.organization_reference or '',
            json_mode=self.json_mode,
            extra={
                'token_param': self.token_parameter,
                'stream_usage': self.stream_usage,
                'omit_sampling': not self.send_sampling,
            },
        )

    def _cai_build_adapter(self):
        """Instantiate the engine adapter for this connection (call on sudo)."""
        self.ensure_one()
        return get_engine_class(self.engine_type)(self._cai_engine_settings())

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def _cai_is_admin(self):
        return self.env.su or self.env.user.has_group('ebshel_ai_suite.group_cai_admin')

    def action_cai_test_connection(self):
        self.ensure_one()
        if not self._cai_is_admin():
            raise UserError(self.env._('Only AI administrators can test connections.'))
        try:
            self.sudo()._cai_build_adapter().check_credentials()
        except EngineError as exc:
            self.write({'last_check_state': 'failed', 'last_check_at': fields.Datetime.now(),
                        'last_check_message': redact_secrets(
                            f'{exc.public_message_for(self.env)} [{exc.code}] {exc.detail}')[:500]})
            kind, title = 'danger', self.env._('Connection failed')
            message = self.last_check_message
        else:
            self.write({'last_check_state': 'ok', 'last_check_at': fields.Datetime.now(),
                        'last_check_message': self.env._('Credentials accepted.')})
            kind, title, message = 'success', self.env._('Connection works'), self.last_check_message
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {'title': title, 'message': message, 'type': kind, 'sticky': kind == 'danger'},
        }

    def action_cai_refresh_models(self):
        self.ensure_one()
        if not self._cai_is_admin():
            raise UserError(self.env._('Only AI administrators can query the engine.'))
        try:
            models_found = self.sudo()._cai_build_adapter().discover_models()
        except EngineError as exc:
            raise UserError(exc.public_message_for(self.env) + '\n' + redact_secrets(exc.detail)[:300]) from exc
        self.write({'available_models': models_found[:500], 'models_refreshed_at': fields.Datetime.now()})
        return True

    def action_cai_set_company_default(self):
        self.ensure_one()
        company = self.company_id or self.env.company
        company.sudo().cai_connection_id = self
        return True
