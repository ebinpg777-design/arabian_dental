# Part of Ebshel AI Suite. See LICENSE file for full copyright and licensing details.
import logging
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError
from odoo.tools.safe_eval import safe_eval

from ..services.value_converter import SUPPORTED_WRITE_TYPES

_logger = logging.getLogger(__name__)
SCAN_BATCH = 200
SCAN_OVERLAP = timedelta(minutes=15)


class CommunityAIAutomation(models.Model):
    """AI analysis triggered by record creation/update or run on demand.

    Triggers are detected by a scheduled scan of ``create_date`` /
    ``write_date`` rather than by patching ``create``/``write`` of business
    models, which keeps business transactions free of AI latency and failures.
    """
    _name = 'community.ai.automation'
    _description = 'AI Automation'
    _order = 'sequence, id'

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    model_id = fields.Many2one('ir.model', string='Trigger Model', required=True, ondelete='cascade',
                               domain="[('transient', '=', False), ('model', 'not like', 'ir.%'), "
                                      "('model', 'not like', 'community.ai.%'), ('model', 'not like', 'res.users%')]")
    model_name = fields.Char(related='model_id.model', store=True, string='Model Name')
    trigger_event = fields.Selection(
        [('on_create', 'Record created'), ('on_create_or_update', 'Record created or updated'),
         ('manual', 'Manual (action menu)')], default='on_create', required=True)
    filter_domain = fields.Char('Trigger Condition', default='[]',
                                help='Only records matching this domain are processed.')
    instruction = fields.Text('AI Instruction', required=True)
    output_field_ids = fields.Many2many(
        'ir.model.fields', 'community_ai_automation_field_rel', 'automation_id', 'field_id',
        string='Output Fields', domain="[('model_id', '=', model_id), ('readonly', '=', False), "
                                       "('ttype', 'in', %s)]" % sorted(SUPPORTED_WRITE_TYPES),
        help='The only fields the AI may set.')
    capability_ids = fields.Many2many('community.ai.capability', 'community_ai_automation_capability_rel',
                                      'automation_id', 'capability_id', string='Allowed Capabilities',
                                      help='Read-only capabilities run directly; modifying ones wait for approval.')
    execution_mode = fields.Selection(
        [('manual', 'Manual'), ('automatic', 'Automatic'), ('approval', 'Automatic with approval')],
        default='approval', required=True,
        help='Manual: only when launched from the action menu. Automatic: values are written directly. '
             'With approval: values are proposed and a manager approves them.')
    assistant_id = fields.Many2one('community.ai.assistant', string='Assistant (connection & limits)')
    run_as_user_id = fields.Many2one(
        'res.users', string='Run As', required=True, default=lambda self: self.env.user,
        domain=[('share', '=', False)],
        help='Jobs run with the access rights of this user. Only AI administrators can pick another user.')
    post_summary = fields.Boolean('Log Summary in Chatter', default=True)
    last_scan_at = fields.Datetime(readonly=True, copy=False)
    job_ids = fields.One2many('community.ai.job', 'automation_id', string='Jobs')
    job_count = fields.Integer(compute='_compute_job_count')
    binding_action_id = fields.Many2one('ir.actions.server', readonly=True, copy=False, ondelete='set null')
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company)

    def _compute_job_count(self):
        counts = dict(self.env['community.ai.job']._read_group(
            [('automation_id', 'in', self.ids)], ['automation_id'], ['__count']))
        for automation in self:
            automation.job_count = counts.get(automation, 0)

    @api.constrains('filter_domain', 'model_id')
    def _check_domain(self):
        for automation in self:
            try:
                domain = automation._cai_domain()
                self.env[automation.model_name].sudo().search_count(domain, limit=1)
            except Exception as exc:  # noqa: BLE001 - any error means the domain is invalid
                raise ValidationError(self.env._('The trigger condition is invalid: %s', exc)) from exc

    @api.constrains('output_field_ids', 'model_id')
    def _check_outputs(self):
        for automation in self:
            if any(f.model_id != automation.model_id for f in automation.output_field_ids):
                raise ValidationError(self.env._('Output fields must belong to the trigger model.'))

    # Not an @api.constrains: constraints run as superuser, this is a permission check.
    def _cai_is_ai_admin(self):
        return self.env.su or self.env.user.has_group('ebshel_ai_suite.group_cai_admin')

    @api.model_create_multi
    def create(self, vals_list):
        if not self._cai_is_ai_admin():
            for vals in vals_list:
                if vals.get('run_as_user_id', self.env.uid) != self.env.uid:
                    raise AccessError(self.env._('Only AI administrators can run automations as another user.'))
        return super().create(vals_list)

    def write(self, vals):
        if not self._cai_is_ai_admin():
            if vals.get('run_as_user_id', self.env.uid) != self.env.uid:
                raise AccessError(self.env._('Only AI administrators can run automations as another user.'))
            if any(automation.run_as_user_id != self.env.user for automation in self):
                # whoever (non-admin) reconfigures an automation becomes the user it runs as
                vals = dict(vals, run_as_user_id=self.env.uid)
        return super().write(vals)

    def _cai_domain(self):
        self.ensure_one()
        return safe_eval(self.filter_domain or '[]', {'uid': self.env.uid, 'user': self.env.user,
                                                     'context_today': fields.Date.context_today})

    # ------------------------------------------------------------------
    def _cai_enqueue(self, records, user=None):
        self.ensure_one()
        Job = self.env['community.ai.job'].sudo()
        existing = set(Job.search([('automation_id', '=', self.id), ('res_id', 'in', records.ids),
                                   ('state', 'in', ['queued', 'running'])]).mapped('res_id'))
        jobs = Job.create([{
            'job_kind': 'automation', 'automation_id': self.id, 'res_model': self.model_name,
            'res_id': record.id, 'user_id': (user or self.run_as_user_id).id,
        } for record in records if record.id not in existing])
        return jobs

    def _cai_run_for_records(self, records):
        """Bound server action: queue the selected records and process small batches immediately."""
        self.ensure_one()
        jobs = self._cai_enqueue(records, user=self.env.user)
        if len(jobs) <= 3:
            jobs._cai_process()
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {'type': 'info', 'message': self.env._(
                '%s record(s) submitted to the AI automation "%s".', len(records), self.name)},
        }

    def action_cai_publish(self):
        for automation in self:
            if automation.binding_action_id:
                continue
            automation.binding_action_id = self.env['ir.actions.server'].sudo().create({
                'name': self.env._('AI: %s', automation.name),
                'model_id': automation.model_id.id,
                'binding_model_id': automation.model_id.id,
                'binding_type': 'action',
                'state': 'code',
                'code': f"action = env['community.ai.automation'].browse({automation.id})._cai_run_for_records(records)",
                'group_ids': [(4, self.env.ref('ebshel_ai_suite.group_cai_user').id)],
            })
        return True

    def action_cai_unpublish(self):
        self.binding_action_id.sudo().unlink()
        return True

    def action_cai_open_jobs(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('ebshel_ai_suite.action_cai_job')
        action['domain'] = [('automation_id', '=', self.id)]
        return action

    def unlink(self):
        self.binding_action_id.sudo().unlink()
        return super().unlink()

    # ------------------------------------------------------------------
    # Scheduled scan
    # ------------------------------------------------------------------
    @staticmethod
    def _cai_new_records(records, jobs, date_field):
        """Filter out records already handled.

        A record is handled when a job exists for it and, for update triggers,
        that job was created or finished after the record's last change (so the
        automation's own write does not trigger it again)."""
        handled = {}
        for job in jobs:
            stamp = max(filter(None, [job.create_date, job.finished_at]))
            handled[job.res_id] = max(stamp, handled.get(job.res_id, stamp))
        if date_field == 'create_date':
            return records.filtered(lambda r: r.id not in handled)
        return records.filtered(lambda r: r.id not in handled or r.write_date > handled[r.id])

    def _cai_scan(self, now):
        self.ensure_one()
        if self.trigger_event == 'manual' or self.execution_mode == 'manual':
            return 0
        Model = self.env[self.model_name].with_user(self.run_as_user_id)
        date_field = 'write_date' if self.trigger_event == 'on_create_or_update' else 'create_date'
        # ``create_date``/``write_date`` hold the *transaction start* time: records committed by a
        # long transaction can appear after a scan. The overlap window plus the per-record dedupe
        # below make sure they are neither missed nor processed twice.
        since = (self.last_scan_at or self.create_date) - SCAN_OVERLAP
        domain = self._cai_domain() + [(date_field, '>', since), (date_field, '<=', now)]
        records = Model.search(domain, limit=SCAN_BATCH, order=f'{date_field} asc, id asc')
        jobs = self.env['community.ai.job'].sudo().search([('automation_id', '=', self.id),
                                                           ('res_id', 'in', records.ids)])
        fresh = self._cai_new_records(records, jobs, date_field)
        if fresh:
            self._cai_enqueue(fresh)
        self.last_scan_at = records[-1][date_field] if len(records) == SCAN_BATCH else now
        return len(fresh)

    @api.model
    def _cai_cron_scan(self):
        now = fields.Datetime.now()
        for automation in self.sudo().search([('active', '=', True)]):
            try:
                with self.env.cr.savepoint():
                    automation._cai_scan(now)
            except Exception:  # noqa: BLE001 - one bad automation must not block the others
                _logger.exception('AI automation %s scan failed', automation.id)
        for rule in self.env['community.ai.field.rule'].sudo().search(
                [('active', '=', True), ('trigger_mode', '=', 'on_create')]):
            since = (rule.last_scan_at or rule.create_date) - SCAN_OVERLAP
            records = self.env[rule.model_name].search([('create_date', '>', since), ('create_date', '<=', now)],
                                                        limit=SCAN_BATCH, order='create_date asc, id asc')
            jobs = self.env['community.ai.job'].sudo().search([('field_rule_id', '=', rule.id),
                                                               ('res_id', 'in', records.ids)])
            fresh = self._cai_new_records(records, jobs, 'create_date')
            owner = rule.write_uid if rule.write_uid.has_group('ebshel_ai_suite.group_cai_user') else rule.create_uid
            # jobs run with the rights of the rule's author
            rule.with_user(owner)._cai_enqueue(fresh)
            rule.last_scan_at = records[-1].create_date if len(records) == SCAN_BATCH else now
        return True
