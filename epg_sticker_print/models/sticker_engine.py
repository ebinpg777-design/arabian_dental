# -*- coding: utf-8 -*-
"""Turns orders + options into a list of stickers; renderers only lay them out.

A sticker is a plain dict (title, address lines, phone, route, refs, lines, patient,
barcode data-URI...). Barcodes are generated here as PNG data URIs with Odoo's own
barcode engine, so the PDF never has to fetch an image over HTTP - the classic reason
labels come out blank on a server that cannot reach its own URL.
"""
import base64
import os

from odoo import _, api, models

MODES = [
    ('customer', 'One sticker per customer'),
    ('order', 'One sticker per order'),
    ('route', 'One sticker per sales route'),
]
BARCODES = [('code128', 'Barcode (Code 128)'), ('qr', 'QR code'), ('none', 'None')]


class StickerEngine(models.AbstractModel):
    _name = 'epg.sticker.engine'
    _description = 'Sticker Engine'

    @api.model
    def default_options(self):
        return {'mode': 'customer', 'barcode_type': 'code128', 'copies': 1,
                'show_route': True, 'show_phone': True, 'show_patient': True,
                'show_lines': True, 'show_sender': False, 'max_lines': 4}

    @api.model
    def barcode_data_uri(self, barcode_type, value, width, height):
        if not value or barcode_type == 'none':
            return False
        symbology = 'QR' if barcode_type == 'qr' else 'Code128'
        try:
            png = self.env['ir.actions.report'].barcode(symbology, value, width=width, height=height,
                                                        humanreadable=0, quiet=1)
        except Exception:  # noqa: BLE001 - a bad value must not kill the print
            return False
        return 'data:image/png;base64,' + base64.b64encode(png).decode()

    def _address_lines(self, partner):
        """Address parts, tidied so they wrap: the migrated data has "GOLD,MK SQUARE,,"
        with no space after the commas, which the browser treats as one unbreakable word
        and throws a whole line for. Normalise to "GOLD, MK SQUARE" before measuring."""
        import re
        parts = [partner.street, partner.street2,
                 ' '.join(x for x in (partner.city, partner.state_id.name, partner.zip) if x),
                 partner.country_id.name if partner.country_id and partner.country_id != self.env.company.country_id else '']
        clean = []
        for p in parts:
            if not p:
                continue
            p = re.sub(r'\s*,\s*', ', ', p)          # one space after each comma
            p = re.sub(r'(, )+', ', ', p).strip(' ,')  # collapse ",," and trailing commas
            p = re.sub(r'\s{2,}', ' ', p)
            if p:
                clean.append(p)
        return clean

    @api.model
    def _order_lines(self, order, limit):
        rows = []
        for line in order.order_line.filtered(lambda l: not l.display_type and not getattr(l, 'is_urgent_service', False)):
            ul = ''
            if 'ul' in line._fields and line.ul:
                ul = dict(line._fields['ul']._description_selection(self.env)).get(line.ul, line.ul)
            colour = line.color_scheme.name if 'color_scheme' in line._fields and line.color_scheme else ''
            qty = line.product_uom_qty
            # 36.0 reads as clutter on a label; whole quantities print as integers
            qty = int(qty) if float(qty).is_integer() else qty
            rows.append({'product': line.product_id.display_name, 'ul': ul, 'colour': colour,
                         'qty': qty if qty != 1 else None})
        more = max(len(rows) - limit, 0)
        return rows[:limit], more

    @api.model
    def build(self, orders, options, fmt=None):
        """Return the list of sticker dicts (copies already expanded)."""
        opts = dict(self.default_options(), **(options or {}))
        opts['_format'] = fmt or opts.get('_format')
        mode = opts['mode']
        stickers = []
        company = orders[:1].company_id or self.env.company
        sender = company.name if opts.get('show_sender') else ''
        bc = lambda value: self.barcode_data_uri(opts['barcode_type'], value, 900, 180 if opts['barcode_type'] != 'qr' else 600)  # noqa: E731

        if mode == 'customer':
            for partner in orders.mapped('partner_id').sorted('display_name'):
                porders = orders.filtered(lambda o: o.partner_id == partner).sorted('name')
                refs = ', '.join(porders.mapped('name'))
                patients = ', '.join(p for p in porders.mapped('patient') if p) if 'patient' in orders._fields else ''
                stickers.append({
                    'kind': 'customer', 'title': _('Destination'), 'name': partner.name,
                    'address': self._address_lines(partner),
                    'phone': partner.phone if opts['show_phone'] else '',
                    'route': (porders[:1].team_id.name or '') if opts['show_route'] else '',
                    'refs': refs, 'count': len(porders), 'patient': patients if opts['show_patient'] else '',
                    'lines': [], 'more': 0, 'barcode': bc(refs), 'sender': sender, 'to_patient': False, 'clinic': '',
                })
        elif mode == 'route':
            for team in orders.mapped('team_id').sorted('name'):
                torders = orders.filtered(lambda o: o.team_id == team).sorted('name')
                refs = ', '.join(torders.mapped('name'))
                stickers.append({
                    'kind': 'route', 'title': _('Sales Route'), 'name': team.name,
                    'address': [l for l in (team.address or '').splitlines() if l.strip()],
                    'phone': (team.phone or '') if opts['show_phone'] else '',
                    'route': team.name if opts['show_route'] else '',
                    'refs': refs, 'count': len(torders), 'patient': '', 'lines': [], 'more': 0,
                    'barcode': bc(refs), 'sender': sender, 'to_patient': False, 'clinic': '',
                })
            no_team = orders.filtered(lambda o: not o.team_id)
            if no_team:
                refs = ', '.join(no_team.sorted('name').mapped('name'))
                stickers.append({'kind': 'route', 'title': _('Sales Route'), 'name': _('(no route)'),
                                 'address': [], 'phone': '', 'route': '', 'refs': refs, 'count': len(no_team),
                                 'patient': '', 'lines': [], 'more': 0, 'barcode': bc(refs), 'sender': sender, 'to_patient': False, 'clinic': ''})
        else:  # order
            for order in orders.sorted('name'):
                partner = order.partner_id
                lines, more = self._order_lines(order, opts.get('max_lines', 6)) if opts['show_lines'] else ([], 0)
                # A case going to the patient carries the patient's address and name,
                # not the clinic's: that is what the courier reads off the label.
                # (client, 2026-08-21)
                to_patient = (order.lab_delivery_address_lines()
                              if hasattr(order, 'lab_delivery_address_lines') else [])
                # A custom address names nobody: no doctor, no patient, no clinic.
                # (client, 2026-08-28)
                anonymous = (order.lab_delivery_is_anonymous()
                             if hasattr(order, 'lab_delivery_is_anonymous') else False)
                stickers.append({
                    'kind': 'order', 'title': order.name,
                    'name': '' if anonymous else (
                        (order.patient or partner.name) if to_patient else partner.name),
                    'address': to_patient or self._address_lines(partner),
                    'to_patient': bool(to_patient) and not anonymous,
                    'phone': (partner.phone or '') if opts['show_phone'] else '',
                    'route': (order.team_id.name or '') if opts['show_route'] else '',
                    'clinic': '' if anonymous else (partner.name if to_patient else ''),
                    'refs': order.name, 'count': 1,
                    'patient': '' if anonymous else (
                        (order.patient or '') if opts['show_patient']
                        and 'patient' in order._fields else ''),
                    'lines': lines, 'more': more, 'barcode': bc(order.name), 'sender': sender,
                    'priority': dict(order._fields['priority']._description_selection(self.env)).get(order.priority, '')
                    if 'priority' in order._fields and order.priority not in (False, 'normal') else '',
                })
        # each sticker carries the type sizes that fit ITS content in the label's bands,
        # and which kind of code it carries (a QR is square and is not rotated)
        for sticker in stickers:
            sticker['barcode_type'] = opts['barcode_type']
            sticker['fit'] = self.fit_fonts(sticker, opts.get('_format'))
        copies = max(int(opts.get('copies') or 1), 1)
        if copies > 1:
            stickers = [s for s in stickers for _i in range(copies)]
        return stickers

    @api.model
    def build_from_partners(self, partners, options, fmt=None):
        """Stickers straight from the clinics themselves - no orders needed.

        For 'One sticker per customer' / 'One sticker per sales route' the counter
        sometimes wants labels for a batch of clinics, or for a whole route, with
        nothing booked yet to point at. Address, phone and route all live on the
        partner - route through `team_id` (or the commercial partner's, for a
        contact booked under its parent clinic), the same field an order takes its
        own route from - so no order is required to print them.

        'One sticker per order' still needs real orders: there is no order number,
        product or patient to put on the label without one, so that mode is not
        offered here and the wizard keeps requiring orders for it.

        No barcode: a barcode on these stickers would encode order references, and
        there are none - printing one anyway would look like a real case code to
        scan against, which it is not. (client, 2026-08-29)
        """
        opts = dict(self.default_options(), **(options or {}))
        opts['_format'] = fmt or opts.get('_format')
        mode = opts['mode']
        stickers = []
        sender = self.env.company.name if opts.get('show_sender') else ''

        def _route(partner):
            return partner.team_id or partner.commercial_partner_id.team_id

        if mode == 'customer':
            for partner in partners.sorted('display_name'):
                stickers.append({
                    'kind': 'customer', 'title': _('Destination'), 'name': partner.name,
                    'address': self._address_lines(partner),
                    'phone': partner.phone if opts['show_phone'] else '',
                    'route': (_route(partner).name or '') if opts['show_route'] else '',
                    'refs': '', 'count': 0, 'patient': '', 'lines': [], 'more': 0,
                    'barcode': False, 'sender': sender, 'to_patient': False, 'clinic': '',
                })
        elif mode == 'route':
            by_team = {}
            for partner in partners:
                team = _route(partner)
                by_team.setdefault(team, self.env['res.partner'])
                by_team[team] |= partner
            named = sorted((t for t in by_team if t), key=lambda t: t.name or '')
            for team in named:
                stickers.append({
                    'kind': 'route', 'title': _('Sales Route'), 'name': team.name,
                    'address': [l for l in (team.address or '').splitlines() if l.strip()],
                    'phone': (team.phone or '') if opts['show_phone'] else '',
                    'route': team.name if opts['show_route'] else '',
                    'refs': '', 'count': 0, 'patient': '', 'lines': [], 'more': 0,
                    'barcode': False, 'sender': sender, 'to_patient': False, 'clinic': '',
                })
            no_team = by_team.get(self.env['crm.team'])
            if no_team:
                stickers.append({
                    'kind': 'route', 'title': _('Sales Route'), 'name': _('(no route)'),
                    'address': [], 'phone': '', 'route': '', 'refs': '', 'count': 0,
                    'patient': '', 'lines': [], 'more': 0, 'barcode': False,
                    'sender': sender, 'to_patient': False, 'clinic': '',
                })
        # mode == 'order' falls through with nothing built - the wizard never calls
        # this method for that mode, but an empty result is the honest answer if it did.

        for sticker in stickers:
            sticker['barcode_type'] = opts['barcode_type']
            sticker['fit'] = self.fit_fonts(sticker, opts.get('_format'))
        copies = max(int(opts.get('copies') or 1), 1)
        if copies > 1:
            stickers = [s for s in stickers for _i in range(copies)]
        return stickers

    # ------------------------------------------------------------------ geometry
    PT_MM = 0.3528

    @api.model
    def zones(self, fmt, has_code=True, is_qr=False):
        """Geometry of the label, in mm.

        Two columns (client, 2026-08-19): everything readable on the LEFT, running the
        full height of the label so the type can be large, and the barcode turned on its
        side down the RIGHT edge, where it takes a narrow strip instead of a band across
        the bottom. A little extra air on the left (`lead`) keeps the text off the cut
        edge of the roll.
        """
        w = fmt.width_mm if fmt else 100.0
        h = fmt.height_mm if fmt else 60.0
        # Rolls (one label per page): the label div must be a hair SHORTER than the
        # page. At 96 dpi 60 mm is 226.77 px and wkhtmltopdf rounds the box up to
        # 227 px — taller than the page — so every label pushed a blank second page
        # (2026-08-20). 0.4 mm off the box is invisible on the roll and keeps the
        # content on one page. Sheet formats keep the exact pitch: their labels are
        # pre-cut and the grid must not drift.
        if fmt and fmt.columns * fmt.rows == 1:
            h = h - 0.4
        margin = fmt.margin_mm if fmt else 2.0
        scale = min(w / 100.0, h / 60.0)
        lead = round(max(2.5 * scale, 1.5), 1)                # blank space before the text
        vmargin = round(min(margin, 1.2), 1)                  # thermal rolls need little top/bottom
        inner_h = max(h - 2 * vmargin, 6.0)
        # the rotated code: its bar height becomes the strip's WIDTH. It sits at the
        # very right edge of the label (client, 2026-08-19): a thin strip, no gap on
        # its outside, only a small gap between it and the text.
        if has_code and is_qr:
            # a QR must be large to scan from a hand-held: a square of ~70 % of the
            # label height (client, 2026-08-19: "increase the size of the QR code")
            code_w = round(min(max(inner_h * 0.55, 14.0), 31.0), 1)
        else:
            code_w = round(min(max(h * 0.15, 6.0), 9.0), 1) if has_code else 0.0
        code_gap = 1.5 if has_code else 0.0
        # "a little space on the right is enough" (client, 2026-08-19): 1 mm past the code
        right_pad = 1.0
        text_w = max(w - margin - right_pad - lead - code_w - code_gap, 10.0)
        head = max(round(3.6 * scale, 1), 2.8)
        rest = max(inner_h - head, 4.0)
        body = round(rest * 0.58, 1)          # name + address (fit_fonts rebalances)
        meta = round(rest - body, 1)          # phone · route · patient · work lines
        return {'width': w, 'height': h, 'margin': margin, 'vmargin': vmargin, 'lead': lead,
                'right_pad': right_pad,
                'inner_w': round(text_w, 1), 'inner_h': round(inner_h, 1),
                'head': head, 'body': body, 'meta_h': meta,
                'code': code_w, 'code_gap': code_gap,
                # the rotated image: long side = label height, short side = strip width
                'barcode_len': round(inner_h - 1.0, 1), 'barcode_h': code_w,
                'scale': scale}

    # ------------------------------------------------------------------ measuring
    # Real font metrics, not a characters-per-line guess. wkhtmltopdf prints
    # "Helvetica" with Nimbus Sans / Liberation Sans (metric-compatible with Helvetica),
    # and PIL can read those same files, so the engine wraps text exactly the way the
    # PDF will. The tables are cached per process; without a font the old estimate
    # (0.57 em per glyph) is the fallback.
    _FONT_FILES = {
        False: ('/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
                '/usr/share/fonts/opentype/urw-base35/NimbusSans-Regular.otf',
                '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'),
        True: ('/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf',
               '/usr/share/fonts/opentype/urw-base35/NimbusSans-Bold.otf',
               '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'),
    }
    _fonts = {}

    @classmethod
    def _font(cls, bold):
        if bold not in cls._fonts:
            cls._fonts[bold] = None
            try:
                from PIL import ImageFont
                for path in cls._FONT_FILES[bold]:
                    if os.path.exists(path):
                        cls._fonts[bold] = ImageFont.truetype(path, 100)   # 100 px em
                        break
            except Exception:                                              # noqa: BLE001
                pass
        return cls._fonts[bold]

    @api.model
    def _text_mm(self, text, pt, bold=False):
        font = self._font(bold)
        if font is not None:
            return font.getlength(text) / 100.0 * pt * self.PT_MM
        return len(text) * 0.57 * pt * self.PT_MM

    @api.model
    def _lines_for(self, text, pt, width_mm, bold=False):
        """How many printed lines `text` needs at `pt` in `width_mm` — word-wrapped the
        way the browser wraps, measured with the print font."""
        if not text:
            return 0
        lines, cur = 1, 0.0
        space = self._text_mm(' ', pt, bold)
        for word in text.split():
            wmm = self._text_mm(word, pt, bold)
            if cur and cur + space + wmm > width_mm:
                lines += 1
                cur = wmm
            else:
                cur = cur + space + wmm if cur else wmm
            while cur > width_mm + 0.01:        # a single word longer than the line
                lines += 1
                cur -= width_mm
        return lines

    @api.model
    def fit_fonts(self, sticker, fmt):
        """Largest type from a fixed ladder at which EVERYTHING fits the label.

        The name, the address and the detail lines are sized together, against the whole
        usable height (head band aside): one ladder step gives name = 1.2 × address and
        details = 0.9 × address, and the first step where the measured total fits is
        taken. Then the body / meta bands are set to what each part actually needs, so
        nothing can clip and nothing can overlap. Deterministic: the same content always
        prints at the same size.
        """
        z = self.zones(fmt, has_code=bool(sticker.get('barcode') or sticker.get('refs')),
                       is_qr=sticker.get('barcode_type') == 'qr')
        w, PT_MM = z['inner_w'], self.PT_MM
        avail = z['body'] + z['meta_h']                     # everything under the head line
        address_text = ', '.join(sticker['address'])
        meta_parts = []
        if sticker['phone']:
            meta_parts.append('Ph %s' % sticker['phone'])
        if sticker['route']:
            meta_parts.append('Route %s' % sticker['route'])
        if sticker['patient'] and not sticker.get('to_patient'):
            meta_parts.append('Patient %s' % sticker['patient'])
        if sticker.get('clinic'):
            meta_parts.append('From clinic %s' % sticker['clinic'])
        meta_texts = [' | '.join(meta_parts)] if meta_parts else []
        work = ' | '.join('%s%s%s%s' % (
            row['product'], ' [%s]' % row['ul'] if row['ul'] else '',
            ' %s' % row['colour'] if row['colour'] else '',
            ' x%s' % row['qty'] if row['qty'] else '') for row in sticker['lines'])
        if work:
            meta_texts.append(work + (' + %s more' % sticker['more'] if sticker['more'] else ''))

        def needs(addr_pt):
            name_pt, meta_pt = addr_pt * 1.15, addr_pt
            # line boxes as the template sets them: name 1.1, address 1.18, details 1.25
            body = (self._lines_for(sticker['name'], name_pt, w, bold=True) * name_pt * PT_MM * 1.08
                    + self._lines_for(address_text, addr_pt, w) * addr_pt * PT_MM * 1.12)
            meta = sum(self._lines_for(t, meta_pt, w) for t in meta_texts) * meta_pt * PT_MM * 1.15
            gap = addr_pt * PT_MM * 0.25 if meta_texts else 0.0       # air between the two
            return body, meta, gap

        # A cushion, not the bare estimate: this is a MEASUREMENT of what wkhtmltopdf
        # will render, not the render itself, and a step that clears `avail` by a
        # fraction of a millimetre clears it in the estimate but not on the label -
        # one real case (a long, comma-heavy address with no barcode column to narrow
        # it, wrapping to five lines) rendered the address's last line on top of the
        # phone/route line beneath it. 3% keeps virtually every ordinary sticker at
        # its usual size (their margin was already generous) while pushing the
        # knife-edge cases to the next size down, which typically has real headroom
        # rather than another sliver. (client, 2026-08-29)
        avail_safe = avail * 0.97
        chosen = None
        for addr_pt in (26.0, 25.0, 24.0, 23.0, 22.0, 21.0, 20.0, 19.0, 18.0, 17.0, 16.0, 15.5,
                        15.0, 14.5, 14.0, 13.5, 13.0, 12.5, 12.0, 11.5, 11.0, 10.5, 10.0, 9.5,
                        9.0, 8.5, 8.0, 7.5, 7.0):
            body, meta, gap = needs(addr_pt)
            if body + gap + meta <= avail_safe:
                chosen = addr_pt
                break
        addr_pt = chosen or 7.0
        body, meta, gap = needs(addr_pt)
        z['body'] = round(min(body + gap, avail), 1)
        z['meta_h'] = round(max(avail - z['body'], 0.0), 1)
        small = round(max(addr_pt * 0.62, 7.0), 1)
        head = round(small * PT_MM * 1.25, 1)
        if head > z['head']:                 # take the extra from the bands below
            extra = head - z['head']
            z['head'] = head
            z['meta_h'] = round(max(z['meta_h'] - extra, 0.0), 1)
        return dict(z, name=round(addr_pt * 1.15, 1), address=round(addr_pt, 1),
                    meta=round(addr_pt, 1), small=small)

    @api.model
    def layout_vars(self, fmt):
        """Band geometry for the page (per-sticker type comes from `sticker['fit']`)."""
        z = self.zones(fmt)
        z['per_page'] = max((fmt.columns * fmt.rows) if fmt else 1, 1)
        z['columns'] = (fmt.columns if fmt else 1)
        z['gap'] = (fmt.gap_mm if fmt else 0.0)
        return z
