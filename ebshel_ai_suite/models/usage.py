# Part of Ebshel AI Suite. See LICENSE file for full copyright and licensing details.
from datetime import timedelta

from odoo import api, fields, models

PURPOSES = [('chat', 'Conversation'), ('text', 'Writing action'), ('field', 'AI field'),
            ('automation', 'Automation'), ('search', 'Natural-language search'), ('embedding', 'Embedding'),
            ('image', 'Image'), ('voice', 'Voice transcription'), ('mcp', 'MCP server'), ('other', 'Other')]


class CommunityAIUsage(models.Model):
    """One engine call, for consumption reporting and quotas."""
    _name = 'community.ai.usage'
    _description = 'AI Usage'
    _order = 'requested_at desc, id desc'
    _rec_name = 'requested_at'

    requested_at = fields.Datetime(default=fields.Datetime.now, required=True, index=True, readonly=True)
    user_id = fields.Many2one('res.users', index=True, readonly=True, ondelete='set null')
    company_id = fields.Many2one('res.company', index=True, readonly=True)
    assistant_id = fields.Many2one('community.ai.assistant', index=True, readonly=True, ondelete='set null')
    connection_id = fields.Many2one('community.ai.connection', readonly=True, ondelete='set null')
    engine_type = fields.Char(readonly=True)
    model_identifier = fields.Char('Model', readonly=True)
    purpose = fields.Selection(PURPOSES, default='other', readonly=True)
    input_tokens = fields.Integer(readonly=True)
    output_tokens = fields.Integer(readonly=True)
    token_total = fields.Integer('Total Tokens', compute='_compute_token_total', store=True)
    duration_ms = fields.Integer('Duration (ms)', readonly=True, aggregator='avg')
    currency_id = fields.Many2one('res.currency', readonly=True)
    # Float, not Monetary: per-call costs are fractions of a cent and must not be rounded away.
    estimated_cost = fields.Float(readonly=True, digits=(16, 6))
    status = fields.Selection([('success', 'Success'), ('failed', 'Failed')], readonly=True, index=True)
    error_code = fields.Char(readonly=True)

    @api.depends('input_tokens', 'output_tokens')
    def _compute_token_total(self):
        for usage in self:
            usage.token_total = usage.input_tokens + usage.output_tokens

    @api.model
    def _cai_record(self, *, connection, assistant, model, purpose, input_tokens=0, output_tokens=0,
                    duration_ms=0, status='success', error_code=False):
        connection = connection.sudo()
        company = self.env.company
        cost = (input_tokens * connection.cost_input_per_million
                + output_tokens * connection.cost_output_per_million) / 1_000_000
        purpose = purpose if purpose in dict(PURPOSES) else 'other'
        return self.sudo().create({
            'user_id': self.env.user.id,
            'company_id': company.id,
            'assistant_id': assistant.id if assistant else False,
            'connection_id': connection.id,
            'engine_type': connection.engine_type,
            'model_identifier': model or False,
            'purpose': purpose,
            'input_tokens': input_tokens or 0,
            'output_tokens': output_tokens or 0,
            'duration_ms': duration_ms,
            'currency_id': connection.currency_id.id,
            'estimated_cost': cost,
            'status': status,
            'error_code': error_code,
        })

    @api.model
    def _cai_cron_purge(self):
        days = int(self.env['ir.config_parameter'].sudo().get_param('ebshel_ai_suite.usage_retention_days', 0) or 0)
        if days > 0:
            self.sudo().search([('requested_at', '<', fields.Datetime.now() - timedelta(days=days))],
                               limit=50000).unlink()
        return True
