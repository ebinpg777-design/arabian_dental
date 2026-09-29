# Part of Ebshel AI Suite. See LICENSE file for full copyright and licensing details.
"""Knowledge ingestion and retrieval (RAG).

Pipeline::

    source → extractor → normalization → chunking → [embedding] → index → search

Two index backends are provided and neither needs a PostgreSQL extension:

``keyword``
    BM25-style lexical scoring computed in Python over the chunks of the
    assistant's sources. Works everywhere, no engine call.
``embedding``
    Vectors produced by the connection's embedding model are stored as JSON
    on each chunk and compared with cosine similarity.

Other backends (pgvector, an external vector database, ...) can be plugged
with :func:`register_index_backend` and a ``selection_add`` on
``community.ai.source.index_method``.
"""
from __future__ import annotations

import base64
import io
import ipaddress
import json
import logging
import math
import re
import socket
from collections import Counter
from dataclasses import dataclass
from urllib.parse import urlparse

from odoo.exceptions import UserError
from odoo.tools import html2plaintext
from odoo.tools.mimetypes import guess_mimetype
from odoo.tools.translate import LazyTranslate

from .context_builder import RecordContextBuilder
from .engines.errors import EngineError
from .guardrails import redact_secrets
from .llm_gateway import LLMGateway

_logger = logging.getLogger(__name__)
_lt = LazyTranslate(__name__)

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

CHUNK_SIZE = 900
CHUNK_OVERLAP = 150
MAX_SOURCE_CHARS = 2_000_000
MAX_DOWNLOAD_BYTES = 5 * 1024 * 1024
MAX_CANDIDATE_CHUNKS = 5000
_WORD = re.compile(r'\w{2,}', re.U)
_STOPWORD_TEXT = """
a an and are as at be but by can could do does did for from has have how i in is it its me my of on or our
that the their them they this to was we were what when where which who why will with would you your about
please tell show give de la le les des du et en un une est pour
que qui dans sur par au aux der die das und ist mit von zu el los las y en por con para
"""
STOPWORDS = frozenset(_STOPWORD_TEXT.split())


class ExtractionError(UserError):
    """User-presentable ingestion failure."""


@dataclass
class RetrievedPassage:
    source_id: int
    source_name: str
    chunk_id: int
    text: str
    score: float

    def as_citation(self) -> dict:
        return {'source_id': self.source_id, 'title': self.source_name, 'chunk_id': self.chunk_id,
                'excerpt': self.text[:240]}


# ---------------------------------------------------------------------------
# Extraction and chunking
# ---------------------------------------------------------------------------

def normalize_text(text: str) -> str:
    text = (text or '').replace('\r\n', '\n').replace('\r', '\n').replace('\x00', '')
    text = re.sub(r'[ \t ]+', ' ', text)
    text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text)
    return text.strip()[:MAX_SOURCE_CHARS]


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split on paragraph boundaries into chunks of roughly ``size`` chars."""
    paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
    chunks: list[str] = []
    current = ''
    for paragraph in paragraphs:
        while len(paragraph) > size:
            cut = paragraph.rfind(' ', 0, size)
            cut = cut if cut > size // 2 else size
            pieces = paragraph[:cut]
            paragraph = paragraph[max(0, cut - overlap):].strip()
            if current:
                chunks.append(current)
                current = ''
            chunks.append(pieces.strip())
        if len(current) + len(paragraph) + 2 <= size:
            current = f'{current}\n\n{paragraph}' if current else paragraph
        else:
            if current:
                chunks.append(current)
            tail = current[-overlap:] if current and overlap else ''
            current = f'{tail} {paragraph}'.strip() if tail else paragraph
    if current:
        chunks.append(current)
    return chunks


def _stem(word: str) -> str:
    """Very light, language-agnostic plural folding (policies → policy, covers → cover)."""
    if len(word) > 4 and word.endswith('ies'):
        return word[:-3] + 'y'
    if len(word) > 3 and word.endswith('s') and not word.endswith('ss'):
        return word[:-1]
    return word


def tokenize(text: str) -> list[str]:
    return [_stem(w) for w in _WORD.findall((text or '').lower()) if w not in STOPWORDS]


def bm25_scores(texts: list[str], terms: list[str], *, k1: float = 1.4, b: float = 0.75,
                min_coverage: float = 0.34) -> list[float]:
    """Okapi BM25 score of each text for the query ``terms`` (0 when not relevant enough)."""
    if not texts or not terms:
        return [0.0] * len(texts)
    docs = [Counter(tokenize(text)) for text in texts]
    lengths = [sum(doc.values()) or 1 for doc in docs]
    average = sum(lengths) / len(lengths)
    idf = {}
    for term in set(terms):
        frequency = sum(1 for doc in docs if term in doc)
        idf[term] = math.log(1 + (len(docs) - frequency + 0.5) / (frequency + 0.5))
    needed = max(1, round(len(terms) * min_coverage)) if len(terms) > 1 else 1
    scores = []
    for doc, length in zip(docs, lengths, strict=True):
        if sum(1 for term in terms if term in doc) < needed:
            scores.append(0.0)
            continue
        score = 0.0
        for term in terms:
            tf = doc.get(term, 0)
            if tf:
                score += idf[term] * tf * (k1 + 1) / (tf + k1 * (1 - b + b * length / average))
        scores.append(score)
    return scores


def select_passages(documents: list[tuple[str, str]], query: str, *, budget: int = 12000,
                    limit: int = 6) -> list[tuple[str, str]]:
    """Pick what to show from ad-hoc documents (``(label, text)``): everything when it
    fits in ``budget`` characters, otherwise the most relevant chunks."""
    documents = [(label, text) for label, text in documents if text]
    if sum(len(text) for _label, text in documents) <= budget:
        return documents
    chunks = [(label, chunk) for label, text in documents for chunk in chunk_text(text)]
    terms = list(dict.fromkeys(tokenize(query)))
    scores = bm25_scores([chunk for _label, chunk in chunks], terms, min_coverage=0.0)
    ranked = sorted(zip(scores, range(len(chunks)), strict=True), reverse=True)
    picked = [chunks[index] for score, index in ranked[:limit] if score > 0] or chunks[:2]
    return picked


def _is_public_host(hostname: str) -> bool:
    try:
        infos = socket.getaddrinfo(hostname, None)
    except (socket.gaierror, UnicodeError):
        return False
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if (address.is_private or address.is_loopback or address.is_link_local or address.is_reserved
                or address.is_multicast or address.is_unspecified):
            return False
    return True


def fetch_url_text(env, url: str) -> str:
    """Download a web page / PDF / text file with SSRF protection."""
    if requests is None:  # pragma: no cover
        raise ExtractionError(env._('The python "requests" library is required to import URLs.'))
    parsed = urlparse(url or '')
    if parsed.scheme not in ('http', 'https') or not parsed.hostname:
        raise ExtractionError(env._('Only http(s) URLs can be imported.'))
    allow_private = env['ir.config_parameter'].sudo().get_param('ebshel_ai_suite.allow_private_urls') == 'True'
    if not allow_private and not _is_public_host(parsed.hostname):
        raise ExtractionError(env._('This URL points to a private or local network address and was blocked.'))
    try:
        response = requests.get(url, timeout=15, stream=True, allow_redirects=False,
                                headers={'User-Agent': 'CommunityAISuite/1.0 (+knowledge import)'})
        if response.is_redirect:
            location = response.headers.get('Location', '')
            response.close()
            raise ExtractionError(env._('The URL redirects to %s; please import the final URL instead.', location[:200]))
        response.raise_for_status()
        content = b''
        for block in response.iter_content(65536):
            content += block
            if len(content) > MAX_DOWNLOAD_BYTES:
                raise ExtractionError(env._('The document is larger than 5 MB.'))
        content_type = (response.headers.get('Content-Type') or '').split(';')[0].strip().lower()
    except requests.exceptions.RequestException as exc:
        raise ExtractionError(env._('Could not download the URL: %s', redact_secrets(str(exc))[:200])) from exc
    return extract_bytes(env, content, content_type, url)


def extract_bytes(env, content: bytes, mimetype: str, name: str = '') -> str:
    mimetype = (mimetype or '').lower()
    lowered = (name or '').lower()
    if mimetype == 'application/pdf' or lowered.endswith('.pdf'):
        return _extract_pdf(env, content)
    if mimetype in ('text/html', 'application/xhtml+xml') or lowered.endswith(('.html', '.htm')):
        return html2plaintext(content.decode('utf-8', errors='replace'))
    if mimetype.startswith('text/') or mimetype in ('application/json', 'application/xml', '') or \
            lowered.endswith(('.txt', '.md', '.csv', '.json', '.xml', '.rst')):
        return content.decode('utf-8', errors='replace')
    raise ExtractionError(env._('Unsupported document type: %s', mimetype or name))


def _extract_pdf(env, content: bytes) -> str:
    try:
        from odoo.tools.pdf import PdfFileReader  # noqa: PLC0415 - optional, only needed for PDFs
    except ImportError as exc:  # pragma: no cover
        raise ExtractionError(env._('PDF support is not available on this server.')) from exc
    try:
        reader = PdfFileReader(io.BytesIO(content), strict=False)
        pages = [page.extract_text() or '' for page in reader.pages]
    except Exception as exc:  # noqa: BLE001 - PDF libraries raise many types
        raise ExtractionError(env._('The PDF could not be read.')) from exc
    return '\n\n'.join(pages)


def extract_source_text(env, source) -> str:
    """Return the raw text of a ``community.ai.source`` record."""
    kind = source.source_kind
    if kind == 'text':
        text = source.content_text or ''
        return html2plaintext(text) if '<' in text and '>' in text else text
    if kind == 'file':
        data = source.sudo().with_context(bin_size=False).file_data
        if not data:
            raise ExtractionError(env._('Please upload a file.'))
        content = base64.b64decode(data)
        return extract_bytes(env, content, guess_mimetype(content, default=''), source.file_name or '')
    if kind == 'url':
        return fetch_url_text(env, source.url)
    if kind == 'record':
        record = source.record_ref
        if not record:
            raise ExtractionError(env._('Please select a record.'))
        builder = RecordContextBuilder(env, payload_limit=60000)
        context = builder.build_record_context(record._name, record.id)
        if not context:
            raise ExtractionError(env._('The selected record cannot be read.'))
        lines = [context['name']]
        for label, value in context['fields'].items():
            if label == '__truncated__':
                continue
            if isinstance(value, list):
                value = ', '.join(map(str, value))
            lines.append(f'{label.split(" (")[0]}: {value}')
        return '\n\n'.join(lines)
    raise ExtractionError(env._('Unknown source type.'))


# ---------------------------------------------------------------------------
# Index backends
# ---------------------------------------------------------------------------

_INDEX_BACKENDS: dict[str, type[IndexBackend]] = {}


def register_index_backend(key: str):
    def decorator(cls):
        cls.key = key
        _INDEX_BACKENDS[key] = cls
        return cls
    return decorator


class IndexBackend:
    key = ''

    def __init__(self, env):
        self.env = env

    def prepare_chunks(self, source, texts: list[str]) -> list[dict]:
        """Return the extra values stored on each chunk (e.g. vectors)."""
        return [{} for _text in texts]

    def search(self, sources, query: str, limit: int) -> list[RetrievedPassage]:
        raise NotImplementedError

    def _candidate_rows(self, sources, extra_fields=()):
        return self.env['community.ai.source.chunk'].sudo().search_read(
            [('source_id', 'in', sources.ids)], ['source_id', 'content', *extra_fields],
            limit=MAX_CANDIDATE_CHUNKS, order='source_id, sequence')


@register_index_backend('keyword')
class KeywordIndexBackend(IndexBackend):
    """Okapi BM25 over the candidate chunks."""
    #: share of the distinct query terms a chunk must contain to be relevant
    min_coverage = 0.34

    def search(self, sources, query, limit):
        terms = list(dict.fromkeys(tokenize(query)))
        if not terms or not sources:
            return []
        rows = self._candidate_rows(sources)
        if not rows:
            return []
        names = {s.id: s.name for s in sources}
        scored = []
        for row, score in zip(rows, bm25_scores([row['content'] for row in rows], terms,
                                                min_coverage=self.min_coverage), strict=True):
            if score > 0:
                source_id = row['source_id'][0]
                scored.append(RetrievedPassage(source_id, names.get(source_id, ''), row['id'], row['content'], score))
        scored.sort(key=lambda p: p.score, reverse=True)
        return scored[:limit]


@register_index_backend('embedding')
class EmbeddingIndexBackend(IndexBackend):
    """Cosine similarity over JSON-stored vectors (no database extension)."""
    min_score = 0.15

    def _gateway(self):
        return LLMGateway(self.env)

    def prepare_chunks(self, source, texts):
        connection = source.embedding_connection_id
        vectors = []
        for start in range(0, len(texts), 64):
            vectors.extend(self._gateway().embed(texts[start:start + 64], connection=connection or None))
        model = (connection or self._gateway().resolve_connection()).sudo().embedding_model or ''
        return [{'vector_data': json.dumps(vector), 'vector_model': model} for vector in vectors]

    def search(self, sources, query, limit):
        by_connection: dict = {}
        for source in sources:
            by_connection.setdefault(source.embedding_connection_id, self.env['community.ai.source'])
            by_connection[source.embedding_connection_id] |= source
        results = []
        for connection, group in by_connection.items():
            try:
                query_vector = self._gateway().embed([query], connection=connection or None)[0]
            except EngineError as exc:
                _logger.info('Embedding search unavailable (%s), falling back to keywords', exc.code)
                results.extend(KeywordIndexBackend(self.env).search(group, query, limit))
                continue
            names = {s.id: s.name for s in group}
            for row in self._candidate_rows(group, ('vector_data',)):
                try:
                    vector = json.loads(row['vector_data'] or '[]')
                except ValueError:
                    continue
                score = _cosine(query_vector, vector)
                if score >= self.min_score:
                    source_id = row['source_id'][0]
                    results.append(RetrievedPassage(source_id, names.get(source_id, ''), row['id'],
                                                    row['content'], score))
        results.sort(key=lambda p: p.score, reverse=True)
        return results[:limit]


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def get_index_backend(env, key: str) -> IndexBackend:
    backend_class = _INDEX_BACKENDS.get(key) or KeywordIndexBackend
    return backend_class(env)


# ---------------------------------------------------------------------------
# Facade
# ---------------------------------------------------------------------------

class RetrievalEngine:

    def __init__(self, env):
        self.env = env

    def index_source(self, source) -> int:
        """(Re)build the chunks of ``source``. Returns the number of chunks."""
        source = source.sudo()
        text = normalize_text(extract_source_text(self.env, source))
        if not text:
            raise ExtractionError(self.env._('The source does not contain any text.'))
        texts = chunk_text(text)
        backend = get_index_backend(self.env, source.index_method)
        extras = backend.prepare_chunks(source, texts)
        source.chunk_ids.unlink()
        self.env['community.ai.source.chunk'].sudo().create([
            {'source_id': source.id, 'sequence': index, 'content': chunk,
             'token_estimate': len(chunk.split()), **extra}
            for index, (chunk, extra) in enumerate(zip(texts, extras))
        ])
        return len(texts)

    def usable_sources(self, assistant):
        sources = assistant.sudo().knowledge_ids.filtered(
            lambda s: s.active and s.state == 'ready'
            and (not s.company_id or s.company_id in self.env.user.company_ids))
        # a record snapshot must stay visible only to users who can still read the record
        return sources.filtered(
            lambda s: s.source_kind != 'record' or (s.record_ref and s.record_ref.with_env(self.env).has_access('read')))

    def retrieve(self, assistant, query: str, limit: int = 4) -> list[RetrievedPassage]:
        sources = self.usable_sources(assistant)
        if not sources or not (query or '').strip():
            return []
        results: list[RetrievedPassage] = []
        for method in set(sources.mapped('index_method')):
            group = sources.filtered(lambda s, m=method: s.index_method == m)
            hits = get_index_backend(self.env, method).search(group, query, limit)
            top = max((hit.score for hit in hits), default=0) or 1
            for hit in hits:
                hit.score = hit.score / top
            results.extend(hits)
        results.sort(key=lambda p: p.score, reverse=True)
        return results[:limit]
