# Part of Ebshel AI Suite. See LICENSE file for full copyright and licensing details.
import logging
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from ..services.automation_engine import AutomationError, AutomationExecutor
from ..services.engines.errors import EngineError
from ..services.field_generator import FieldValueGenerator
from ..services.guardrails import redact_secrets, truncate

_logger = logging.getLogger(__name__)
BATCH = 20
BACKOFF_MINUTES = (2, 10, 60)


class CommunityAIJob(models.Model):
    """Background AI work item, executed by a cron with the rights of ``user_id``."""
    _name = 'community.ai.job'
    _description = 'AI Background Job'
    _order = 'id desc'

    name = fields.Char(compute='_compute_name')
    job_kind = fields.Selection([('automation', 'Automation'), ('field_rule', 'AI field'),
                                 ('source_index', 'Knowledge indexing')], required=True, readonly=True)
    automation_id = fields.Many2one('community.ai.automation', ondelete='cascade', readonly=True, index=True)
    field_rule_id = fields.Many2one('community.ai.field.rule', ondelete='cascade', readonly=True, index=True)
    source_id = fields.Many2one('community.ai.source', ondelete='cascade', readonly=True)
    res_model = fields.Char('Model', readonly=True)
    res_id = fields.Integer('Record ID', readonly=True, index=True)
    record_label = fields.Char('Record', compute='_compute_record_label')
    user_id = fields.Many2one('res.users', string='Run As', required=True, readonly=True)
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company, readonly=True)
    state = fields.Selection(
        [('queued', 'Queued'), ('running', 'Running'), ('awaiting_approval', 'Awaiting approval'),
         ('done', 'Done'), ('rejected', 'Rejected'), ('failed', 'Failed'), ('cancelled', 'Cancelled')],
        default='queued', required=True, readonly=True, index=True)
    attempt_count = fields.Integer(readonly=True)
    max_attempts = fields.Integer(default=3, readonly=True)
    next_attempt_at = fields.Datetime(default=fields.Datetime.now, readonly=True, index=True)
    proposed_values = fields.Json(readonly=True, help='Converted values waiting for approval.')
    proposal_display = fields.Json(readonly=True)
    proposal_text = fields.Text('Proposal', compute='_compute_proposal_text')
    rejected_fields = fields.Json(readonly=True)
    summary = fields.Text(readonly=True)
    operation_ids = fields.One2many('community.ai.operation', 'job_id', string='Pending Operations')
    error_public = fields.Char('Error', readonly=True)
    error_detail = fields.Text(readonly=True, groups='ebshel_ai_suite.group_cai_admin')
    finished_at = fields.Datetime(readonly=True)

    def _compute_name(self):
        labels = dict(self._fields['job_kind']._description_selection(self.env))
        for job in self:
            job.name = f'{labels.get(job.job_kind, "")} #{job.id}'

    def _compute_record_label(self):
        for job in self:
            label = False
            if job.res_model in self.env and job.res_id:
                record = self.env[job.res_model].browse(job.res_id).exists()
                label = record.display_name if record and record.has_access('read') else f'{job.res_model}#{job.res_id}'
            job.record_label = label

    def _compute_proposal_text(self):
        for job in self:
            lines = [f'{key}: {value}' for key, value in (job.proposal_display or {}).items()]
            lines += [f'✗ {key}: {reason}' for key, reason in (job.rejected_fields or {}).items()]
            job.proposal_text = '\n'.join(lines) or False

    # ------------------------------------------------------------------
    # Processing
    # ------------------------------------------------------------------
    def _cai_record(self):
        self.ensure_one()
        return self.env[self.res_model].with_user(self.user_id).browse(self.res_id).exists()

    def _cai_process(self):
        for job in self:
            if job.state != 'queued':
                continue
            job.write({'state': 'running', 'attempt_count': job.attempt_count + 1})
            try:
                with self.env.cr.savepoint():
                    job._cai_execute()
            except EngineError as exc:
                user_env = self.with_context(lang=job.user_id.lang).env   # message in the user's language
                job._cai_fail(exc.public_message_for(user_env), f'[{exc.code}] {exc.detail}', retry=exc.retryable)
            except (UserError, AccessError, AutomationError) as exc:
                job._cai_fail(str(exc.args[0] if exc.args else exc), '', retry=False)
            except Exception as exc:  # noqa: BLE001 - keep the queue alive
                _logger.exception('AI job %s crashed', job.id)
                job._cai_fail(self.env._('Unexpected error.'), repr(exc), retry=True)
            job._cai_notify()

    def _cai_fail(self, public, detail, retry):
        self.ensure_one()
        if retry and self.attempt_count < self.max_attempts:
            delay = BACKOFF_MINUTES[min(self.attempt_count - 1, len(BACKOFF_MINUTES) - 1)]
            self.write({'state': 'queued', 'error_public': public,
                        'next_attempt_at': fields.Datetime.now() + timedelta(minutes=delay)})
        else:
            self.write({'state': 'failed', 'error_public': public, 'finished_at': fields.Datetime.now()})
        self.sudo().error_detail = truncate(redact_secrets(detail), 4000)

    def _cai_execute(self):
        self.ensure_one()
        user_env = self.env(user=self.user_id, su=False, context=dict(self.env.context, lang=self.user_id.lang))
        if self.job_kind == 'source_index':
            ok = self.source_id.with_env(user_env)._cai_index_now()
            if not ok:
                raise UserError(self.source_id.index_error or self.env._('Indexing failed.'))
            self.write({'state': 'done', 'finished_at': fields.Datetime.now()})
            return
        record = self._cai_record().with_env(user_env)
        if not record:
            self.write({'state': 'cancelled', 'error_public': self.env._('The record no longer exists.'),
                        'finished_at': fields.Datetime.now()})
            return
        if self.job_kind == 'field_rule':
            result = FieldValueGenerator(user_env).generate_field_value(self.field_rule_id.with_env(user_env), record)
            self.write({'state': 'done', 'finished_at': fields.Datetime.now(),
                        'summary': result.skipped_reason or self.env._('Value generated.')})
            return
        automation = self.automation_id.with_env(user_env)
        executor = AutomationExecutor(user_env)
        proposal = executor.analyze(automation, record, job=self.with_env(user_env))
        values = {
            'summary': proposal.summary, 'rejected_fields': proposal.rejected or False,
            'proposal_display': {k: str(v)[:500] for k, v in proposal.display.items()} or False,
        }
        if automation.execution_mode == 'approval' and (proposal.values or proposal.summary):
            self.write(dict(values, state='awaiting_approval',
                            proposed_values=self._cai_jsonable(proposal.values) or False))
            return
        executor.apply(automation, record, proposal.values, proposal.summary)
        self.write(dict(values, state='done', finished_at=fields.Datetime.now()))

    @staticmethod
    def _cai_jsonable(values):
        result = {}
        for key, value in values.items():
            if hasattr(value, 'isoformat'):
                value = value.isoformat()
            result[key] = value
        return result

    def _cai_notify(self):
        for job in self:
            if job.state in ('done', 'failed', 'awaiting_approval'):
                job.user_id._bus_send('ebshel_ai/job', {
                    'id': job.id, 'state': job.state, 'name': job.name,
                    'record': job.record_label or '', 'error': job.error_public or '',
                })

    @api.model
    def _cai_cron_run(self):
        cron = self.env['ir.cron']
        in_cron = bool(self.env.context.get('ir_cron_progress_id'))
        jobs = self.sudo().search([('state', '=', 'queued'), ('next_attempt_at', '<=', fields.Datetime.now())],
                                  limit=BATCH, order='next_attempt_at, id')
        for index, job in enumerate(jobs, start=1):
            job._cai_process()
            if in_cron:   # commit each job so that a later crash does not lose finished work
                cron._commit_progress(processed=1, remaining=len(jobs) - index)
        return True

    # ------------------------------------------------------------------
    # Buttons
    # ------------------------------------------------------------------
    def _cai_check_manager(self):
        if not (self.env.su or self.env.user.has_group('ebshel_ai_suite.group_cai_manager')):
            raise AccessError(self.env._('Only AI managers can decide on automation proposals.'))

    def action_cai_approve(self):
        """Apply the proposal. The record is written with the approver's rights;
        the job bookkeeping itself is system-managed (sudo)."""
        self._cai_check_manager()
        for job in self.filtered(lambda j: j.state == 'awaiting_approval'):
            record = self.env[job.res_model].browse(job.res_id).exists()
            if not record:
                job.sudo().state = 'cancelled'
                continue
            values = dict(job.proposed_values or {})
            AutomationExecutor(self.env).apply(job.automation_id, record, values, job.summary or '')
            job.sudo().write({'state': 'done', 'finished_at': fields.Datetime.now()})
        return True

    def action_cai_reject(self):
        self._cai_check_manager()
        self.filtered(lambda j: j.state == 'awaiting_approval').sudo().write(
            {'state': 'rejected', 'finished_at': fields.Datetime.now()})
        return True

    def action_cai_retry(self):
        self._cai_check_manager()
        self.filtered(lambda j: j.state in ('failed', 'cancelled')).sudo().write(
            {'state': 'queued', 'next_attempt_at': fields.Datetime.now(), 'attempt_count': 0,
             'error_public': False})
        return True

    def action_cai_run_now(self):
        self._cai_check_manager()
        self.filtered(lambda j: j.state == 'queued').sudo()._cai_process()
        return True
