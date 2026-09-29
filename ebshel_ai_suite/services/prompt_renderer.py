# Part of Ebshel AI Suite. See LICENSE file for full copyright and licensing details.
"""A deliberately tiny template language for prompt templates.

Supported syntax::

    {{ partner_name }}                     plain variable
    {{ record.partner_id.name }}           record path (allowed fields only)
    {{ description | striphtml | truncate(400) }}
    {{ note | default("n/a") }}
    {% if record.email %}Email: {{ record.email }}{% else %}No email{% endif %}
    {% if not urgent %}…{% endif %}

There is no expression evaluation: only dotted identifiers, a fixed set of
filters, and ``if``/``else`` on the truthiness of a single path. Anything
else is a syntax error. Record paths go through
:meth:`RecordContextBuilder.safe_value_by_path`, so secrets and fields the
user cannot read are never rendered.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from odoo.tools import html2plaintext

from .context_builder import RecordContextBuilder

MAX_TEMPLATE_SIZE = 50000
MAX_OUTPUT_SIZE = 100000

_TOKEN = re.compile(r'(\{\{.*?\}\}|\{%.*?%\})', re.S)
_PATH = re.compile(r'^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)*$')
_FILTER = re.compile(r'^([a-z_]+)(?:\((.*)\))?$', re.S)
_STRING_ARG = re.compile(r'^"((?:[^"\\]|\\.)*)"$|^\'((?:[^\'\\]|\\.)*)\'$', re.S)
_INT_ARG = re.compile(r'^\d{1,6}$')


class PromptRenderError(ValueError):
    """Raised on syntax errors, forbidden paths or (in strict mode) missing variables."""

    def __init__(self, message: str, missing: list[str] | None = None):
        super().__init__(message)
        self.missing = missing or []


@dataclass
class _Node:
    kind: str                     # text | var | if
    value: str = ''
    filters: list = field(default_factory=list)
    negate: bool = False
    body: list = field(default_factory=list)
    orelse: list = field(default_factory=list)


def _parse_arg(raw: str):
    raw = raw.strip()
    if not raw:
        return None
    match = _STRING_ARG.match(raw)
    if match:
        text = match.group(1) if match.group(1) is not None else match.group(2)
        return re.sub(r'\\(.)', r'\1', text)
    if _INT_ARG.match(raw):
        return int(raw)
    raise PromptRenderError(f'Invalid filter argument: {raw[:40]}')


def _check_path(path: str) -> str:
    path = path.strip()
    if not _PATH.match(path):
        raise PromptRenderError(f'Invalid placeholder: {path[:60]!r}')
    return path


def _filter_default(value, arg=None):
    return value if value not in (None, '', False, []) else (arg or '')


def _filter_truncate(value, arg=None):
    limit = int(arg or 200)
    text = '' if value is None else str(value)
    return text if len(text) <= limit else text[:limit].rstrip() + '…'


def _filter_join(value, arg=None):
    if isinstance(value, (list, tuple)):
        return (arg if arg is not None else ', ').join(map(str, value))
    return value


FILTERS = {
    'default': _filter_default,
    'upper': lambda value, arg=None: str(value or '').upper(),
    'lower': lambda value, arg=None: str(value or '').lower(),
    'strip': lambda value, arg=None: str(value or '').strip(),
    'truncate': _filter_truncate,
    'striphtml': lambda value, arg=None: html2plaintext(str(value)) if value else '',
    'join': _filter_join,
}


class PromptRenderer:
    """Render templates against plain variables and an optional record."""

    def __init__(self, env=None, record=None, variables: dict[str, Any] | None = None, *, strict: bool = False):
        self.env = env
        self.record = record
        self.variables = dict(variables or {})
        self.strict = strict
        self.missing: list[str] = []
        self._builder = None
        if env is not None and record is not None:
            self._builder = RecordContextBuilder(env)

    # -- parsing ---------------------------------------------------------
    @staticmethod
    def parse(template: str) -> list[_Node]:
        if len(template or '') > MAX_TEMPLATE_SIZE:
            raise PromptRenderError('Template is too large.')
        root: list[_Node] = []
        stack: list[tuple[_Node, str]] = []   # (if-node, active branch)

        def target() -> list:
            if not stack:
                return root
            node, branch = stack[-1]
            return node.body if branch == 'body' else node.orelse

        for chunk in _TOKEN.split(template or ''):
            if not chunk:
                continue
            if chunk.startswith('{{'):
                parts = [p.strip() for p in chunk[2:-2].split('|')]
                node = _Node('var', value=_check_path(parts[0]))
                for raw_filter in parts[1:]:
                    match = _FILTER.match(raw_filter)
                    if not match or match.group(1) not in FILTERS:
                        raise PromptRenderError(f'Unknown filter: {raw_filter[:40]!r}')
                    node.filters.append((match.group(1), _parse_arg(match.group(2) or '')))
                target().append(node)
            elif chunk.startswith('{%'):
                words = chunk[2:-2].split()
                if not words:
                    raise PromptRenderError('Empty block tag.')
                keyword = words[0]
                if keyword == 'if':
                    negate = len(words) == 3 and words[1] == 'not'
                    if len(words) != (3 if negate else 2):
                        raise PromptRenderError('Malformed if tag: only "{% if [not] path %}" is allowed.')
                    node = _Node('if', value=_check_path(words[-1]), negate=negate)
                    target().append(node)
                    stack.append((node, 'body'))
                elif keyword == 'else' and len(words) == 1:
                    if not stack or stack[-1][1] != 'body':
                        raise PromptRenderError('"else" without matching "if".')
                    stack[-1] = (stack[-1][0], 'orelse')
                elif keyword == 'endif' and len(words) == 1:
                    if not stack:
                        raise PromptRenderError('"endif" without matching "if".')
                    stack.pop()
                else:
                    raise PromptRenderError(f'Unsupported tag: {keyword[:20]!r}')
            else:
                target().append(_Node('text', value=chunk))
        if stack:
            raise PromptRenderError('Unclosed "if" block.')
        return root

    @classmethod
    def placeholders(cls, template: str) -> list[str]:
        found: list[str] = []

        def walk(nodes):
            for node in nodes:
                if node.kind in ('var', 'if') and node.value not in found:
                    found.append(node.value)
                walk(node.body)
                walk(node.orelse)
        walk(cls.parse(template))
        return found

    # -- evaluation ------------------------------------------------------
    def resolve(self, path: str) -> Any:
        head, _sep, rest = path.partition('.')
        if head == 'record':
            if self.record is None or self._builder is None:
                self.missing.append(path)
                return None
            if not rest:
                return self.record.display_name
            try:
                return self._builder.safe_value_by_path(self.record, rest)
            except KeyError:
                raise PromptRenderError(f'Field path not allowed in prompt: {path}') from None
        if head not in self.variables:
            self.missing.append(path)
            return None
        value = self.variables[head]
        for part in rest.split('.') if rest else []:
            if isinstance(value, dict) and part in value:
                value = value[part]
            else:
                self.missing.append(path)
                return None
        return value

    def _eval(self, nodes: list[_Node], out: list[str]) -> None:
        for node in nodes:
            if node.kind == 'text':
                out.append(node.value)
            elif node.kind == 'var':
                value = self.resolve(node.value)
                for name, arg in node.filters:
                    value = FILTERS[name](value, arg)
                if isinstance(value, (list, tuple)):
                    value = ', '.join(map(str, value))
                out.append('' if value is None or value is False else str(value))
            elif node.kind == 'if':
                before = len(self.missing)
                truthy = bool(self.resolve(node.value))
                del self.missing[before:]   # conditions may legitimately test absent values
                if node.negate:
                    truthy = not truthy
                self._eval(node.body if truthy else node.orelse, out)
            if sum(map(len, out)) > MAX_OUTPUT_SIZE:
                raise PromptRenderError('Rendered prompt is too large.')

    def render(self, template: str) -> str:
        self.missing = []
        out: list[str] = []
        self._eval(self.parse(template), out)
        if self.strict and self.missing:
            raise PromptRenderError('Missing prompt variables: ' + ', '.join(sorted(set(self.missing))),
                                    missing=sorted(set(self.missing)))
        return ''.join(out)
