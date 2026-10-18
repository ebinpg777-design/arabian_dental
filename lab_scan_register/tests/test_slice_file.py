# -*- coding: utf-8 -*-
"""The ChituBox file reader, on files built byte by byte so every figure is known."""
import struct

from odoo.tests.common import BaseCase, tagged

from ..models import slice_file as sf

NAME = b'Phrozen Sonic Mini 8K'


def ctb(magic=sf.MAGIC_CTB, version=3, layers=500, print_time=7200, volume=12.5, previews=True,
        params=True, slicer=True):
    """A ctb head laid out the way ChituBox lays it out: header, print parameters,
    slicer info, the machine name, then the two previews."""
    pp_off = sf.HEADER_SIZE if params else 0
    si_off = (pp_off + 60) if slicer else 0
    name_off = (si_off + 76) if slicer else 0
    cursor = max(sf.HEADER_SIZE, (pp_off + 60) if params else 0, (name_off + len(NAME)) if slicer else 0)
    large_off = small_off = 0
    body = b''
    if previews:
        # 2x2 white picture: one run of 4 white pixels (0xFFFF with the run bit, 3 more)
        picture = struct.pack('<HH', 0xFFFF, 0x3003)
        large_off = cursor
        small_off = large_off + 32
        image_off = small_off + 32
        body = struct.pack(sf.PREVIEW_FMT, 2, 2, image_off, len(picture), 0, 0, 0, 0)
        body += struct.pack(sf.PREVIEW_FMT, 2, 2, image_off, len(picture), 0, 0, 0, 0)
        body += picture
    header = struct.pack(sf.HEADER_FMT, magic, version, 68.04, 120.96, 150.0, 0, 0, 25.0, 0.05,
                         2.5, 30.0, 0.5, 6, 1620, 2560, large_off, 0, layers, small_off, print_time, 1,
                         pp_off, 60, 1, 255, 255, 0, si_off, 76)
    out = header
    if params:
        out += struct.pack(sf.PRINT_PARAMS_FMT, 5.0, 65.0, 5.0, 65.0, 150.0, volume, 13.75, 0.41,
                           0.5, 0.5, 6, 0, 0, 0, 0)
    if slicer:
        out += struct.pack(sf.SLICER_INFO_FMT, 0, 0, 0, 0, 0, 0, 0, name_off, len(NAME), 0x0F, 0, 0x30,
                           29_000_000, 1, 0x01060300, 0.0, 0.0, 0, 0, 0, 0)
        out += NAME
    out += b'\0' * (cursor - len(out))
    return out + body


@tagged('post_install', '-at_install')
class TestSliceFile(BaseCase):

    def test_the_layouts_are_the_documented_sizes(self):
        self.assertEqual(struct.calcsize(sf.HEADER_FMT), 112)
        self.assertEqual(struct.calcsize(sf.PRINT_PARAMS_FMT), 60)
        self.assertEqual(struct.calcsize(sf.SLICER_INFO_FMT), 76)
        self.assertEqual(struct.calcsize(sf.PREVIEW_FMT), 32)

    def test_a_ctb_head_gives_every_figure(self):
        r = sf.read(ctb())
        self.assertIsNone(r['error'])
        self.assertFalse(r['truncated'])
        self.assertEqual((r['format'], r['version']), ('ctb', 3))
        self.assertEqual(r['layer_height_mm'], 0.05)
        self.assertEqual(r['layer_count'], 500)
        self.assertEqual(r['print_time_s'], 7200)
        self.assertEqual(sf.hours(r['print_time_s']), 2.0)
        self.assertEqual((r['resolution_x'], r['resolution_y']), (1620, 2560))
        self.assertEqual((r['bed_x_mm'], r['bed_y_mm'], r['bed_z_mm']), (68.04, 120.96, 150.0))
        self.assertEqual(r['total_height_mm'], 25.0)
        self.assertEqual((r['exposure_s'], r['bottom_exposure_s'], r['bottom_layers']), (2.5, 30.0, 6))
        self.assertEqual((r['volume_ml'], r['weight_g'], r['cost']), (12.5, 13.75, 0.41))
        self.assertEqual(r['machine_name'], 'Phrozen Sonic Mini 8K')
        self.assertEqual(r['software_version'], '1.6.3')
        self.assertTrue(r['preview_png'].startswith(b'\x89PNG'))
        self.assertTrue(r['preview_small_png'].startswith(b'\x89PNG'))

    def test_the_preview_is_the_picture_chitubox_drew(self):
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(sf.read(ctb())['preview_png']))
        self.assertEqual(img.size, (2, 2))
        self.assertEqual(img.getpixel((1, 1)), (248, 248, 248), "5-bit white, widened to 8")

    def test_the_run_length_rule(self):
        self.assertEqual(sf.decode_rle15(struct.pack('<H', 0xFFDF), 1), bytes((248, 248, 248)))
        self.assertEqual(sf.decode_rle15(struct.pack('<HH', 0xFFFF, 0x302A), 43), bytes((248, 248, 248)) * 43,
                         "the count is the number of further copies: 42 more")
        self.assertEqual(sf.decode_rle15(struct.pack('<H', 0x0000), 3), b'\0' * 9, "short data is padded black")
        with self.assertRaises(ValueError):
            sf.decode_rle15(struct.pack('<HH', 0xFFFF, 0x302A), 10)

    def test_cbddlp_and_the_v4_magic_read_the_same_header(self):
        for magic, kind in ((sf.MAGIC_CBDDLP, 'cbddlp'), (sf.MAGIC_CTB_V4, 'ctb')):
            r = sf.read(ctb(magic=magic, version=4))
            with self.subTest(kind=kind):
                self.assertEqual((r['format'], r['version'], r['layer_count']), (kind, 4, 500))

    def test_a_version_one_file_has_no_parameter_blocks(self):
        r = sf.read(ctb(version=1, params=False, slicer=False, previews=False))
        self.assertIsNone(r['error'])
        self.assertEqual(r['layer_count'], 500)
        self.assertEqual((r['volume_ml'], r['machine_name']), (0.0, ''))
        self.assertIsNone(r['preview_png'])

    def test_a_head_cut_before_the_previews_keeps_the_figures_and_says_so(self):
        whole = ctb()
        r = sf.read(whole[:sf.HEADER_SIZE + 60 + 76 + len(NAME)])
        self.assertIsNone(r['error'])
        self.assertTrue(r['truncated'])
        self.assertEqual(r['print_time_s'], 7200)
        self.assertEqual(r['machine_name'], 'Phrozen Sonic Mini 8K')
        self.assertIsNone(r['preview_png'])

    def test_an_encrypted_ctb_is_named_not_guessed(self):
        head = struct.pack(sf.ENCRYPTED_HEADER_FMT, sf.MAGIC_CTB_ENCRYPTED, 288, 48, 0, 5, 0, 0, 0, 1, 1, 0, 42, 0)
        r = sf.read(head + b'\0' * 300)
        self.assertEqual((r['format'], r['version']), ('ctb-encrypted', 5))
        self.assertIn('encrypted', r['error'])
        self.assertEqual(r['layer_count'], 0)

    def test_anything_else_is_refused_with_its_magic(self):
        for blob in (b'', b'V3.0' + b'\0' * 200, b'ANYCUBIC' + b'\0' * 200, b'solid cube\n'):
            with self.subTest(head=blob[:8]):
                r = sf.read(blob)
                self.assertTrue(r['error'])
                self.assertIsNone(r['format'])
        short = struct.pack('<I', sf.MAGIC_CTB) + b'\0' * 20
        self.assertIn('shorter', sf.read(short)['error'])

    def test_a_phz_head_reads_the_flat_header(self):
        head = bytearray(sf.PHZ_HEADER_SIZE)
        struct.pack_into('<II', head, 0, sf.MAGIC_PHZ, 2)
        struct.pack_into('<fff', head, 0x08, 0.05, 2.0, 25.0)
        struct.pack_into('<III', head, 0x18, 1620, 2560, 0)
        struct.pack_into('<I', head, 0x28, 640)
        struct.pack_into('<I', head, 0x30, 5400)
        struct.pack_into('<f', head, 0x48, 32.0)
        struct.pack_into('<fff', head, 0x4C, 68.04, 120.96, 150.0)
        struct.pack_into('<fff', head, 0x80, 9.5, 10.45, 0.3)
        struct.pack_into('<II', head, 0x90, sf.PHZ_HEADER_SIZE, len(NAME))
        struct.pack_into('<I', head, 0xBC, 0x01080100)
        r = sf.read(bytes(head) + NAME)
        self.assertIsNone(r['error'])
        self.assertEqual((r['format'], r['version'], r['layer_count'], r['print_time_s']), ('phz', 2, 640, 5400))
        self.assertEqual((r['volume_ml'], r['machine_name'], r['software_version']),
                         (9.5, 'Phrozen Sonic Mini 8K', '1.8.1'))
