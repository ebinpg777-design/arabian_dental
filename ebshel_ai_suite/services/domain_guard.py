# Part of Ebshel AI Suite. See LICENSE file for full copyright and licensing details.
"""Validation of search domains proposed by a language model.

An LLM-proposed domain is *data*, never code: it must be a JSON list made
of ``"&"``, ``"|"``, ``"!"`` and ``[field_path, operator, value]`` leaves.
Every leaf is checked against the model definition and the current user's
rights before an ORM search is run with the user's own environment, so
record rules still apply on top.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from odoo.fields import Domain

from .context_builder import RecordContextBuilder

ALLOWED_OPERATORS = {'=', '!=', '>', '>=', '<', '<=', 'in', 'not in', 'ilike', 'not ilike', '=ilike'}
TEXT_OPERATORS = {'ilike', 'not ilike', '=ilike'}
ORDER_OPERATORS = {'>', '>=', '<', '<='}
_DATE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
_DATETIME = re.compile(r'^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?$')
_ORDER = re.compile(r'^\s*([a-z_][a-z0-9_]*)(\s+(asc|desc))?\s*$', re.I)


class DomainRejected(ValueError):
    """The proposed domain is invalid or not allowed for this user."""


class DomainGuard:

    def __init__(self, env, model_name: str, *, allowed_fields: set[str] | None = None,
                 max_leaves: int = 12, max_hops: int = 2):
        if model_name not in env:
            raise DomainRejected(f'Unknown model: {model_name}')
        self.env = env
        self.model = env[model_name]
        self.allowed_fields = set(allowed_fields) if allowed_fields else None
        self.max_leaves = max_leaves
        self.max_hops = max_hops
        self.builder = RecordContextBuilder(env)
        if not self.model.has_access('read'):
            raise DomainRejected(f'You are not allowed to read {model_name}.')

    # ------------------------------------------------------------------
    def validate(self, raw: Any) -> list:
        """Return a validated domain (list form) or raise :class:`DomainRejected`."""
        if raw in (None, '', []):
            return []
        if not isinstance(raw, list):
            raise DomainRejected('A domain must be a list.')
        result: list = []
        pending = 1   # number of operands still expected (prefix notation)
        leaves = 0
        for item in raw:
            if pending == 0:
                # implicit AND between consecutive top-level terms
                result.insert(0, '&')
                pending = 1
            if isinstance(item, str):
                if item not in ('&', '|', '!'):
                    raise DomainRejected(f'Unknown domain operator {item!r}.')
                result.append(item)
                pending += 0 if item == '!' else 1
                continue
            if isinstance(item, dict):
                item = [item.get('field'), item.get('operator'), item.get('value')]
            if not isinstance(item, (list, tuple)) or len(item) != 3:
                raise DomainRejected('Each condition must be [field, operator, value].')
            leaves += 1
            if leaves > self.max_leaves:
                raise DomainRejected('Too many conditions.')
            result.append(self._validate_leaf(*item))
            pending -= 1
        if pending != 0:
            raise DomainRejected('Incomplete domain: an operator is missing an operand.')
        # Final structural check by the ORM itself.
        try:
            Domain(result).validate(self.model)
        except (ValueError, TypeError, KeyError) as exc:
            raise DomainRejected(f'Invalid domain: {exc}') from exc
        return result

    def _resolve_path(self, path: Any):
        if not isinstance(path, str) or not re.fullmatch(r'[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)*', path):
            raise DomainRejected(f'Invalid field path {path!r}.')
        parts = path.split('.')
        if len(parts) - 1 > self.max_hops:
            raise DomainRejected(f'Field path {path} is too deep.')
        if self.allowed_fields is not None and parts[0] not in self.allowed_fields:
            raise DomainRejected(f'Field {parts[0]} may not be used in searches.')
        model = self.model
        field = None
        for index, part in enumerate(parts):
            field = model._fields.get(part)
            if field is None or part.startswith('_'):
                raise DomainRejected(f'Unknown field {part} on {model._name}.')
            if part != 'id' and not self.builder.field_allowed(model, part, allow_technical=part in (
                    'create_date', 'write_date', 'display_name', 'create_uid', 'write_uid')):
                raise DomainRejected(f'Field {part} on {model._name} is not available.')
            if not field._description_searchable:
                raise DomainRejected(f'Field {part} cannot be searched.')
            if index < len(parts) - 1:
                if field.type not in ('many2one', 'many2many', 'one2many'):
                    raise DomainRejected(f'{part} is not a relational field.')
                model = self.env[field.comodel_name]
                if not model.has_access('read'):
                    raise DomainRejected(f'You are not allowed to read {model._name}.')
        return model, field

    def _validate_leaf(self, path, operator, value) -> tuple:
        model, field = self._resolve_path(path)
        if not isinstance(operator, str) or operator.lower() not in ALLOWED_OPERATORS:
            raise DomainRejected(f'Operator {operator!r} is not allowed.')
        operator = operator.lower()
        ftype = field.type
        if value is False or value is None:
            if operator not in ('=', '!='):
                raise DomainRejected('Empty values can only be compared with = or !=.')
            return (path, operator, False)
        if operator in ('in', 'not in'):
            if not isinstance(value, list) or not value or len(value) > 100:
                raise DomainRejected(f'Operator {operator} needs a non-empty list (max 100 items).')
            return (path, operator, [self._check_scalar(model, field, v, '=') for v in value])
        if operator in TEXT_OPERATORS and ftype not in ('char', 'text', 'html', 'selection', 'many2one',
                                                       'many2many', 'one2many'):
            raise DomainRejected(f'Operator {operator} is not valid for {ftype} fields.')
        if operator in ORDER_OPERATORS and ftype not in ('integer', 'float', 'monetary', 'date', 'datetime',
                                                        'char'):
            raise DomainRejected(f'Operator {operator} is not valid for {ftype} fields.')
        return (path, operator, self._check_scalar(model, field, value, operator))

    def _check_scalar(self, model, field, value, operator):
        ftype = field.type
        if ftype in ('char', 'text', 'html'):
            if not isinstance(value, str) or len(value) > 256:
                raise DomainRejected(f'{field.name} expects a short text value.')
            return value
        if ftype == 'selection':
            if not isinstance(value, str):
                raise DomainRejected(f'{field.name} expects a text value.')
            if operator in TEXT_OPERATORS:
                return value
            keys = dict(field._description_selection(self.env))
            if value not in keys:
                by_label = {str(label).lower(): key for key, label in keys.items()}
                if value.lower() in by_label:
                    return by_label[value.lower()]
                raise DomainRejected(f'{value!r} is not a valid value for {field.name}.')
            return value
        if ftype == 'boolean':
            if not isinstance(value, bool):
                raise DomainRejected(f'{field.name} expects true or false.')
            return value
        if ftype == 'integer':
            if isinstance(value, bool) or not isinstance(value, int):
                raise DomainRejected(f'{field.name} expects an integer.')
            return value
        if ftype in ('float', 'monetary'):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise DomainRejected(f'{field.name} expects a number.')
            return value
        if ftype == 'date':
            if isinstance(value, str) and _DATE.match(value):
                self._parse_date(value)
                return value
            raise DomainRejected(f'{field.name} expects a date formatted YYYY-MM-DD.')
        if ftype == 'datetime':
            if isinstance(value, str) and _DATE.match(value):
                self._parse_date(value)
                return f'{value} 00:00:00'
            if isinstance(value, str) and _DATETIME.match(value):
                return value.replace('T', ' ') + (':00' if value.count(':') == 1 else '')
            raise DomainRejected(f'{field.name} expects a datetime formatted YYYY-MM-DD HH:MM:SS.')
        if ftype in ('many2one', 'many2many', 'one2many'):
            if isinstance(value, str) and operator in TEXT_OPERATORS | {'='}:
                if len(value) > 256:
                    raise DomainRejected('Search text is too long.')
                return value
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                return value
            raise DomainRejected(f'{field.name} expects a record id or a name.')
        raise DomainRejected(f'Fields of type {ftype} cannot be searched.')

    @staticmethod
    def _parse_date(value: str) -> date:
        try:
            return datetime.strptime(value, '%Y-%m-%d').date()
        except ValueError as exc:
            raise DomainRejected(f'Invalid date {value}.') from exc

    def validate_order(self, order: Any) -> str | None:
        if not order:
            return None
        if not isinstance(order, str):
            raise DomainRejected('Order must be a string like "date_order desc".')
        clauses = []
        for clause in order.split(',')[:3]:
            match = _ORDER.match(clause)
            if not match:
                raise DomainRejected(f'Invalid order clause {clause!r}.')
            name = match.group(1)
            field = self.model._fields.get(name)
            if field is None or not field._description_sortable(self.env) or \
                    (name != 'id' and not self.builder.field_allowed(
                        self.model, name, allow_technical=name in ('create_date', 'write_date'))):
                raise DomainRejected(f'Cannot sort on {name}.')
            clauses.append(f'{name} {(match.group(3) or "asc").lower()}')
        return ', '.join(clauses)
