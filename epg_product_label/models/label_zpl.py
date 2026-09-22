# -*- coding: utf-8 -*-
"""A small ZPL II writer.

The PDF engine can lean on a browser to lay text out; a Zebra printer cannot.  Every
coordinate here is in printer dots, every string has to survive ``^FD``, and images
have to arrive as a 1-bit bitmap.  This module owns those three problems so the
element models can stay in millimetres.
"""

import base64
import io
import logging

from .label_units import ZPL_DENSITIES, pt_to_mm

_logger = logging.getLogger(__name__)

try:
    from PIL import Image, ImageOps
except ImportError:  # pragma: no cover - Pillow is an Odoo hard requirement
    Image = ImageOps = None
    _logger.warning("Pillow is not available: images will be skipped in ZPL output.")

# ZPL field orientation codes, keyed by the rotation the designer stores.
ORIENTATION = {0: 'N', 90: 'R', 180: 'I', 270: 'B'}

# Justification codes accepted by ^FB.
JUSTIFY = {'left': 'L', 'center': 'C', 'right': 'R', 'justify': 'J'}

# Characters that terminate or re-interpret a ^FD block and therefore must never
# reach the printer unescaped.  ``_`` belongs here too: ^FH makes it the hex
# indicator, so a literal underscore ("LOT_42") would swallow the next two characters.
_UNSAFE = ('^', '~', '\\', '_')


class ZplBuilder:
    """Accumulates ZPL commands for a single label and renders them to a string.

    :param width_mm: printable label width
    :param height_mm: printable label length
    :param density: one of the keys of :data:`label_units.ZPL_DENSITIES`
    :param rotation: whole-label rotation, 0/90/180/270
    :param encoding: ``utf8`` uses ^CI28, ``cp850`` and ``ascii`` fall back to ^CI13
    :param darkness: media darkness ^MD, -30..30, or ``None`` to leave the printer alone
    """

    def __init__(self, width_mm, height_mm, density='8', rotation=0,
                 encoding='utf8', darkness=None, print_speed=None):
        self.dpmm = ZPL_DENSITIES.get(density, 8.0)
        self.density = density
        self.width_mm = width_mm
        self.height_mm = height_mm
        self.rotation = rotation or 0
        self.encoding = encoding
        self.darkness = darkness
        self.print_speed = print_speed
        self._body = []

    # ------------------------------------------------------------------
    # units
    # ------------------------------------------------------------------
    def dots(self, value_mm):
        return int(round(float(value_mm or 0.0) * self.dpmm))

    def font_dots(self, font_size_pt):
        """A ZPL font height is a character cell in dots; the designer stores points."""
        return max(6, self.dots(pt_to_mm(font_size_pt)))

    # ------------------------------------------------------------------
    # escaping
    # ------------------------------------------------------------------
    def escape(self, text):
        """Make ``text`` safe for a ^FD block.

        ^FH lets us write any byte as ``_xx``.  We always emit ^FH with the fields we
        build, so hex-escaping the ZPL command characters, the ``_`` indicator itself,
        control characters and anything non-ASCII is enough.  Control characters keep
        their byte (a vCard QR needs its line feeds); non-ASCII has to be hex-escaped
        anyway, because ^FD is byte oriented and a raw multi-byte character would be
        split into mojibake.
        """
        if text is None:
            return ''
        text = str(text)
        codec = 'utf-8' if self.encoding == 'utf8' else (
            'cp850' if self.encoding == 'cp850' else 'ascii')
        out = []
        for char in text:
            if char in _UNSAFE or ord(char) < 32 or ord(char) == 127:
                out.append('_%02X' % ord(char))
            elif ord(char) < 127:
                out.append(char)
            else:
                try:
                    raw = char.encode(codec)
                except UnicodeEncodeError:
                    raw = b'?'
                out.append(''.join('_%02X' % byte for byte in raw))
        return ''.join(out)

    # ------------------------------------------------------------------
    # primitives
    # ------------------------------------------------------------------
    def raw(self, command):
        if command:
            self._body.append(command)

    def text(self, x_mm, y_mm, value, font_size_pt=10, width_mm=None, rotation=0,
             align='left', bold=False, max_lines=6, line_spacing=0, reverse=False,
             font='0'):
        """Place a text field, wrapping inside ``width_mm`` when one is given."""
        if value in (None, ''):
            return
        orient = ORIENTATION.get(rotation % 360, 'N')
        height = self.font_dots(font_size_pt)
        # Bold has no first-class ZPL equivalent for scalable fonts.  Widening the
        # glyph cell is the conventional stand-in and reads correctly on thermal stock.
        width = int(height * 0.62) if bold else 0
        self.raw('^FO%d,%d' % (self.dots(x_mm), self.dots(y_mm)))
        self.raw('^A%s%s,%d,%d' % (font, orient, height, width))
        if width_mm:
            self.raw('^FB%d,%d,%d,%s,0' % (
                self.dots(width_mm), max_lines, self.dots(line_spacing),
                JUSTIFY.get(align, 'L')))
        if reverse:
            self.raw('^FR')
        # A line break in a text field is layout, not data: ^FB breaks on ``\\&``,
        # and a single-line field has nowhere to break, so it gets a space.
        lines = str(value).replace('\r\n', '\n').replace('\r', '\n').replace('\t', ' ').split('\n')
        joiner = '\\&' if width_mm else ' '
        self.raw('^FH^FD%s^FS' % joiner.join(self.escape(line) for line in lines))

    def box(self, x_mm, y_mm, width_mm, height_mm, thickness_mm=0.3,
            color='B', rounding=0, fill=False):
        """Draw a rectangle. ``fill=True`` makes the border as thick as the box.

        A border as thick as the SHORT side already fills it.  ^GB grows both sides
        to at least the thickness, so anything thicker turns a 20x10 box into 20x20.
        """
        w = max(1, self.dots(width_mm))
        h = max(1, self.dots(height_mm))
        thickness = min(w, h) if fill else max(1, self.dots(thickness_mm))
        self.raw('^FO%d,%d^GB%d,%d,%d,%s,%d^FS' % (
            self.dots(x_mm), self.dots(y_mm), w, h,
            min(thickness, w, h), color, rounding))

    def line(self, x_mm, y_mm, width_mm, height_mm, thickness_mm=0.3, color='B'):
        thickness = max(1, self.dots(thickness_mm))
        self.raw('^FO%d,%d^GB%d,%d,%d,%s,0^FS' % (
            self.dots(x_mm), self.dots(y_mm),
            max(1, self.dots(width_mm)), max(1, self.dots(height_mm)),
            thickness, color))

    def ellipse(self, x_mm, y_mm, width_mm, height_mm, thickness_mm=0.3, color='B'):
        self.raw('^FO%d,%d^GE%d,%d,%d,%s^FS' % (
            self.dots(x_mm), self.dots(y_mm),
            max(1, self.dots(width_mm)), max(1, self.dots(height_mm)),
            max(1, self.dots(thickness_mm)), color))

    def reverse_field(self, x_mm, y_mm, width_mm, height_mm):
        """Paint a solid black rectangle; anything drawn after it with ^FR knocks out."""
        self.box(x_mm, y_mm, width_mm, height_mm, fill=True)

    # ------------------------------------------------------------------
    # barcodes
    # ------------------------------------------------------------------
    def barcode(self, x_mm, y_mm, value, symbology='auto', height_mm=10.0,
                width_mm=None, rotation=0, human_readable=True, module_width=2,
                ratio=3.0, reverse=False):
        """Emit a 1D barcode, or delegate to :meth:`qrcode` for QR symbologies."""
        if not value:
            return
        value = str(value)
        symbology = self._resolve_symbology(symbology, value)
        if symbology == 'QR':
            self.qrcode(x_mm, y_mm, value, size_mm=min(
                width_mm or height_mm, height_mm), rotation=rotation)
            return
        orient = ORIENTATION.get(rotation % 360, 'N')
        height = max(1, self.dots(height_mm))
        interpretation = 'Y' if human_readable else 'N'
        module = max(1, int(module_width))
        if width_mm:
            # Fit the symbol to the box the designer drew rather than trusting the
            # default module width, which overflows narrow labels.
            module = max(1, min(10, int(self.dots(width_mm) / self._estimate_modules(
                symbology, value))))
        self.raw('^BY%d,%s,%d' % (module, round(ratio, 1), height))
        self.raw('^FO%d,%d' % (self.dots(x_mm), self.dots(y_mm)))
        if reverse:
            self.raw('^FR')
        commands = {
            'Code128': '^BC%s,%d,%s,N,N' % (orient, height, interpretation),
            'Code39': '^B3%s,N,%d,%s,N' % (orient, height, interpretation),
            'EAN13': '^BE%s,%d,%s,N' % (orient, height, interpretation),
            'EAN8': '^B8%s,%d,%s,N' % (orient, height, interpretation),
            'UPCA': '^BU%s,%d,%s,N,Y' % (orient, height, interpretation),
            'UPCE': '^B9%s,%d,%s,N,Y' % (orient, height, interpretation),
            'ITF': '^B2%s,%d,%s,N,N' % (orient, height, interpretation),
            'Codabar': '^BK%s,N,%d,%s,N,A,A' % (orient, height, interpretation),
        }
        self.raw(commands.get(symbology, commands['Code128']))
        self.raw('^FH^FD%s^FS' % self.escape(value))

    def qrcode(self, x_mm, y_mm, value, size_mm=20.0, rotation=0,
               error_correction='M', reverse=False):
        if not value:
            return
        orient = ORIENTATION.get(rotation % 360, 'N')
        # A QR of version 3-4 - the usual size for a URL or a vCard fragment - is
        # about 29 modules across, plus a 4-module quiet zone on each side.
        magnification = max(1, min(10, int(self.dots(size_mm) / 37)))
        self.raw('^FO%d,%d' % (self.dots(x_mm), self.dots(y_mm)))
        if reverse:
            self.raw('^FR')
        self.raw('^BQ%s,2,%d,%s,7' % (orient, magnification, error_correction))
        self.raw('^FH^FD%sA,%s^FS' % (error_correction, self.escape(value)))

    def datamatrix(self, x_mm, y_mm, value, size_mm=15.0, rotation=0):
        if not value:
            return
        orient = ORIENTATION.get(rotation % 360, 'N')
        module = max(2, min(20, int(self.dots(size_mm) / 20)))
        self.raw('^FO%d,%d^BX%s,%d,200^FH^FD%s^FS' % (
            self.dots(x_mm), self.dots(y_mm), orient, module, self.escape(value)))

    # ------------------------------------------------------------------
    # images
    # ------------------------------------------------------------------
    def image(self, x_mm, y_mm, image_bytes, width_mm, height_mm,
              threshold=128, invert=False):
        """Convert ``image_bytes`` to a 1-bit bitmap and emit it as ^GFA."""
        if not image_bytes or Image is None:
            return
        try:
            payload, byte_width, total = self._to_bitmap(
                image_bytes, self.dots(width_mm), self.dots(height_mm),
                threshold, invert)
        except Exception:  # pragma: no cover - corrupt attachments are not fatal
            _logger.warning("Could not convert image for ZPL output.", exc_info=True)
            return
        if not total:
            return
        self.raw('^FO%d,%d^GFA,%d,%d,%d,%s^FS' % (
            self.dots(x_mm), self.dots(y_mm), total, total, byte_width, payload))

    def _to_bitmap(self, image_bytes, width_dots, height_dots, threshold, invert):
        image = Image.open(io.BytesIO(image_bytes))
        if image.mode in ('RGBA', 'LA', 'P'):
            image = image.convert('RGBA')
            # Thermal media is white; flatten transparency onto white so that a
            # transparent PNG does not come out as a solid black block.
            background = Image.new('RGBA', image.size, (255, 255, 255, 255))
            image = Image.alpha_composite(background, image)
        image = image.convert('L')
        width_dots = max(1, int(width_dots))
        height_dots = max(1, int(height_dots))
        image = image.resize((width_dots, height_dots), Image.LANCZOS)
        if invert:
            image = ImageOps.invert(image)
        # ``1`` in a ^GFA bitmap means "burn a dot", so dark pixels become 1.
        image = image.point(lambda value: 0 if value > threshold else 255, mode='1')

        byte_width = (width_dots + 7) // 8
        raw = image.tobytes()
        # Pillow already pads each row to a byte boundary, but it inverts the sense
        # of mode "1" (255 -> bit set) only for the pixels we set above.
        total = byte_width * height_dots
        payload = raw[:total].hex().upper()
        return payload, byte_width, total

    # ------------------------------------------------------------------
    # assembly
    # ------------------------------------------------------------------
    def render(self):
        header = ['^XA']
        header.append('^CI28' if self.encoding == 'utf8' else '^CI13')
        header.append('^PW%d' % self.dots(self.width_mm))
        header.append('^LL%d' % self.dots(self.height_mm))
        header.append('^LH0,0')
        header.append('^LT0')
        header.append('^PON' if self.rotation != 180 else '^POI')
        if self.rotation in (90, 270):
            header.append('^FW%s' % ORIENTATION[self.rotation])
        if self.darkness is not None:
            header.append('^MD%d' % max(-30, min(30, int(self.darkness))))
        if self.print_speed:
            header.append('^PR%d' % max(1, min(14, int(self.print_speed))))
        return ''.join(header) + ''.join(self._body) + '^XZ\n'

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _resolve_symbology(symbology, value):
        """Mirror Odoo's ``auto`` guess so PDF and ZPL pick the same symbology."""
        if symbology and symbology != 'auto':
            return symbology
        digits = value.isdigit()
        if digits and len(value) == 13:
            return 'EAN13'
        if digits and len(value) == 12:
            return 'UPCA'
        if digits and len(value) == 8:
            return 'EAN8'
        return 'Code128'

    @staticmethod
    def _estimate_modules(symbology, value):
        """Rough module count, used only to fit a symbol into its designed width."""
        counts = {
            'EAN13': 113, 'EAN8': 81, 'UPCA': 113, 'UPCE': 67,
            'ITF': 9 * (len(value) / 2.0) + 15,
            'Code39': 16 * (len(value) + 2),
        }
        # Code128 packs roughly 11 modules per character plus start/stop/checksum.
        return max(20, counts.get(symbology, 11 * (len(value) + 4)))


def encode_image_b64(image_b64):
    """Decode an Odoo binary field into bytes, tolerating ``False`` and str/bytes."""
    if not image_b64:
        return None
    if isinstance(image_b64, str):
        image_b64 = image_b64.encode()
    try:
        return base64.b64decode(image_b64)
    except Exception:
        return None
