# Part of Ebshel AI Suite. See LICENSE file for full copyright and licensing details.
import base64

from odoo import fields, models
from odoo.exceptions import AccessError, UserError

from ..services.engines.errors import EngineError
from ..services.llm_gateway import LLMGateway


class CommunityAIImageWizard(models.TransientModel):
    """Optional image generation; results are stored as standard attachments."""
    _name = 'community.ai.image.wizard'
    _description = 'AI Image Generation'

    prompt = fields.Text(required=True)
    size = fields.Selection([('1024x1024', 'Square 1024'), ('1536x1024', 'Landscape'), ('1024x1536', 'Portrait')],
                            default='1024x1024', required=True)
    quality = fields.Selection([('standard', 'Standard'), ('hd', 'High')], default='standard', required=True)
    image_format = fields.Selection([('png', 'PNG'), ('webp', 'WebP'), ('jpeg', 'JPEG')], default='png', required=True)
    connection_id = fields.Many2one('community.ai.connection', string='Connection')
    res_model = fields.Char()
    res_id = fields.Integer()
    image = fields.Binary(readonly=True, attachment=False)
    attachment_id = fields.Many2one('ir.attachment', readonly=True)

    def action_cai_generate(self):
        self.ensure_one()
        if not self.env.user.has_group('ebshel_ai_suite.group_cai_user'):
            raise AccessError(self.env._('You are not allowed to use AI features.'))
        try:
            data = LLMGateway(self.env).render_image(
                self.prompt, connection=self.connection_id or None, size=self.size, quality=self.quality,
                image_format=self.image_format)
        except EngineError as exc:
            raise UserError(exc.public_message_for(self.env)) from exc
        values = {'name': f'ai-image.{self.image_format}', 'raw': data,
                  'description': self.prompt[:500]}
        if self.res_model in self.env and self.res_id:
            record = self.env[self.res_model].browse(self.res_id).exists()
            if record:
                record.check_access('write')
                values.update(res_model=record._name, res_id=record.id)
        self.write({'image': base64.b64encode(data), 'attachment_id': self.env['ir.attachment'].create(values).id})
        return {'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': self.id,
                'views': [[False, 'form']], 'target': 'new'}
