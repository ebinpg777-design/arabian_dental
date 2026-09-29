# Part of Ebshel AI Suite. See LICENSE file for full copyright and licensing details.
"""Deterministic offline engine for tests, demos and development.

Behaviour
---------
* ``endpoint_url`` selects a failure scenario: ``mock://auth-error``,
  ``mock://timeout``, ``mock://unavailable``, ``mock://rate-limit`` or
  ``mock://malformed``. Anything else behaves normally.
* Tests can pre-program answers with :meth:`MockEngine.queue`; queued items
  are consumed first (``EngineReply`` instances, plain strings, dicts that
  are JSON-encoded, or exceptions to raise).
* Otherwise the last user message is inspected. The directive
  ``[[call:<capability> {json arguments}]]`` makes the engine request that
  capability (once). After a tool result it summarizes the result. In JSON
  mode it answers ``{"value": ...}``. Plain prompts get an echo answer.
* Embeddings are hashed bag-of-words vectors, so similar texts really are
  closer to each other, which makes retrieval tests meaningful.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import re
from typing import TYPE_CHECKING

from .base import (
    EngineAdapter,
    EngineReply,
    EngineRequest,
    StreamPiece,
    ToolInvocation,
    register_engine,
)
from .errors import (
    EngineAuthError,
    EngineBadResponse,
    EngineRateLimited,
    EngineTimeout,
    EngineUnavailable,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

_DIRECTIVE = re.compile(r'\[\[call:([a-z0-9_\.]+)\s*(\{.*?\})?\]\]', re.S | re.I)
_WORD = re.compile(r'\w+', re.U)
_EMBED_DIM = 64
# 1x1 transparent PNG
_PIXEL_PNG = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==')


def _count_tokens(text: str) -> int:
    return max(1, len(_WORD.findall(text or '')))


@register_engine('mock')
class MockEngine(EngineAdapter):
    supports_streaming = True
    supports_embeddings = True
    supports_images = True

    _queue: list = []
    #: every request received, most recent last (inspection helper for tests)
    received: list[EngineRequest] = []

    # -- test helpers -----------------------------------------------------
    @classmethod
    def queue(cls, *answers) -> None:
        cls._queue.extend(answers)

    @classmethod
    def reset(cls) -> None:
        cls._queue.clear()
        cls.received.clear()

    # -- scenarios --------------------------------------------------------
    def _scenario(self) -> None:
        scenario = (self.settings.endpoint_url or '').removeprefix('mock://')
        if scenario == 'auth-error':
            raise EngineAuthError(detail='Mock: invalid API key')
        if scenario == 'timeout':
            raise EngineTimeout(detail='Mock: read timed out')
        if scenario == 'unavailable':
            raise EngineUnavailable(detail='Mock: connection refused')
        if scenario == 'rate-limit':
            raise EngineRateLimited(detail='Mock: HTTP 429')
        if scenario == 'malformed':
            raise EngineBadResponse(detail='Mock: missing choices')

    # -- contract ---------------------------------------------------------
    def complete(self, request: EngineRequest) -> EngineReply:
        self._scenario()
        type(self).received.append(request)
        prompt_tokens = sum(_count_tokens(t.content) for t in request.turns)
        if self._queue:
            item = self._queue.pop(0)
            if isinstance(item, Exception):
                raise item
            if isinstance(item, EngineReply):
                item.input_tokens = item.input_tokens or prompt_tokens
                item.output_tokens = item.output_tokens or _count_tokens(item.text)
                item.model = item.model or self._model_name(request)
                return item
            text = json.dumps(item) if isinstance(item, (dict, list)) else str(item)
            return self._reply(request, text, prompt_tokens)

        last_user = next((t for t in reversed(request.turns) if t.role == 'user'), None)
        last_turn = request.turns[-1] if request.turns else None
        if last_turn is not None and last_turn.role == 'tool':
            status = re.search(r'"status":"(\w+)"', last_turn.content or '')
            summary = (f'The capability {last_turn.tool_name} finished with status '
                       f'"{status.group(1) if status else "unknown"}".')
            if status and status.group(1) == 'awaiting_confirmation':
                summary += ' Please confirm the operation to proceed.'
            return self._reply(request, summary, prompt_tokens)

        user_text = last_user.content if last_user else ''
        if user_text.startswith('[Interface notification'):
            decision = 'approved and executed' if 'APPROVED' in user_text else 'cancelled'
            if 'Execution status: failed' in user_text or 'Execution status: rejected' in user_text:
                decision = 'approved, but it could not be executed'
            return self._reply(request, f'Understood: the operation was {decision}.', prompt_tokens)
        match = _DIRECTIVE.search(user_text)
        tool_names = {tool.name for tool in request.tools}
        if match and match.group(1) in tool_names:
            try:
                arguments = json.loads(match.group(2) or '{}')
            except ValueError:
                arguments = {'__unparsable__': match.group(2)}
            call = ToolInvocation(call_id=f'mock_{len(self.received)}', name=match.group(1), arguments=arguments)
            return EngineReply(text='', tool_invocations=[call], input_tokens=prompt_tokens,
                               output_tokens=3, finish_reason='tool_calls', model=self._model_name(request))

        clean = _DIRECTIVE.sub('', user_text).strip()
        if last_user and last_user.images:
            clean = f'{clean} (with {len(last_user.images)} image(s))'
        if request.json_output:
            return self._reply(request, json.dumps({'value': f'Mock value for: {clean[:80]}'}), prompt_tokens)
        grounded = any('kind="document"' in t.content for t in request.turns if t.role == 'user')
        prefix = 'Based on the provided knowledge, ' if grounded else ''
        return self._reply(request, f'{prefix}[mock] You said: {clean[:300]}', prompt_tokens)

    def stream(self, request: EngineRequest) -> Iterator[StreamPiece]:
        reply = self.complete(request)
        for word in re.findall(r'\S+\s*', reply.text):
            yield StreamPiece(text=word)
        yield StreamPiece(done=True, tool_invocations=reply.tool_invocations,
                          input_tokens=reply.input_tokens, output_tokens=reply.output_tokens,
                          finish_reason=reply.finish_reason, model=reply.model)

    def embed(self, texts: list[str]) -> list[list[float]]:
        self._scenario()
        vectors = []
        for text in texts:
            vector = [0.0] * _EMBED_DIM
            for word in _WORD.findall((text or '').lower()):
                digest = hashlib.md5(word.encode(), usedforsecurity=False).digest()
                vector[digest[0] % _EMBED_DIM] += 1.0
            norm = math.sqrt(sum(v * v for v in vector)) or 1.0
            vectors.append([v / norm for v in vector])
        return vectors

    def discover_models(self) -> list[str]:
        self._scenario()
        return ['mock-large', 'mock-small']

    supports_transcription = True

    def transcribe(self, audio: bytes, mimetype: str) -> str:
        self._scenario()
        return f'Mock transcript of {len(audio)} bytes of {mimetype or "audio"}: we agreed to ship on Friday.'

    def render_image(self, prompt, size='1024x1024', quality='standard', image_format='png') -> bytes:
        self._scenario()
        return _PIXEL_PNG

    # -- internals --------------------------------------------------------
    def _model_name(self, request: EngineRequest) -> str:
        return request.model or self.settings.model or 'mock-small'

    def _reply(self, request: EngineRequest, text: str, prompt_tokens: int) -> EngineReply:
        return EngineReply(text=text, input_tokens=prompt_tokens, output_tokens=_count_tokens(text),
                           finish_reason='stop', model=self._model_name(request))
