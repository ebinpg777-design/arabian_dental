# Part of Ebshel AI Suite. See LICENSE file for full copyright and licensing details.
from odoo import fields, models

AI_OPERATOR_MODEL = 'community.ai.assistant'


class ImLivechatChannel(models.Model):
    _inherit = 'im_livechat.channel'

    cai_assistant_id = fields.Many2one(
        'community.ai.assistant', string='AI Assistant', groups='im_livechat.im_livechat_group_manager',
        help='When set, the assistant answers visitors first and hands over to an operator when needed. '
             'A chatbot script selected by a rule still takes precedence.')
    cai_max_replies = fields.Integer(
        'Max AI Answers', default=15, groups='im_livechat.im_livechat_group_manager',
        help='After this many answers in a conversation the visitor is handed over to an operator.')
    cai_operator_partner_id = fields.Many2one('res.partner', string='AI Operator', readonly=True, copy=False,
                                              groups='im_livechat.im_livechat_group_manager')

    def _cai_operator_partner(self):
        """Inactive partner used as the author of the AI answers (one per live chat channel)."""
        self.ensure_one()
        channel = self.sudo()
        if not channel.cai_operator_partner_id:
            channel.cai_operator_partner_id = self.env['res.partner'].sudo().create({
                'name': channel.cai_assistant_id.title or self.env._('AI Assistant'),
                'active': False,
            })
        return channel.cai_operator_partner_id

    def _get_operator_info(self, /, **kwargs):
        channel = self.sudo()
        assistant = channel.cai_assistant_id
        previous = kwargs.get('previous_operator_id')
        returning_to_human = previous and (not channel.cai_operator_partner_id
                                           or str(previous) != str(channel.cai_operator_partner_id.id))
        if assistant.active and not kwargs.get('chatbot_script_id') and not returning_to_human:
            return {
                'agent': self.env['res.users'],
                'chatbot_script': self.env['chatbot.script'],
                'operator_partner': channel._cai_operator_partner(),
                'operator_model': AI_OPERATOR_MODEL,
            }
        return super()._get_operator_info(**kwargs)

    def _get_livechat_discuss_channel_vals(self, /, **kwargs):
        vals = super()._get_livechat_discuss_channel_vals(**kwargs)
        if kwargs.get('operator_model') == AI_OPERATOR_MODEL:
            vals['cai_livechat_assistant_id'] = self.sudo().cai_assistant_id.id
        return vals

    def _get_channel_name(self, /, *, operator_model, **kwargs):
        if operator_model == AI_OPERATOR_MODEL:
            return self.sudo().cai_assistant_id.title
        return super()._get_channel_name(operator_model=operator_model, **kwargs)
