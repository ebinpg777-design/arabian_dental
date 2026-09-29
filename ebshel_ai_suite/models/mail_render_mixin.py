# Part of Ebshel AI Suite. See LICENSE file for full copyright and licensing details.
from odoo import models

from ..services.template_prompts import fill_prompt_blocks, has_prompt_blocks

AI_FIELDS = ('body_html', 'body')


class MailRenderMixin(models.AbstractModel):
    _inherit = 'mail.render.mixin'

    def _render_field(self, field, res_ids, *args, **kwargs):
        rendered = super()._render_field(field, res_ids, *args, **kwargs)
        if self.env.context.get('cai_skip_prompt_blocks') or field not in AI_FIELDS \
                or not any(has_prompt_blocks(value) for value in rendered.values()):
            return rendered
        model = self.render_model
        return {
            res_id: fill_prompt_blocks(self.env, value, model, res_id) if has_prompt_blocks(value) else value
            for res_id, value in rendered.items()
        }


class MailTemplate(models.Model):
    _inherit = 'mail.template'

    def _check_can_be_rendered(self, fnames=None, render_options=None):
        # the syntax check on save renders a sample record: never spend AI calls on it
        return super(MailTemplate, self.with_context(cai_skip_prompt_blocks=True))._check_can_be_rendered(
            fnames=fnames, render_options=render_options)
