# Part of Ebshel AI Suite. See LICENSE file for full copyright and licensing details.
"""Convert model-produced values into safe ORM write values."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from odoo.fields import Command
from odoo.tools import html_sanitize, plaintext2html

SUPPORTED_WRITE_TYPES = {'char', 'text', 'html', 'integer', 'float', 'monetary', 'date', 'datetime',
                         'boolean', 'selection', 'many2one', 'many2many'}
_TRUE = {'true', 'yes', 'y', '1', 'on', 'oui', 'si', 'ja'}
_FALSE = {'false', 'no', 'n', '0', 'off', 'non', 'nein'}


class ConversionError(ValueError):
    pass


def _to_number(value: Any, integer: bool):
    if isinstance(value, bool):
        raise ConversionError('expected a number')
    if isinstance(value, str):
        cleaned = value.strip().replace(' ', '').replace(' ', '')
        if cleaned.count(',') == 1 and '.' not in cleaned:
            cleaned = cleaned.replace(',', '.')
        cleaned = cleaned.replace(',', '')
        try:
            value = float(cleaned)
        except ValueError as exc:
            raise ConversionError(f'{value!r} is not a number') from exc
    if not isinstance(value, (int, float)):
        raise ConversionError('expected a number')
    if integer:
        if isinstance(value, float) and not value.is_integer():
            raise ConversionError(f'{value} is not an integer')
        return int(value)
    return float(value)


def _resolve_record(env, comodel: str, value: Any):
    Model = env[comodel]
    if isinstance(value, dict):
        value = value.get('id') or value.get('name')
    if isinstance(value, int) and not isinstance(value, bool):
        record = Model.browse(value).exists()
        if not record or not record.has_access('read'):
            raise ConversionError(f'no accessible {comodel} with id {value}')
        return record
    if isinstance(value, str) and value.strip():
        name = value.strip()
        matches = Model.search([('display_name', '=ilike', name)], limit=2)
        if len(matches) != 1:
            matches = Model.search([('display_name', 'ilike', name)], limit=2)
        if len(matches) == 1:
            return matches
        raise ConversionError(f'{name!r} does not match exactly one {comodel}')
    raise ConversionError(f'cannot interpret {value!r} as a {comodel}')


def convert_value(env, field, value: Any) -> Any:
    """Return a value suitable for ``record.write({field.name: ...})``.

    :raises ConversionError: when the value does not fit the field.
    """
    ftype = field.type
    if ftype not in SUPPORTED_WRITE_TYPES:
        raise ConversionError(f'fields of type {ftype} are not supported')
    if value is None or (isinstance(value, str) and not value and ftype not in ('char', 'text', 'html')):
        if field.required:
            raise ConversionError(f'{field.string} is required')
        return False
    if ftype in ('char', 'text'):
        if isinstance(value, (dict, list)):
            raise ConversionError('expected text')
        text = str(value).strip()
        if field.type == 'char' and field.size:
            text = text[: field.size]
        return text
    if ftype == 'html':
        text = str(value)
        if '<' not in text:
            return plaintext2html(text)
        return html_sanitize(text)
    if ftype == 'integer':
        return _to_number(value, integer=True)
    if ftype in ('float', 'monetary'):
        return _to_number(value, integer=False)
    if ftype == 'boolean':
        if isinstance(value, bool):
            return value
        lowered = str(value).strip().lower()
        if lowered in _TRUE:
            return True
        if lowered in _FALSE:
            return False
        raise ConversionError(f'{value!r} is not a boolean')
    if ftype == 'date':
        try:
            return datetime.strptime(str(value).strip()[:10], '%Y-%m-%d').date()
        except ValueError as exc:
            raise ConversionError(f'{value!r} is not a YYYY-MM-DD date') from exc
    if ftype == 'datetime':
        raw = str(value).strip().replace('T', ' ').rstrip('Z')
        for pattern in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
            try:
                return datetime.strptime(raw[:19], pattern)
            except ValueError:
                continue
        raise ConversionError(f'{value!r} is not a datetime')
    if ftype == 'selection':
        options = dict(field._description_selection(env))
        if value in options:
            return value
        lowered = str(value).strip().lower()
        for key, label in options.items():
            if lowered in (str(key).lower(), str(label).lower()):
                return key
        raise ConversionError(f'{value!r} is not one of {", ".join(map(str, options))}')
    if ftype == 'many2one':
        return _resolve_record(env, field.comodel_name, value).id
    if ftype == 'many2many':
        items = value if isinstance(value, list) else [value]
        if len(items) > 50:
            raise ConversionError('too many items')
        ids = [_resolve_record(env, field.comodel_name, item).id for item in items]
        return [Command.set(ids)]
    raise ConversionError(f'fields of type {ftype} are not supported')  # pragma: no cover


def describe_field_for_model(env, field) -> str:
    """Short type description used in prompts that ask for structured output."""
    ftype = field.type
    if ftype == 'selection':
        keys = [str(k) for k, _label in field._description_selection(env)]
        return f'one of {keys}'
    return {
        'char': 'short text', 'text': 'text', 'html': 'text (may contain simple HTML)',
        'integer': 'integer', 'float': 'number', 'monetary': 'number (amount)',
        'date': 'date YYYY-MM-DD', 'datetime': 'datetime YYYY-MM-DD HH:MM:SS (UTC)',
        'boolean': 'true or false', 'many2one': f'name or id of one {field.comodel_name} record',
        'many2many': f'list of names or ids of {field.comodel_name} records',
    }.get(ftype, ftype)
