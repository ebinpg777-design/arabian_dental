# Part of Ebshel AI Suite. See LICENSE file for full copyright and licensing details.
"""Engine adapter contract.

An *engine* is the remote (or local) language-model service. Every engine is
wrapped by an :class:`EngineAdapter` subclass which translates the neutral
request/response structures defined here into the vendor wire format.

Adapters never touch the ORM: they receive an immutable
:class:`EngineSettings` snapshot built by ``community.ai.connection`` and
return plain data. This keeps them unit-testable and lets the gateway enforce
limits, logging and error translation in one place.
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING
from dataclasses import dataclass, field
from typing import Any

from ..guardrails import redact_secrets
from .errors import (
    EngineAuthError,
    EngineBadResponse,
    EngineConfigError,
    EngineError,
    EngineRateLimited,
    EngineRequestRejected,
    EngineTimeout,
    EngineUnavailable,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

_logger = logging.getLogger(__name__)

try:  # ``requests`` ships with every Odoo installation, but stay defensive.
    import requests
except ImportError:  # pragma: no cover
    requests = None


# ---------------------------------------------------------------------------
# Neutral data structures
# ---------------------------------------------------------------------------

@dataclass
class ToolInvocation:
    """A request emitted by the model to run one registered capability."""
    call_id: str
    name: str
    arguments: dict = field(default_factory=dict)
    raw_arguments: str = ''

    def as_dict(self) -> dict:
        return {'id': self.call_id, 'name': self.name, 'arguments': self.arguments}

    @classmethod
    def from_dict(cls, data: dict) -> ToolInvocation:
        return cls(call_id=data.get('id') or '', name=data.get('name') or '',
                   arguments=data.get('arguments') or {})


@dataclass
class ChatTurn:
    """One message of the conversation sent to the engine.

    ``role`` is one of ``system``, ``user``, ``assistant`` or ``tool``.
    """
    role: str
    content: str = ''
    tool_invocations: list[ToolInvocation] = field(default_factory=list)
    tool_call_id: str | None = None
    tool_name: str | None = None
    #: images sent with a user turn, as ``(mimetype, raw bytes)``
    images: list[tuple[str, bytes]] = field(default_factory=list)


@dataclass
class ToolSpec:
    """A capability advertised to the engine (JSON-schema parameters)."""
    name: str
    description: str
    parameters: dict


@dataclass(frozen=True)
class EngineSettings:
    """Connection snapshot handed to adapters. Holds the secret: never log it."""
    engine_type: str
    endpoint_url: str = ''
    secret: str = field(default='', repr=False)
    model: str = ''
    embedding_model: str = ''
    image_model: str = ''
    transcription_model: str = ''
    timeout: float = 60.0
    temperature: float | None = None
    max_tokens: int | None = None
    organization: str = ''
    json_mode: bool = True
    extra: dict = field(default_factory=dict)


@dataclass
class EngineRequest:
    turns: list[ChatTurn]
    tools: list[ToolSpec] = field(default_factory=list)
    model: str = ''
    temperature: float | None = None
    max_tokens: int | None = None
    json_output: bool = False


@dataclass
class EngineReply:
    text: str = ''
    tool_invocations: list[ToolInvocation] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    finish_reason: str = ''
    model: str = ''


@dataclass
class StreamPiece:
    """Incremental streaming output. The final piece has ``done=True`` and
    carries the tool invocations and token counts."""
    text: str = ''
    done: bool = False
    tool_invocations: list[ToolInvocation] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    finish_reason: str = ''
    model: str = ''


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_ENGINE_REGISTRY: dict[str, type[EngineAdapter]] = {}


def register_engine(key: str):
    """Class decorator registering an adapter for ``engine_type == key``.

    Third-party modules add a selection value on ``community.ai.connection``
    (``selection_add``) and decorate their adapter with this function.
    """
    def decorator(cls):
        cls.engine_key = key
        _ENGINE_REGISTRY[key] = cls
        return cls
    return decorator


def get_engine_class(key: str) -> type[EngineAdapter]:
    try:
        return _ENGINE_REGISTRY[key]
    except KeyError:
        raise EngineConfigError(f'No adapter registered for engine type {key!r}') from None


def registered_engine_keys() -> list[str]:
    return sorted(_ENGINE_REGISTRY)


# ---------------------------------------------------------------------------
# Adapter contract
# ---------------------------------------------------------------------------

class EngineAdapter:
    """Base class for engine adapters.

    Subclasses must implement :meth:`complete`. Streaming, embeddings, model
    discovery and image rendering are optional; the defaults either degrade
    gracefully (streaming) or raise :class:`EngineConfigError`.
    """
    engine_key: str = ''
    supports_streaming: bool = False
    supports_embeddings: bool = False
    supports_images: bool = False
    supports_transcription: bool = False

    def __init__(self, settings: EngineSettings):
        self.settings = settings

    # -- mandatory --------------------------------------------------------
    def complete(self, request: EngineRequest) -> EngineReply:
        raise NotImplementedError

    # -- optional ---------------------------------------------------------
    def stream(self, request: EngineRequest) -> Iterator[StreamPiece]:
        """Fallback streaming: run a normal completion and emit it at once."""
        reply = self.complete(request)
        if reply.text:
            yield StreamPiece(text=reply.text)
        yield StreamPiece(
            done=True, tool_invocations=reply.tool_invocations,
            input_tokens=reply.input_tokens, output_tokens=reply.output_tokens,
            finish_reason=reply.finish_reason, model=reply.model,
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise EngineConfigError('This engine does not provide embeddings.')

    def check_credentials(self) -> None:
        """Raise an :class:`EngineError` when the connection is unusable."""
        self.discover_models()

    def discover_models(self) -> list[str]:
        return [self.settings.model] if self.settings.model else []

    def render_image(self, prompt: str, size: str = '1024x1024', quality: str = 'standard',
                     image_format: str = 'png') -> bytes:
        raise EngineConfigError('This engine cannot generate images.')

    def transcribe(self, audio: bytes, mimetype: str) -> str:
        """Return the text spoken in ``audio``."""
        raise EngineConfigError('This engine cannot transcribe audio.')

    # -- helpers ----------------------------------------------------------
    def _model(self, request: EngineRequest | None = None) -> str:
        model = (request and request.model) or self.settings.model
        if not model:
            raise EngineConfigError('No model configured for this connection.')
        return model

    def _http(self, method: str, url: str, *, headers: dict | None = None,
              payload: Any = None, stream: bool = False, timeout: float | None = None,
              files: dict | None = None, form: dict | None = None):
        """Perform an HTTP call and translate transport failures into
        :class:`EngineError` subclasses. Returns the ``requests`` response.
        ``files``/``form`` send a multipart body instead of JSON."""
        if requests is None:  # pragma: no cover
            raise EngineConfigError('The python "requests" library is not available.')
        try:
            if files is not None:
                response = requests.request(method, url, headers=headers or {}, files=files, data=form or {},
                                            timeout=timeout or self.settings.timeout)
            else:
                response = requests.request(
                    method, url, headers=headers or {},
                    data=json.dumps(payload) if payload is not None else None,
                    timeout=timeout or self.settings.timeout, stream=stream,
                )
        except requests.exceptions.Timeout as exc:
            raise EngineTimeout(detail=str(exc)) from exc
        except requests.exceptions.RequestException as exc:
            raise EngineUnavailable(detail=redact_secrets(str(exc))) from exc
        if response.status_code >= 400:
            self._raise_for_status(response)
        return response

    def _raise_for_status(self, response) -> None:
        status = response.status_code
        try:
            body = response.text[:2000]
        except Exception:  # noqa: BLE001 - body is purely diagnostic
            body = ''
        detail = redact_secrets(f'HTTP {status}: {body}')
        if status in (401, 403):
            raise EngineAuthError(detail=detail)
        if status == 404:
            raise EngineConfigError(detail=detail)
        if status == 408:
            raise EngineTimeout(detail=detail)
        if status == 429:
            raise EngineRateLimited(detail=detail)
        if status >= 500:
            raise EngineUnavailable(detail=detail)
        raise EngineRequestRejected(detail=detail)

    @staticmethod
    def _json(response) -> Any:
        try:
            return response.json()
        except ValueError as exc:
            raise EngineBadResponse(detail='Response body is not valid JSON.') from exc

    @staticmethod
    def _iter_sse_data(response) -> Iterator[str]:
        """Yield the ``data:`` payloads of a server-sent-events stream."""
        try:
            for raw in response.iter_lines(decode_unicode=True):
                if not raw or raw.startswith(':'):
                    continue
                if raw.startswith('data:'):
                    yield raw[5:].strip()
        except requests.exceptions.Timeout as exc:
            raise EngineTimeout(detail=str(exc)) from exc
        except requests.exceptions.RequestException as exc:
            raise EngineUnavailable(detail=redact_secrets(str(exc))) from exc

    @staticmethod
    def _parse_arguments(raw: Any) -> dict:
        if isinstance(raw, dict):
            return raw
        if not raw:
            return {}
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            # Keep the malformed payload: the capability validator will reject it.
            return {'__unparsable__': str(raw)[:500]}
        return value if isinstance(value, dict) else {'__unparsable__': str(raw)[:500]}


__all__ = [
    'ChatTurn', 'EngineAdapter', 'EngineError', 'EngineReply', 'EngineRequest',
    'EngineSettings', 'StreamPiece', 'ToolInvocation', 'ToolSpec',
    'get_engine_class', 'register_engine', 'registered_engine_keys',
]
