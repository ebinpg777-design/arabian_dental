# Part of Ebshel AI Suite. See LICENSE file for full copyright and licensing details.
from odoo import api, fields, models, tools
from odoo.exceptions import ValidationError


class CommunityAIPrivacyRule(models.Model):
    """Administrator-defined fields that must never be sent to an AI engine.

    They extend the non-removable baseline patterns of the context builder
    (passwords, tokens, keys, bank and card numbers, ...).
    """
    _name = 'community.ai.privacy.rule'
    _description = 'AI Privacy Rule'
    _order = 'model_id, field_pattern'

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    model_id = fields.Many2one('ir.model', string='Model', ondelete='cascade',
                               help='Leave empty to apply the rule to every model.')
    field_pattern = fields.Char('Field Name Pattern', required=True,
                                help='Technical field name or shell-style pattern, e.g. "vat", "*_salary", "x_*".')
    note = fields.Text()

    @api.constrains('field_pattern')
    def _check_pattern(self):
        for rule in self:
            pattern = (rule.field_pattern or '').strip()
            if not pattern or len(pattern) > 64 or any(c.isspace() for c in pattern):
                raise ValidationError(self.env._('The field pattern must be a single word of at most 64 characters.'))

    @api.model
    @tools.ormcache('model_name')
    def _cai_patterns_for(self, model_name):
        rules = self.sudo().search([('active', '=', True), '|', ('model_id', '=', False),
                                    ('model_id.model', '=', model_name)])
        return tuple(sorted({rule.field_pattern.strip().lower() for rule in rules}))

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        self.env.registry.clear_cache()
        return records

    def write(self, vals):
        result = super().write(vals)
        self.env.registry.clear_cache()
        return result

    def unlink(self):
        result = super().unlink()
        self.env.registry.clear_cache()
        return result
