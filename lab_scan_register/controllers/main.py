# -*- coding: utf-8 -*-
"""The plate's files for ChituBox, as one zip - and the sliced file coming back, in pieces.

The zip is streamed from a temporary file in chunks: a plate of eight patients' scans
is easily a few hundred megabytes, and building that in memory - twice, once for the
archive and once for the response - is how a worker gets killed.

The sliced file goes the other way. Odoo refuses any request over 128 MiB (unless
web.max_file_upload_size says otherwise, database-wide), a proxy in front of it often
less (Cloudflare: 100 MB), and a ChituBox .ctb for a full plate is
50-300 MB. So the browser sends it in 8 MiB pieces; the pieces are put together on
disk and become one attachment, which belongs to nobody until a plate takes it.
"""
import logging
import os
import re
import time
import uuid

from odoo import _, http
from odoo.http import request
from odoo.http.stream import content_disposition
from odoo.tools import config

_logger = logging.getLogger(__name__)

SLICE_CHUNK = 8 * 1024 * 1024            # what the browser sends at a time
SLICE_MAX_MB = 1024                      # the largest sliced file taken, unless set otherwise
UPLOAD_ID = re.compile(r'^[0-9a-f]{32}$')
STALE_UPLOAD_S = 24 * 3600               # a half-sent file older than this is swept away

from ..models.print_order import ZIP_CHUNK


class PrintOrderFiles(http.Controller):

    @http.route('/scan/print-order/<int:order_id>/files.zip', type='http', auth='user')
    def print_order_files(self, order_id, **kwargs):
        order = request.env['lab.scan.print.order'].browse(order_id).exists()
        if not order:
            raise request.not_found()
        order.check_access('read')
        archive = order._zip_files()
        size = os.fstat(archive.fileno()).st_size

        def chunks():
            try:
                while True:
                    block = archive.read(ZIP_CHUNK)
                    if not block:
                        break
                    yield block
            finally:
                archive.close()

        filename = '%s.zip' % order.name.replace('/', '-')
        return request.make_response(chunks(), headers=[
            ('Content-Type', 'application/zip'),
            ('Content-Length', str(size)),
            ('Content-Disposition', content_disposition(filename)),
        ])


class SliceUpload(http.Controller):

    @staticmethod
    def _folder():
        folder = os.path.join(config['data_dir'], 'slice_uploads', request.db)
        os.makedirs(folder, exist_ok=True)
        return folder

    @staticmethod
    def _sweep(folder):
        """Pieces of uploads given up on (a closed tab, a dropped connection)."""
        cutoff = time.time() - STALE_UPLOAD_S
        for name in os.listdir(folder):
            path = os.path.join(folder, name)
            try:
                if name.endswith('.part') and os.path.getmtime(path) < cutoff:
                    os.unlink(path)
            except OSError:
                pass

    @http.route('/scan/slice/chunk', type='http', auth='user', methods=['POST'],
                max_content_length=SLICE_CHUNK + 2 * 1024 * 1024)
    def slice_chunk(self, upload_id='', offset='0', total='0', name='', **kwargs):
        """One piece of a sliced file. Answers how much has arrived; the last piece
        answers the attachment the file became. Errors are answered, not raised, so
        the widget can say them."""
        # whoever may make a plate may bring its file
        request.env['lab.scan.print.order'].check_access('write')
        try:
            offset, total = int(offset), int(total)
        except ValueError:
            return request.make_json_response({'error': _("The upload was garbled; try again.")})
        limit_mb = request.env['ir.config_parameter'].sudo().get_int(
            'lab_scan_register.slice_max_mb', SLICE_MAX_MB) or SLICE_MAX_MB
        if total <= 0 or total > limit_mb * 1024 * 1024:
            return request.make_json_response({'error': _(
                "A sliced file of up to %s MB is taken; this one is %s MB.", limit_mb, round(total / 1048576))})
        folder = self._folder()
        if not upload_id:
            upload_id = uuid.uuid4().hex
            self._sweep(folder)
        elif not UPLOAD_ID.match(upload_id):
            return request.make_json_response({'error': _("The upload was garbled; try again.")})
        path = os.path.join(folder, '%d-%s.part' % (request.env.uid, upload_id))
        have = os.path.getsize(path) if os.path.exists(path) else 0
        if offset != have:
            # a piece lost or sent twice: carry on from what is really here
            return request.make_json_response({'upload_id': upload_id, 'expected': have})
        piece = request.httprequest.files.get('chunk')
        data = piece.read() if piece else b''
        if not data or offset + len(data) > total:
            return request.make_json_response({'error': _("The upload was garbled; try again.")})
        with open(path, 'ab') as handle:
            handle.write(data)
        received = offset + len(data)
        if received < total:
            return request.make_json_response({'upload_id': upload_id, 'received': received})
        # the last piece: the whole file becomes an attachment of nobody yet - the
        # plate (or the New Print Order dialog) takes it when it is saved
        try:
            with open(path, 'rb') as handle:
                raw = handle.read()
        finally:
            os.unlink(path)
        attachment = request.env['ir.attachment'].create({
            'name': (name or 'plate.ctb').strip()[:255], 'raw': raw, 'mimetype': 'application/octet-stream'})
        _logger.info("Sliced file %s received: %d bytes as attachment %d", attachment.name, len(raw), attachment.id)
        return request.make_json_response({'done': True, 'id': attachment.id, 'name': attachment.name,
                                           'size': attachment.file_size})

