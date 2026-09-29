# Part of Ebshel AI Suite. See LICENSE file for full copyright and licensing details.
import base64

from odoo import fields, models
from odoo.exceptions import UserError
from odoo.tools.mimetypes import guess_mimetype

from ..services.retrieval_engine import ExtractionError, extract_bytes, normalize_text

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_FILES_PER_MESSAGE = 5
IMAGE_TYPES = ('image/png', 'image/jpeg', 'image/webp', 'image/gif')


class CommunityAISessionFile(models.Model):
    """A file attached to a conversation. Text files act as temporary knowledge
    for that conversation only; images are shown to vision-capable engines."""
    _name = 'community.ai.session.file'
    _description = 'AI Conversation File'
    _order = 'id'

    session_id = fields.Many2one('community.ai.session', required=True, ondelete='cascade', index=True)
    exchange_id = fields.Many2one('community.ai.exchange', ondelete='cascade', index=True,
                                  help='Message the file was sent with (empty while it is staged).')
    user_id = fields.Many2one(related='session_id.user_id', store=True, index=True)
    company_id = fields.Many2one(related='session_id.company_id', store=True)
    name = fields.Char(required=True)
    mimetype = fields.Char()
    file_size = fields.Integer()
    is_image = fields.Boolean()
    text_content = fields.Text()
    attachment_id = fields.Many2one('ir.attachment', ondelete='set null')

    def _cai_payload(self):
        return [{'id': f.id, 'name': f.name, 'mimetype': f.mimetype, 'is_image': f.is_image,
                 'url': f'/web/image/{f.attachment_id.id}' if f.is_image and f.attachment_id else False}
                for f in self]

    def _cai_image_bytes(self):
        self.ensure_one()
        return self.attachment_id.sudo().raw or b''

    def _cai_create_from_upload(self, session, name, data_b64):
        """Validate, extract and store an uploaded file (called by the session owner)."""
        try:
            content = base64.b64decode(data_b64 or '', validate=True)
        except (ValueError, TypeError) as exc:
            raise UserError(self.env._('The file could not be read.')) from exc
        if not content:
            raise UserError(self.env._('The file is empty.'))
        if len(content) > MAX_FILE_BYTES:
            raise UserError(self.env._('Files attached to a conversation are limited to 10 MB.'))
        staged = self.sudo().search_count([('session_id', '=', session.id), ('exchange_id', '=', False)])
        if staged >= MAX_FILES_PER_MESSAGE:
            raise UserError(self.env._('You can attach up to %s files per message.', MAX_FILES_PER_MESSAGE))
        mimetype = guess_mimetype(content, default='application/octet-stream')
        is_image = mimetype in IMAGE_TYPES
        text = ''
        if not is_image:
            try:
                text = normalize_text(extract_bytes(self.env, content, mimetype, name))
            except ExtractionError as exc:
                raise UserError(exc.args[0]) from exc
            if not text:
                raise UserError(self.env._('No text could be extracted from %s.', name))
        record = self.sudo().create({
            'session_id': session.id, 'name': (name or 'file')[:200], 'mimetype': mimetype,
            'file_size': len(content), 'is_image': is_image, 'text_content': text,
        })
        record.attachment_id = self.env['ir.attachment'].sudo().create({
            'name': record.name, 'raw': content, 'mimetype': mimetype,
            'res_model': self._name, 'res_id': record.id,
        })
        return record
