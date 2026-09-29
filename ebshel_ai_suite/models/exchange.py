# Part of Ebshel AI Suite. See LICENSE file for full copyright and licensing details.
from odoo import fields, models
from odoo.exceptions import UserError

from ..services.text_tools import markdown_to_html


class CommunityAIExchange(models.Model):
    """One message of a conversation (user prompt, assistant answer, tool step)."""
    _name = 'community.ai.exchange'
    _description = 'AI Conversation Message'
    _order = 'id'
    _rec_name = 'speaker_type'

    session_id = fields.Many2one('community.ai.session', required=True, ondelete='cascade', index=True)
    user_id = fields.Many2one(related='session_id.user_id', store=True, index=True)
    company_id = fields.Many2one(related='session_id.company_id', store=True)
    speaker_type = fields.Selection(
        [('user', 'User'), ('assistant', 'Assistant'), ('tool', 'Capability'), ('event', 'Interface event')],
        required=True)
    message_body = fields.Text('Message')
    posted_at = fields.Datetime(default=fields.Datetime.now, readonly=True)
    processing_state = fields.Selection(
        [('queued', 'Queued'), ('running', 'Running'), ('done', 'Done'), ('failed', 'Failed'),
         ('cancelled', 'Cancelled')], default='done', required=True)
    is_intermediate = fields.Boolean(help='Step of a tool-calling round, hidden in the chat.')
    tool_calls = fields.Json('Requested Capabilities')
    tool_name = fields.Char('Capability')
    tool_label = fields.Char()
    tool_call_ref = fields.Char()
    tool_status = fields.Selection(
        [('executed', 'Executed'), ('awaiting_confirmation', 'Awaiting confirmation'),
         ('rejected', 'Rejected'), ('failed', 'Failed')])
    operation_id = fields.Many2one('community.ai.operation', ondelete='set null')
    input_tokens = fields.Integer()
    output_tokens = fields.Integer()
    execution_time = fields.Integer('Duration (ms)')
    citations = fields.Json()
    navigation_action = fields.Json()
    media = fields.Json(help='Files produced for the user (e.g. generated images).')
    error_public = fields.Char('Error')
    error_detail = fields.Text('Technical Error', groups='ebshel_ai_suite.group_cai_admin')
    file_ids = fields.One2many('community.ai.session.file', 'exchange_id', string='Files')

    def _cai_payload(self):
        self.ensure_one()
        payload = {
            'id': self.id,
            'speaker': self.speaker_type,
            'body': self.message_body or '',
            'state': self.processing_state,
            'intermediate': self.is_intermediate,
            'posted_at': fields.Datetime.to_string(self.posted_at),
            'error': self.error_public or False,
            'citations': self.citations or [],
            'navigation': self.navigation_action or False,
            'media': self.media or [],
        }
        if self.speaker_type == 'tool':
            payload.update(tool=self.tool_name, tool_label=self.tool_label or self.tool_name,
                           tool_status=self.tool_status)
            payload['body'] = ''   # raw tool payloads stay server-side
        if self.operation_id:
            payload['operation'] = self.operation_id.sudo()._cai_payload()
        if self.file_ids:
            payload['files'] = self.file_ids.sudo()._cai_payload()
        if self.speaker_type == 'assistant' and self.processing_state == 'done' and self.message_body:
            payload['can_post'] = self.session_id._cai_context_thread() is not None
        return payload

    def cai_compose_action(self, mode='message'):
        """Open the standard composer prefilled with this answer (never sends by itself).

        ``mode`` is ``message`` (send to followers/recipients) or ``note`` (internal note).
        """
        self.ensure_one()
        session = self.session_id
        session._cai_check_owner()
        if self.speaker_type != 'assistant' or not self.message_body:
            raise UserError(self.env._('Only answers of the assistant can be reused.'))
        record = session._cai_context_thread()
        if record is None:
            raise UserError(self.env._('This conversation is not linked to a record with a chatter.'))
        context = {
            'default_model': record._name,
            'default_res_ids': record.ids,
            'default_composition_mode': 'comment',
            'default_body': markdown_to_html(self.message_body),
        }
        if mode == 'note':
            context['default_subtype_xmlid'] = 'mail.mt_note'
        return {
            'type': 'ir.actions.act_window', 'res_model': 'mail.compose.message', 'view_mode': 'form',
            'views': [[False, 'form']], 'target': 'new', 'context': context,
            'name': self.env._('Log a note') if mode == 'note' else self.env._('Send a message'),
        }
