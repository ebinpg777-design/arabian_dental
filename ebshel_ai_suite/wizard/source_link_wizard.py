# Part of Ebshel AI Suite. See LICENSE file for full copyright and licensing details.
from odoo import fields, models
from odoo.exceptions import UserError


class CommunityAISourceLinkWizard(models.TransientModel):
    """Add several web pages as knowledge sources at once (one URL per line)."""
    _name = 'community.ai.source.link.wizard'
    _description = 'Add Knowledge Links'

    urls = fields.Text('Links', required=True, help='One http(s) URL per line.')
    index_method = fields.Selection([('keyword', 'Keyword (BM25)'), ('embedding', 'Semantic (embeddings)')],
                                    default='keyword', required=True)
    assistant_ids = fields.Many2many('community.ai.assistant', string='Assistants')

    def action_cai_add(self):
        self.ensure_one()
        urls = []
        for line in (self.urls or '').splitlines():
            url = line.strip()
            if not url:
                continue
            if not url.startswith(('http://', 'https://')):
                raise UserError(self.env._('Not a web address: %s', url[:200]))
            if url not in urls:
                urls.append(url)
        if not urls:
            raise UserError(self.env._('Please enter at least one link.'))
        if len(urls) > 50:
            raise UserError(self.env._('Please add at most 50 links at a time.'))
        sources = self.env['community.ai.source'].create([{
            'name': url.split('://', 1)[1][:120], 'source_kind': 'url', 'url': url,
            'index_method': self.index_method, 'assistant_ids': [(6, 0, self.assistant_ids.ids)],
        } for url in urls])
        for source in sources:
            source._cai_enqueue_indexing()
        return {'type': 'ir.actions.act_window_close'}
