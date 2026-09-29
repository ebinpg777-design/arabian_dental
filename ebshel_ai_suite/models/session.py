# Part of Ebshel AI Suite. See LICENSE file for full copyright and licensing details.
from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from ..services.conversation_runner import ConversationRunner

RECENT_SESSIONS = 15


class CommunityAISession(models.Model):
    """One conversation between a user and an assistant."""
    _name = 'community.ai.session'
    _description = 'AI Conversation'
    _order = 'last_activity desc, id desc'
    _rec_name = 'subject'

    subject = fields.Char(default=lambda self: self._cai_default_subject())
    assistant_id = fields.Many2one('community.ai.assistant', required=True, ondelete='restrict', index=True)
    user_id = fields.Many2one('res.users', required=True, default=lambda self: self.env.user, index=True,
                              ondelete='cascade', readonly=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company, readonly=True)
    started_at = fields.Datetime(default=fields.Datetime.now, readonly=True)
    last_activity = fields.Datetime(default=fields.Datetime.now, readonly=True, index=True)
    status = fields.Selection([('open', 'Open'), ('closed', 'Closed')], default='open', required=True)
    context_model = fields.Char('Context Model', readonly=True)
    context_res_id = fields.Integer('Context Record', readonly=True)
    context_label = fields.Char(compute='_compute_context_label')
    exchange_ids = fields.One2many('community.ai.exchange', 'session_id', string='Messages')
    preset_id = fields.Many2one('community.ai.context.preset', string='Context Preset', readonly=True,
                                ondelete='set null')
    file_ids = fields.One2many('community.ai.session.file', 'session_id', string='Files')
    exchange_count = fields.Integer(compute='_compute_totals')
    total_input_tokens = fields.Integer(compute='_compute_totals')
    total_output_tokens = fields.Integer(compute='_compute_totals')

    @api.model
    def _cai_default_subject(self):
        return self.env._('New conversation')

    def _compute_context_label(self):
        for session in self:
            label = False
            if session.context_model in self.env and session.context_res_id:
                record = self.env[session.context_model].browse(session.context_res_id).exists()
                if record and record.has_access('read'):
                    label = record.display_name
            session.context_label = label

    def _compute_totals(self):
        rows = self.env['community.ai.exchange']._read_group(
            [('session_id', 'in', self.ids)], ['session_id'],
            ['__count', 'input_tokens:sum', 'output_tokens:sum'])
        totals = {session: (count, tin, tout) for session, count, tin, tout in rows}
        for session in self:
            count, tin, tout = totals.get(session, (0, 0, 0))
            session.exchange_count, session.total_input_tokens, session.total_output_tokens = count, tin, tout

    # ------------------------------------------------------------------
    # Guards
    # ------------------------------------------------------------------
    def _cai_check_owner(self):
        self.ensure_one()
        if self.user_id != self.env.user:
            raise AccessError(self.env._('Only the author of a conversation can continue it.'))
        if self.status != 'open':
            raise UserError(self.env._('This conversation is closed.'))

    @api.model
    def _cai_sanitize_context(self, context_model, context_res_id):
        """Keep the record context only when it designates a readable record."""
        if not context_model or not context_res_id or context_model not in self.env:
            return False, 0
        Model = self.env[context_model]
        if Model._transient or Model._abstract or context_model.startswith(('ir.', 'community.ai.', 'res.users')):
            return False, 0
        try:
            record = Model.browse(int(context_res_id)).exists()
        except (TypeError, ValueError):
            return False, 0
        if not record or not record.has_access('read'):
            return False, 0
        return context_model, record.id

    # ------------------------------------------------------------------
    # Public API used by the web client
    # ------------------------------------------------------------------
    @api.model
    def cai_launch_info(self, context_model=None, context_res_id=None):
        """Preset (assistant + quick prompts) applying where the console is opened."""
        model, res_id = self._cai_sanitize_context(context_model, context_res_id)
        preset = self.env['community.ai.context.preset']._cai_find(
            'assist', model or context_model or None, has_record=bool(res_id))
        if preset and preset.assistant_id and \
                preset.assistant_id not in self.env['community.ai.assistant']._cai_usable_assistants():
            preset = preset.browse()
        return {'preset': preset._cai_card() if preset else False}

    @api.model
    def cai_start(self, assistant_id=None, context_model=None, context_res_id=None, preset_id=None):
        """Create a conversation and return its payload."""
        Assistant = self.env['community.ai.assistant']
        usable = Assistant._cai_usable_assistants()
        model, res_id = self._cai_sanitize_context(context_model, context_res_id)
        preset = self.env['community.ai.context.preset']
        if preset_id:
            expected = preset._cai_find('assist', model or context_model or None, has_record=bool(res_id))
            preset = expected if expected.id == preset_id else preset
        if not assistant_id and preset.assistant_id:
            assistant_id = preset.assistant_id.id
        if assistant_id:
            assistant = Assistant.browse(assistant_id)
            if assistant not in usable:
                raise AccessError(self.env._('You are not allowed to use this assistant.'))
        else:
            assistant = self.env.company.cai_assistant_id
            if assistant not in usable:
                assistant = usable[:1]
        if not assistant:
            raise UserError(self.env._('No AI assistant is available for you. Please contact an administrator.'))
        session = self.create({'assistant_id': assistant.id, 'context_model': model or False,
                               'context_res_id': res_id, 'preset_id': preset.id or False})
        return session._cai_payload()

    def cai_stage_file(self, name, data_b64):
        """Attach a file to the next message of this conversation."""
        self.ensure_one()
        self._cai_check_owner()
        return self.env['community.ai.session.file']._cai_create_from_upload(self, name, data_b64)._cai_payload()[0]

    def cai_unstage_file(self, file_id):
        self.ensure_one()
        self._cai_check_owner()
        staged = self.env['community.ai.session.file'].sudo().search(
            [('id', '=', int(file_id)), ('session_id', '=', self.id), ('exchange_id', '=', False)])
        staged.attachment_id.unlink()
        staged.unlink()
        return True

    def cai_send(self, text):
        self.ensure_one()
        return ConversationRunner(self.env, self).run(text)

    def cai_retry(self):
        self.ensure_one()
        return ConversationRunner(self.env, self).run(None)

    def cai_messages(self):
        self.ensure_one()
        if self.user_id != self.env.user:
            self.check_access('read')
        return self._cai_payload(with_exchanges=True)

    def cai_close(self):
        self.ensure_one()
        self._cai_check_owner()
        self.status = 'closed'
        return True

    def cai_set_context(self, context_model=None, context_res_id=None):
        self.ensure_one()
        self._cai_check_owner()
        model, res_id = self._cai_sanitize_context(context_model, context_res_id)
        self.write({'context_model': model or False, 'context_res_id': res_id})
        return self._cai_payload()

    @api.model
    def cai_recent(self):
        sessions = self.search([('user_id', '=', self.env.user.id), ('status', '=', 'open')],
                               limit=RECENT_SESSIONS)
        return [s._cai_payload() for s in sessions]

    def _cai_payload(self, with_exchanges=False):
        self.ensure_one()
        assistant = self.assistant_id.sudo()
        payload = {
            'id': self.id,
            'subject': self.subject,
            'status': self.status,
            'assistant': assistant._cai_card(),
            'context': {'model': self.context_model, 'res_id': self.context_res_id,
                        'label': self.context_label} if self.context_model else None,
            'last_activity': fields.Datetime.to_string(self.last_activity),
            'preset': self.preset_id.sudo()._cai_card() if self.preset_id else False,
            'staged_files': self.file_ids.sudo().filtered(lambda f: not f.exchange_id)._cai_payload(),
        }
        if with_exchanges:
            exchanges = self.exchange_ids.sorted('id')
            payload['exchanges'] = [ex._cai_payload() for ex in exchanges]
        return payload

    def _cai_context_thread(self):
        """The context record if the user can read it and it has a chatter, else ``None``."""
        self.ensure_one()
        if not self.context_model or self.context_model not in self.env or not self.context_res_id:
            return None
        record = self.env[self.context_model].browse(self.context_res_id).exists()
        if not record or not record.has_access('read') or not hasattr(record, 'message_post'):
            return None
        return record

    def action_cai_open_messages(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'res_model': 'community.ai.exchange', 'name': self.subject,
            'view_mode': 'list,form', 'domain': [('session_id', '=', self.id)],
        }
