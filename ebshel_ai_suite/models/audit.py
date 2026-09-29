# Part of Ebshel AI Suite. See LICENSE file for full copyright and licensing details.
import json
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError

from ..services.guardrails import redact_secrets, truncate


class CommunityAIAudit(models.Model):
    """Append-only trail of AI operations touching business data."""
    _name = 'community.ai.audit'
    _description = 'AI Audit Entry'
    _order = 'event_time desc, id desc'
    _rec_name = 'operation'

    event_time = fields.Datetime(default=fields.Datetime.now, required=True, readonly=True, index=True)
    user_id = fields.Many2one('res.users', readonly=True, index=True, ondelete='set null')
    company_id = fields.Many2one('res.company', readonly=True, index=True)
    assistant_id = fields.Many2one('community.ai.assistant', readonly=True, ondelete='set null')
    capability_id = fields.Many2one('community.ai.capability', readonly=True, ondelete='set null')
    session_id = fields.Many2one('community.ai.session', readonly=True, ondelete='set null')
    operation_id = fields.Many2one('community.ai.operation', string='Pending Operation', readonly=True,
                                   ondelete='set null')
    target_model = fields.Char(readonly=True, index=True)
    target_res_id = fields.Integer('Target Record ID', readonly=True)
    operation = fields.Char('Requested Operation', readonly=True)
    arguments = fields.Text(readonly=True)
    confirmation_status = fields.Selection(
        [('not_required', 'Not required'), ('pending', 'Pending'), ('approved', 'Approved'),
         ('preapproved', 'Pre-approved by configuration'), ('rejected', 'Rejected')], readonly=True)
    execution_status = fields.Selection(
        [('pending', 'Pending'), ('done', 'Done'), ('failed', 'Failed'), ('rejected', 'Rejected')],
        readonly=True, index=True)
    result_summary = fields.Text(readonly=True)

    @api.model
    def _cai_log(self, *, user, assistant=None, capability=None, target_model=False, target_res_id=0,
                 operation='', arguments=None, confirmation_status='not_required', execution_status='done',
                 result_summary='', session=None, operation_record=None):
        try:
            args_text = json.dumps(arguments, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            args_text = str(arguments)
        return self.sudo().create({
            'user_id': user.id if user else False,
            'company_id': self.env.company.id,
            'assistant_id': assistant.id if assistant else False,
            'capability_id': capability.id if capability else False,
            'session_id': session.id if session else False,
            'operation_id': operation_record.id if operation_record else False,
            'target_model': target_model or False,
            'target_res_id': target_res_id if isinstance(target_res_id, int) else 0,
            'operation': operation or '',
            'arguments': truncate(redact_secrets(args_text), 4000),
            'confirmation_status': confirmation_status,
            'execution_status': execution_status,
            'result_summary': truncate(redact_secrets(result_summary or ''), 2000),
        })

    def write(self, vals):
        raise UserError(self.env._('AI audit entries cannot be modified.'))

    def unlink(self):
        if not self.env.context.get('cai_audit_purge'):
            raise UserError(self.env._('AI audit entries can only be removed by the retention policy.'))
        return super().unlink()

    def action_cai_open_target(self):
        self.ensure_one()
        if self.target_model not in self.env or not self.target_res_id:
            raise UserError(self.env._('There is no target record to open.'))
        record = self.env[self.target_model].browse(self.target_res_id).exists()
        if not record:
            raise UserError(self.env._('The target record no longer exists.'))
        record.check_access('read')
        return {'type': 'ir.actions.act_window', 'res_model': record._name, 'res_id': record.id,
                'views': [[False, 'form']]}

    @api.model
    def _cai_cron_purge(self):
        days = int(self.env['ir.config_parameter'].sudo().get_param('ebshel_ai_suite.audit_retention_days', 0) or 0)
        if days > 0:
            old = self.sudo().search([('event_time', '<', fields.Datetime.now() - timedelta(days=days))], limit=50000)
            old.with_context(cai_audit_purge=True).unlink()
        return True
