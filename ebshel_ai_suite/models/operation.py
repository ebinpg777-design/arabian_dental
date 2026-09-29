# Part of Ebshel AI Suite. See LICENSE file for full copyright and licensing details.
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from ..services.capability_runner import CapabilityRunner
from ..services.conversation_runner import ConversationRunner
from ..services.guardrails import compact_json

EXPIRY_HOURS = 24


class CommunityAIOperation(models.Model):
    """A data-modifying capability call prepared by the AI and waiting for a human decision."""
    _name = 'community.ai.operation'
    _description = 'AI Pending Operation'
    _order = 'id desc'

    name = fields.Char(compute='_compute_name')
    capability_id = fields.Many2one('community.ai.capability', required=True, ondelete='cascade', readonly=True)
    session_id = fields.Many2one('community.ai.session', ondelete='cascade', readonly=True, index=True)
    exchange_id = fields.Many2one('community.ai.exchange', ondelete='set null', readonly=True)
    job_id = fields.Many2one('community.ai.job', ondelete='cascade', readonly=True, index=True)
    user_id = fields.Many2one('res.users', required=True, readonly=True, index=True,
                              help='User on whose behalf the AI prepared the operation.')
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company, readonly=True)
    arguments = fields.Json(readonly=True)
    preview_text = fields.Text('What will happen', readonly=True)
    target_model = fields.Char(readonly=True)
    target_res_id = fields.Integer(readonly=True)
    state = fields.Selection(
        [('proposed', 'Awaiting confirmation'), ('executed', 'Executed'), ('rejected', 'Rejected'),
         ('failed', 'Failed'), ('expired', 'Expired')], default='proposed', required=True, readonly=True)
    forced_by_untrusted_data = fields.Boolean(
        readonly=True, help='Confirmation was required because the request involved untrusted data '
                            '(record content, documents or tool results).')
    result_summary = fields.Text(readonly=True)
    decided_by = fields.Many2one('res.users', readonly=True)
    decided_at = fields.Datetime(readonly=True)

    def _compute_name(self):
        for operation in self:
            operation.name = f'{operation.capability_id.name or "?"} #{operation.id}'

    def _cai_payload(self):
        self.ensure_one()
        return {
            'id': self.id,
            'title': self.capability_id.name,
            'preview': self.preview_text or '',
            'state': self.state,
            'forced': self.forced_by_untrusted_data,
            'result': self.result_summary or '',
        }

    # ------------------------------------------------------------------
    def _cai_check_decider(self):
        self.ensure_one()
        if self.state != 'proposed':
            raise UserError(self.env._('This operation has already been processed.'))
        is_manager = self.env.user.has_group('ebshel_ai_suite.group_cai_manager')
        if self.session_id and self.user_id != self.env.user:
            raise AccessError(self.env._('Only the author of the conversation can confirm this operation.'))
        if not self.session_id and not is_manager:
            raise AccessError(self.env._('Only AI managers can approve automated operations.'))

    def cai_decide(self, approve):
        """Approve or reject the operation. Returns the continuation events."""
        self.ensure_one()
        operation = self.sudo()
        operation.with_env(self.env)._cai_check_decider()
        values = {'decided_by': self.env.user.id, 'decided_at': fields.Datetime.now()}
        if not approve:
            operation.write(dict(values, state='rejected', result_summary=self.env._('Rejected by the user.')))
            self.env['community.ai.audit'].sudo()._cai_log(
                user=self.env.user, assistant=operation.session_id.assistant_id, capability=operation.capability_id,
                target_model=operation.target_model, target_res_id=operation.target_res_id,
                operation=operation.capability_id.handler_key, arguments=operation.arguments,
                confirmation_status='rejected', execution_status='rejected',
                result_summary='rejected by user', session=operation.session_id, operation_record=operation)
            note = (f'The user REJECTED the operation "{operation.capability_id.name}" (ref {operation.id}). '
                    'It was not executed.')
        else:
            assistant = operation.session_id.assistant_id or operation.job_id.automation_id.assistant_id
            runner = CapabilityRunner(self.env, assistant, operation.session_id.with_env(self.env))
            outcome = runner.execute_operation(operation)
            state = 'executed' if outcome.status == 'executed' else 'failed'
            operation.write(dict(values, state=state, result_summary=compact_json(outcome.payload, 1500)))
            note = (f'The user APPROVED the operation "{operation.capability_id.name}" (ref {operation.id}). '
                    f'Execution status: {outcome.status}. Result: {compact_json(outcome.payload, 2000)}')
        result = {'operation': operation._cai_payload(), 'events': []}
        if operation.session_id and operation.session_id.status == 'open':
            session = operation.session_id.with_env(self.env)
            result['events'] = ConversationRunner(self.env, session).run(event_text=note)['events']
        return result

    def action_cai_approve(self):
        for operation in self:
            operation.cai_decide(True)
        return True

    def action_cai_reject(self):
        for operation in self:
            operation.cai_decide(False)
        return True

    @api.model
    def _cai_cron_expire(self):
        limit = fields.Datetime.now() - timedelta(hours=EXPIRY_HOURS)
        stale = self.sudo().search([('state', '=', 'proposed'), ('create_date', '<', limit),
                                    ('session_id', '!=', False)])
        stale.write({'state': 'expired', 'result_summary': 'Expired without a decision.'})
        return True
