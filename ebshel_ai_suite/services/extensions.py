# Part of Ebshel AI Suite. See LICENSE file for full copyright and licensing details.
"""Optional extension points that ship without a concrete implementation.

Web search
----------
Register a backend in your own module::

    from odoo.addons.ebshel_ai_suite.services.extensions import (
        WebSearchBackend, register_web_backend)

    @register_web_backend('my_search')
    class MySearch(WebSearchBackend):
        def search(self, query, limit=5):
            return [{'title': ..., 'url': ..., 'snippet': ...}]

then set the system parameter ``ebshel_ai_suite.web_backend`` to
``my_search`` and enable the "Search the web" capability on an assistant.

Two backends ship with the module: ``brave`` (Brave Search API, needs an API
key) and ``searxng`` (a self-hosted SearXNG instance with the JSON format
enabled).
"""
from __future__ import annotations

import logging

from .guardrails import redact_secrets, truncate

_logger = logging.getLogger(__name__)

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

_WEB_BACKENDS: dict[str, type[WebSearchBackend]] = {}


class WebSearchBackend:
    """Interface of a web search provider."""

    def __init__(self, env):
        self.env = env

    def search(self, query: str, limit: int = 5) -> list[dict]:
        """Return ``[{'title', 'url', 'snippet'}]``. Results are treated as
        untrusted data by the capability runner."""
        raise NotImplementedError


def register_web_backend(key: str):
    def decorator(cls):
        _WEB_BACKENDS[key] = cls
        return cls
    return decorator


def get_web_backend(env) -> WebSearchBackend | None:
    key = env['ir.config_parameter'].sudo().get_param('ebshel_ai_suite.web_backend')
    backend_class = _WEB_BACKENDS.get(key or '')
    return backend_class(env) if backend_class else None


class WebSearchUnavailable(Exception):
    """The configured web search backend failed; the message is user-presentable."""


def _get_json(url: str, *, params: dict, headers: dict | None = None) -> dict:
    if requests is None:  # pragma: no cover
        raise WebSearchUnavailable('The python "requests" library is not available.')
    try:
        response = requests.get(url, params=params, headers=headers or {}, timeout=15)
        response.raise_for_status()
        return response.json()
    except (requests.exceptions.RequestException, ValueError) as exc:
        _logger.info('Web search failed: %s', redact_secrets(str(exc))[:300])
        raise WebSearchUnavailable('The web search service did not answer correctly.') from exc


def _param(env, key: str) -> str:
    return env['ir.config_parameter'].sudo().get_param(f'ebshel_ai_suite.{key}') or ''


@register_web_backend('brave')
class BraveWebSearch(WebSearchBackend):
    """Brave Search API (https://api.search.brave.com)."""

    def search(self, query, limit=5):
        key = _param(self.env, 'brave_api_key')
        if not key:
            raise WebSearchUnavailable('No Brave Search API key is configured.')
        data = _get_json('https://api.search.brave.com/res/v1/web/search',
                         params={'q': query, 'count': min(limit, 10)},
                         headers={'Accept': 'application/json', 'X-Subscription-Token': key})
        results = (data.get('web') or {}).get('results') or []
        return [{'title': truncate(r.get('title') or '', 200), 'url': r.get('url') or '',
                 'snippet': truncate(r.get('description') or '', 500)} for r in results[:limit]]


@register_web_backend('searxng')
class SearxngWebSearch(WebSearchBackend):
    """Self-hosted SearXNG instance (``format=json`` must be enabled on the instance)."""

    def search(self, query, limit=5):
        base = _param(self.env, 'searxng_url').rstrip('/')
        if not base.startswith(('http://', 'https://')):
            raise WebSearchUnavailable('No SearXNG URL is configured.')
        data = _get_json(f'{base}/search', params={'q': query, 'format': 'json'})
        results = data.get('results') or []
        return [{'title': truncate(r.get('title') or '', 200), 'url': r.get('url') or '',
                 'snippet': truncate(r.get('content') or '', 500)} for r in results[:limit]]
