# Part of Ebshel AI Suite. See LICENSE file for full copyright and licensing details.
"""Adapter for the de-facto standard "chat completions" wire protocol.

Works with OpenAI and with the many servers exposing the same protocol
(Azure-style gateways, vLLM, LM Studio, Ollama's ``/v1`` endpoint, LocalAI,
OpenRouter, Mistral, Groq, ...). Only the base URL and key differ.
"""
from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING

from .base import (
    ChatTurn,
    EngineAdapter,
    EngineReply,
    EngineRequest,
    StreamPiece,
    ToolInvocation,
    register_engine,
)
from .errors import EngineBadResponse, EngineConfigError

if TYPE_CHECKING:
    from collections.abc import Iterator

DEFAULT_BASE_URL = 'https://api.openai.com/v1'


@register_engine('openai_compatible')
class OpenAICompatibleEngine(EngineAdapter):
    supports_streaming = True
    supports_embeddings = True
    supports_images = True
    supports_transcription = True

    # -- wire helpers -----------------------------------------------------
    @property
    def base_url(self) -> str:
        return (self.settings.endpoint_url or DEFAULT_BASE_URL).rstrip('/')

    def _headers(self) -> dict:
        headers = {'Content-Type': 'application/json'}
        if self.settings.secret:
            headers['Authorization'] = f'Bearer {self.settings.secret}'
        if self.settings.organization:
            headers['OpenAI-Organization'] = self.settings.organization
        return headers

    @staticmethod
    def _encode_turn(turn: ChatTurn) -> dict:
        if turn.role == 'tool':
            return {'role': 'tool', 'tool_call_id': turn.tool_call_id or '', 'content': turn.content or ''}
        message = {'role': turn.role, 'content': turn.content or ''}
        if turn.role == 'user' and turn.images:
            message['content'] = [{'type': 'text', 'text': turn.content or ''}] + [{
                'type': 'image_url',
                'image_url': {'url': f'data:{mimetype};base64,{base64.b64encode(data).decode()}'},
            } for mimetype, data in turn.images]
        if turn.role == 'assistant' and turn.tool_invocations:
            message['content'] = turn.content or None
            message['tool_calls'] = [{
                'id': call.call_id,
                'type': 'function',
                'function': {'name': call.name, 'arguments': json.dumps(call.arguments)},
            } for call in turn.tool_invocations]
        return message

    def _payload(self, request: EngineRequest, stream: bool = False) -> dict:
        payload = {
            'model': self._model(request),
            'messages': [self._encode_turn(t) for t in request.turns],
        }
        temperature = request.temperature if request.temperature is not None else self.settings.temperature
        if temperature is not None and not self.settings.extra.get('omit_sampling'):
            payload['temperature'] = temperature
        max_tokens = request.max_tokens or self.settings.max_tokens
        if max_tokens:
            payload[self.settings.extra.get('token_param') or 'max_tokens'] = max_tokens
        if request.tools:
            payload['tools'] = [{
                'type': 'function',
                'function': {'name': t.name, 'description': t.description, 'parameters': t.parameters},
            } for t in request.tools]
        if request.json_output and self.settings.json_mode:
            payload['response_format'] = {'type': 'json_object'}
        if stream:
            payload['stream'] = True
            if self.settings.extra.get('stream_usage', True):
                payload['stream_options'] = {'include_usage': True}
        return payload

    # -- contract ---------------------------------------------------------
    def complete(self, request: EngineRequest) -> EngineReply:
        response = self._http('POST', f'{self.base_url}/chat/completions',
                              headers=self._headers(), payload=self._payload(request))
        data = self._json(response)
        try:
            choice = data['choices'][0]
            message = choice['message']
        except (KeyError, IndexError, TypeError) as exc:
            raise EngineBadResponse(detail='Missing "choices[0].message" in response.') from exc
        calls = []
        for index, raw in enumerate(message.get('tool_calls') or []):
            function = raw.get('function') or {}
            calls.append(ToolInvocation(
                call_id=raw.get('id') or f'call_{index}',
                name=function.get('name') or '',
                arguments=self._parse_arguments(function.get('arguments')),
                raw_arguments=function.get('arguments') or '',
            ))
        usage = data.get('usage') or {}
        content = message.get('content')
        if isinstance(content, list):  # some servers return content parts
            content = ''.join(part.get('text', '') for part in content if isinstance(part, dict))
        return EngineReply(
            text=content or '',
            tool_invocations=calls,
            input_tokens=int(usage.get('prompt_tokens') or 0),
            output_tokens=int(usage.get('completion_tokens') or 0),
            finish_reason=choice.get('finish_reason') or '',
            model=data.get('model') or self._model(request),
        )

    def stream(self, request: EngineRequest) -> Iterator[StreamPiece]:
        response = self._http('POST', f'{self.base_url}/chat/completions', headers=self._headers(),
                              payload=self._payload(request, stream=True), stream=True)
        partial_calls: dict[int, dict] = {}
        usage: dict = {}
        finish_reason = ''
        model = self._model(request)
        try:
            for data in self._iter_sse_data(response):
                if data == '[DONE]':
                    break
                try:
                    event = json.loads(data)
                except ValueError as exc:
                    raise EngineBadResponse(detail='Invalid JSON chunk in stream.') from exc
                usage = event.get('usage') or usage
                model = event.get('model') or model
                for choice in event.get('choices') or []:
                    delta = choice.get('delta') or {}
                    finish_reason = choice.get('finish_reason') or finish_reason
                    if delta.get('content'):
                        yield StreamPiece(text=delta['content'])
                    for raw in delta.get('tool_calls') or []:
                        slot = partial_calls.setdefault(raw.get('index', 0), {'id': '', 'name': '', 'args': ''})
                        slot['id'] = raw.get('id') or slot['id']
                        function = raw.get('function') or {}
                        slot['name'] += function.get('name') or ''
                        slot['args'] += function.get('arguments') or ''
        finally:
            response.close()
        calls = [
            ToolInvocation(call_id=slot['id'] or f'call_{index}', name=slot['name'],
                           arguments=self._parse_arguments(slot['args']), raw_arguments=slot['args'])
            for index, slot in sorted(partial_calls.items())
        ]
        yield StreamPiece(
            done=True, tool_invocations=calls,
            input_tokens=int(usage.get('prompt_tokens') or 0),
            output_tokens=int(usage.get('completion_tokens') or 0),
            finish_reason=finish_reason, model=model,
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        model = self.settings.embedding_model
        if not model:
            raise EngineConfigError(detail='No embedding model configured.')
        response = self._http('POST', f'{self.base_url}/embeddings', headers=self._headers(),
                              payload={'model': model, 'input': texts})
        data = self._json(response)
        try:
            rows = sorted(data['data'], key=lambda row: row.get('index', 0))
            return [list(map(float, row['embedding'])) for row in rows]
        except (KeyError, TypeError, ValueError) as exc:
            raise EngineBadResponse(detail='Malformed embeddings response.') from exc

    def discover_models(self) -> list[str]:
        response = self._http('GET', f'{self.base_url}/models', headers=self._headers())
        data = self._json(response)
        try:
            return sorted({row['id'] for row in data.get('data', [])})
        except (KeyError, TypeError, AttributeError) as exc:
            raise EngineBadResponse(detail='Malformed model list.') from exc

    def check_credentials(self) -> None:
        try:
            self.discover_models()
        except EngineConfigError:
            # Some compatible servers do not implement /models: fall back to
            # a minimal completion, which also validates the model name.
            self.complete(EngineRequest(turns=[ChatTurn('user', 'ping')], max_tokens=1))

    def transcribe(self, audio: bytes, mimetype: str) -> str:
        model = self.settings.transcription_model
        if not model:
            raise EngineConfigError(detail='No transcription model configured.')
        headers = {key: value for key, value in self._headers().items() if key != 'Content-Type'}
        extension = (mimetype or 'audio/webm').split('/')[-1].split(';')[0] or 'webm'
        response = self._http('POST', f'{self.base_url}/audio/transcriptions', headers=headers,
                              files={'file': (f'recording.{extension}', audio, mimetype or 'audio/webm')},
                              form={'model': model}, timeout=max(self.settings.timeout, 180))
        data = self._json(response)
        if not isinstance(data, dict) or 'text' not in data:
            raise EngineBadResponse(detail='Malformed transcription response.')
        return data['text'] or ''

    def render_image(self, prompt: str, size: str = '1024x1024', quality: str = 'standard',
                     image_format: str = 'png') -> bytes:
        model = self.settings.image_model
        if not model:
            raise EngineConfigError(detail='No image model configured.')
        payload = {'model': model, 'prompt': prompt, 'size': size, 'n': 1}
        if model.startswith('dall-e'):
            payload['response_format'] = 'b64_json'
            if quality in ('standard', 'hd'):
                payload['quality'] = quality
        else:
            payload['output_format'] = image_format
        response = self._http('POST', f'{self.base_url}/images/generations',
                              headers=self._headers(), payload=payload, timeout=max(self.settings.timeout, 120))
        data = self._json(response)
        try:
            return base64.b64decode(data['data'][0]['b64_json'])
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise EngineBadResponse(detail='Malformed image response.') from exc
