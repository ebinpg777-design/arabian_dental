# Part of Ebshel AI Suite. See LICENSE file for full copyright and licensing details.
"""Single entry point between business code and engine adapters.

Business code never instantiates an adapter directly::

    gateway = LLMGateway(env)
    reply = gateway.generate(turns, assistant=assistant, purpose='chat')

The gateway resolves the connection (assistant → company default → first
available), enforces usage limits, retries transient failures, measures
latency and records a ``community.ai.usage`` line for every call, successful
or not.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING


from odoo.tools.translate import LazyTranslate

from .engines import (
    ChatTurn,
    EngineReply,
    EngineRequest,
    StreamPiece,
    ToolSpec,
)
from .engines.errors import EngineConfigError, EngineError
from .guardrails import redact_secrets
from .usage_guard import UsageGuard

if TYPE_CHECKING:
    from collections.abc import Iterator

_logger = logging.getLogger(__name__)
_lt = LazyTranslate(__name__)


class LLMGateway:

    def __init__(self, env):
        self.env = env

    # ------------------------------------------------------------------
    # Connection resolution
    # ------------------------------------------------------------------
    def resolve_connection(self, assistant=None, connection=None):
        """Return the ``community.ai.connection`` to use (sudo-free record)."""
        Connection = self.env['community.ai.connection']
        candidate = connection or (assistant and assistant.connection_id) or self.env.company.cai_connection_id
        if not candidate:
            candidate = Connection.sudo().search(
                [('active', '=', True), ('company_id', 'in', [False, self.env.company.id])], limit=1)
        if not candidate or not candidate.sudo().active:
            raise EngineConfigError(
                detail='No active AI connection available.',
                public_text=_lt('No AI connection is configured. Please ask an administrator to set one up.'))
        company = candidate.sudo().company_id
        if company and company not in self.env.user.company_ids:
            raise EngineConfigError(detail=f'Connection {candidate.id} belongs to another company.')
        return candidate

    def _model_for(self, connection, assistant=None, model=None) -> str:
        return model or (assistant and assistant.model_identifier) or connection.sudo().default_model or ''

    # ------------------------------------------------------------------
    # Calls
    # ------------------------------------------------------------------
    def generate(self, turns: list[ChatTurn], *, assistant=None, connection=None,
                 tools: list[ToolSpec] | None = None, json_output: bool = False, model: str | None = None,
                 temperature: float | None = None, max_tokens: int | None = None,
                 purpose: str = 'chat') -> EngineReply:
        connection = self.resolve_connection(assistant, connection)
        UsageGuard(self.env).ensure_allowed(assistant=assistant, connection=connection)
        adapter = connection.sudo()._cai_build_adapter()
        request = EngineRequest(
            turns=turns, tools=tools or [], model=self._model_for(connection, assistant, model),
            temperature=temperature if temperature is not None else (assistant and assistant._cai_temperature()),
            max_tokens=max_tokens, json_output=json_output,
        )
        attempts = 1 + max(0, connection.sudo().retry_count)
        started = time.monotonic()
        for attempt in range(1, attempts + 1):
            try:
                reply = adapter.complete(request)
            except EngineError as exc:
                if exc.retryable and attempt < attempts and exc.code != 'timeout':
                    time.sleep(min(0.5 * 2 ** (attempt - 1), 4))
                    continue
                self._log_usage(connection, assistant, request.model, purpose, started, error=exc)
                raise
            except Exception as exc:  # noqa: BLE001 - never leak raw adapter bugs
                _logger.exception('AI engine adapter crashed')
                wrapped = EngineError(detail=redact_secrets(repr(exc)))
                self._log_usage(connection, assistant, request.model, purpose, started, error=wrapped)
                raise wrapped from exc
            self._log_usage(connection, assistant, reply.model or request.model, purpose, started,
                            input_tokens=reply.input_tokens, output_tokens=reply.output_tokens)
            return reply
        raise EngineError(detail='retry loop exhausted')  # pragma: no cover

    def stream(self, turns: list[ChatTurn], *, assistant=None, connection=None,
               tools: list[ToolSpec] | None = None, model: str | None = None,
               purpose: str = 'chat') -> Iterator[StreamPiece]:
        """Stream a completion. Falls back to a single piece when the engine
        cannot stream (handled by the adapter base class)."""
        connection = self.resolve_connection(assistant, connection)
        UsageGuard(self.env).ensure_allowed(assistant=assistant, connection=connection)
        adapter = connection.sudo()._cai_build_adapter()
        request = EngineRequest(turns=turns, tools=tools or [],
                                model=self._model_for(connection, assistant, model),
                                temperature=assistant and assistant._cai_temperature())
        started = time.monotonic()
        try:
            for piece in adapter.stream(request):
                if piece.done:
                    self._log_usage(connection, assistant, piece.model or request.model, purpose, started,
                                    input_tokens=piece.input_tokens, output_tokens=piece.output_tokens)
                yield piece
        except EngineError as exc:
            self._log_usage(connection, assistant, request.model, purpose, started, error=exc)
            raise
        except Exception as exc:  # noqa: BLE001
            _logger.exception('AI engine adapter crashed while streaming')
            wrapped = EngineError(detail=redact_secrets(repr(exc)))
            self._log_usage(connection, assistant, request.model, purpose, started, error=wrapped)
            raise wrapped from exc

    def embed(self, texts: list[str], *, connection=None, assistant=None) -> list[list[float]]:
        connection = self.resolve_connection(assistant, connection)
        UsageGuard(self.env).ensure_allowed(assistant=assistant, connection=connection)
        adapter = connection.sudo()._cai_build_adapter()
        started = time.monotonic()
        try:
            vectors = adapter.embed(texts)
        except EngineError as exc:
            self._log_usage(connection, assistant, connection.sudo().embedding_model, 'embedding', started, error=exc)
            raise
        tokens = sum(len((t or '').split()) for t in texts)
        self._log_usage(connection, assistant, connection.sudo().embedding_model, 'embedding', started,
                        input_tokens=tokens)
        return vectors

    def transcribe(self, audio: bytes, mimetype: str, *, connection=None) -> str:
        connection = self.resolve_connection(None, connection)
        UsageGuard(self.env).ensure_allowed(connection=connection)
        adapter = connection.sudo()._cai_build_adapter()
        started = time.monotonic()
        model = connection.sudo().transcription_model or connection.sudo().default_model
        try:
            text = adapter.transcribe(audio, mimetype)
        except EngineError as exc:
            self._log_usage(connection, None, model, 'voice', started, error=exc)
            raise
        self._log_usage(connection, None, model, 'voice', started, output_tokens=len(text.split()))
        return text

    def render_image(self, prompt: str, *, connection=None, size='1024x1024', quality='standard',
                     image_format='png') -> bytes:
        connection = self.resolve_connection(None, connection)
        UsageGuard(self.env).ensure_allowed(connection=connection)
        adapter = connection.sudo()._cai_build_adapter()
        started = time.monotonic()
        try:
            data = adapter.render_image(prompt, size=size, quality=quality, image_format=image_format)
        except EngineError as exc:
            self._log_usage(connection, None, connection.sudo().image_model, 'image', started, error=exc)
            raise
        self._log_usage(connection, None, connection.sudo().image_model, 'image', started,
                        input_tokens=len(prompt.split()))
        return data

    # ------------------------------------------------------------------
    # Usage logging
    # ------------------------------------------------------------------
    def _log_usage(self, connection, assistant, model, purpose, started, *, input_tokens=0,
                   output_tokens=0, error: EngineError | None = None) -> None:
        duration_ms = int((time.monotonic() - started) * 1000)
        if error is not None:
            _logger.info('AI call failed (%s, connection %s): %s', error.code, connection.id,
                         redact_secrets(error.detail)[:300])
        try:
            self.env['community.ai.usage'].sudo()._cai_record(
                connection=connection, assistant=assistant, model=model, purpose=purpose,
                input_tokens=input_tokens, output_tokens=output_tokens, duration_ms=duration_ms,
                status='failed' if error else 'success', error_code=error.code if error else False,
            )
        except Exception:  # noqa: BLE001 - usage logging must never break the call
            _logger.exception('Could not record AI usage')
