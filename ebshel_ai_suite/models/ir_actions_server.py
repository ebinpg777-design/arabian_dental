# Part of Ebshel AI Suite. See LICENSE file for full copyright and licensing details.
import re

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from ..services.decision_engine import DecisionEngine
from ..services.engines.errors import EngineError


class IrActionsServer(models.Model):
    _inherit = 'ir.actions.server'

    state = fields.Selection(selection_add=[('cai_decision', 'AI Decision')],
                             ondelete={'cai_decision': 'cascade'})
    cai_instruction = fields.Text(
        'AI Instruction', help='What the AI should decide for the record. Placeholders such as '
                               '{{ record.name }} are allowed.')
    cai_capability_ids = fields.Many2many(
        'community.ai.capability', 'ir_act_server_cai_capability_rel', 'action_id', 'capability_id',
        string='Tools the AI May Use',
        domain="['|', ('model_id', '=', False), ('model_id', '=', model_id)]")
    cai_assistant_id = fields.Many2one('community.ai.assistant', string='Assistant (connection & limits)')
    cai_auto_execute = fields.Boolean(
        'Run Tools Without Approval',
        help='When enabled, tools chosen by the AI that modify data run immediately. Otherwise they are '
             'prepared as operations that an AI manager approves in Ebshel AI › Approvals.')
    cai_capability_id = fields.One2many('community.ai.capability', 'server_action_id',
                                        string='AI Capability')

    @api.constrains('state', 'cai_capability_ids', 'model_id')
    def _check_cai_tools(self):
        for action in self.filtered(lambda a: a.state == 'cai_decision'):
            wrong = action.cai_capability_ids.filtered(lambda c: c.model_id and c.model_id != action.model_id)
            if wrong:
                raise ValidationError(self.env._('Tools must work on the model of the action: %s',
                                                 ', '.join(wrong.mapped('name'))))

    def _get_eval_context(self, action=None):
        context = super()._get_eval_context(action=action)
        args = self.env.context.get('cai_tool_args')
        output = self.env.context.get('cai_tool_output')
        context['cai_args'] = dict(args) if isinstance(args, dict) else {}
        context['cai_output'] = output if isinstance(output, dict) else {}
        return context

    def _run_action_cai_decision_multi(self, eval_context=None):
        records = eval_context.get('records') or eval_context.get('record')
        if not records:
            return False
        env = records.env
        if not (env.su or env.user.has_group('ebshel_ai_suite.group_cai_user')):
            raise AccessError(self.env._('You are not allowed to use AI features.'))
        engine = DecisionEngine(env)
        results = []
        for record in records[:50]:
            try:
                results.append(engine.decide(self, record))
            except EngineError as exc:
                raise UserError(exc.public_message_for(env)) from exc
        if len(results) == 1 and self.env.context.get('active_model'):
            result = results[0]
            message = result.summary or self.env._('The AI did not choose any tool.')
            if result.pending:
                message += ' ' + self.env._('%s change(s) wait for approval.', len(result.pending))
            return {'type': 'ir.actions.client', 'tag': 'display_notification',
                    'params': {'title': self.env._('AI decision'), 'message': message,
                               'type': 'warning' if result.failed else 'info'}}
        return False

    def action_cai_offer_as_tool(self):
        """Create (or open) the AI capability that lets assistants run this server action."""
        self.ensure_one()
        capability = self.cai_capability_id[:1]
        if not capability:
            identifier = re.sub(r'[^a-z0-9_]+', '_', (self.name or 'tool').lower()).strip('_')[:40] or 'tool'
            identifier = f'sa_{identifier}_{self.id}'
            capability = self.env['community.ai.capability'].create({
                'name': self.name,
                'technical_identifier': identifier,
                'handler_key': 'server_action',
                'model_id': self.model_id.id,
                'server_action_id': self.id,
                'description': self.name,
                'requires_confirmation': True,
            })
        return {'type': 'ir.actions.act_window', 'res_model': 'community.ai.capability', 'res_id': capability.id,
                'views': [[False, 'form']], 'target': 'current'}
