# Part of Ebshel AI Suite. See LICENSE file for full copyright and licensing details.
import logging

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

from ..services.engines.errors import EngineError
from ..services.retrieval_engine import RetrievalEngine

_logger = logging.getLogger(__name__)
INLINE_INDEX_LIMIT = 200_000   # characters indexed synchronously; larger sources go to the job queue


class CommunityAISource(models.Model):
    """A piece of knowledge an assistant may consult (RAG)."""
    _name = 'community.ai.source'
    _description = 'AI Knowledge Source'
    _order = 'sequence, name'

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    source_kind = fields.Selection(
        [('text', 'Text'), ('file', 'File (PDF, text, HTML, Markdown)'), ('url', 'Web page / URL'),
         ('record', 'Odoo record')],
        string='Type', required=True, default='text')
    content_text = fields.Text('Content')
    file_data = fields.Binary('File', attachment=True)
    file_name = fields.Char('File Name')
    url = fields.Char('URL')
    record_ref = fields.Reference(selection='_cai_reference_models', string='Record')
    index_method = fields.Selection([('keyword', 'Keyword (BM25)'), ('embedding', 'Semantic (embeddings)')],
                                    default='keyword', required=True,
                                    help='Semantic indexing needs a connection with an embedding model.')
    embedding_connection_id = fields.Many2one('community.ai.connection', string='Embedding Connection',
                                              help='Defaults to the company connection.')
    state = fields.Selection([('draft', 'Not indexed'), ('queued', 'Queued'), ('ready', 'Ready'),
                              ('error', 'Error')], default='draft', readonly=True, copy=False)
    index_error = fields.Char(readonly=True, copy=False)
    last_indexed_at = fields.Datetime(readonly=True, copy=False)
    chunk_ids = fields.One2many('community.ai.source.chunk', 'source_id', string='Chunks', copy=False)
    chunk_count = fields.Integer(compute='_compute_chunk_count')
    assistant_ids = fields.Many2many('community.ai.assistant', 'community_ai_assistant_source_rel',
                                     'source_id', 'assistant_id', string='Assistants')
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company)

    @api.model
    def _cai_reference_models(self):
        models_ = self.env['ir.model'].sudo().search([
            ('transient', '=', False), ('model', 'not like', 'ir.%'), ('model', 'not like', 'community.ai.%'),
            ('model', 'not in', ['res.users', 'res.groups', 'res.users.apikeys', 'res.device', 'bus.bus']),
        ])
        return [(m.model, m.name) for m in models_]

    def _compute_chunk_count(self):
        counts = dict(self.env['community.ai.source.chunk']._read_group(
            [('source_id', 'in', self.ids)], ['source_id'], ['__count']))
        for source in self:
            source.chunk_count = counts.get(source, 0)

    @api.constrains('source_kind', 'url', 'record_ref')
    def _check_kind(self):
        for source in self:
            if source.source_kind == 'url' and source.url and not source.url.startswith(('http://', 'https://')):
                raise ValidationError(self.env._('The URL must start with http:// or https://.'))

    def write(self, vals):
        content_keys = {'content_text', 'file_data', 'url', 'record_ref', 'source_kind', 'index_method',
                        'embedding_connection_id'}
        if content_keys & set(vals) and 'state' not in vals:
            vals = dict(vals, state='draft')
        return super().write(vals)

    # ------------------------------------------------------------------
    def action_cai_index(self):
        """Index now; very large sources are handed to the background queue."""
        for source in self:
            size = len(source.content_text or '') if source.source_kind == 'text' else 0
            if source.source_kind in ('url', 'file') or size > INLINE_INDEX_LIMIT:
                if source.source_kind == 'file' and source.file_data and len(source.file_data) < 400_000:
                    source._cai_index_now()
                    continue
                source._cai_enqueue_indexing()
            else:
                source._cai_index_now()
        return True

    def _cai_enqueue_indexing(self):
        self.ensure_one()
        self.env['community.ai.job'].sudo().create({
            'job_kind': 'source_index', 'source_id': self.id, 'user_id': self.env.user.id,
            'res_model': self._name, 'res_id': self.id,
        })
        self.state = 'queued'

    def _cai_index_now(self):
        self.ensure_one()
        try:
            with self.env.cr.savepoint():
                count = RetrievalEngine(self.env).index_source(self)
        except (UserError, EngineError) as exc:
            if isinstance(exc, EngineError):
                message = exc.public_message_for(self.env)
            else:
                message = str(exc.args[0] if exc.args else exc)
            self.sudo().write({'state': 'error', 'index_error': message[:250]})
            return False
        self.sudo().write({'state': 'ready', 'index_error': False, 'last_indexed_at': fields.Datetime.now()})
        _logger.info('Indexed AI source %s into %s chunks', self.id, count)
        return True


class CommunityAISourceChunk(models.Model):
    _name = 'community.ai.source.chunk'
    _description = 'AI Knowledge Chunk'
    _order = 'source_id, sequence'

    source_id = fields.Many2one('community.ai.source', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer()
    content = fields.Text(required=True)
    token_estimate = fields.Integer()
    vector_data = fields.Text('Vector (JSON)')
    vector_model = fields.Char()
