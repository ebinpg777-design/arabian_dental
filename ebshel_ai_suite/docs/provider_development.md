# Developing an engine (provider) adapter

Adapters translate the neutral structures of `services/engines/base.py` to a vendor API.
They never touch the ORM and receive everything through `EngineSettings`.

## 1. Write the adapter

```python
# my_ai_engine/services/acme.py
from odoo.addons.ebshel_ai_suite.services.engines import (
    EngineAdapter, EngineReply, StreamPiece, ToolInvocation, register_engine)
from odoo.addons.ebshel_ai_suite.services.engines.errors import EngineBadResponse


@register_engine('acme')
class AcmeEngine(EngineAdapter):
    supports_streaming = False      # the base class then emits the whole answer at once
    supports_embeddings = False

    def complete(self, request):
        payload = {
            'model': self._model(request),
            'messages': [{'role': t.role, 'content': t.content} for t in request.turns],
        }
        response = self._http('POST', f'{self.settings.endpoint_url}/generate',
                              headers={'X-Key': self.settings.secret}, payload=payload)
        data = self._json(response)
        if 'output' not in data:
            raise EngineBadResponse(detail='missing "output"')
        return EngineReply(text=data['output'], input_tokens=data.get('in', 0),
                           output_tokens=data.get('out', 0), model=payload['model'])

    def discover_models(self):
        return ['acme-small', 'acme-large']
```

`_http()` already maps timeouts, connection failures and HTTP status codes to the error
classes (`EngineAuthError` 401/403, `EngineConfigError` 404, `EngineRateLimited` 429,
`EngineUnavailable` 5xx, `EngineRequestRejected` other 4xx) and redacts secrets from the
detail. `_iter_sse_data()` helps with server-sent-event streams.

## 2. Register the engine type

```python
# my_ai_engine/models/connection.py
from odoo import fields, models
from ..services import acme  # noqa: F401  (registers the adapter)


class CommunityAIConnection(models.Model):
    _inherit = 'community.ai.connection'

    engine_type = fields.Selection(selection_add=[('acme', 'Acme AI')],
                                   ondelete={'acme': 'set default'})
```

Manifest: `'depends': ['ebshel_ai_suite']`.

## 3. Contract

| Method | Required | Notes |
| --- | --- | --- |
| `complete(request) -> EngineReply` | yes | Honour `request.tools` (return `ToolInvocation`s), `request.json_output`, `temperature`, `max_tokens`. |
| `stream(request) -> Iterator[StreamPiece]` | no | Yield text pieces, then one `done=True` piece with tool invocations and token counts. |
| `embed(texts) -> list[list[float]]` | no | Needed for semantic knowledge indexing. |
| `check_credentials()` | no | Default: `discover_models()`. Raise an `EngineError` on failure. |
| `discover_models() -> list[str]` | no | Used by *Fetch Models*. |
| `render_image(prompt, size, quality, image_format) -> bytes` | no | Used by the image wizard. |

Message roles in `request.turns`: `system`, `user`, `assistant` (may carry
`tool_invocations`), `tool` (carries `tool_call_id`, `tool_name` and the fenced result).
Tool parameters are standard JSON schema objects.

## 4. Test it without the network

Patch `requests.request` and assert on the payload and the parsed reply — see
`tests/test_provider.py` for the OpenAI-compatible and Gemini examples. For higher-level
tests use the `mock` engine: `MockEngine.queue(...)` pre-programs answers (strings, dicts
encoded as JSON, `EngineReply` objects or exceptions), and `MockEngine.received` records
every request.
