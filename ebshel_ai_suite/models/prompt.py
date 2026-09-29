# Part of Ebshel AI Suite. See LICENSE file for full copyright and licensing details.
from odoo import api, fields, models, tools
from odoo.exceptions import UserError, ValidationError

from ..services.prompt_renderer import PromptRenderError, PromptRenderer


class CommunityAIPrompt(models.Model):
    """Reusable prompt template rendered with the safe template language."""
    _name = 'community.ai.prompt'
    _description = 'AI Prompt Template'
    _order = 'purpose, sequence, name'

    name = fields.Char(required=True, translate=True)
    code = fields.Char('Technical Code', help='Stable key used by features to find this template, '
                                              'e.g. text.summarize.')
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    purpose = fields.Selection(
        [('general', 'General'), ('text_action', 'Writing action'), ('email', 'Email'),
         ('field', 'AI field'), ('automation', 'Automation')],
        default='general', required=True)
    system_part = fields.Text('System Instruction', help='Optional instruction placed in the system message.')
    body = fields.Text('Template', required=True,
                       help='Use {{ variable }}, {{ record.field }}, filters like | truncate(200) and '
                            '{% if variable %}…{% else %}…{% endif %}.')
    model_id = fields.Many2one('ir.model', string='Applies To', ondelete='cascade')
    output_format = fields.Selection([('text', 'Plain text'), ('html', 'HTML'), ('json', 'JSON')],
                                     default='text', required=True)
    response_language = fields.Selection([('user', "User's language"), ('input', 'Same as input'),
                                          ('fixed', 'Fixed language')], default='user', required=True)
    fixed_lang = fields.Selection(selection='_cai_lang_selection', string='Language')
    placeholder_list = fields.Char('Placeholders', compute='_compute_placeholder_list')
    company_id = fields.Many2one('res.company')

    _code_unique = models.Constraint('UNIQUE(code)', 'The technical code of a prompt must be unique.')

    @api.model
    def _cai_lang_selection(self):
        return self.env['res.lang'].get_installed()

    @api.depends('body')
    def _compute_placeholder_list(self):
        for prompt in self:
            try:
                prompt.placeholder_list = ', '.join(PromptRenderer.placeholders(prompt.body or ''))
            except PromptRenderError as exc:
                prompt.placeholder_list = self.env._('Invalid template: %s', exc)

    @api.constrains('body', 'system_part')
    def _check_syntax(self):
        for prompt in self:
            for template in (prompt.body, prompt.system_part):
                try:
                    PromptRenderer.parse(template or '')
                except PromptRenderError as exc:
                    raise ValidationError(self.env._('Invalid prompt template: %s', exc)) from exc

    @api.model
    @tools.ormcache('code', 'self.env.lang')
    def _cai_prompt_id_by_code(self, code):
        return self.sudo().search([('code', '=', code), ('active', '=', True)], limit=1).id

    @api.model
    def _cai_get_by_code(self, code):
        return self.browse(self._cai_prompt_id_by_code(code))

    def render(self, record=None, variables=None, strict=False):
        """Return ``(system_text, user_text)`` rendered for ``record``."""
        self.ensure_one()
        renderer = PromptRenderer(self.env, record=record, variables=variables, strict=strict)
        return renderer.render(self.system_part or ''), renderer.render(self.body or '')

    def action_cai_preview(self):
        self.ensure_one()
        try:
            _system, body = self.render(variables={})
        except PromptRenderError as exc:
            raise UserError(str(exc)) from exc
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {'title': self.env._('Rendered without variables'), 'message': body[:1500], 'sticky': True},
        }

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        self.env.registry.clear_cache()
        return records

    def write(self, vals):
        result = super().write(vals)
        if {'code', 'active'} & set(vals):
            self.env.registry.clear_cache()
        return result
