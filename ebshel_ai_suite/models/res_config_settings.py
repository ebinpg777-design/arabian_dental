# Part of Ebshel AI Suite. See LICENSE file for full copyright and licensing details.
from odoo import fields, models

PREFIX = 'ebshel_ai_suite.'


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    cai_connection_id = fields.Many2one(related='company_id.cai_connection_id', readonly=False,
                                        string='Default Connection')
    cai_assistant_id = fields.Many2one(related='company_id.cai_assistant_id', readonly=False,
                                       string='Default Assistant')
    cai_streaming = fields.Boolean('Stream Answers', config_parameter=PREFIX + 'streaming', default=True)
    cai_confirmation_policy = fields.Selection(
        [('capability', 'As configured on each capability / assistant'), ('always', 'Always confirm changes')],
        string='Confirmation of Changes', config_parameter=PREFIX + 'confirmation_policy', default='capability')
    cai_limit_user_daily_requests = fields.Integer('Requests / User / Day',
                                                   config_parameter=PREFIX + 'limit_user_daily_requests')
    cai_limit_user_daily_tokens = fields.Integer('Tokens / User / Day',
                                                 config_parameter=PREFIX + 'limit_user_daily_tokens')
    cai_limit_assistant_daily_requests = fields.Integer('Requests / Assistant / Day',
                                                        config_parameter=PREFIX + 'limit_assistant_daily_requests')
    cai_limit_monthly_tokens = fields.Integer('Tokens / Company / Month',
                                              config_parameter=PREFIX + 'limit_monthly_tokens')
    cai_context_max_chars = fields.Integer('Record Context Size (characters)',
                                           config_parameter=PREFIX + 'context_max_chars', default=12000)
    cai_audit_read_operations = fields.Boolean('Audit Read-only Capabilities',
                                               config_parameter=PREFIX + 'audit_read_operations')
    cai_audit_retention_days = fields.Integer('Audit Retention (days)',
                                              config_parameter=PREFIX + 'audit_retention_days', default=365)
    cai_usage_retention_days = fields.Integer('Usage Retention (days)',
                                              config_parameter=PREFIX + 'usage_retention_days', default=730)
    cai_allow_private_urls = fields.Boolean('Allow Private Network URLs',
                                            config_parameter=PREFIX + 'allow_private_urls')
    cai_web_backend = fields.Selection([('brave', 'Brave Search API'), ('searxng', 'SearXNG (self-hosted)')],
                                       string='Web Search', config_parameter=PREFIX + 'web_backend')
    cai_brave_api_key = fields.Char('Brave API Key', config_parameter=PREFIX + 'brave_api_key')
    cai_searxng_url = fields.Char('SearXNG URL', config_parameter=PREFIX + 'searxng_url')
    cai_mcp_enabled = fields.Boolean('MCP Server', config_parameter=PREFIX + 'mcp_enabled')
    cai_mcp_allow_write = fields.Boolean('Allow Changes Through MCP', config_parameter=PREFIX + 'mcp_allow_write')
    cai_mcp_url = fields.Char('MCP Endpoint', compute='_compute_cai_mcp_url')

    def _compute_cai_mcp_url(self):
        base = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '')
        for settings in self:
            settings.cai_mcp_url = f'{base.rstrip("/")}/ebshel_ai/mcp'

    def action_cai_generate_mcp_key(self):
        """Open the standard API key wizard, producing a key limited to the MCP server."""
        return {
            'type': 'ir.actions.act_window', 'res_model': 'res.users.apikeys.description',
            'name': self.env._('New MCP Key'), 'views': [(False, 'form')], 'target': 'new',
            'context': {'cai_mcp_scope': True},
        }
