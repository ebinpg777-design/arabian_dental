# Part of Ebshel AI Suite. See LICENSE file for full copyright and licensing details.
"""Built-in capability handlers.

A *handler* is the Python implementation behind a ``community.ai.capability``
record. Administrators create capability records (name, target model,
allowed fields, confirmation policy); the handler implements the behaviour
once for every model. The engine can only reach a handler through a
capability record that is enabled and allowed for the current user.

Add a handler from another module::

    from odoo.addons.ebshel_ai_suite.services.capability_handlers import (
        CapabilityHandler, register_handler)

    @register_handler
    class ShipOrderHandler(CapabilityHandler):
        key = 'ship_order'
        label = 'Ship a sales order'
        mutates = True
        ...

and extend the ``handler_key`` selection of ``community.ai.capability``.
"""
from __future__ import annotations

from typing import Any

from odoo.exceptions import AccessError, UserError
from odoo.fields import Domain
from odoo.tools import plaintext2html

from .context_builder import RecordContextBuilder
from .domain_guard import DomainGuard, DomainRejected
from .extensions import WebSearchUnavailable, get_web_backend
from .llm_gateway import LLMGateway
from .nl_search import build_window_action
from .retrieval_engine import RetrievalEngine
from .value_converter import ConversionError, convert_value

_HANDLERS: dict[str, CapabilityHandler] = {}


def register_handler(cls):
    _HANDLERS[cls.key] = cls()
    return cls


def get_handler(key: str) -> CapabilityHandler:
    try:
        return _HANDLERS[key]
    except KeyError:
        raise UserError(f'Unknown capability handler {key!r}.') from None


def handler_selection() -> list[tuple[str, str]]:
    return [(key, handler.label) for key, handler in sorted(_HANDLERS.items())]


class CapabilityFailure(Exception):
    """Expected, user-presentable failure raised by a handler."""


FILTER_SCHEMA = {
    'type': 'array',
    'description': 'Optional conditions, each {"field": name, "operator": one of '
                   '=, !=, >, >=, <, <=, in, not in, ilike, not ilike, "value": value}.',
    'items': {'type': 'object'},
}


class CapabilityHandler:
    key: str = ''
    label: str = ''
    #: True when the handler creates, changes or deletes data
    mutates: bool = False
    #: False for handlers that do not work on a target model
    requires_model: bool = True
    #: fields required on the capability for the handler to be usable
    needs_fields: bool = False

    def default_schema(self) -> dict:
        return {}

    def effective_schema(self, capability) -> dict:
        """Argument schema of ``capability`` (administrator override or default)."""
        return dict(capability.argument_schema or self.default_schema())

    def is_mutating(self, capability) -> bool:
        return self.mutates

    # -- access -----------------------------------------------------------
    def access_operation(self) -> str:
        return 'read'

    def check_access(self, env, capability, args: dict) -> None:
        if not self.requires_model:
            return
        operation = self.access_operation_for(capability)
        model = env[capability.model_name]
        model.check_access(operation)
        if args.get('record_id'):
            record = self._record(env, capability, args['record_id'])
            record.check_access(operation)

    def access_operation_for(self, capability) -> str:
        return self.access_operation()

    # -- behaviour --------------------------------------------------------
    def preview(self, env, capability, args: dict) -> str:
        return f'{capability.name}: {args}'

    def execute(self, env, capability, args: dict, runner) -> dict:
        raise NotImplementedError

    # -- helpers ----------------------------------------------------------
    @staticmethod
    def _record(env, capability, record_id: int):
        record = env[capability.model_name].browse(record_id).exists()
        if not record:
            raise CapabilityFailure(f'No {capability.model_name} record with id {record_id}.')
        return record

    @staticmethod
    def _field_names(capability) -> list[str]:
        return capability.field_ids.mapped('name')

    @staticmethod
    def _filters_to_domain(env, capability, filters) -> list:
        guard = DomainGuard(env, capability.model_name,
                            allowed_fields=set(capability.field_ids.mapped('name')) | {'name', 'display_name', 'id'})
        try:
            return guard.validate(filters or [])
        except DomainRejected as exc:
            raise CapabilityFailure(str(exc)) from exc

    def _convert_values(self, env, capability, values: dict) -> dict:
        allowed = {f.name: f for f in capability.field_ids}
        if not allowed:
            raise CapabilityFailure('This capability does not allow writing any field.')
        model = env[capability.model_name]
        builder = RecordContextBuilder(env)
        converted = {}
        for name, value in (values or {}).items():
            if name not in allowed:
                raise CapabilityFailure(f'Field "{name}" may not be set by this capability. '
                                        f'Allowed fields: {", ".join(sorted(allowed))}.')
            field = model._fields.get(name)
            if field is None or builder.is_field_blocked(model._name, name) or \
                    not model._has_field_access(field, 'write'):
                raise CapabilityFailure(f'Field "{name}" cannot be written.')
            try:
                converted[name] = convert_value(env, field, value)
            except ConversionError as exc:
                raise CapabilityFailure(f'Invalid value for "{name}": {exc}') from exc
        if not converted:
            raise CapabilityFailure('No values were provided.')
        return converted

    @staticmethod
    def _summaries(env, records, field_names) -> list[dict]:
        builder = RecordContextBuilder(env, payload_limit=1500)
        rows = []
        for record in records:
            row = {'id': record.id, 'name': record.display_name}
            if field_names:
                row.update(builder.extract_safe_fields(record, field_names))
            rows.append(row)
        return rows


@register_handler
class SearchRecordsHandler(CapabilityHandler):
    key = 'search_records'
    label = 'Search records'

    def default_schema(self):
        return {
            'query': {'type': 'string', 'description': 'Text to look for in the record name.'},
            'filters': FILTER_SCHEMA,
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 50, 'description': 'Maximum results.'},
        }

    def execute(self, env, capability, args, runner):
        domain = Domain(self._filters_to_domain(env, capability, args.get('filters')))
        if args.get('query'):
            domain &= Domain('display_name', 'ilike', args['query'])
        limit = min(args.get('limit') or capability.result_limit, capability.result_limit)
        Model = env[capability.model_name]
        records = Model.search(domain, limit=limit)
        return {
            'model': capability.model_name,
            'total_matches': Model.search_count(domain, limit=1000),
            'records': self._summaries(env, records, self._field_names(capability)),
        }


@register_handler
class ReadRecordHandler(CapabilityHandler):
    key = 'read_record'
    label = 'Read one record'

    def default_schema(self):
        return {'record_id': {'type': 'record_id', 'required': True, 'description': 'Id of the record to read.'}}

    def execute(self, env, capability, args, runner):
        record = self._record(env, capability, args['record_id'])
        builder = RecordContextBuilder(env)
        return builder.build_record_context(record._name, record.id, self._field_names(capability) or None,
                                            include_chatter=5) or {}


@register_handler
class CountRecordsHandler(CapabilityHandler):
    key = 'count_records'
    label = 'Count / group records'

    def default_schema(self):
        return {
            'filters': FILTER_SCHEMA,
            'group_by': {'type': 'string', 'description': 'Optional field to group the count by.'},
        }

    def execute(self, env, capability, args, runner):
        domain = self._filters_to_domain(env, capability, args.get('filters'))
        Model = env[capability.model_name]
        group_by = args.get('group_by')
        if not group_by:
            return {'model': capability.model_name, 'count': Model.search_count(domain)}
        field = Model._fields.get(group_by)
        if field is None or group_by not in self._field_names(capability) or \
                field.type not in ('selection', 'many2one', 'boolean', 'date', 'datetime', 'char'):
            raise CapabilityFailure(f'Cannot group by "{group_by}".')
        groups = Model._read_group(domain, groupby=[group_by], aggregates=['__count'], limit=50)
        result = []
        for key, count in groups:
            label = key.display_name if hasattr(key, 'display_name') else key
            result.append({'group': label if label not in (None, False) else 'Undefined', 'count': count})
        return {'model': capability.model_name, 'groups': result}


@register_handler
class CreateRecordHandler(CapabilityHandler):
    key = 'create_record'
    label = 'Create a record'
    mutates = True
    needs_fields = True

    def default_schema(self):
        return {'values': {'type': 'object', 'required': True,
                           'description': 'Field values for the new record, keyed by field name.'}}

    def access_operation(self):
        return 'create'

    def preview(self, env, capability, args):
        values = ', '.join(f'{k} = {v}' for k, v in (args.get('values') or {}).items())
        return f'Create {env["ir.model"]._get(capability.model_name).name}: {values}'

    def execute(self, env, capability, args, runner):
        values = self._convert_values(env, capability, args['values'])
        record = env[capability.model_name].create(values)
        return {'created': {'id': record.id, 'name': record.display_name, 'model': record._name}}


@register_handler
class UpdateRecordHandler(CapabilityHandler):
    key = 'update_record'
    label = 'Update a record'
    mutates = True
    needs_fields = True

    def default_schema(self):
        return {
            'record_id': {'type': 'record_id', 'required': True, 'description': 'Id of the record to update.'},
            'values': {'type': 'object', 'required': True, 'description': 'Field values to change.'},
        }

    def access_operation(self):
        return 'write'

    def preview(self, env, capability, args):
        record = env[capability.model_name].browse(args.get('record_id')).exists()
        values = ', '.join(f'{k} = {v}' for k, v in (args.get('values') or {}).items())
        return f'Update {record.display_name or args.get("record_id")}: {values}'

    def execute(self, env, capability, args, runner):
        record = self._record(env, capability, args['record_id'])
        values = self._convert_values(env, capability, args['values'])
        record.write(values)
        return {'updated': {'id': record.id, 'name': record.display_name, 'fields': sorted(values)}}


@register_handler
class OpenViewHandler(CapabilityHandler):
    key = 'open_view'
    label = 'Open a view'

    def default_schema(self):
        return {
            'filters': FILTER_SCHEMA,
            'record_id': {'type': 'record_id', 'description': 'Open this record in form view.'},
            'view_type': {'type': 'string', 'enum': ['list', 'kanban', 'form']},
            'title': {'type': 'string', 'description': 'Title of the window.'},
            'group_by': {'type': 'string', 'description': 'Optional allowed field to group the list by.'},
            'interval': {'type': 'string', 'enum': ['day', 'week', 'month', 'quarter', 'year'],
                         'description': 'Time interval when grouping by a date field.'},
        }

    def execute(self, env, capability, args, runner):
        model_label = env['ir.model']._get(capability.model_name).name
        if args.get('record_id'):
            record = self._record(env, capability, args['record_id'])
            record.check_access('read')
            action = build_window_action(env, capability.model_name, [], title=args.get('title') or record.display_name,
                                         view_type='form', res_id=record.id)
            return {'navigation': action, 'opened': record.display_name}
        domain = self._filters_to_domain(env, capability, args.get('filters'))
        group_by = self._group_by(env, capability, args.get('group_by'), args.get('interval'))
        action = build_window_action(env, capability.model_name, domain, title=args.get('title') or model_label,
                                     view_type=args.get('view_type') or 'list', group_by=group_by)
        return {'navigation': action, 'matching_records': env[capability.model_name].search_count(domain)}

    def _group_by(self, env, capability, field_name, interval):
        """Validated ``group_by`` context value (never taken verbatim from the model)."""
        if not field_name:
            return None
        field = env[capability.model_name]._fields.get(field_name)
        allowed = set(self._field_names(capability)) | {'create_date'}
        if field is None or field_name not in allowed or not field._description_groupable(env):
            raise CapabilityFailure(f'Cannot group by "{field_name}".')
        if field.type in ('date', 'datetime'):
            return f'{field_name}:{interval or "month"}'
        return field_name


@register_handler
class SearchKnowledgeHandler(CapabilityHandler):
    key = 'search_knowledge'
    label = 'Search knowledge sources'
    requires_model = False

    def default_schema(self):
        return {'query': {'type': 'string', 'required': True, 'description': 'What to look for.'}}

    def execute(self, env, capability, args, runner):
        if not runner.assistant:
            raise CapabilityFailure('No knowledge sources are available.')
        hits = RetrievalEngine(env).retrieve(runner.assistant, args['query'], limit=4)
        runner.note_citations(hits)
        return {'excerpts': [{'source': hit.source_name, 'text': hit.text} for hit in hits]}


@register_handler
class PostNoteHandler(CapabilityHandler):
    key = 'post_note'
    label = 'Log an internal note'
    mutates = True

    def default_schema(self):
        return {
            'record_id': {'type': 'record_id', 'required': True, 'description': 'Record to annotate.'},
            'body': {'type': 'text', 'required': True, 'description': 'Content of the note.'},
        }

    def check_access(self, env, capability, args):
        model = env[capability.model_name]
        if not hasattr(model, 'message_post'):
            raise AccessError(f'{capability.model_name} has no chatter.')
        super().check_access(env, capability, args)

    def preview(self, env, capability, args):
        record = env[capability.model_name].browse(args.get('record_id')).exists()
        return f'Log a note on {record.display_name or args.get("record_id")}: {(args.get("body") or "")[:300]}'

    def execute(self, env, capability, args, runner):
        record = self._record(env, capability, args['record_id'])
        message = record.message_post(body=plaintext2html(args['body']), message_type='comment',
                                      subtype_xmlid='mail.mt_note')
        return {'note_posted': {'record': record.display_name, 'message_id': message.id}}


@register_handler
class GenerateImageHandler(CapabilityHandler):
    """Creates an image with the connection's image model and shows it in the chat."""
    key = 'generate_image'
    label = 'Generate an image'
    requires_model = False

    def default_schema(self):
        return {
            'prompt': {'type': 'text', 'required': True,
                       'description': 'Detailed description: subject, setting, composition, lighting, style.'},
            'size': {'type': 'string', 'enum': ['1024x1024', '1536x1024', '1024x1536']},
        }

    def execute(self, env, capability, args, runner):
        data = LLMGateway(env).render_image(args['prompt'], size=args.get('size') or '1024x1024')
        values = {'name': 'ai-image.png', 'raw': data, 'description': args['prompt'][:500]}
        if runner.session:
            values.update(res_model='community.ai.session', res_id=runner.session.id)
        attachment = env['ir.attachment'].create(values)
        return {'image_created': True, 'media': [{'type': 'image', 'url': f'/web/image/{attachment.id}',
                                                  'attachment_id': attachment.id, 'name': attachment.name}]}


@register_handler
class WebLookupHandler(CapabilityHandler):
    key = 'web_lookup'
    label = 'Search the web (requires a web search backend)'
    requires_model = False

    def default_schema(self):
        return {'query': {'type': 'string', 'required': True, 'description': 'Web search query.'}}

    def execute(self, env, capability, args, runner):
        backend = get_web_backend(env)
        if backend is None:
            raise CapabilityFailure('Web search is not configured.')
        try:
            results = backend.search(args['query'], limit=5)
        except WebSearchUnavailable as exc:
            raise CapabilityFailure(str(exc)) from exc
        return {'results': results, 'note': 'Web results are third-party content; cite the URLs you use.'}


@register_handler
class ServerActionHandler(CapabilityHandler):
    """Runs a server action written by an administrator on one record.

    The arguments declared on the capability are exposed to the code as the
    ``cai_args`` dictionary; the code may put a result in the ``cai_output``
    dictionary. The code must enforce its own business rules.
    """
    key = 'server_action'
    label = 'Run a server action'
    mutates = True

    def default_schema(self):
        return {}

    def effective_schema(self, capability):
        schema = {'record_id': {'type': 'record_id', 'required': True,
                                'description': f'Id of the {capability.model_name} record to act on.'}}
        schema.update(capability.argument_schema or {})
        return schema

    def is_mutating(self, capability):
        return not capability.declared_read_only

    def access_operation_for(self, capability):
        return 'read' if capability.declared_read_only else 'write'

    def preview(self, env, capability, args):
        record = env[capability.model_name].browse(args.get('record_id')).exists()
        details = ', '.join(f'{k} = {v}' for k, v in args.items() if k != 'record_id')
        return f'{capability.server_action_id.name} on {record.display_name or args.get("record_id")}' + (
            f': {details}' if details else '')

    def execute(self, env, capability, args, runner):
        record = self._record(env, capability, args['record_id'])
        action = capability.server_action_id
        if not action or action.model_id != capability.model_id:
            raise CapabilityFailure('The server action of this capability is missing or targets another model.')
        output: dict = {}
        tool_args = {key: value for key, value in args.items() if key != 'record_id'}
        result = action.with_env(env).with_context(
            active_model=record._name, active_id=record.id, active_ids=record.ids,
            cai_tool_args=tool_args, cai_tool_output=output,
        ).run()
        payload = {'executed': action.name, 'record': record.display_name}
        if output:
            payload['output'] = output
        if isinstance(result, dict) and result.get('type') == 'ir.actions.act_window':
            payload['navigation'] = {key: result[key] for key in
                                     ('type', 'name', 'res_model', 'res_id', 'views', 'domain', 'target')
                                     if key in result}
        return payload


def default_schema_for(key: str) -> dict[str, Any]:
    return get_handler(key).default_schema()
