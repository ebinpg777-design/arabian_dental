# Part of Ebshel AI Suite. See LICENSE file for full copyright and licensing details.
import json
import re

from odoo import api, fields, models
from odoo.exceptions import ValidationError

from ..services.capability_handlers import get_handler, handler_selection
from ..services.capability_schema import SchemaError, check_schema

IDENTIFIER = re.compile(r'^[a-z][a-z0-9_]{2,63}$')
#: models no capability may target at all
FORBIDDEN_PREFIXES = ('ir.', 'res.users', 'res.groups', 'community.ai.', 'bus.', 'base.', 'res.config',
                      'res.device', 'auth_', 'change.password', 'mail.mail')
#: additional models that capabilities may read but never modify
READ_ONLY_PREFIXES = ('res.company', 'res.currency', 'res.country', 'res.lang', 'mail.message', 'mail.followers')


class CommunityAICapability(models.Model):
    """An operation that assistants may request, backed by a registered handler."""
    _name = 'community.ai.capability'
    _description = 'AI Capability'
    _order = 'sequence, name'

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean('Enabled', default=True)
    description = fields.Text(translate=True, required=True,
                              help='Explains to the model when and how to use this capability.')
    technical_identifier = fields.Char(required=True, copy=False,
                                       help='Name exposed to the model (lowercase, digits and underscores).')
    handler_key = fields.Selection(selection=lambda self: handler_selection(), string='Behaviour', required=True)
    model_id = fields.Many2one('ir.model', string='Target Model', ondelete='cascade',
                               domain=[('transient', '=', False)])
    model_name = fields.Char(related='model_id.model', store=True, string='Model Name')
    field_ids = fields.Many2many(
        'ir.model.fields', 'community_ai_capability_field_rel', 'capability_id', 'field_id',
        string='Allowed Fields', domain="[('model_id', '=', model_id)]",
        help='Fields the capability may read, filter on or write. Always subject to privacy rules.')
    argument_schema = fields.Json(
        'Argument Schema', help='Optional override of the default argument schema of the behaviour.')
    argument_schema_text = fields.Text('Arguments', compute='_compute_argument_schema_text',
                                       inverse='_inverse_argument_schema_text')
    execution_type = fields.Selection([('read', 'Read-only'), ('write', 'Modifies data')],
                                      compute='_compute_execution_type', store=True)
    read_only = fields.Boolean(compute='_compute_execution_type', store=True)
    requires_confirmation = fields.Boolean(
        default=True, help='Ask the user to confirm before executing. Operations that modify data are also '
                           'confirmed whenever untrusted data (records, documents, tool results) is involved.')
    result_limit = fields.Integer('Max Results', default=10)
    server_action_id = fields.Many2one(
        'ir.actions.server', string='Server Action', ondelete='cascade',
        domain="[('model_id', '=', model_id), ('state', 'in', ['code', 'object_write', 'object_create', 'multi'])]",
        help='Executed by the "Run a server action" behaviour. Its code receives the declared arguments in '
             'the cai_args dictionary and may return data in the cai_output dictionary.')
    mcp_exposed = fields.Boolean(
        'Available through MCP', help='External AI clients connected to the MCP server can call this capability. '
                                      'Capabilities that modify data are only offered when MCP writing is enabled.')
    declared_read_only = fields.Boolean(
        'Read-only Tool', help='The server action only reads data: it may run without confirmation.')
    group_ids = fields.Many2many('res.groups', 'community_ai_capability_group_rel', 'capability_id', 'group_id',
                                 string='Allowed Groups', help='Leave empty to allow every AI user.')
    assistant_ids = fields.Many2many('community.ai.assistant', 'community_ai_assistant_capability_rel',
                                     'capability_id', 'assistant_id', string='Assistants')

    _identifier_unique = models.Constraint('UNIQUE(technical_identifier)',
                                           'The technical identifier of a capability must be unique.')

    @api.depends('handler_key', 'declared_read_only')
    def _compute_execution_type(self):
        for capability in self:
            mutates = bool(capability.handler_key and get_handler(capability.handler_key).is_mutating(capability))
            capability.execution_type = 'write' if mutates else 'read'
            capability.read_only = not mutates

    @api.depends('argument_schema', 'handler_key')
    def _compute_argument_schema_text(self):
        for capability in self:
            capability.argument_schema_text = json.dumps(capability._cai_schema(), indent=2)

    def _inverse_argument_schema_text(self):
        for capability in self:
            text = (capability.argument_schema_text or '').strip()
            if not text:
                capability.argument_schema = False
                continue
            try:
                schema = json.loads(text)
            except ValueError as exc:
                raise ValidationError(self.env._('The argument schema is not valid JSON: %s', exc)) from exc
            handler = get_handler(capability.handler_key) if capability.handler_key else None
            if handler and handler.key == 'server_action':
                schema.pop('record_id', None)   # always provided by the framework
                capability.argument_schema = schema or False
                continue
            default = handler.default_schema() if handler else {}
            capability.argument_schema = False if schema == default else schema

    @api.constrains('technical_identifier')
    def _check_identifier(self):
        for capability in self:
            if not IDENTIFIER.match(capability.technical_identifier or ''):
                raise ValidationError(self.env._(
                    'The technical identifier must be 3-64 lowercase letters, digits or underscores.'))

    @api.constrains('handler_key', 'model_id', 'field_ids', 'argument_schema', 'result_limit')
    def _check_configuration(self):
        for capability in self:
            handler = get_handler(capability.handler_key)
            model = capability.model_name or ''
            if handler.requires_model and not capability.model_id:
                raise ValidationError(self.env._('Capability "%s" needs a target model.', capability.name))
            if model.startswith(FORBIDDEN_PREFIXES) or model in ('res.users', 'res.groups'):
                raise ValidationError(self.env._('Capabilities cannot target the model %s.', model))
            if handler.is_mutating(capability) and model.startswith(READ_ONLY_PREFIXES):
                raise ValidationError(self.env._('Capabilities cannot modify the model %s.', model))
            if capability.handler_key == 'server_action' and (
                    not capability.server_action_id or capability.server_action_id.model_id != capability.model_id):
                raise ValidationError(self.env._('Capability "%s" needs a server action on its target model.',
                                                 capability.name))
            if handler.needs_fields and not capability.field_ids:
                raise ValidationError(self.env._('Capability "%s" must list the fields it may write.',
                                                 capability.name))
            if handler.mutates and capability.field_ids.filtered(lambda f: f.readonly and not f.store):
                raise ValidationError(self.env._('Read-only computed fields cannot be written.'))
            if not 1 <= capability.result_limit <= 50:
                raise ValidationError(self.env._('The result limit must be between 1 and 50.'))
            try:
                check_schema(capability.argument_schema or {})
            except SchemaError as exc:
                raise ValidationError(str(exc)) from exc

    # ------------------------------------------------------------------
    def _cai_schema(self) -> dict:
        self.ensure_one()
        if not self.handler_key:
            return {}
        schema = get_handler(self.handler_key).effective_schema(self)
        if 'limit' in schema:
            schema['limit'] = dict(schema['limit'], maximum=self.result_limit)
        return schema

    def _cai_tool_description(self) -> str:
        self.ensure_one()
        parts = [self.description or self.name]
        if self.model_name:
            parts.append(f'Target model: {self.model_name} ({self.model_id.name}).')
        if self.field_ids:
            parts.append('Allowed fields: ' + ', '.join(
                f'{f.name} ({f.ttype})' for f in self.field_ids.sorted('name')) + '.')
        if self.execution_type == 'write':
            parts.append('This modifies data and may require the user\'s confirmation before it runs.')
        return ' '.join(parts)[:1000]

    def _cai_usable_by(self, user) -> bool:
        self.ensure_one()
        if self.group_ids and not (self.group_ids & user.all_group_ids):
            return False
        return user.has_group('ebshel_ai_suite.group_cai_user') or user._is_superuser()
