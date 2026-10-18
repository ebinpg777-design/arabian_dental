# -*- coding: utf-8 -*-
"""The asset bundles, gzip-compressed for every browser that takes gzip.

Odoo 20 sends /web/assets bundles as stored (nothing in odoo/http or the Binary
controller compresses), and gzip takes the JavaScript to about a fifth. A bundle is
compressed the first time it is asked for in this process and kept, keyed by its
content checksum, so a new deploy (a new bundle) is compressed once and the old one
ages out of the cache. (from Ortho, 2026-10-15)
"""
import gzip
import logging
import threading
from collections import OrderedDict

from odoo import api, http
from odoo.addons.base.models.assetsbundle import ANY_UNIQUE
from odoo.addons.web.controllers.binary import Binary
from odoo.http import request

_logger = logging.getLogger(__name__)

COMPRESSIBLE = ('.js', '.css')
MIN_SIZE = 1024                     # below this, the gzip header costs more than it saves
CACHE_BYTES = 48 * 1024 * 1024      # compressed bytes kept per process
LEVEL = 6
# the plain answer's headers the compressed one keeps: the same caching (immutable,
# long-lived, revalidated by ETag) and the same content sniffing rule
KEPT_HEADERS = ('Cache-Control', 'Expires', 'Last-Modified', 'X-Content-Type-Options')

_cache = OrderedDict()              # (db, attachment id, checksum) -> gzip bytes
_cache_size = 0
_lock = threading.Lock()


def _gzipped(key, read):
    """The compressed bytes for `key`, compressing `read()` the first time."""
    global _cache_size
    with _lock:
        data = _cache.get(key)
        if data is not None:
            _cache.move_to_end(key)
            return data
    raw = read()
    data = gzip.compress(raw, compresslevel=LEVEL, mtime=0)
    with _lock:
        if key not in _cache:
            _cache[key] = data
            _cache_size += len(data)
            while _cache_size > CACHE_BYTES and len(_cache) > 1:
                _old, dropped = _cache.popitem(last=False)
                _cache_size -= len(dropped)
    _logger.info("asset gzip: %s %d -> %d bytes", key[1], len(raw), len(data))
    return data


def _accepts_gzip():
    accepted = request.httprequest.headers.get('Accept-Encoding', '')
    return any(part.split(';')[0].strip().lower() in ('gzip', '*') for part in accepted.split(','))


def _content(attachment):
    """The attachment's bytes: Odoo 20 reads a Binary field back as a BinaryValue."""
    raw = attachment.raw
    if not raw:
        return b''
    return raw if isinstance(raw, bytes) else raw.content


class AssetsGzip(Binary):

    @http.route()
    def content_assets(self, filename=None, unique=ANY_UNIQUE, nocache=False, assets_params=None):
        response = super().content_assets(filename=filename, unique=unique, nocache=nocache,
                                          assets_params=assets_params)
        try:
            return self._gzip_asset(response, filename, unique, assets_params) or response
        except Exception:                                           # noqa: BLE001
            # compression is an improvement, never a reason for a page not to load
            _logger.exception("asset gzip: serving %s uncompressed", filename)
            return response

    def _gzip_asset(self, response, filename, unique, assets_params):
        """The compressed answer, or None to send `response` as it is."""
        if (response.status_code != 200 or request.httprequest.method != 'GET'
                or not filename or not filename.endswith(COMPRESSIBLE)
                or unique in ('debug', 'any', '%', ANY_UNIQUE)
                or response.headers.get('Content-Encoding') or not _accepts_gzip()):
            return None
        env = request.env
        url = env['ir.asset']._get_asset_bundle_url(filename, unique, assets_params or {})
        # the same attachment the core route served (web/controllers/binary.py)
        attachment = env['ir.attachment'].sudo().search([
            ('public', '=', True), ('url', '!=', False), ('url', '=like', url),
            ('res_model', '=', 'ir.ui.view'), ('res_id', '=', 0),
            ('create_uid', '=', api.SUPERUSER_ID),
        ], limit=1)
        if not attachment or (attachment.file_size or 0) < MIN_SIZE:
            return None
        key = (env.cr.dbname, attachment.id, attachment.checksum or '')
        data = _gzipped(key, lambda: _content(attachment))
        compressed = request.make_response(data, headers=[
            ('Content-Type', response.headers.get('Content-Type') or attachment.mimetype),
            ('Content-Encoding', 'gzip'),
            ('Content-Length', str(len(data))),
            ('Vary', 'Accept-Encoding'),
        ])
        for header in KEPT_HEADERS:
            if response.headers.get(header):
                compressed.headers[header] = response.headers[header]
        etag = response.headers.get('ETag')
        if etag:
            # a different encoding of the same content: a weak validator, as proxies do
            compressed.headers['ETag'] = etag if etag.startswith('W/') else 'W/' + etag
        # the plain answer may hold an open file of the filestore; it is not sent
        try:
            response.close()
        except Exception:                                           # noqa: BLE001
            pass
        return compressed
