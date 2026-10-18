# -*- coding: utf-8 -*-
"""The sliced file goes up in pieces: Odoo refuses any request over 128 MiB, a proxy
often less, and a ChituBox .ctb is 50-300 MB. Through the real route, the way the
widget sends it."""
from odoo.tests import HttpCase, tagged

from .test_slice_file import ctb


@tagged('post_install', '-at_install')
class TestSliceUpload(HttpCase):

    def _post(self, chunk=None, **data):
        data['csrf_token'] = self.csrf_token()
        files = {'chunk': ('PLATE.ctb', chunk, 'application/octet-stream')} if chunk is not None else None
        response = self.url_open('/scan/slice/chunk', data=data, files=files)
        response.raise_for_status()
        return response.json()

    def test_a_sliced_file_arrives_in_pieces_and_becomes_one_attachment_of_nobody_yet(self):
        self.authenticate('admin', 'admin')
        data = ctb() + bytes(range(256)) * 12
        pieces = [data[i:i + 1000] for i in range(0, len(data), 1000)]
        first = self._post(chunk=pieces[0], offset=0, total=len(data), name='PLATE.ctb')
        upload_id = first['upload_id']
        self.assertEqual(first['received'], 1000)
        again = self._post(chunk=pieces[0], upload_id=upload_id, offset=0, total=len(data), name='PLATE.ctb')
        self.assertEqual(again['expected'], 1000, "a piece sent twice: carry on from what is really there")
        offset, answer = 1000, None
        for piece in pieces[1:]:
            answer = self._post(chunk=piece, upload_id=upload_id, offset=offset, total=len(data), name='PLATE.ctb')
            offset += len(piece)
        self.assertTrue(answer['done'])
        attachment = self.env['ir.attachment'].browse(answer['id'])
        self.assertEqual((bytes(attachment.raw), attachment.name, answer['size']), (data, 'PLATE.ctb', len(data)))
        self.assertFalse(attachment.res_model or attachment.res_id, "nobody's until a plate takes it")

    def test_a_file_over_the_limit_or_a_garbled_upload_is_refused_with_a_reason(self):
        self.authenticate('admin', 'admin')
        self.env['ir.config_parameter'].sudo().set_int('lab_scan_register.slice_max_mb', 1)
        self.assertIn('error', self._post(chunk=b'x' * 10, offset=0, total=2 * 1024 * 1024, name='big.ctb'))
        self.assertIn('error', self._post(chunk=b'x' * 10, upload_id='../../etc', offset=0, total=10, name='a.ctb'))
        self.assertIn('error', self._post(chunk=b'x' * 20, offset=0, total=10, name='a.ctb'), "more than it said")
