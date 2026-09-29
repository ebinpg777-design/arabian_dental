# Part of Ebshel AI Suite. See LICENSE file for full copyright and licensing details.
"""Turns Odoo records into a compact, privacy-filtered context payload.

Nothing is serialized blindly. For a record to reach the model:

1. the effective user must be able to read it (ACL + record rules);
2. each field must be readable by that user (field ``groups``);
3. the field must not match a baseline secret pattern nor an administrator
   defined privacy rule (``community.ai.privacy.rule``);
4. binary/image fields are skipped unless explicitly requested;
5. values are converted to short human-readable text and the whole payload
   is capped (default 12 000 characters).
"""
from __future__ import annotations

import fnmatch
import logging
from typing import Any

from odoo.exceptions import AccessError, MissingError
from odoo.tools import html2plaintext

from .guardrails import redact_secrets, truncate, wrap_untrusted

_logger = logging.getLogger(__name__)

#: Field-name patterns that can never be sent to an engine, whatever the settings.
BASELINE_BLOCKED_PATTERNS = (
    '*password*', '*passwd*', '*secret*', '*token*', '*api_key*', '*apikey*',
    '*private_key*', '*credential*', 'totp_*', '*_totp*', 'signup_*', 'oauth_*',
    '*session_id*', '*cookie*', 'cai_secret*', 'secret_*', '*_pin', 'pin',
    'acc_number', '*iban*', 'card_number', '*cvv*', '*cvc*',
)

#: Fields that are pure ORM plumbing and never useful to the model.
TECHNICAL_FIELDS = {
    'id', 'create_uid', 'write_uid', 'create_date', 'write_date', '__last_update',
    'display_name', 'message_ids', 'message_follower_ids', 'message_partner_ids',
    'activity_ids', 'website_message_ids', 'message_main_attachment_id', 'rating_ids',
    'access_url', 'access_warning', 'access_token',
}

SKIPPED_TYPES = {'binary', 'image', 'many2one_reference', 'properties_definition', 'json'}
TYPE_PRIORITY = {
    'char': 0, 'text': 1, 'html': 1, 'selection': 0, 'boolean': 2, 'date': 1, 'datetime': 1,
    'integer': 1, 'float': 1, 'monetary': 0, 'many2one': 1, 'many2many': 3, 'one2many': 4,
    'properties': 5, 'reference': 3,
}

DEFAULT_PAYLOAD_LIMIT = 12000
FIELD_TEXT_LIMIT = 1500
X2MANY_LIMIT = 10


class RecordContextBuilder:

    def __init__(self, env, *, payload_limit: int | None = None, include_binary: bool = False):
        self.env = env
        if payload_limit is None:
            param = env['ir.config_parameter'].sudo().get_param('ebshel_ai_suite.context_max_chars')
            payload_limit = int(param) if param and str(param).isdigit() else DEFAULT_PAYLOAD_LIMIT
        self.payload_limit = payload_limit
        self.include_binary = include_binary

    # ------------------------------------------------------------------
    # Field filtering
    # ------------------------------------------------------------------
    def blocked_patterns(self, model_name: str) -> tuple[str, ...]:
        extra = self.env['community.ai.privacy.rule']._cai_patterns_for(model_name)
        return BASELINE_BLOCKED_PATTERNS + tuple(extra)

    def is_field_blocked(self, model_name: str, field_name: str) -> bool:
        lowered = field_name.lower()
        return any(fnmatch.fnmatchcase(lowered, pattern) for pattern in self.blocked_patterns(model_name))

    def field_allowed(self, model, field_name: str, *, allow_technical: bool = False) -> bool:
        """Return whether ``field_name`` of ``model`` may be exposed to the engine."""
        field = model._fields.get(field_name)
        if field is None or field_name.startswith('_'):
            return False
        if not allow_technical and field_name in TECHNICAL_FIELDS:
            return False
        if field.type in SKIPPED_TYPES and not (self.include_binary and field.type in ('binary', 'image')):
            return False
        if self.is_field_blocked(model._name, field_name):
            return False
        return model._has_field_access(field, 'read')

    def extract_safe_fields(self, record, field_names: list[str] | None = None) -> dict[str, Any]:
        """Return ``{field label: readable value}`` for the allowed fields.

        :raises AccessError: if the current user cannot read ``record``.
        """
        record.ensure_one()
        record.check_access('read')
        if field_names:
            candidates = [name for name in field_names if name in record._fields]
        else:
            candidates = list(record._fields)
        allowed = [name for name in candidates if self.field_allowed(record, name)]
        if not field_names:
            allowed.sort(key=lambda n: (n not in ('name', 'partner_id', 'description'),
                                        not record._fields[n].required,
                                        TYPE_PRIORITY.get(record._fields[n].type, 6), n))
        values: dict[str, Any] = {}
        budget = self.payload_limit
        for name in allowed:
            field = record._fields[name]
            try:
                value = self.format_value(record, field)
            except (AccessError, MissingError):
                continue
            if value in (None, '', [], False) and field.type != 'boolean':
                continue
            label = f'{field.string} ({name})'
            cost = len(label) + len(str(value)) + 4
            if cost > budget:
                if values:
                    values['__truncated__'] = True
                    break
                value = truncate(str(value), max(200, budget - len(label)))
                cost = budget
            values[label] = value
            budget -= cost
        return values

    def format_value(self, record, field) -> Any:
        value = record[field.name]
        ftype = field.type
        if ftype == 'boolean':
            return bool(value)
        if ftype in ('many2one', 'one2many', 'many2many', 'reference'):
            if not value:
                return None
        elif value is None or value is False or (isinstance(value, str) and not value):
            return None
        if ftype == 'html':
            return truncate(redact_secrets(html2plaintext(value or '')), FIELD_TEXT_LIMIT)
        if ftype in ('char', 'text'):
            return truncate(redact_secrets(value), FIELD_TEXT_LIMIT)
        if ftype == 'selection':
            selection = dict(field._description_selection(self.env))
            return selection.get(value, value)
        if ftype in ('date', 'datetime'):
            return value.isoformat()
        if ftype == 'monetary':
            currency = record[field.get_currency_field(record)] if field.get_currency_field(record) else None
            return f'{value:.2f} {currency.name}' if currency else value
        if ftype in ('integer', 'float'):
            return value
        if ftype == 'many2one':
            return value.display_name if value.has_access('read') else None
        if ftype in ('one2many', 'many2many'):
            readable = value.filtered(lambda r: r.has_access('read'))
            names = readable[:X2MANY_LIMIT].mapped('display_name')
            if len(readable) > X2MANY_LIMIT:
                names.append(f'… and {len(readable) - X2MANY_LIMIT} more')
            return names
        if ftype == 'reference':
            return value.display_name if value and value.has_access('read') else None
        if ftype in ('binary', 'image'):
            return '[binary content omitted]'
        return truncate(redact_secrets(str(value)), FIELD_TEXT_LIMIT)

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------
    def build_record_context(self, model_name: str, res_id: int, field_names: list[str] | None = None,
                             include_chatter: int = 0) -> dict[str, Any] | None:
        """Return a structured context for ``model_name(res_id)`` or ``None``
        when the record does not exist or is not readable."""
        if model_name not in self.env or not res_id:
            return None
        Model = self.env[model_name]
        if Model._transient or Model._abstract or not Model.has_access('read'):
            return None
        record = Model.browse(int(res_id)).exists()
        if not record:
            return None
        try:
            fields_payload = self.extract_safe_fields(record, field_names)
        except AccessError:
            return None
        context = {
            'model': model_name,
            'model_label': self.env['ir.model']._get(model_name).name,
            'id': record.id,
            'name': record.display_name,
            'fields': fields_payload,
        }
        if include_chatter and 'message_ids' in record._fields:
            context['recent_messages'] = self._chatter_excerpt(record, include_chatter)
        return context

    def _chatter_excerpt(self, record, limit: int) -> list[str]:
        Message = self.env['mail.message']
        messages = Message.search([
            ('model', '=', record._name), ('res_id', '=', record.id),
            ('message_type', 'in', ['comment', 'email']),
        ], limit=limit, order='id desc')
        excerpt = []
        for message in reversed(messages):
            body = truncate(redact_secrets(html2plaintext(message.body or '')), 600)
            if body:
                excerpt.append(f'{message.date:%Y-%m-%d} {message.author_id.display_name or "?"}: {body}')
        return excerpt

    def prepare_context_payload(self, context: dict[str, Any] | None) -> str:
        """Render a context dict as a fenced, untrusted text block."""
        if not context:
            return ''
        lines = [f"Record: {context['model_label']} #{context['id']} — {context['name']}"]
        for label, value in context['fields'].items():
            if label == '__truncated__':
                lines.append('(some fields omitted to respect the size limit)')
                continue
            if isinstance(value, list):
                value = ', '.join(map(str, value))
            lines.append(f'- {label}: {value}')
        if context.get('recent_messages'):
            lines.append('Recent messages:')
            lines.extend(f'  * {line}' for line in context['recent_messages'])
        body = truncate('\n'.join(lines), self.payload_limit)
        return wrap_untrusted('record', body, label=f"{context['model']}#{context['id']}")

    def safe_value_by_path(self, record, path: str) -> Any:
        """Resolve a dotted path (``partner_id.name``) through allowed fields
        only. Used by the prompt renderer. Raises ``KeyError`` when a hop is
        not allowed; intermediate hops must be many2one fields."""
        current = record
        parts = path.split('.')
        for index, part in enumerate(parts):
            field = current._fields.get(part)
            if part.startswith('_') or field is None:
                raise KeyError(path)
            if not self.field_allowed(current, part, allow_technical=part in ('display_name', 'id')):
                raise KeyError(path)
            if not current:
                return ''
            current = current[:1]
            current.check_access('read')
            if index == len(parts) - 1:
                value = self.format_value(current, field)
                if isinstance(value, list):
                    return ', '.join(map(str, value))
                return '' if value is None else value
            if field.type != 'many2one':
                raise KeyError(path)
            current = current[part]
        return current
