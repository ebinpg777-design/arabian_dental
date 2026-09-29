# Part of Ebshel AI Suite. See LICENSE file for full copyright and licensing details.
from odoo import api, fields, models


class CommunityAIContextPreset(models.Model):
    """How the AI behaves when it is opened from a given kind of record.

    A preset picks the assistant to call, adds context instructions on top of
    the assistant's own instructions and offers quick-prompt buttons.
    """
    _name = 'community.ai.context.preset'
    _description = 'AI Context Preset'
    _order = 'sequence, id'

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    purpose = fields.Selection(
        [('assist', 'Ask the assistant (console)'), ('write', 'Write or improve text (writing menu)')],
        string='When Users Need To', default='assist', required=True)
    model_ids = fields.Many2many('ir.model', 'community_ai_context_preset_model_rel', 'preset_id', 'model_id',
                                 string='On the Models', domain=[('transient', '=', False)],
                                 help='Leave empty to apply the preset everywhere.')
    needs_record = fields.Boolean('Only on a Record', help='Only offered when a record is open in a form view.')
    assistant_id = fields.Many2one('community.ai.assistant', string='Call the Assistant',
                                   help='Leave empty to keep the default assistant.')
    instructions = fields.Text('Context Instructions', translate=True,
                               help='How the assistant should behave in this context, in addition to its own '
                                    'instructions.')
    quick_prompt_ids = fields.One2many('community.ai.quick.prompt', 'preset_id', string='Buttons', copy=True)
    group_ids = fields.Many2many('res.groups', 'community_ai_context_preset_group_rel', 'preset_id', 'group_id',
                                 string='Available To', help='Leave empty for every AI user.')
    company_id = fields.Many2one('res.company')

    @api.model
    def _cai_find(self, purpose, model_name=None, has_record=False):
        """Best matching preset: model-specific first, then generic, by sequence."""
        user = self.env.user
        presets = self.sudo().search([('active', '=', True), ('purpose', '=', purpose),
                                      ('company_id', 'in', [False, *user.company_ids.ids])])
        candidates = presets.filtered(
            lambda p: (not p.needs_record or has_record)
            and (not p.group_ids or p.group_ids & user.all_group_ids)
            and (not p.model_ids or (model_name and model_name in p.model_ids.mapped('model'))))
        specific = candidates.filtered('model_ids')
        return (specific or candidates)[:1]

    def _cai_card(self):
        self.ensure_one()
        return {
            'id': self.id,
            'name': self.name,
            'assistant_id': self.assistant_id.id or False,
            'buttons': [{'id': b.id, 'title': b.title, 'prompt': b.prompt}
                        for b in self.quick_prompt_ids.sorted('sequence')],
        }


class CommunityAIQuickPrompt(models.Model):
    _name = 'community.ai.quick.prompt'
    _description = 'AI Quick Prompt'
    _order = 'sequence, id'

    preset_id = fields.Many2one('community.ai.context.preset', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    title = fields.Char(required=True, translate=True, help='Label of the button.')
    prompt = fields.Text(required=True, translate=True, help='Request sent to the assistant when clicked.')
