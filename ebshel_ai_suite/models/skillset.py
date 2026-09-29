# Part of Ebshel AI Suite. See LICENSE file for full copyright and licensing details.
from odoo import api, fields, models


class CommunityAISkillset(models.Model):
    """A reusable bundle of instructions and capabilities.

    Assistants combine their own instruction with the instructions of their
    skill sets, and may use the capabilities of every assigned skill set in
    addition to the capabilities assigned to them directly.
    """
    _name = 'community.ai.skillset'
    _description = 'AI Skill Set'
    _order = 'sequence, name'

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    summary = fields.Char(translate=True, help='When should an assistant use this skill set?')
    instructions = fields.Text(
        translate=True,
        help='Purpose, rules and step-by-step workflow the assistant follows when it uses this skill set.')
    capability_ids = fields.Many2many('community.ai.capability', 'community_ai_skillset_capability_rel',
                                      'skillset_id', 'capability_id', string='Capabilities')
    assistant_ids = fields.Many2many('community.ai.assistant', 'community_ai_assistant_skillset_rel',
                                     'skillset_id', 'assistant_id', string='Assistants')
    capability_count = fields.Integer(compute='_compute_capability_count')

    @api.depends('capability_ids')
    def _compute_capability_count(self):
        for skillset in self:
            skillset.capability_count = len(skillset.capability_ids)

    def _cai_prompt_section(self) -> str:
        parts = []
        for skillset in self.filtered('active'):
            header = f'### Skill set: {skillset.name}'
            if skillset.summary:
                header += f' — {skillset.summary}'
            parts.append(header + ('\n' + skillset.instructions.strip() if skillset.instructions else ''))
        return '\n\n'.join(parts)
