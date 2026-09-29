# Part of Ebshel AI Suite. See LICENSE file for full copyright and licensing details.
from odoo import fields, models


class CommunityAISearchTarget(models.Model):
    """Business object that natural-language search is allowed to query."""
    _name = 'community.ai.search.target'
    _description = 'AI Search Target'
    _order = 'sequence, id'

    model_id = fields.Many2one('ir.model', required=True, ondelete='cascade',
                               domain="[('transient', '=', False), ('model', 'not like', 'ir.%'), "
                                      "('model', 'not like', 'community.ai.%')]")
    model_name = fields.Char(related='model_id.model', store=True, string='Model Name')
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    keywords = fields.Char(help='Extra words that point to this object, e.g. "customer client contact".')
    field_ids = fields.Many2many('ir.model.fields', 'community_ai_search_target_field_rel', 'target_id', 'field_id',
                                 string='Searchable Fields', domain="[('model_id', '=', model_id)]",
                                 help='Leave empty to allow every readable, non-sensitive field.')

    _model_unique = models.Constraint('UNIQUE(model_id)', 'This model is already a search target.')
