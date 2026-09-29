# Part of Ebshel AI Suite. See LICENSE file for full copyright and licensing details.
"""Adapter for the Google Gemini ``generateContent`` REST API."""
from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING

from .base import (
    EngineAdapter,
    EngineReply,
    EngineRequest,
    StreamPiece,
    ToolInvocation,
    register_engine,
)
from .errors import EngineBadResponse, EngineConfigError, EngineRequestRejected

if TYPE_CHECKING:
    from collections.abc import Iterator

DEFAULT_BASE_URL = 'https://generativelanguage.googleapis.com/v1beta'

# Gemini accepts an OpenAPI subset for function parameters.
_SCHEMA_KEYS = {'type', 'description', 'properties', 'required', 'enum', 'items', 'format', 'nullable'}


def _clean_schema(schema: dict) -> dict:
    cleaned = {}
    for key, value in (schema or {}).items():
        if key not in _SCHEMA_KEYS:
            continue
        if key == 'properties':
            cleaned[key] = {name: _clean_schema(sub) for name, sub in value.items()}
        elif key == 'items':
            cleaned[key] = _clean_schema(value)
        elif key == 'format' and value not in ('date-time', 'enum'):
            continue
        else:
            cleaned[key] = value
    return cleaned


@register_engine('gemini')
class GeminiEngine(EngineAdapter):
    supports_streaming = True
    supports_embeddings = True
    supports_transcription = True

    @property
    def base_url(self) -> str:
        return (self.settings.endpoint_url or DEFAULT_BASE_URL).rstrip('/')

    def _headers(self) -> dict:
        return {'Content-Type': 'application/json', 'x-goog-api-key': self.settings.secret or ''}

    def _payload(self, request: EngineRequest) -> dict:
        system_parts = []
        contents: list[dict] = []

        def push(role: str, part: dict):
            if contents and contents[-1]['role'] == role:
                contents[-1]['parts'].append(part)
            else:
                contents.append({'role': role, 'parts': [part]})

        for turn in request.turns:
            if turn.role == 'system':
                system_parts.append({'text': turn.content})
            elif turn.role == 'user':
                push('user', {'text': turn.content or ' '})
                for mimetype, data in turn.images:
                    push('user', {'inlineData': {'mimeType': mimetype, 'data': base64.b64encode(data).decode()}})
            elif turn.role == 'assistant':
                if turn.content:
                    push('model', {'text': turn.content})
                for call in turn.tool_invocations:
                    push('model', {'functionCall': {'name': call.name, 'args': call.arguments}})
            elif turn.role == 'tool':
                try:
                    result = json.loads(turn.content)
                except (TypeError, ValueError):
                    result = None
                if not isinstance(result, dict):
                    result = {'content': turn.content}
                push('user', {'functionResponse': {'name': turn.tool_name or '', 'response': result}})

        payload: dict = {'contents': contents}
        if system_parts:
            payload['systemInstruction'] = {'parts': system_parts}
        generation = {}
        temperature = request.temperature if request.temperature is not None else self.settings.temperature
        if temperature is not None and not self.settings.extra.get('omit_sampling'):
            generation['temperature'] = temperature
        max_tokens = request.max_tokens or self.settings.max_tokens
        if max_tokens:
            generation['maxOutputTokens'] = max_tokens
        if request.json_output and self.settings.json_mode:
            generation['responseMimeType'] = 'application/json'
        if generation:
            payload['generationConfig'] = generation
        if request.tools:
            payload['tools'] = [{'functionDeclarations': [{
                'name': tool.name,
                'description': tool.description,
                'parameters': _clean_schema(tool.parameters),
            } for tool in request.tools]}]
        return payload

    @staticmethod
    def _read_candidate(data: dict, counter_start: int = 0):
        if data.get('promptFeedback', {}).get('blockReason') and not data.get('candidates'):
            raise EngineRequestRejected(detail=f"Prompt blocked: {data['promptFeedback']['blockReason']}")
        candidates = data.get('candidates')
        if not isinstance(candidates, list) or not candidates:
            raise EngineBadResponse(detail='Missing "candidates" in response.')
        candidate = candidates[0]
        parts = (candidate.get('content') or {}).get('parts') or []
        texts, calls = [], []
        for part in parts:
            if 'text' in part and not part.get('thought'):
                texts.append(part['text'])
            elif 'functionCall' in part:
                call = part['functionCall']
                calls.append(ToolInvocation(
                    call_id=call.get('id') or f'gemini_call_{counter_start + len(calls)}',
                    name=call.get('name') or '',
                    arguments=call.get('args') or {},
                ))
        return ''.join(texts), calls, candidate.get('finishReason') or ''

    def complete(self, request: EngineRequest) -> EngineReply:
        model = self._model(request)
        response = self._http('POST', f'{self.base_url}/models/{model}:generateContent',
                              headers=self._headers(), payload=self._payload(request))
        data = self._json(response)
        if not isinstance(data, dict):
            raise EngineBadResponse(detail='Unexpected response type.')
        text, calls, finish = self._read_candidate(data)
        usage = data.get('usageMetadata') or {}
        return EngineReply(
            text=text, tool_invocations=calls,
            input_tokens=int(usage.get('promptTokenCount') or 0),
            output_tokens=int(usage.get('candidatesTokenCount') or 0),
            finish_reason=finish, model=data.get('modelVersion') or model,
        )

    def stream(self, request: EngineRequest) -> Iterator[StreamPiece]:
        model = self._model(request)
        response = self._http('POST', f'{self.base_url}/models/{model}:streamGenerateContent?alt=sse',
                              headers=self._headers(), payload=self._payload(request), stream=True)
        calls: list[ToolInvocation] = []
        usage: dict = {}
        finish = ''
        try:
            for data in self._iter_sse_data(response):
                try:
                    event = json.loads(data)
                except ValueError as exc:
                    raise EngineBadResponse(detail='Invalid JSON chunk in stream.') from exc
                usage = event.get('usageMetadata') or usage
                if not event.get('candidates'):
                    continue
                text, new_calls, finish_part = self._read_candidate(event, len(calls))
                finish = finish_part or finish
                calls.extend(new_calls)
                if text:
                    yield StreamPiece(text=text)
        finally:
            response.close()
        yield StreamPiece(
            done=True, tool_invocations=calls,
            input_tokens=int(usage.get('promptTokenCount') or 0),
            output_tokens=int(usage.get('candidatesTokenCount') or 0),
            finish_reason=finish, model=model,
        )

    def transcribe(self, audio: bytes, mimetype: str) -> str:
        model = self.settings.transcription_model or self._model()
        payload = {'contents': [{'role': 'user', 'parts': [
            {'text': 'Transcribe this audio verbatim. Answer with the transcript only.'},
            {'inlineData': {'mimeType': (mimetype or 'audio/webm').split(';')[0],
                            'data': base64.b64encode(audio).decode()}},
        ]}]}
        response = self._http('POST', f'{self.base_url}/models/{model}:generateContent',
                              headers=self._headers(), payload=payload, timeout=max(self.settings.timeout, 180))
        text, _calls, _finish = self._read_candidate(self._json(response))
        return text.strip()

    def embed(self, texts: list[str]) -> list[list[float]]:
        model = self.settings.embedding_model
        if not model:
            raise EngineConfigError(detail='No embedding model configured.')
        payload = {'requests': [
            {'model': f'models/{model}', 'content': {'parts': [{'text': text}]}} for text in texts
        ]}
        response = self._http('POST', f'{self.base_url}/models/{model}:batchEmbedContents',
                              headers=self._headers(), payload=payload)
        data = self._json(response)
        try:
            return [list(map(float, row['values'])) for row in data['embeddings']]
        except (KeyError, TypeError, ValueError) as exc:
            raise EngineBadResponse(detail='Malformed embeddings response.') from exc

    def discover_models(self) -> list[str]:
        response = self._http('GET', f'{self.base_url}/models?pageSize=200', headers=self._headers())
        data = self._json(response)
        try:
            return sorted(
                row['name'].split('/', 1)[-1] for row in data.get('models', [])
                if 'generateContent' in (row.get('supportedGenerationMethods') or [])
                or 'embedContent' in (row.get('supportedGenerationMethods') or [])
            )
        except (KeyError, TypeError, AttributeError) as exc:
            raise EngineBadResponse(detail='Malformed model list.') from exc
