# -*- coding: utf-8 -*-
"""Dynamic Dashboards - a board, or one card, as a PDF.

Odoo's report engine draws with wkhtmltopdf, which is not installed
everywhere and could not draw these charts anyway: Chart.js paints them in
the reader's browser, on a canvas. So the file is composed here with
reportlab and Pillow - both of which Odoo already requires, so nothing new
has to be installed - and the browser hands over the charts it has on screen
as PNGs.

The pictures come from the screen; the figures do not. Every number in the
file is recomputed here, with the reader's own rights, exactly as the board
computes it. A card the reader may not see never reaches the page.
"""
import base64
import binascii
import io
import re
from datetime import datetime

from odoo import models
from odoo.exceptions import UserError

# Odoo's own requirements.txt pins reportlab, so this import all but always
# works. All but: a hand-built environment can be missing it, and the rest of
# the module has no business failing to install over a download button.
try:
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas as pdf_canvas
except ImportError:  # pragma: no cover - only on an incomplete install
    A4 = landscape = ImageReader = pdfmetrics = TTFont = pdf_canvas = None

from .dashboard_item import BOARD_COLUMNS, NUMBER_KINDS, PERIODS, STACKED_KINDS

# The same hexes as static/src/core/board_colors.js - a card keeps its colour
# on paper. Kept in step by hand; there are eleven of them and they never move.
CARD_HEX = {
    'indigo': '#4f46e5', 'sky': '#0284c7', 'cyan': '#0891b2', 'teal': '#0d9488',
    'emerald': '#059669', 'lime': '#65a30d', 'amber': '#d97706', 'orange': '#ea580c',
    'rose': '#e11d48', 'violet': '#7c3aed', 'slate': '#64748b',
}
DEFAULT_HEX = CARD_HEX['indigo']
INK = '#1f2937'
MUTED = '#6b7280'
LINE = '#e5e7eb'
DANGER = '#dc2626'
WARNING = '#d97706'

# A latin-1 font cannot write a Japanese card title. DejaVu ships with every
# desktop and most server images; Liberation is the usual stand-in. When
# neither is there reportlab's built-in Helvetica takes over and the text is
# folded to latin-1 - readable, and never a traceback.
FONT_CANDIDATES = [
    ('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
     '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'),
    ('/usr/share/fonts/dejavu/DejaVuSans.ttf',
     '/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf'),
    ('/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
     '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf'),
    ('/usr/share/fonts/liberation/LiberationSans-Regular.ttf',
     '/usr/share/fonts/liberation/LiberationSans-Bold.ttf'),
]
FONT = 'Helvetica'
FONT_BOLD = 'Helvetica-Bold'
_FONTS_READY = False

MARGIN = 26
GUTTER = 9
HEADER_HEIGHT = 34
FOOTER_HEIGHT = 20
CARD_PAD = 8
NUMBER_ROW = 96
# Three rows of charts fill a landscape page: 3 x (152 + 9) = 483 of the 489
# points a page has between its header and its footer. A shorter row would
# waste a band at the bottom, a taller one would push the third row over.
CHART_ROW = 152
MAX_BODY_ROWS = 9


def _ensure_fonts():
    """Register a Unicode font once per process, if the system has one."""
    global _FONTS_READY, FONT, FONT_BOLD
    if _FONTS_READY:
        return
    _FONTS_READY = True
    for regular, bold in FONT_CANDIDATES:
        try:
            pdfmetrics.registerFont(TTFont('DashboardSans', regular))
            pdfmetrics.registerFont(TTFont('DashboardSans-Bold', bold))
        except Exception:  # noqa: BLE001 - a missing font is not an error
            continue
        FONT, FONT_BOLD = 'DashboardSans', 'DashboardSans-Bold'
        return


def _safe(text, font):
    """Text the chosen font can actually write."""
    text = '' if text is None else str(text)
    if font.startswith('Dashboard'):
        return text
    return text.encode('latin-1', 'replace').decode('latin-1')


class DashboardPdf:
    """Draws a board onto a reportlab canvas, one row of cards at a time."""

    def __init__(self, env, board, items, images=None, single=False):
        _ensure_fonts()
        self.env = env
        self.board = board
        self.items = items
        self.images = images or {}
        self.single = single
        # Landscape throughout: a chart is wider than it is tall, and one card
        # alone gets the whole sheet.
        self.size = landscape(A4)
        self.width, self.height = self.size
        self.page = 0
        self.buffer = io.BytesIO()
        self.canvas = pdf_canvas.Canvas(self.buffer, pagesize=self.size)
        self.canvas.setTitle(_safe(board.get('name') or 'Dashboard', FONT))

    # ------------------------------------------------------------------
    # The file
    # ------------------------------------------------------------------
    def build(self):
        rows = self._rows()
        self._start_page()
        y = self.height - MARGIN - HEADER_HEIGHT
        floor = MARGIN + FOOTER_HEIGHT
        for row in rows:
            height = self._row_height(row)
            if y - height < floor and y < self.height - MARGIN - HEADER_HEIGHT:
                self.canvas.showPage()
                self._start_page()
                y = self.height - MARGIN - HEADER_HEIGHT
            usable = self.width - 2 * MARGIN
            unit = (usable - GUTTER * (BOARD_COLUMNS - 1)) / BOARD_COLUMNS
            x = MARGIN
            for item, span in row:
                box = unit * span + GUTTER * (span - 1)
                self._card(item, x, y - height, box, height - GUTTER)
                x += box + GUTTER
            y -= height
        self.canvas.showPage()
        self.canvas.save()
        return self.buffer.getvalue()

    def _rows(self):
        """Pack the cards into rows of twelve columns, the way the grid does."""
        rows, row, used = [], [], 0
        for item in self.items:
            span = BOARD_COLUMNS if self.single \
                else max(1, min(int(item.get('width') or 3), BOARD_COLUMNS))
            if used + span > BOARD_COLUMNS and row:
                rows.append(row)
                row, used = [], 0
            row.append((item, span))
            used += span
        if row:
            rows.append(row)
        return rows

    def _row_height(self, row):
        if self.single:
            return self.height - 2 * MARGIN - HEADER_HEIGHT - FOOTER_HEIGHT
        tall = any(self._is_tall(item) for item, _span in row)
        return (CHART_ROW if tall else NUMBER_ROW) + GUTTER

    def _is_tall(self, item):
        if self.images.get(str(item['id'])) or self.images.get(item['id']):
            return True
        return item['kind'] not in NUMBER_KINDS and item['kind'] != 'text'

    # ------------------------------------------------------------------
    # Page furniture
    # ------------------------------------------------------------------
    def _start_page(self):
        self.page += 1
        c = self.canvas
        name = self.board.get('name') or self.env._('Dashboard')
        c.setFillColor(INK)
        c.setFont(FONT_BOLD, 14)
        c.drawString(MARGIN, self.height - MARGIN - 11, _safe(name, FONT_BOLD))
        subtitle = ' · '.join(part for part in self._subtitle_parts() if part)
        if subtitle:
            c.setFont(FONT, 8.5)
            c.setFillColor(MUTED)
            c.drawRightString(self.width - MARGIN, self.height - MARGIN - 10, _safe(subtitle, FONT))
        c.setStrokeColor(LINE)
        c.setLineWidth(0.6)
        c.line(MARGIN, self.height - MARGIN - HEADER_HEIGHT + 8,
               self.width - MARGIN, self.height - MARGIN - HEADER_HEIGHT + 8)
        # Footer: who printed it, when, and where in the file we are.
        c.setFont(FONT, 7.5)
        c.setFillColor(MUTED)
        stamp = self.env._(
            '%(user)s · %(when)s',
            user=self.env.user.name,
            when=datetime.now().strftime('%Y-%m-%d %H:%M'))
        c.drawString(MARGIN, MARGIN, _safe(stamp, FONT))
        c.drawRightString(self.width - MARGIN, MARGIN, _safe(self.env._('Page %s', self.page), FONT))

    def _subtitle_parts(self):
        parts = [self.period_label]
        if self.board.get('mine'):
            parts.append(self.env._('Only my records'))
        focus = self.board.get('focus_label')
        if focus:
            parts.append(focus)
        parts.append(self.env._('%s cards', len(self.items)))
        return parts

    @property
    def period_label(self):
        period = self.board.get('period')
        if isinstance(period, dict):
            return '%s – %s' % (period.get('start') or '', period.get('end') or '')
        labels = dict(PERIODS)
        return str(labels.get(period, labels.get('all', 'All Time')))

    # ------------------------------------------------------------------
    # One card
    # ------------------------------------------------------------------
    def _card(self, item, x, y, width, height):
        c = self.canvas
        colour = CARD_HEX.get(item.get('color'), DEFAULT_HEX)
        c.setStrokeColor(LINE)
        c.setLineWidth(0.8)
        c.setFillColor('#ffffff')
        c.roundRect(x, y, width, height, 5, stroke=1, fill=1)
        # A rule in the card's colour, so a printed board is still colour-coded.
        c.setFillColor(colour)
        c.rect(x + 0.4, y + 6, 2.4, height - 12, stroke=0, fill=1)

        inner_x = x + CARD_PAD + 4
        inner_w = width - 2 * CARD_PAD - 4
        title_y = y + height - CARD_PAD - 7
        c.setFillColor(DANGER if item.get('alert') == 'danger'
                       else WARNING if item.get('alert') else INK)
        c.setFont(FONT_BOLD, 8.5)
        c.drawString(inner_x, title_y, self._fit(item.get('name') or '', FONT_BOLD, 8.5, inner_w))

        body_top = title_y - 8
        body_bottom = y + CARD_PAD
        body_h = body_top - body_bottom
        if item.get('error'):
            self._muted(inner_x, body_top - 12, self.env._('No access to this data.'))
            return
        image = self.images.get(str(item['id'])) or self.images.get(item['id'])
        if image and self._draw_image(image, inner_x, body_bottom, inner_w, body_h):
            return
        if item['kind'] == 'text':
            self._paragraph(item.get('note') or '', inner_x, body_top, inner_w, body_h)
        elif item['kind'] in NUMBER_KINDS:
            self._number(item, inner_x, body_top, inner_w, body_h, colour)
        elif item['kind'] == 'list':
            self._list(item, inner_x, body_top, inner_w, body_h)
        elif item['kind'] in STACKED_KINDS:
            self._matrix(item, inner_x, body_top, inner_w, body_h)
        elif item.get('points'):
            self._points(item, inner_x, body_top, inner_w, body_h, colour)
        else:
            self._muted(inner_x, body_top - 12, self.env._('Nothing to show.'))

    def _draw_image(self, data_url, x, y, width, height):
        """A chart, as the reader saw it. A broken image is simply skipped."""
        raw = re.sub(r'^data:image/[a-z+]+;base64,', '', data_url or '', flags=re.I)
        try:
            image = ImageReader(io.BytesIO(base64.b64decode(raw, validate=True)))
            iw, ih = image.getSize()
        except (binascii.Error, ValueError, OSError, TypeError):
            return False
        if not iw or not ih:
            return False
        scale = min(width / iw, height / ih)
        draw_w, draw_h = iw * scale, ih * scale
        self.canvas.drawImage(image, x + (width - draw_w) / 2, y + (height - draw_h) / 2,
                              draw_w, draw_h, mask='auto')
        return True

    def _number(self, item, x, top, width, height, colour):
        c = self.canvas
        size = 64 if self.single else 26 if height > 50 else 18
        text = self._fit(self._value(item), FONT_BOLD, size, width)
        c.setFillColor(colour)
        c.setFont(FONT_BOLD, size)
        c.drawString(x, top - size + 2, text)
        line = top - size - 6
        delta = item.get('delta')
        if delta is not None:
            against = self.env._('vs last year') if item.get('compare_mode') == 'year' \
                else self.env._('vs the previous period')
            percent = item.get('delta_percent')
            moved = ('%+.1f%%' % percent) if percent is not None \
                else '%s%s' % ('+' if delta >= 0 else '-', self._format(abs(delta), item))
            self._muted(x, line, '%s %s' % (moved, against))
            line -= 11
        if item.get('target'):
            share = (item.get('value') or 0) / item['target'] * 100 if item['target'] else 0
            self._muted(x, line, self.env._(
                '%(share)s%% of %(target)s',
                share=round(share), target=self._format(item['target'], item)))
            line -= 11
        pace = item.get('pace')
        if pace:
            words = {'ahead': self.env._('Ahead'), 'on_track': self.env._('On track'),
                     'behind': self.env._('Behind')}
            self._muted(x, line, '%s · %s%% of the period' % (
                words.get(pace['status'], pace['status']), pace['elapsed']))

    def _points(self, item, x, top, width, height, colour):
        """A split as a small table: label, value, and a bar for the share."""
        rows = [point for point in (item.get('points') or [])][:self._room(height)]
        if not rows:
            return self._muted(x, top - 12, self.env._('Nothing to show.'))
        biggest = max([abs(float(row.get('value') or 0)) for row in rows] or [1]) or 1
        y = top - 10
        for row in rows:
            value = float(row.get('value') or 0)
            label_w = width * 0.46
            self.canvas.setFillColor(INK)
            self.canvas.setFont(FONT, 7.5)
            self.canvas.drawString(x, y, self._fit(row.get('label') or '', FONT, 7.5, label_w))
            self.canvas.drawRightString(x + width, y, self._fit(self._format(value, item), FONT, 7.5, width * 0.2))
            bar_x = x + label_w + 4
            bar_w = max(0.0, (width - label_w - width * 0.22 - 6)) * (abs(value) / biggest)
            self.canvas.setFillColor(colour)
            self.canvas.rect(bar_x, y - 1.5, bar_w, 4.5, stroke=0, fill=1)
            y -= 11
        self._more(item.get('points') or [], rows, x, y, width)

    def _matrix(self, item, x, top, width, height):
        """Two crossed fields: the totals per row, which is what fits."""
        categories = item.get('categories') or []
        series = item.get('series') or []
        rows = categories[:self._room(height)]
        if not rows:
            return self._muted(x, top - 12, self.env._('Nothing to show.'))
        y = top - 10
        self.canvas.setFont(FONT, 7.5)
        for index, category in enumerate(rows):
            total = sum(float((entry.get('values') or [0] * len(categories))[index] or 0)
                        for entry in series)
            self.canvas.setFillColor(INK)
            self.canvas.drawString(x, y, self._fit(category.get('label') or '', FONT, 7.5, width * 0.6))
            self.canvas.drawRightString(x + width, y, self._format(total, item))
            y -= 11
        self._more(categories, rows, x, y, width)

    def _list(self, item, x, top, width, height):
        rows = (item.get('rows') or [])[:self._room(height)]
        if not rows:
            return self._muted(x, top - 12, self.env._('Nothing to show.'))
        y = top - 10
        self.canvas.setFont(FONT, 7.5)
        for index, row in enumerate(rows, start=1):
            self.canvas.setFillColor(MUTED)
            self.canvas.drawString(x, y, '%s' % index)
            self.canvas.setFillColor(INK)
            self.canvas.drawString(x + 14, y, self._fit(row.get('label') or '', FONT, 7.5, width * 0.62))
            if item.get('measure'):
                self.canvas.drawRightString(x + width, y, self._format(row.get('value') or 0, item))
            y -= 11
        self._more(item.get('rows') or [], rows, x, y, width)

    def _paragraph(self, text, x, top, width, height):
        y = top - 9
        self.canvas.setFillColor(INK)
        self.canvas.setFont(FONT, 8)
        for line in self._wrap(re.sub(r'<[^>]+>', ' ', text), FONT, 8, width)[:self._room(height)]:
            self.canvas.drawString(x, y, line)
            y -= 10

    # ------------------------------------------------------------------
    # Small helpers
    # ------------------------------------------------------------------
    def _more(self, everything, shown, x, y, width):
        rest = len(everything) - len(shown)
        if rest > 0:
            self._muted(x, y, self.env._('and %s more', rest), size=7)

    def _muted(self, x, y, text, size=7.5):
        self.canvas.setFillColor(MUTED)
        self.canvas.setFont(FONT, size)
        self.canvas.drawString(x, y, _safe(text, FONT))

    def _room(self, height):
        return max(1, min(MAX_BODY_ROWS, int((height - 6) // 11)))

    def _value(self, item):
        # A ratio card shows its share, the way it does on screen.
        if item.get('as_ratio') and item.get('ratio') is not None:
            return '%s%%' % item['ratio']
        return '%s%s' % (item.get('prefix') or '', self._format(item.get('value') or 0, item))

    def _format(self, value, item):
        """The number as the card shows it: its digits, its system, its symbol."""
        digits = 0 if item.get('aggregate') == 'count' and item.get('kind') not in ('formula', 'scatter') \
            else int(item.get('digits') or 0)
        try:
            number = float(value)
        except (TypeError, ValueError):
            return str(value)
        system = item.get('number_system') or 'auto'
        size = abs(number)
        if system == 'indian' and size >= 1e7:
            text = '{:,.2f} Cr'.format(number / 1e7)
        elif system == 'indian' and size >= 1e5:
            text = '{:,.2f} L'.format(number / 1e5)
        elif system in ('short', 'auto') and size >= 1e9:
            text = '{:,.1f}B'.format(number / 1e9)
        elif system in ('short', 'auto') and size >= 1e6:
            text = '{:,.1f}M'.format(number / 1e6)
        elif system == 'short' and size >= 1e3:
            text = '{:,.1f}k'.format(number / 1e3)
        elif system == 'auto' and size >= 1e5:
            text = '{:,.1f}k'.format(number / 1e3)
        else:
            text = '{:,.{d}f}'.format(number, d=digits)
        symbol = item.get('symbol') or ''
        return '%s%s' % (text, symbol)

    def _fit(self, text, font, size, width):
        """Cut a string to the width it has, with an ellipsis if it was cut."""
        text = _safe(text, font)
        if pdfmetrics.stringWidth(text, font, size) <= width:
            return text
        ellipsis = '…' if font.startswith('Dashboard') else '...'
        while text and pdfmetrics.stringWidth(text + ellipsis, font, size) > width:
            text = text[:-1]
        return text + ellipsis if text else ''

    def _wrap(self, text, font, size, width):
        lines, line = [], ''
        for word in _safe(text, font).split():
            candidate = ('%s %s' % (line, word)).strip()
            if pdfmetrics.stringWidth(candidate, font, size) > width and line:
                lines.append(line)
                line = word
            else:
                line = candidate
        if line:
            lines.append(line)
        return lines


class DashboardBoardPdf(models.Model):
    _inherit = 'dashboard.board'

    def download_pdf(self, images=None, period=None, focus=None, mine=False, item_ids=None):
        """This dashboard as a PDF file, ready to download.

        ``images`` maps a card id to the PNG data URL of its chart, captured
        from the reader's own screen; cards without one are drawn from their
        numbers. ``item_ids`` narrows the file to some cards - one card, from
        its own menu, gets a page of its own.
        """
        self.ensure_one()
        name, pdf = self._build_pdf(images=images, period=period, focus=focus,
                                    mine=mine, item_ids=item_ids)
        attachment = self.env['ir.attachment'].create({
            'name': name,
            'type': 'binary',
            'datas': base64.b64encode(pdf),
            'mimetype': 'application/pdf',
            'res_model': 'dashboard.board',
            'res_id': self.id,
        })
        return {
            'type': 'ir.actions.act_url',
            'url': '/web/content/%s?download=true' % attachment.id,
            'target': 'self',
        }

    def _build_pdf(self, images=None, period=None, focus=None, mine=False, item_ids=None):
        """``(file name, bytes)`` of this board as a PDF.

        Split out of :meth:`download_pdf` because the digest needs the file
        without a download: no browser is involved there, so no chart images
        either - those cards print their numbers instead.
        """
        self.ensure_one()
        if pdf_canvas is None:
            raise UserError(self.env._(
                'This server has no reportlab library, so it cannot build a PDF. '
                'Odoo lists it among its own requirements: install it with '
                '"pip install reportlab" and restart the service.'))
        data = self.read_board(self.id, period=period, focus=focus, mine=mine)
        items = [item for item in data['items'] if not item.get('hidden')]
        if item_ids:
            wanted = {int(one) for one in item_ids}
            items = [item for item in items if item['id'] in wanted]
        if not items:
            raise UserError(self.env._('There is nothing to print on this dashboard.'))
        board = dict(data['board'], mine=data.get('mine'))
        focus_data = data.get('focus')
        if isinstance(focus_data, dict) and focus_data.get('label'):
            board['focus_label'] = '%s: %s' % (
                focus_data.get('field_label') or focus_data.get('field') or self.env._('Focus'),
                focus_data['label'])
        single = len(items) == 1
        pdf = DashboardPdf(self.env, board, items, images=images, single=single).build()
        stem = re.sub(r'[^A-Za-z0-9_-]+', '_',
                      items[0]['name'] if single else (self.name or 'dashboard')).strip('_')
        return '%s.pdf' % (stem or 'dashboard'), pdf
