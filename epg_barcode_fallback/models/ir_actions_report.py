# -*- coding: utf-8 -*-
"""Draw barcodes without reportlab's renderPM backend.

reportlab 4.x dropped the bundled `_renderPM` C extension and now draws raster images
through **rlPyCairo**, which needs system cairo headers to build. On a machine without
them `createBarcodeDrawing(...).asString('png')` raises, and because every barcode in
every report goes through that one call, the failure is total: job cards, product
labels, delivery slips and stickers all stop printing, in core and custom modules alike.

The images here come from `qrcode` and `python-barcode`, both of which rasterise through
Pillow and need nothing from the system. This is a fallback, not a replacement — if the
reportlab backend works, it is used, so installing cairo later silently restores the
native output rather than leaving the lab on a substitute for ever.
"""
import io
import logging

from odoo import _, api, models, tools
from odoo.exceptions import UserError
from odoo.tools.barcode import check_barcode_encoding

_logger = logging.getLogger(__name__)

try:
    import qrcode
except ImportError:
    qrcode = None
try:
    import barcode as pybarcode
    from barcode.writer import ImageWriter
except ImportError:
    pybarcode = None
    ImageWriter = None

# Odoo/reportlab symbology names → python-barcode's
SYMBOLOGY = {
    'Code128': 'code128', 'Code39': 'code39', 'EAN13': 'ean13', 'EAN8': 'ean8',
    'UPCA': 'upca', 'ITF': 'itf', 'Standard39': 'code39', 'Extended39': 'code39',
    # 'auto' is not a symbology, it is an instruction: nine core report templates —
    # every product label among them — ask for it and mean "pick something that fits
    # this value". `_core_symbology` resolves it exactly as core does (EAN-8 / EAN-13
    # by length and check digit, else Code128) before this map is read; the entry
    # only keeps 'auto' from being refused as unmapped.
    'auto': 'code128',
    # python-barcode draws both of these; refusing them would have been the same kind
    # of wrong answer in the other direction.
    'Codabar': 'codabar', 'I2of5': 'itf', 'UPCE': 'upca', 'ISBN13': 'isbn13',
}

# Turning this off restores the old behaviour of drawing any unknown symbology as a
# Code128. It exists because this module is auto-installed and sits under every barcode
# in the system, including core's package and lot labels, which ask for DataMatrix: a
# lab that would rather have a wrong-but-printed label than a stopped print can say so
# without a code change.
STRICT_PARAM = 'epg_barcode_fallback.strict_symbology'


class IrActionsReport(models.Model):
    _inherit = 'ir.actions.report'

    @api.model
    @tools.ormcache()
    def _renderpm_works(self):
        """Can reportlab actually rasterise? Asked once, then cached.

        Probing per barcode would mean an exception per image on a report full of
        labels, which is slow in exactly the case that is already broken.
        """
        try:
            from reportlab.graphics.barcode import createBarcodeDrawing
            createBarcodeDrawing('Code128', value='0', format='png',
                                 width=100, height=50).asString('png')
            return True
        except Exception as exc:
            _logger.info(
                "reportlab cannot rasterise barcodes (%s); falling back to "
                "qrcode/python-barcode. Install system cairo and rlPyCairo to use "
                "reportlab's own renderer.", type(exc).__name__)
            return False

    @api.model
    def barcode(self, barcode_type, value, **kwargs):
        if self._renderpm_works():
            return super().barcode(barcode_type, value, **kwargs)
        png = self._barcode_via_pillow(barcode_type, value, **kwargs)
        if png is None:
            # Nothing we can draw with — let the original raise, so the real reason
            # still reaches the log instead of being swallowed into a blank image.
            return super().barcode(barcode_type, value, **kwargs)
        return png

    # ------------------------------------------------------------------ drawing
    @api.model
    def _barcode_via_pillow(self, barcode_type, value, **kwargs):
        width = int(kwargs.get('width', 600))
        height = int(kwargs.get('height', 100))
        # Core refuses absurd sizes before drawing (ir_actions_report.barcode); this
        # override returns before that ever runs, so /report/barcode?width=99999 would
        # otherwise ask Pillow for a multi-gigabyte image on an unauthenticated route.
        if width * height > 1200000 or max(width, height) > 10000:
            raise ValueError("Barcode too large")
        try:
            if barcode_type == 'QR':
                return self._qr_png(value, width, height, kwargs)
            return self._linear_png(barcode_type, value, width, height, kwargs)
        except UserError:
            # A symbology this server cannot draw is a template bug, and it must reach
            # the person printing. Swallowing it here would send the print on to
            # reportlab, which is broken on this machine, and report that instead — the
            # wrong diagnosis for the right problem.
            raise
        except Exception as exc:
            _logger.warning("Fallback barcode rendering failed for %s %r: %s",
                            barcode_type, value, exc)
            return None

    @api.model
    def _qr_png(self, value, width, height, kwargs):
        if qrcode is None:
            return None
        border = int(kwargs.get('barBorder', 4))
        level = {'L': qrcode.constants.ERROR_CORRECT_L,
                 'M': qrcode.constants.ERROR_CORRECT_M,
                 'Q': qrcode.constants.ERROR_CORRECT_Q,
                 'H': qrcode.constants.ERROR_CORRECT_H}.get(
                     kwargs.get('barLevel', 'L'), qrcode.constants.ERROR_CORRECT_L)
        qr = qrcode.QRCode(error_correction=level, border=border, box_size=10)
        qr.add_data(value)
        qr.make(fit=True)
        img = qr.make_image(fill_color='black', back_color='white').convert('RGB')
        # A QR code must stay square: scaling it to a non-square box is what turns a
        # scannable code into a decorative rectangle.
        side = max(1, min(width, height))
        return self._to_png(img.resize((side, side)))

    @staticmethod
    def _flag(value):
        if isinstance(value, str):
            return value.strip().lower() not in ('', '0', 'false', 'no')
        return bool(value)

    @api.model
    def _strict_symbology(self):
        # Default '1': an ABSENT parameter must mean strict. Reading a missing key
        # returns False, and `str(False).lower()` is 'false' — so without the default
        # the rail would be off on every database that had never set it, which is all
        # of them.
        param = self.env['ir.config_parameter'].sudo().get_param(STRICT_PARAM, '1')
        return str(param).strip().lower() not in ('0', 'false', 'no', 'off')

    @staticmethod
    def _core_symbology(barcode_type, value):
        """Resolve `barcode_type` the way core's `barcode()` does before drawing.

        'auto' is EAN-8 for 8 characters, EAN-13 for 13, Code128 otherwise; and an
        EAN whose check digit is wrong becomes Code128 - python-barcode would
        otherwise drop the last digit and compute its own, printing a code that
        scans as a DIFFERENT number from the one on the label.
        """
        value = str(value)
        if barcode_type == 'auto':
            barcode_type = {8: 'EAN8', 13: 'EAN13'}.get(len(value), 'Code128')
        if barcode_type in ('EAN8', 'EAN13') and not check_barcode_encoding(value, barcode_type):
            barcode_type = 'Code128'
        return barcode_type

    @api.model
    def _linear_png(self, barcode_type, value, width, height, kwargs):
        if pybarcode is None:
            return None
        barcode_type = self._core_symbology(barcode_type, value)
        # An UNMAPPED symbology is refused rather than quietly drawn as Code128.
        #
        # The silent substitution this replaces is the worst failure mode in a barcode
        # stack, because it does not fail where anyone can see it: ask for DataMatrix or
        # PDF417 and a perfectly clean Code128 comes out, the sheet looks right, it goes
        # in the box, and it fails at the customer's scanner a week later. A symbology
        # that is not in the map is a template bug, and a template bug should stop the
        # print. (Falling back WITHIN a mapped symbology — a bad EAN check digit — is a
        # different case and is still handled below: there the value is wrong, not the
        # request.) (client, 2026-08-26)
        if barcode_type not in SYMBOLOGY:
            if self._strict_symbology():
                # The detail goes to the log, not to the message: `barcode()` is also
                # reachable through the UNAUTHENTICATED /report/barcode route, where
                # this text is rendered straight back to an anonymous caller. What that
                # caller needs is "no", not an inventory of the server's capabilities
                # and the name of the parameter that relaxes them.
                _logger.warning(
                    "Refusing to draw %s: not supported by this server's barcode "
                    "library (%s). Set %s to 0 to accept a Code128 substitution.",
                    barcode_type, ', '.join(sorted(SYMBOLOGY)), STRICT_PARAM)
                raise UserError(_(
                    "This server cannot draw %(kind)s barcodes.\n\n"
                    "Printing one as a different symbology would produce a label that "
                    "looks perfectly correct and scans as the wrong kind of code, which "
                    "fails at whoever receives it rather than here. Change the report to "
                    "a supported symbology, or ask an administrator to allow the "
                    "substitution.",
                    kind=barcode_type))
            _logger.warning(
                "Drawing %s as Code128: this server cannot render that symbology and "
                "%s is off. The label will scan as the wrong kind of code.",
                barcode_type, STRICT_PARAM)
        name = SYMBOLOGY.get(barcode_type, 'code128')
        try:
            cls = pybarcode.get_barcode_class(name)
        except Exception:
            cls = pybarcode.get_barcode_class('code128')
        writer = ImageWriter()
        try:
            drawing = cls(str(value), writer=writer)
        except Exception:
            # Value does not satisfy this symbology (a bad EAN check digit, say).
            # Code128 accepts anything, and a readable barcode of the right value
            # beats a correct-looking one that is not there.
            drawing = pybarcode.get_barcode_class('code128')(str(value), writer=writer)
        buf = io.BytesIO()
        drawing.write(buf, options={
            'module_height': 15.0,
            'quiet_zone': 2.0 if kwargs.get('quiet', True) else 0.0,
            # Core normalises this to camelCase only INSIDE its own barcode(); this
            # override is the public entry point and sees whatever the caller wrote,
            # and every caller in this codebase and in core's templates writes it
            # lowercase. '0' is a non-empty string, so bool() alone reads it as True.
            'write_text': self._flag(kwargs.get('humanReadable',
                                                kwargs.get('humanreadable'))),
            'font_size': 10,
            'text_distance': 3.0,
        })
        buf.seek(0)
        from PIL import Image
        img = Image.open(buf).convert('RGB')
        return self._to_png(img.resize((max(1, width), max(1, height))))

    @staticmethod
    def _to_png(img):
        out = io.BytesIO()
        img.save(out, format='PNG')
        return out.getvalue()
