# -*- coding: utf-8 -*-
"""Reading what ChituBox wrote: the plate's figures out of a sliced file's header.

A .ctb / .cbddlp / .phz is a binary with a small header at the front - bed size,
layer height, layer count, ChituBox's own estimated print time, the resin it
will use, the printer profile it was sliced for - and two run-length-encoded
preview pictures of the plate pointed to from that header. All of it sits in the
first few hundred kilobytes of a file that is otherwise tens or hundreds of
megabytes of layer images: the plate keeps the whole file (uploaded in pieces,
see controllers/main.py) and this module reads only its first megabyte.

Layout per UVtools (ChituboxFile.cs, PHZFile.cs) and catibo (doc/cbddlp-ctb.adoc),
cross-checked. Little-endian throughout. Newer ChituBox builds can write an
ENCRYPTED .ctb (magic 0x12FD0107) whose settings block is AES-encrypted: that is
recognised and reported, never guessed at.

No Odoo in here: `read(data)` takes bytes and returns a plain dict, so it can be
tested on a synthetic header and reused outside the model.
"""
import io
import struct

MAGIC_CBDDLP = 0x12FD0019
MAGIC_CTB = 0x12FD0086
MAGIC_CTB_V4 = 0x12FD0106
MAGIC_CTB_GKTWO = 0xFF220810
MAGIC_CTB_ENCRYPTED = 0x12FD0107
MAGIC_PHZ = 0x9FDA83AE
FORMATS = {MAGIC_CBDDLP: 'cbddlp', MAGIC_CTB: 'ctb', MAGIC_CTB_V4: 'ctb', MAGIC_CTB_GKTWO: 'ctb',
           MAGIC_CTB_ENCRYPTED: 'ctb-encrypted', MAGIC_PHZ: 'phz'}

HEADER_FMT = '<IIfffIIfffffIIIIIIIIIIIIHHIII'      # 112 bytes, every ctb/cbddlp version
PRINT_PARAMS_FMT = '<10f5I'                       # 60 bytes
SLICER_INFO_FMT = '<7fIIBHBIIIffIIII'              # 76 bytes
PREVIEW_FMT = '<8I'                               # 32 bytes
ENCRYPTED_HEADER_FMT = '<IIIIIIIIHHIII'           # 48 bytes; version at offset 16
PHZ_HEADER_SIZE = 216
HEADER_SIZE = struct.calcsize(HEADER_FMT)
# How much of a file holds everything this reads: header, parameters, slicer info,
# the printer's name and both previews. ChituBox writes them all at the front.
HEAD_BYTES = 1024 * 1024
# a preview larger than this is not a preview
MAX_PREVIEW_PIXELS = 1024 * 1024

# What a read hands back when the file says nothing (or cannot be read).
EMPTY = {
    'format': None, 'version': None, 'error': None, 'truncated': False,
    'layer_count': 0, 'layer_height_mm': 0.0, 'total_height_mm': 0.0,
    'exposure_s': 0.0, 'bottom_exposure_s': 0.0, 'bottom_layers': 0,
    'resolution_x': 0, 'resolution_y': 0, 'bed_x_mm': 0.0, 'bed_y_mm': 0.0, 'bed_z_mm': 0.0,
    'print_time_s': 0, 'volume_ml': 0.0, 'weight_g': 0.0, 'cost': 0.0,
    'machine_name': '', 'software_version': '',
    'preview_png': None, 'preview_small_png': None,
}


def read(data):
    """The figures in a sliced file's head, as a dict (see EMPTY for the keys).

    `error` is set when nothing could be read (not a ChituBox file, encrypted, or
    shorter than its own header); `truncated` when the head given stops before a
    block the header points to - the figures read before that point are still
    returned."""
    out = dict(EMPTY)
    if not data or len(data) < 4:
        out['error'] = 'empty'
        return out
    magic = struct.unpack_from('<I', data, 0)[0]
    kind = FORMATS.get(magic)
    if not kind:
        out['error'] = 'not a ChituBox file (magic 0x%08X)' % magic
        return out
    out['format'] = kind
    if kind == 'ctb-encrypted':
        if len(data) >= struct.calcsize(ENCRYPTED_HEADER_FMT):
            out['version'] = struct.unpack_from(ENCRYPTED_HEADER_FMT, data, 0)[4]
        out['error'] = 'encrypted ctb: ChituBox wrote this file with encryption, so its figures cannot be read'
        return out
    if kind == 'phz':
        return _read_phz(data, out)
    return _read_ctb(data, out)


def _read_ctb(data, out):
    if len(data) < HEADER_SIZE:
        out['error'] = 'file shorter than its header'
        return out
    h = struct.unpack_from(HEADER_FMT, data, 0)
    out.update({
        'version': h[1],
        'bed_x_mm': _r(h[2]), 'bed_y_mm': _r(h[3]), 'bed_z_mm': _r(h[4]),
        'total_height_mm': _r(h[7]), 'layer_height_mm': _r(h[8], 4),
        'exposure_s': _r(h[9]), 'bottom_exposure_s': _r(h[10]), 'bottom_layers': h[12],
        'resolution_x': h[13], 'resolution_y': h[14], 'layer_count': h[17], 'print_time_s': h[19],
    })
    params_offset, slicer_offset = h[21], h[27]
    if params_offset:
        p = _unpack(PRINT_PARAMS_FMT, data, params_offset, out)
        if p:
            out.update({'volume_ml': _r(p[5]), 'weight_g': _r(p[6]), 'cost': _r(p[7])})
            if not out['bottom_layers']:
                out['bottom_layers'] = p[10]
    if slicer_offset:
        s = _unpack(SLICER_INFO_FMT, data, slicer_offset, out)
        if s:
            out['machine_name'] = _string(data, s[7], s[8], out)
            out['software_version'] = _version(s[14])
    out['preview_png'] = _preview(data, h[15], out)
    out['preview_small_png'] = _preview(data, h[18], out)
    return out


def _read_phz(data, out):
    """Phrozen's own flavour: one flat header, the same previews."""
    if len(data) < PHZ_HEADER_SIZE:
        out['error'] = 'file shorter than its header'
        return out
    u32 = lambda off: struct.unpack_from('<I', data, off)[0]
    f32 = lambda off: struct.unpack_from('<f', data, off)[0]
    out.update({
        'version': u32(0x04), 'layer_height_mm': _r(f32(0x08), 4), 'exposure_s': _r(f32(0x0C)),
        'bottom_exposure_s': _r(f32(0x10)), 'bottom_layers': u32(0x14),
        'resolution_x': u32(0x18), 'resolution_y': u32(0x1C), 'layer_count': u32(0x28),
        'print_time_s': u32(0x30), 'total_height_mm': _r(f32(0x48)),
        'bed_x_mm': _r(f32(0x4C)), 'bed_y_mm': _r(f32(0x50)), 'bed_z_mm': _r(f32(0x54)),
        'volume_ml': _r(f32(0x80)), 'weight_g': _r(f32(0x84)), 'cost': _r(f32(0x88)),
        'machine_name': _string(data, u32(0x90), u32(0x94), out),
        'software_version': _version(u32(0xBC)),
    })
    out['preview_png'] = _preview(data, u32(0x20), out)
    out['preview_small_png'] = _preview(data, u32(0x2C), out)
    return out


# ---------------------------------------------------------------------- pieces
def _r(value, places=2):
    return round(float(value), places)


def _version(packed):
    if not packed:
        return ''
    return '%d.%d.%d' % (packed >> 24, (packed >> 16) & 0xFF, (packed >> 8) & 0xFF)


def _unpack(fmt, data, offset, out):
    if offset + struct.calcsize(fmt) > len(data):
        out['truncated'] = True
        return None
    return struct.unpack_from(fmt, data, offset)


def _string(data, offset, size, out):
    if not offset or not size or size > 256:
        return ''
    if offset + size > len(data):
        out['truncated'] = True
        return ''
    return data[offset:offset + size].rstrip(b'\0').decode('utf-8', 'replace').strip()


def _preview(data, record_offset, out):
    """The plate picture: a Preview record pointing at RGB555 run-length data,
    returned as PNG bytes - or None when there is none, or the head stops short."""
    if not record_offset:
        return None
    rec = _unpack(PREVIEW_FMT, data, record_offset, out)
    if not rec:
        return None
    width, height, image_offset, length = rec[0], rec[1], rec[2], rec[3]
    if not (width and height and image_offset and length) or width * height > MAX_PREVIEW_PIXELS:
        return None
    if image_offset + length > len(data):
        out['truncated'] = True
        return None
    try:
        pixels = decode_rle15(data[image_offset:image_offset + length], width * height)
    except ValueError:
        return None
    try:
        from PIL import Image
    except ImportError:                                       # pragma: no cover
        return None
    img = Image.frombytes('RGB', (width, height), pixels)
    buf = io.BytesIO()
    img.save(buf, format='PNG', optimize=True)
    return buf.getvalue()


def decode_rle15(blob, pixel_count):
    """ChituBox's preview encoding: 16-bit words, RGB 5-5-5 with bit 5 saying
    'a run follows', the next word's low 12 bits being how many more copies.
    Short data is padded with black; a run past the end is an error."""
    out = bytearray()
    limit = pixel_count * 3
    i, n = 0, len(blob) - 1
    while i < n and len(out) < limit:
        dot = blob[i] | (blob[i + 1] << 8)
        i += 2
        r = ((dot >> 11) & 0x1F) << 3
        g = ((dot >> 6) & 0x1F) << 3
        b = (dot & 0x1F) << 3
        count = 1
        if dot & 0x20:
            if i >= n:
                break
            run = blob[i] | (blob[i + 1] << 8)
            i += 2
            count = 1 + (run & 0x0FFF)
        if len(out) + count * 3 > limit:
            raise ValueError('preview run past the end of the picture')
        out += bytes((r, g, b)) * count
    if len(out) < limit:
        out += b'\0' * (limit - len(out))
    return bytes(out)


def hours(seconds):
    return round((seconds or 0) / 3600.0, 2)
