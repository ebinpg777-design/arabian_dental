# Part of Ebshel AI Suite. See LICENSE file for full copyright and licensing details.
"""Explicit argument schemas for capabilities.

Schemas are declared as a flat mapping, e.g.::

    {
        "customer_name": {"type": "string", "required": true,
                          "description": "Name of the customer"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        "status": {"type": "string", "enum": ["draft", "done"]}
    }

Supported types: ``string``, ``text`` (long string), ``integer``,
``number``, ``boolean``, ``date``, ``datetime``, ``record_id``, ``array``
(with ``items``) and ``object`` (free-form mapping validated by the handler).
The model never sees Python code: it receives the JSON-schema produced by
:func:`to_json_schema` and its arguments are checked by :func:`validate_arguments`.
"""
from __future__ import annotations

import re
from typing import Any

SUPPORTED_TYPES = {'string', 'text', 'integer', 'number', 'boolean', 'date', 'datetime',
                   'record_id', 'array', 'object', 'any'}
_NAME = re.compile(r'^[a-z][a-z0-9_]{0,48}$')
_DATE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
_DATETIME = re.compile(r'^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?$')
STRING_LIMIT = 500
TEXT_LIMIT = 20000
ARRAY_LIMIT = 50


class SchemaError(ValueError):
    """The schema definition itself is invalid (administrator error)."""


class ArgumentError(ValueError):
    """The arguments proposed by the model do not satisfy the schema."""


def check_schema(schema: Any) -> dict:
    if schema in (None, ''):
        return {}
    if not isinstance(schema, dict):
        raise SchemaError('The argument schema must be a JSON object.')
    for name, spec in schema.items():
        if not _NAME.match(name):
            raise SchemaError(f'Invalid argument name {name!r} (lowercase letters, digits and _ only).')
        if not isinstance(spec, dict):
            raise SchemaError(f'Definition of {name!r} must be an object.')
        if spec.get('type') not in SUPPORTED_TYPES:
            raise SchemaError(f'Argument {name!r} has an unsupported type {spec.get("type")!r}.')
        if 'enum' in spec and not isinstance(spec['enum'], list):
            raise SchemaError(f'"enum" of {name!r} must be a list.')
        if spec['type'] == 'array':
            item = spec.get('items') or {'type': 'string'}
            if not isinstance(item, dict) or item.get('type') not in SUPPORTED_TYPES - {'array'}:
                raise SchemaError(f'"items" of {name!r} must declare a supported, non-array type.')
    return schema


def _json_type(spec: dict) -> dict:
    kind = spec['type']
    mapping = {
        'string': {'type': 'string'}, 'text': {'type': 'string'},
        'integer': {'type': 'integer'}, 'number': {'type': 'number'},
        'boolean': {'type': 'boolean'},
        'date': {'type': 'string', 'description': 'Date formatted YYYY-MM-DD'},
        'datetime': {'type': 'string', 'description': 'Datetime formatted YYYY-MM-DD HH:MM:SS (UTC)'},
        'record_id': {'type': 'integer', 'description': 'Database id of the record'},
        'object': {'type': 'object'},
        'any': {},
    }
    if kind == 'array':
        result = {'type': 'array', 'items': _json_type(spec.get('items') or {'type': 'string'})}
    else:
        result = dict(mapping[kind])
    description = ' '.join(filter(None, [spec.get('description'), result.get('description')]))
    if description:
        result['description'] = description
    if 'enum' in spec:
        result['enum'] = spec['enum']
    return result


def to_json_schema(schema: dict) -> dict:
    """Convert a capability schema into a JSON-schema ``object`` for engines."""
    properties = {name: _json_type(spec) for name, spec in (schema or {}).items()}
    required = [name for name, spec in (schema or {}).items() if spec.get('required')]
    result = {'type': 'object', 'properties': properties}
    if required:
        result['required'] = required
    return result


def _coerce(name: str, spec: dict, value: Any) -> Any:
    kind = spec['type']
    if kind in ('string', 'text'):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            value = str(value)
        if not isinstance(value, str):
            raise ArgumentError(f'"{name}" must be a string.')
        limit = spec.get('max_length') or (TEXT_LIMIT if kind == 'text' else STRING_LIMIT)
        if len(value) > limit:
            raise ArgumentError(f'"{name}" is too long (max {limit} characters).')
        value = value.strip()
    elif kind in ('integer', 'record_id'):
        if isinstance(value, str) and value.strip().isdigit():
            value = int(value.strip())
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ArgumentError(f'"{name}" must be an integer.')
        if kind == 'record_id' and value <= 0:
            raise ArgumentError(f'"{name}" must be a positive record id.')
    elif kind == 'number':
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ArgumentError(f'"{name}" must be a number.')
    elif kind == 'boolean':
        if not isinstance(value, bool):
            raise ArgumentError(f'"{name}" must be true or false.')
    elif kind == 'date':
        if not isinstance(value, str) or not _DATE.match(value):
            raise ArgumentError(f'"{name}" must be a date formatted YYYY-MM-DD.')
    elif kind == 'datetime':
        if not isinstance(value, str) or not _DATETIME.match(value):
            raise ArgumentError(f'"{name}" must be a datetime formatted YYYY-MM-DD HH:MM:SS.')
    elif kind == 'object':
        if not isinstance(value, dict):
            raise ArgumentError(f'"{name}" must be an object.')
        if len(value) > ARRAY_LIMIT:
            raise ArgumentError(f'"{name}" has too many keys.')
    elif kind == 'any':
        if len(repr(value)) > TEXT_LIMIT:
            raise ArgumentError(f'"{name}" is too large.')
    elif kind == 'array':
        if not isinstance(value, list):
            raise ArgumentError(f'"{name}" must be a list.')
        if len(value) > spec.get('max_items', ARRAY_LIMIT):
            raise ArgumentError(f'"{name}" has too many items.')
        item_spec = spec.get('items') or {'type': 'string'}
        value = [_coerce(f'{name}[{i}]', item_spec, item) for i, item in enumerate(value)]
    if 'enum' in spec and value not in spec['enum']:
        raise ArgumentError(f'"{name}" must be one of: {", ".join(map(str, spec["enum"]))}.')
    if kind in ('integer', 'number'):
        if 'minimum' in spec and value < spec['minimum']:
            raise ArgumentError(f'"{name}" must be at least {spec["minimum"]}.')
        if 'maximum' in spec and value > spec['maximum']:
            raise ArgumentError(f'"{name}" must be at most {spec["maximum"]}.')
    return value


def validate_arguments(schema: dict, arguments: Any) -> dict:
    """Validate and coerce ``arguments`` against ``schema``.

    Unknown parameters, missing required parameters, wrong types, values out
    of range or outside ``enum`` raise :class:`ArgumentError`.
    """
    if arguments in (None, ''):
        arguments = {}
    if not isinstance(arguments, dict):
        raise ArgumentError('Arguments must be a JSON object.')
    if '__unparsable__' in arguments:
        raise ArgumentError('Arguments were not valid JSON.')
    schema = schema or {}
    unknown = sorted(set(arguments) - set(schema))
    if unknown:
        raise ArgumentError(f'Unknown parameter(s): {", ".join(unknown)}.')
    cleaned = {}
    for name, spec in schema.items():
        if name not in arguments or arguments[name] in (None, ''):   # 0 and False are real values
            if spec.get('required'):
                raise ArgumentError(f'Missing required parameter "{name}".')
            if 'default' in spec:
                cleaned[name] = spec['default']
            continue
        cleaned[name] = _coerce(name, spec, arguments[name])
    return cleaned
