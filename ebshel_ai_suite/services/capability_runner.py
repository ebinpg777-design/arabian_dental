# Part of Ebshel AI Suite. See LICENSE file for full copyright and licensing details.
"""Mediates every tool call requested by a model.

Pipeline for each call::

    capability lookup (enabled + allowed for this user)
      → argument validation (explicit schema)
      → access validation (ACL, record rules, field access)
      → confirmation gate (mutating capabilities)
      → execution inside a savepoint, with the user's own environment
      → result sanitization (redaction + size limit)
      → audit

The model only ever sees the sanitized, fenced result.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from odoo.exceptions import AccessError, MissingError, UserError, ValidationError

from .capability_handlers import CapabilityFailure, get_handler
from .capability_schema import ArgumentError, to_json_schema, validate_arguments
from .engines import ToolInvocation, ToolSpec
from .guardrails import compact_json, redact_secrets, truncate

_logger = logging.getLogger(__name__)

RESULT_LIMIT = 6000


@dataclass
class InvocationOutcome:
    status: str                       # executed | awaiting_confirmation | rejected | failed
    payload: dict                     # sent back to the model (before fencing)
    capability: object = None
    operation: object = None
    navigation: dict | None = None
    media: list = field(default_factory=list)
    label: str = ''
    citations: list = field(default_factory=list)

    def model_text(self) -> str:
        return compact_json({'status': self.status, **self.payload}, RESULT_LIMIT)


class CapabilityRunner:

    def __init__(self, env, assistant=None, session=None, *, capabilities=None, tainted: bool = False,
                 job=None, bound_record=None, preapproved: bool = False):
        self.env = env
        self.assistant = assistant
        self.session = session
        self.job = job
        self.tainted = tainted
        #: record every capability implicitly acts on (AI decision actions): the model never picks it
        self.bound_record = bound_record
        #: an administrator pre-approved unattended execution of these capabilities
        self.preapproved = preapproved
        self._capabilities = capabilities
        self.citations: list = []

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------
    def available_capabilities(self):
        if self._capabilities is not None:
            capabilities = self._capabilities
        elif self.assistant:
            capabilities = self.assistant._cai_effective_capabilities()
        else:
            capabilities = self.env['community.ai.capability']
        # Configuration (allowed fields, schemas) is read with sudo: regular users cannot read
        # ir.model.fields. Handlers only use it as configuration; every data operation runs
        # through ``self.env`` (the user's own rights).
        capabilities = capabilities.sudo().filtered(lambda c: c.active and c._cai_usable_by(self.env.user))
        if self.bound_record is not None:
            capabilities = capabilities.filtered(
                lambda c: not c.model_name or c.model_name == self.bound_record._name)
        return capabilities

    def tool_specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(name=cap.technical_identifier, description=cap._cai_tool_description(),
                     parameters=to_json_schema(self._visible_schema(cap)))
            for cap in self.available_capabilities()
        ]

    def _visible_schema(self, capability) -> dict:
        schema = capability._cai_schema()
        if self.bound_record is not None:
            schema.pop('record_id', None)
        return schema

    def label_for(self, name: str) -> str:
        capability = self.available_capabilities().filtered(lambda c: c.technical_identifier == name)[:1]
        return capability.name or name

    def note_citations(self, hits) -> None:
        self.citations.extend(hits)

    # ------------------------------------------------------------------
    # Invocation
    # ------------------------------------------------------------------
    def invoke(self, call: ToolInvocation, exchange=None) -> InvocationOutcome:
        capability = self.available_capabilities().filtered(lambda c: c.technical_identifier == call.name)[:1]
        if not capability:
            return InvocationOutcome('rejected', {'error': f'Unknown or unavailable capability "{call.name}".'},
                                     label=call.name)
        handler = get_handler(capability.handler_key)
        label = capability.name
        # 1. arguments
        arguments = dict(call.arguments or {}) if isinstance(call.arguments, dict) else call.arguments
        if self.bound_record is not None and isinstance(arguments, dict) and 'record_id' in capability._cai_schema():
            arguments['record_id'] = self.bound_record.id
        try:
            args = validate_arguments(capability._cai_schema(), arguments)
        except ArgumentError as exc:
            self._audit(capability, call.arguments, 'rejected', 'not_required', str(exc), exchange=exchange)
            return InvocationOutcome('rejected', {'error': f'Invalid arguments: {exc}'}, capability, label=label)
        # 2. access
        try:
            handler.check_access(self.env, capability, args)
        except (AccessError, MissingError) as exc:
            self._audit(capability, args, 'rejected', 'not_required', 'access denied', exchange=exchange)
            return InvocationOutcome('rejected', {'error': 'Access denied: ' + truncate(str(exc), 300)},
                                     capability, label=label)
        # 3. confirmation gate
        mutates = handler.is_mutating(capability)
        if mutates and not self.preapproved and self._needs_confirmation(capability):
            try:
                preview = handler.preview(self.env, capability, args)
            except Exception:  # noqa: BLE001 - preview is cosmetic
                preview = f'{capability.name}: {args}'
            operation = self.env['community.ai.operation'].sudo().create({
                'capability_id': capability.id,
                'session_id': self.session.id if self.session else False,
                'exchange_id': exchange.id if exchange else False,
                'job_id': self.job.id if self.job else False,
                'user_id': self.env.user.id,
                'arguments': args,
                'preview_text': truncate(redact_secrets(preview), 2000),
                'target_model': capability.model_name or False,
                'target_res_id': args.get('record_id') or 0,
                'forced_by_untrusted_data': self.tainted and not capability.requires_confirmation,
            })
            self._audit(capability, args, 'pending', 'pending', 'awaiting confirmation', exchange=exchange,
                        operation=operation)
            return InvocationOutcome(
                'awaiting_confirmation',
                {'message': 'The operation was prepared but NOT executed. The user must confirm it in the '
                            'interface. Tell the user what will happen and ask them to confirm.',
                 'operation_ref': operation.id, 'preview': operation.preview_text},
                capability, operation=operation, label=label)
        # 4. execution
        confirmation = 'preapproved' if mutates and self.preapproved else 'not_required'
        return self._execute(capability, handler, args, exchange=exchange, confirmation=confirmation)

    def _needs_confirmation(self, capability) -> bool:
        policy = self.env['ir.config_parameter'].sudo().get_param(
            'ebshel_ai_suite.confirmation_policy', 'capability')
        return bool(
            capability.requires_confirmation
            or policy == 'always'
            or (self.assistant and self.assistant.sudo().require_confirmation)
            or self.tainted
            or self.job is not None
        )

    def _execute(self, capability, handler, args, *, exchange=None, confirmation='not_required',
                 operation=None) -> InvocationOutcome:
        label = capability.name
        try:
            with self.env.cr.savepoint():
                payload = handler.execute(self.env, capability, args, self)
        except CapabilityFailure as exc:
            self._audit(capability, args, 'failed', confirmation, str(exc), exchange=exchange, operation=operation)
            return InvocationOutcome('failed', {'error': str(exc)}, capability, operation=operation, label=label)
        except (AccessError, MissingError) as exc:
            self._audit(capability, args, 'rejected', confirmation, 'access denied', exchange=exchange,
                        operation=operation)
            return InvocationOutcome('rejected', {'error': 'Access denied: ' + truncate(str(exc), 300)},
                                     capability, operation=operation, label=label)
        except (UserError, ValidationError) as exc:
            message = truncate(str(exc.args[0] if exc.args else exc), 500)
            self._audit(capability, args, 'failed', confirmation, message, exchange=exchange, operation=operation)
            return InvocationOutcome('failed', {'error': message}, capability, operation=operation, label=label)
        except Exception:  # noqa: BLE001 - never leak tracebacks to the model
            _logger.exception('Capability %s crashed', capability.technical_identifier)
            self._audit(capability, args, 'failed', confirmation, 'internal error', exchange=exchange,
                        operation=operation)
            return InvocationOutcome('failed', {'error': 'The operation failed because of an internal error.'},
                                     capability, operation=operation, label=label)
        navigation = payload.pop('navigation', None) if isinstance(payload, dict) else None
        if navigation:
            payload['navigation_offered'] = True
        media = payload.pop('media', None) if isinstance(payload, dict) else None
        if media:
            payload['shown_to_user'] = f'{len(media)} file(s) displayed in the chat'
        if handler.is_mutating(capability) or self._audit_reads():
            summary = truncate(redact_secrets(compact_json(payload, 800)), 800)
            self._audit(capability, args, 'done', confirmation, summary, exchange=exchange, operation=operation)
        citations = list(self.citations)
        return InvocationOutcome('executed', payload, capability, operation=operation, navigation=navigation,
                                 media=media or [], label=label, citations=citations)

    def execute_operation(self, operation) -> InvocationOutcome:
        """Execute a previously confirmed operation with the current user's rights."""
        capability = operation.sudo().capability_id
        if not capability.active or not capability._cai_usable_by(self.env.user):
            raise UserError(self.env._('This capability is no longer available to you.'))
        handler = get_handler(capability.handler_key)
        try:
            args = validate_arguments(capability._cai_schema(), operation.arguments)
            handler.check_access(self.env, capability, args)
        except ArgumentError as exc:
            raise UserError(str(exc)) from exc
        return self._execute(capability, handler, args, confirmation='approved', operation=operation,
                             exchange=operation.exchange_id)

    # ------------------------------------------------------------------
    def _audit_reads(self) -> bool:
        return self.env['ir.config_parameter'].sudo().get_param('ebshel_ai_suite.audit_read_operations') == 'True'

    def _audit(self, capability, args, status, confirmation, summary, *, exchange=None, operation=None):
        self.env['community.ai.audit'].sudo()._cai_log(
            user=self.env.user,
            assistant=self.assistant,
            capability=capability,
            target_model=capability.model_name if capability else False,
            target_res_id=(args or {}).get('record_id') if isinstance(args, dict) else 0,
            operation=capability.handler_key if capability else 'unknown',
            arguments=args,
            confirmation_status=confirmation,
            execution_status=status,
            result_summary=summary,
            session=self.session,
            operation_record=operation,
        )
