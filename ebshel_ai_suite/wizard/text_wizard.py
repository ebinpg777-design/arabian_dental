# Part of Ebshel AI Suite. See LICENSE file for full copyright and licensing details.
import ast

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import html2plaintext, plaintext2html

from ..services.engines.errors import EngineError
from ..services.text_tools import TONES, TextTransformer, operation_selection

WRITABLE_TYPES = ('char', 'text', 'html')


class CommunityAITextWizard(models.TransientModel):
    """Writing assistant dialog. Proposes text; the user decides to apply it."""
    _name = 'community.ai.text.wizard'
    _description = 'AI Writing Assistant'

    operation = fields.Selection(selection=lambda self: operation_selection(), default='improve', required=True)
    source_text = fields.Text('Original Text')
    result_text = fields.Text('Proposal')
    language = fields.Selection(selection='_cai_lang_selection', string='Target Language')
    tone = fields.Selection(selection=lambda self: [(key, str(label)) for key, label in TONES], default='neutral')
    instruction = fields.Char('Extra Guidance')
    assistant_id = fields.Many2one('community.ai.assistant', domain=[('active', '=', True)])
    target_model = fields.Char()
    target_res_id = fields.Integer()
    target_field = fields.Char()
    composer_id = fields.Many2one('mail.compose.message', ondelete='cascade')
    use_record_context = fields.Boolean('Use Document Context', default=True)

    @api.model
    def _cai_lang_selection(self):
        return self.env['res.lang'].get_installed()

    def _cai_context_record(self):
        self.ensure_one()
        model, res_id = self.target_model, self.target_res_id
        if self.composer_id and self.composer_id.model and self.composer_id.res_ids:
            try:
                ids = ast.literal_eval(self.composer_id.res_ids)
            except (ValueError, SyntaxError):
                ids = []
            model, res_id = self.composer_id.model, (ids[0] if isinstance(ids, list) and len(ids) == 1 else 0)
        if not self.use_record_context or model not in self.env or not res_id:
            return None
        record = self.env[model].browse(res_id).exists()
        return record if record and record.has_access('read') else None

    def _cai_reopen(self):
        return {'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': self.id,
                'views': [[False, 'form']], 'target': 'new', 'name': self.env._('AI Writing Assistant')}

    def action_cai_generate(self):
        self.ensure_one()
        if not self.env.user.has_group('ebshel_ai_suite.group_cai_user'):
            raise AccessError(self.env._('You are not allowed to use AI features.'))
        if self.assistant_id:
            self.assistant_id._cai_check_usable()
        try:
            self.result_text = TextTransformer(self.env).transform(
                self.operation, self.source_text or '', language=self.language, tone=self.tone,
                instruction=self.instruction, assistant=self.assistant_id or None,
                context_record=self._cai_context_record())
        except EngineError as exc:
            raise UserError(exc.public_message_for(self.env)) from exc
        return self._cai_reopen()

    def action_cai_use_proposal(self):
        """Replace the original text by the proposal to iterate on it."""
        self.ensure_one()
        self.source_text = self.result_text
        return self._cai_reopen()

    def action_cai_apply(self):
        self.ensure_one()
        if not self.result_text:
            raise UserError(self.env._('Generate a proposal first.'))
        if self.composer_id:
            composer = self.composer_id
            composer.check_access('write')
            composer.body = plaintext2html(self.result_text)
            return {'type': 'ir.actions.act_window', 'res_model': 'mail.compose.message', 'res_id': composer.id,
                    'views': [[False, 'form']], 'target': 'new', 'context': self.env.context}
        if self.target_model in self.env and self.target_res_id and self.target_field:
            record = self.env[self.target_model].browse(self.target_res_id).exists()
            field = record._fields.get(self.target_field)
            if not record or field is None or field.type not in WRITABLE_TYPES:
                raise UserError(self.env._('The target field cannot receive text.'))
            record.check_access('write')
            if not record._has_field_access(field, 'write'):
                raise AccessError(self.env._('You are not allowed to modify this field.'))
            value = plaintext2html(self.result_text) if field.type == 'html' else self.result_text
            record.write({field.name: value})
            self.env['community.ai.audit'].sudo()._cai_log(
                user=self.env.user, target_model=record._name, target_res_id=record.id,
                operation=f'text_{self.operation}', arguments={'field': field.name},
                confirmation_status='approved', execution_status='done',
                result_summary=self.result_text[:300])
            return {'type': 'ir.actions.act_window_close'}
        return {'type': 'ir.actions.act_window_close'}

    @api.model
    def cai_open_for_composer(self, composer):
        wizard = self.create({
            'composer_id': composer.id,
            'source_text': html2plaintext(composer.body or '') if composer.body else '',
            'operation': 'professional' if composer.body and html2plaintext(composer.body).strip() else 'reply',
        })
        return wizard._cai_reopen()
