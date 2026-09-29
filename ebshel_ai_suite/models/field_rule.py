# Part of Ebshel AI Suite. See LICENSE file for full copyright and licensing details.
from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

from ..services.engines.errors import EngineError
from ..services.field_generator import FieldValueGenerator
from ..services.value_converter import SUPPORTED_WRITE_TYPES

SYNC_LIMIT = 5


class CommunityAIFieldRule(models.Model):
    """Configuration of an AI-generated or AI-improved field value."""
    _name = 'community.ai.field.rule'
    _description = 'AI Field Rule'
    _order = 'model_id, sequence, id'

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    model_id = fields.Many2one('ir.model', required=True, ondelete='cascade',
                               domain="[('transient', '=', False), ('model', 'not like', 'ir.%'), "
                                      "('model', 'not like', 'community.ai.%')]")
    model_name = fields.Char(related='model_id.model', store=True, string='Model Name')
    field_id = fields.Many2one(
        'ir.model.fields', string='Target Field', required=True, ondelete='cascade',
        domain="[('model_id', '=', model_id), ('ttype', 'in', %s), ('readonly', '=', False)]"
               % sorted(SUPPORTED_WRITE_TYPES))
    instruction = fields.Text(help='What the AI should produce. Placeholders such as {{ record.name }} are allowed.')
    prompt_id = fields.Many2one('community.ai.prompt', string='Prompt Template',
                                domain=[('purpose', 'in', ['field', 'general'])])
    input_field_ids = fields.Many2many(
        'ir.model.fields', 'community_ai_field_rule_input_rel', 'rule_id', 'field_id',
        string='Input Fields', domain="[('model_id', '=', model_id)]",
        help='Fields sent to the AI. Leave empty to send every allowed field (privacy rules still apply).')
    assistant_id = fields.Many2one('community.ai.assistant', string='Assistant (limits)')
    connection_id = fields.Many2one('community.ai.connection', string='Connection')
    overwrite_mode = fields.Selection([('empty', 'Only fill empty values'), ('always', 'Always overwrite')],
                                      default='empty', required=True)
    response_language = fields.Selection([('user', "User's language"), ('free', 'Let the AI decide')],
                                         default='user', required=True)
    trigger_mode = fields.Selection([('manual', 'Manual (action menu)'), ('on_create', 'When a record is created')],
                                    default='manual', required=True)
    fill_empty_daily = fields.Boolean(
        'Fill Empty Values Daily', help='A daily scheduled job queues generation for records where the field is empty.')
    form_view_id = fields.Many2one('ir.ui.view', string='Form Button View', readonly=True, copy=False,
                                   ondelete='set null')
    last_scan_at = fields.Datetime(readonly=True, copy=False)
    binding_action_id = fields.Many2one('ir.actions.server', readonly=True, copy=False, ondelete='set null')
    company_id = fields.Many2one('res.company')

    @api.constrains('model_id', 'field_id', 'input_field_ids')
    def _check_fields(self):
        for rule in self:
            if rule.field_id.model_id != rule.model_id or any(f.model_id != rule.model_id for f in rule.input_field_ids):
                raise ValidationError(self.env._('All fields must belong to the selected model.'))
            if rule.field_id.ttype not in SUPPORTED_WRITE_TYPES:
                raise ValidationError(self.env._('Fields of type %s cannot be generated.', rule.field_id.ttype))

    @api.constrains('instruction', 'prompt_id')
    def _check_instruction(self):
        for rule in self:
            if not rule.instruction and not rule.prompt_id:
                raise ValidationError(self.env._('Please give an instruction or choose a prompt template.'))

    # ------------------------------------------------------------------
    def _cai_generate_for_records(self, records):
        """Entry point of the bound server action (manual trigger)."""
        self.ensure_one()
        records = records.with_env(self.env)
        if len(records) > SYNC_LIMIT:
            self._cai_enqueue(records)
            return {
                'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'type': 'info', 'message': self.env._(
                    '%s records queued; you will be notified when the AI has finished.', len(records))},
            }
        generator = FieldValueGenerator(self.env)
        done, skipped = 0, 0
        for record in records:
            try:
                result = generator.generate_field_value(self, record)
            except EngineError as exc:
                raise UserError(exc.public_message_for(self.env)) from exc
            done += result.written
            skipped += not result.written
        message = self.env._('%(done)s value(s) generated, %(skipped)s skipped.', done=done, skipped=skipped)
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {'type': 'success', 'message': message, 'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'}},
        }

    def _cai_enqueue(self, records):
        self.ensure_one()
        Job = self.env['community.ai.job'].sudo()
        for record in records:
            Job.create({'job_kind': 'field_rule', 'field_rule_id': self.id, 'res_model': record._name,
                        'res_id': record.id, 'user_id': self.env.user.id})

    # ------------------------------------------------------------------
    # Refresh button on the form
    # ------------------------------------------------------------------
    @api.model
    def cai_generate_for_field(self, res_model, res_id, field_name):
        """Regenerate one field of a saved record (AI button next to the field)."""
        if not self.env.user.has_group('ebshel_ai_suite.group_cai_user'):
            raise UserError(self.env._('You are not allowed to use AI features.'))
        rule = self.sudo().search([('active', '=', True), ('model_name', '=', res_model),
                                   ('field_id.name', '=', field_name),
                                   ('company_id', 'in', [False, *self.env.user.company_ids.ids])], limit=1)
        if not rule:
            raise UserError(self.env._('No AI field rule is configured for this field.'))
        record = self.env[res_model].browse(int(res_id)).exists()
        if not record:
            raise UserError(self.env._('Please save the record first.'))
        try:
            result = FieldValueGenerator(self.env).generate_field_value(rule.with_env(self.env), record, force=True)
        except EngineError as exc:
            raise UserError(exc.public_message_for(self.env)) from exc
        return {'written': result.written, 'message': result.skipped_reason or ''}

    def _cai_form_arch(self):
        self.ensure_one()
        return ('<data><xpath expr="(//field[@name=\'%s\'])[1]" position="attributes">'
                '<attribute name="widget">cai_ai_field</attribute></xpath></data>') % self.field_id.name

    def action_cai_add_form_button(self):
        """Show an AI refresh button next to the field in the model's main form view."""
        for rule in self:
            if rule.form_view_id:
                continue
            if rule.field_id.ttype in ('many2many', 'one2many'):
                raise UserError(self.env._('The form button is not available for relational list fields; '
                                           'use the Action menu instead.'))
            # the form view the model opens by default (not simply the first primary one)
            base_view = self.env['ir.ui.view'].sudo().browse(
                self.env[rule.model_name].sudo().get_view(view_type='form').get('id'))
            if not base_view:
                raise UserError(self.env._('The model has no form view.'))
            try:
                with self.env.cr.savepoint():
                    rule.form_view_id = self.env['ir.ui.view'].sudo().create({
                        'name': f'ebshel_ai_suite.ai_field.{rule.model_name}.{rule.field_id.name}',
                        'model': rule.model_name, 'inherit_id': base_view.id, 'mode': 'extension',
                        'priority': 99, 'arch': rule._cai_form_arch(),
                    })
            except (ValidationError, ValueError) as exc:
                raise UserError(self.env._('The field %s is not displayed in the form view of %s.',
                                           rule.field_id.name, rule.model_name)) from exc
        return True

    def action_cai_remove_form_button(self):
        self.form_view_id.sudo().unlink()
        return True

    @api.model
    def _cai_cron_fill_empty(self):
        Job = self.env['community.ai.job'].sudo()
        for rule in self.sudo().search([('active', '=', True), ('fill_empty_daily', '=', True)]):
            model = self.env[rule.model_name]
            if rule.field_id.name not in model._fields:
                continue
            records = model.search([(rule.field_id.name, '=', False)], limit=100, order='id desc')
            busy = set(Job.search([('field_rule_id', '=', rule.id), ('res_id', 'in', records.ids),
                                   ('state', 'in', ['queued', 'running'])]).mapped('res_id'))
            owner = rule.write_uid if rule.write_uid.has_group('ebshel_ai_suite.group_cai_user') else rule.create_uid
            rule.with_user(owner)._cai_enqueue(records.filtered(lambda r, busy=busy: r.id not in busy))
        return True

    def action_cai_publish(self):
        """Add a 'Generate <field> with AI' entry to the Action menu of the model."""
        for rule in self:
            if rule.binding_action_id:
                continue
            action = self.env['ir.actions.server'].sudo().create({
                'name': self.env._('AI: %s', rule.name),
                'model_id': rule.model_id.id,
                'binding_model_id': rule.model_id.id,
                'binding_type': 'action',
                'state': 'code',
                'code': f"action = env['community.ai.field.rule'].browse({rule.id})._cai_generate_for_records(records)",
                'group_ids': [(4, self.env.ref('ebshel_ai_suite.group_cai_user').id)],
            })
            rule.binding_action_id = action
        return True

    def action_cai_unpublish(self):
        self.binding_action_id.sudo().unlink()
        return True

    def unlink(self):
        self.binding_action_id.sudo().unlink()
        self.form_view_id.sudo().unlink()
        return super().unlink()
