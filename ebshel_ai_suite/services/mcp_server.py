# Part of Ebshel AI Suite. See LICENSE file for full copyright and licensing details.
"""Model Context Protocol (MCP) server logic.

External AI clients (Claude Code / Desktop, Codex, Antigravity, ...) connect to
``/ebshel_ai/mcp`` with an API key and call the tools listed here. Every
tool runs as the API key's user: access rights, record rules, field groups and
the AI privacy rules all apply, and search domains go through
:class:`DomainGuard` exactly like in the chat. Writing tools are only listed
when an administrator enabled them; the MCP client is expected to ask its user
before calling a tool that is not marked read-only.

Only JSON-RPC over HTTP POST is implemented (no server-initiated streams),
which is enough for tool calling.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from odoo import fields
from odoo.exceptions import AccessError, MissingError, UserError, ValidationError

from .capability_runner import CapabilityRunner
from .capability_schema import ArgumentError, to_json_schema, validate_arguments
from .context_builder import RecordContextBuilder
from .domain_guard import DomainGuard, DomainRejected
from .engines import ToolInvocation
from .guardrails import redact_secrets, truncate
from .value_converter import ConversionError, convert_value

_logger = logging.getLogger(__name__)

API_KEY_SCOPE = 'ebshel_ai_mcp'
SUPPORTED_VERSIONS = ('2025-06-18', '2025-03-26', '2024-11-05')
SERVER_NAME = 'odoo-ebshel-ai-suite'
FORBIDDEN_PREFIXES = ('ir.', 'res.users.apikeys', 'res.groups', 'community.ai.', 'bus.', 'base.',
                      'res.config', 'res.device', 'auth_', 'change.password', 'mail.mail')
READ_ONLY_PREFIXES = ('res.users', 'res.company', 'res.currency', 'res.country', 'res.lang',
                      'mail.message', 'mail.followers')
MAX_ROWS = 200
TEXT_LIMIT = 2000
AGGREGATORS = ('sum', 'avg', 'min', 'max', 'count', 'count_distinct')
INTERVALS = ('day', 'week', 'month', 'quarter', 'year')


class ToolFailure(Exception):
    """Expected tool error, reported to the client with ``isError: true``."""


class JsonRpcError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _param(env, key: str) -> str:
    return env['ir.config_parameter'].sudo().get_param(f'ebshel_ai_suite.{key}') or ''


class McpServer:

    def __init__(self, env):
        self.env = env
        self.builder = RecordContextBuilder(env)
        self.allow_write = _param(env, 'mcp_allow_write') == 'True'

    # ------------------------------------------------------------------
    # JSON-RPC plumbing
    # ------------------------------------------------------------------
    def handle(self, message: Any):
        """Return the JSON-RPC response for ``message`` (``None`` for notifications)."""
        if isinstance(message, list):
            responses = [r for r in (self.handle(item) for item in message) if r is not None]
            return responses or None
        if not isinstance(message, dict) or message.get('jsonrpc') != '2.0' or 'method' not in message:
            return self._error(None, -32600, 'Invalid Request')
        request_id = message.get('id')
        is_notification = 'id' not in message
        try:
            result = self._dispatch(message['method'], message.get('params') or {})
        except JsonRpcError as exc:
            return None if is_notification else self._error(request_id, exc.code, exc.message)
        except Exception:  # noqa: BLE001 - never leak tracebacks to the client
            _logger.exception('MCP request failed')
            return None if is_notification else self._error(request_id, -32603, 'Internal error')
        return None if is_notification else {'jsonrpc': '2.0', 'id': request_id, 'result': result}

    @staticmethod
    def _error(request_id, code, message):
        return {'jsonrpc': '2.0', 'id': request_id, 'error': {'code': code, 'message': message}}

    def _dispatch(self, method: str, params: dict):
        if method == 'initialize':
            requested = params.get('protocolVersion')
            return {
                'protocolVersion': requested if requested in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0],
                'capabilities': {'tools': {'listChanged': False}},
                'serverInfo': {'name': SERVER_NAME, 'version': '19.0'},
                'instructions': (
                    'Tools of an Odoo database. Call get_context first. Use list_models and describe_model to '
                    'discover data, search_records / aggregate_records to read it. Domains are lists of '
                    '[field, operator, value] conditions with "&", "|", "!" in prefix notation. All calls run '
                    'with the rights of the connected user.'),
            }
        if method in ('notifications/initialized', 'notifications/cancelled'):
            return {}
        if method == 'ping':
            return {}
        if method == 'tools/list':
            return {'tools': self.list_tools()}
        if method == 'tools/call':
            name = params.get('name')
            if not isinstance(name, str):
                raise JsonRpcError(-32602, 'Missing tool name')
            return self.call_tool(name, params.get('arguments') or {})
        raise JsonRpcError(-32601, f'Method not found: {method}')

    # ------------------------------------------------------------------
    # Tool catalogue
    # ------------------------------------------------------------------
    def _builtin_tools(self) -> dict:
        domain_help = 'Conditions [[field, operator, value], ...]; operators = != > >= < <= in "not in" ilike.'
        tools = {
            'get_context': (True, 'Current user, language, timezone, active and allowed companies, today.', {}),
            'list_models': (True, 'Business models the user can read, with their labels.', {
                'search': {'type': 'string', 'description': 'Optional text to filter models.'},
                'limit': {'type': 'integer', 'minimum': 1, 'maximum': 500},
            }),
            'describe_model': (True, 'Fields of a model (type, label, relation, selection values).', {
                'model': {'type': 'string', 'required': True, 'description': 'Technical model name.'},
            }),
            'list_menus': (True, 'Menus the user can open, with the model they display.', {
                'search': {'type': 'string', 'description': 'Optional text to filter menus.'},
            }),
            'search_records': (True, 'Search records and return the requested fields.', {
                'model': {'type': 'string', 'required': True},
                'domain': {'type': 'array', 'items': {'type': 'any'}, 'description': domain_help},
                'fields': {'type': 'array', 'items': {'type': 'string'}},
                'limit': {'type': 'integer', 'minimum': 1, 'maximum': MAX_ROWS},
                'offset': {'type': 'integer', 'minimum': 0},
                'order': {'type': 'string', 'description': 'e.g. "create_date desc"'},
            }),
            'aggregate_records': (True, 'Group records and compute aggregates (count, sum, avg, min, max).', {
                'model': {'type': 'string', 'required': True},
                'domain': {'type': 'array', 'items': {'type': 'any'}, 'description': domain_help},
                'group_by': {'type': 'array', 'items': {'type': 'string'}, 'required': True,
                             'description': 'Fields, dates may use an interval: "date_order:month".'},
                'aggregates': {'type': 'array', 'items': {'type': 'string'},
                               'description': '"__count" or "field:sum|avg|min|max|count|count_distinct".'},
                'limit': {'type': 'integer', 'minimum': 1, 'maximum': MAX_ROWS},
            }),
        }
        if self.allow_write:
            tools['create_records'] = (False, 'Create records with the given field values.', {
                'model': {'type': 'string', 'required': True},
                'values': {'type': 'array', 'items': {'type': 'object'}, 'required': True, 'max_items': 50},
            })
            tools['update_records'] = (False, 'Write the same field values on existing records.', {
                'model': {'type': 'string', 'required': True},
                'ids': {'type': 'array', 'items': {'type': 'integer'}, 'required': True, 'max_items': MAX_ROWS},
                'values': {'type': 'object', 'required': True},
            })
        return tools

    def _exposed_capabilities(self):
        capabilities = self.env['community.ai.capability'].sudo().search([('mcp_exposed', '=', True)])
        runner = CapabilityRunner(self.env, capabilities=capabilities)
        available = runner.available_capabilities()
        if not self.allow_write:
            available = available.filtered(lambda c: c.read_only)
        return available

    def list_tools(self) -> list[dict]:
        tools = []
        for name, (read_only, description, schema) in self._builtin_tools().items():
            tools.append(self._tool_entry(name, description, schema, read_only))
        for capability in self._exposed_capabilities():
            tools.append(self._tool_entry(f'cap_{capability.technical_identifier}',
                                          capability._cai_tool_description(), capability._cai_schema(),
                                          capability.read_only, title=capability.name))
        return tools

    @staticmethod
    def _tool_entry(name, description, schema, read_only, title=None):
        return {
            'name': name,
            'title': title or name.replace('_', ' ').capitalize(),
            'description': description,
            'inputSchema': to_json_schema(schema),
            'annotations': {'readOnlyHint': bool(read_only), 'destructiveHint': False,
                            'openWorldHint': False},
        }

    # ------------------------------------------------------------------
    # Tool execution
    # ------------------------------------------------------------------
    def call_tool(self, name: str, arguments: dict) -> dict:
        builtins = self._builtin_tools()
        try:
            if name in builtins:
                read_only, _description, schema = builtins[name]
                args = validate_arguments(schema, arguments)
                with self.env.cr.savepoint():
                    result = getattr(self, f'_tool_{name}')(**args)
                if not read_only or _param(self.env, 'audit_read_operations') == 'True':
                    self._audit(name, args, 'done', result)
            elif name.startswith('cap_'):
                result = self._call_capability(name[4:], arguments)
            else:
                raise ToolFailure(f'Unknown tool {name}.')
        except (ToolFailure, ArgumentError, DomainRejected, ConversionError) as exc:
            return self._tool_error(str(exc))
        except (AccessError, MissingError) as exc:
            self._audit(name, arguments, 'rejected', 'access denied')
            return self._tool_error('Access denied: ' + truncate(str(exc.args[0] if exc.args else exc), 300))
        except (UserError, ValidationError) as exc:
            return self._tool_error(truncate(str(exc.args[0] if exc.args else exc), 500))
        text = redact_secrets(json.dumps(result, default=str, ensure_ascii=False))
        return {'content': [{'type': 'text', 'text': truncate(text, 60000)}], 'structuredContent': result,
                'isError': False}

    @staticmethod
    def _tool_error(message: str) -> dict:
        return {'content': [{'type': 'text', 'text': message}], 'isError': True}

    def _audit(self, name, args, status, result):
        self.env['community.ai.audit'].sudo()._cai_log(
            user=self.env.user, operation=f'mcp:{name}', target_model=(args or {}).get('model') or False,
            arguments=args, confirmation_status='not_required', execution_status=status,
            result_summary=truncate(json.dumps(result, default=str)[:600], 600))

    def _call_capability(self, identifier, arguments):
        capability = self._exposed_capabilities().filtered(lambda c: c.technical_identifier == identifier)[:1]
        if not capability:
            raise ToolFailure(f'Unknown tool cap_{identifier}.')
        runner = CapabilityRunner(self.env, capabilities=capability, preapproved=self.allow_write)
        outcome = runner.invoke(ToolInvocation(call_id='mcp', name=identifier, arguments=arguments))
        if outcome.status != 'executed':
            raise ToolFailure(outcome.payload.get('error') or outcome.status)
        return outcome.payload

    # -- helpers -------------------------------------------------------------
    def _model(self, name: str, write: bool = False):
        if not isinstance(name, str) or name not in self.env or name.startswith(FORBIDDEN_PREFIXES):
            raise ToolFailure(f'Model {name} is not available.')
        model = self.env[name]
        if model._transient or model._abstract:
            raise ToolFailure(f'Model {name} is not available.')
        if write and name.startswith(READ_ONLY_PREFIXES):
            raise ToolFailure(f'Model {name} cannot be modified through MCP.')
        model.check_access('write' if write else 'read')
        return model

    def _readable_fields(self, model, names=None):
        allowed = []
        for name in names or ['display_name']:
            if name in ('id', 'display_name') or self.builder.field_allowed(
                    model, name, allow_technical=name in ('create_date', 'write_date', 'create_uid', 'write_uid')):
                allowed.append(name)
            else:
                raise ToolFailure(f'Field {name} of {model._name} is not available.')
        return allowed

    @staticmethod
    def _plain(value):
        if hasattr(value, '_name'):                       # recordset
            if not value:
                return False
            return [{'id': r.id, 'name': r.display_name} for r in value][:20] if len(value) != 1 else \
                {'id': value.id, 'name': value.display_name}
        if hasattr(value, 'isoformat'):
            return value.isoformat()
        if isinstance(value, str):
            return truncate(redact_secrets(value), TEXT_LIMIT)
        return value

    # -- builtin tools -------------------------------------------------------
    def _tool_get_context(self):
        user = self.env.user
        return {
            'user': {'id': user.id, 'name': user.name, 'partner_id': user.partner_id.id},
            'lang': user.lang, 'timezone': user.tz or 'UTC',
            'company': {'id': self.env.company.id, 'name': self.env.company.name},
            'allowed_companies': [{'id': c.id, 'name': c.name} for c in user.company_ids],
            'today': fields.Date.context_today(user).isoformat(),
            'write_tools_enabled': self.allow_write,
        }

    def _tool_list_models(self, search=None, limit=None):
        domain = [('transient', '=', False)]
        if search:
            domain += ['|', ('model', 'ilike', search), ('name', 'ilike', search)]
        rows = []
        for record in self.env['ir.model'].sudo().search(domain, order='model'):
            name = record.model
            if name.startswith(FORBIDDEN_PREFIXES) or name not in self.env:
                continue
            model = self.env[name]
            if model._abstract or not model.has_access('read'):
                continue
            rows.append({'model': name, 'name': record.name})
            if len(rows) >= (limit or 500):
                break
        return {'models': rows}

    def _tool_describe_model(self, model):
        records = self._model(model)
        described = []
        for name, field in sorted(records._fields.items()):
            if field.type in ('binary', 'image', 'json', 'properties_definition', 'many2one_reference'):
                continue
            if name not in ('id', 'display_name') and not self.builder.field_allowed(
                    records, name, allow_technical=name in ('create_date', 'write_date', 'create_uid', 'write_uid')):
                continue
            entry = {'name': name, 'label': field.string, 'type': field.type, 'required': bool(field.required),
                     'readonly': bool(field.readonly), 'searchable': field._description_searchable,
                     'groupable': field._description_groupable(self.env)}
            if field.relational:
                entry['relation'] = field.comodel_name
            if field.type == 'selection':
                entry['selection'] = [[str(k), str(v)] for k, v in field._description_selection(self.env)][:30]
            described.append(entry)
        return {'model': model, 'name': self.env['ir.model']._get(model).name, 'fields': described}

    def _tool_list_menus(self, search=None):
        menus = self.env['ir.ui.menu'].search([('action', '!=', False)])._filter_visible_menus()
        rows = []
        for menu in menus:
            if search and search.lower() not in (menu.complete_name or '').lower():
                continue
            action = menu.action
            entry = {'id': menu.id, 'path': menu.complete_name}
            if action and action._name == 'ir.actions.act_window':
                entry.update(model=action.res_model, views=(action.view_mode or '').split(','))
            rows.append(entry)
            if len(rows) >= 300:
                break
        return {'menus': rows}

    def _tool_search_records(self, model, domain=None, fields=None, limit=None, offset=None, order=None):
        records_model = self._model(model)
        guard = DomainGuard(self.env, model, max_leaves=30, max_hops=3)
        valid_domain = guard.validate(domain or [])
        valid_order = guard.validate_order(order)
        names = self._readable_fields(records_model, fields)
        records = records_model.search(valid_domain, limit=min(limit or 50, MAX_ROWS), offset=offset or 0,
                                       order=valid_order)
        rows = [{'id': record.id, **{name: self._plain(record[name]) for name in names}} for record in records]
        return {'count': records_model.search_count(valid_domain, limit=100000), 'records': rows}

    def _tool_aggregate_records(self, model, group_by, domain=None, aggregates=None, limit=None):
        records_model = self._model(model)
        valid_domain = DomainGuard(self.env, model, max_leaves=30, max_hops=3).validate(domain or [])
        groupby = []
        for spec in group_by[:3]:
            name, _sep, interval = spec.partition(':')
            field = records_model._fields.get(name)
            self._readable_fields(records_model, [name])
            if field is None or not field._description_groupable(self.env):
                raise ToolFailure(f'Cannot group by {name}.')
            if interval and (field.type not in ('date', 'datetime') or interval not in INTERVALS):
                raise ToolFailure(f'Invalid interval for {name}.')
            groupby.append(f'{name}:{interval}' if interval else name)
        specs = []
        for spec in aggregates or ['__count']:
            if spec == '__count':
                specs.append(spec)
                continue
            name, _sep, aggregator = spec.partition(':')
            field = records_model._fields.get(name)
            self._readable_fields(records_model, [name])
            if field is None or aggregator not in AGGREGATORS or (
                    aggregator not in ('count', 'count_distinct') and field.type not in ('integer', 'float', 'monetary')):
                raise ToolFailure(f'Invalid aggregate {spec}.')
            specs.append(f'{name}:{aggregator}')
        rows = records_model._read_group(valid_domain, groupby=groupby, aggregates=specs,
                                         limit=min(limit or 100, MAX_ROWS))
        result = []
        for row in rows:
            keys = row[:len(groupby)]
            values = row[len(groupby):]
            result.append({**{spec: self._plain(key) for spec, key in zip(groupby, keys, strict=True)},
                           **{spec: self._plain(value) for spec, value in zip(specs, values, strict=True)}})
        return {'groups': result}

    def _writable_values(self, model, values: dict) -> dict:
        if not isinstance(values, dict) or not values:
            raise ToolFailure('Values must be a non-empty object.')
        converted = {}
        for name, raw in values.items():
            field = model._fields.get(name)
            if field is None or name.startswith('_') or self.builder.is_field_blocked(model._name, name) \
                    or field.readonly or not model._has_field_access(field, 'write'):
                raise ToolFailure(f'Field {name} of {model._name} cannot be written.')
            converted[name] = convert_value(self.env, field, raw)
        return converted

    def _tool_create_records(self, model, values):
        records_model = self._model(model, write=True)
        records_model.check_access('create')
        created = records_model.create([self._writable_values(records_model, vals) for vals in values])
        return {'created': [{'id': r.id, 'name': r.display_name} for r in created]}

    def _tool_update_records(self, model, ids, values):
        records_model = self._model(model, write=True)
        records = records_model.browse(ids).exists()
        if len(records) != len(set(ids)):
            raise ToolFailure('Some records do not exist.')
        records.write(self._writable_values(records_model, values))
        return {'updated': [{'id': r.id, 'name': r.display_name} for r in records]}
