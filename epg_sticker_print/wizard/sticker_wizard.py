# -*- coding: utf-8 -*-
import os
import json
import logging
import base64
from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..models.sticker_engine import BARCODES, MODES


_logger = logging.getLogger(__name__)

# The preview shows every sticker of the run up to this many - the whole run of a
# morning's parcels, not just the first label (client, 2026-10-14: "print sticker
# should show the previews of multiple selected orders"). Past it, Preview all.
PREVIEW_MAX = 20


class StickerPrintWizard(models.TransientModel):
    _name = 'epg.sticker.print.wizard'
    _description = 'Print Stickers'

    def _compute_display_name(self):
        # This wizard is also a full PAGE (the Sales > Orders menu), and a transient
        # model's default display name is 'model,id' - which is what the breadcrumb
        # would say. Name it for the person standing in it.
        for wiz in self:
            wiz.display_name = _('Print Stickers')

    # The counter works doctor-first: it knows whose parcels are going out today long
    # before it knows which order numbers they are. So the doctors are chosen first and
    # the order picker narrows to them, instead of hunting order numbers out of a list
    # of 24,000. (client, 2026-08-28)
    partner_ids = fields.Many2many(
        'res.partner', string='Doctors / Clinics',
        default=lambda self: self._default_partners(),
        help="Pick the clinics going out. The order list below then only offers their "
             "orders. Leave empty to search every order.")
    order_ids = fields.Many2many('sale.order', string='Orders', default=lambda self: self._default_orders())
    # A domain carried in a field, not written in the view: the view cannot express
    # "these partners and their contacts" on its own.
    order_domain = fields.Char(compute='_compute_order_choice')
    available_count = fields.Integer(compute='_compute_order_choice')
    mode = fields.Selection(MODES, string='One sticker per', required=True,
                            default=lambda self: self._default_mode())
    format_id = fields.Many2one('epg.sticker.format', string='Label Format', required=True,
                                default=lambda self: self.env['epg.sticker.format'].get_default())
    copies = fields.Integer(default=1, required=True)
    barcode_type = fields.Selection(BARCODES, string='Code', default='code128', required=True)
    # What starts ticked is what the lab prints on nearly every run, so nobody
    # unticks boxes one at a time first: the patient and the work lines - the
    # sticker goes on the case (client, 2026-10-20; on 2026-09-12, when it was a
    # despatch label, only the phone started on). The rest are there on demand.
    show_route = fields.Boolean(string='Sales route', default=False)
    show_phone = fields.Boolean(string='Phone', default=False)
    show_patient = fields.Boolean(string='Patient', default=True)
    show_lines = fields.Boolean(string='Work lines (order stickers)', default=True)
    show_sender = fields.Boolean(string='Sender (company)', default=False)
    sticker_count = fields.Integer(compute='_compute_preview')
    preview_html = fields.Html(compute='_compute_preview', sanitize=False)
    format_hint = fields.Char(compute='_compute_preview')

    # The sheet in the tray (client, 2026-10-19: an A4 of 3 x 8 labels in an ordinary
    # printer, most of them already peeled off). The grid widget on `sheet_cells`
    # shows the sheet; the cells marked are gone and the run fills the rest, in
    # reading order, then fresh sheets. Pre-filled from what the last print took
    # (epg.sticker.sheet), so the counter usually has nothing to click.
    format_is_sheet = fields.Boolean(related='format_id.is_sheet')
    format_columns = fields.Integer(related='format_id.columns')
    format_rows = fields.Integer(related='format_id.rows')
    format_width_mm = fields.Float(related='format_id.width_mm')
    format_height_mm = fields.Float(related='format_id.height_mm')
    sheet_cells = fields.Char(
        string='Sheet in the tray', compute='_compute_sheet_cells', store=True, readonly=False,
        help="The labels already peeled off the sheet in the tray. Click a cell to mark it.")

    @api.depends('format_id')
    def _compute_sheet_cells(self):
        Sheet = self.env['epg.sticker.sheet']
        for wiz in self:
            wiz.sheet_cells = json.dumps(Sheet.cells_for(wiz.format_id))

    def _skip_cells(self):
        """The tray sheet's used cells as a list, empty for a roll."""
        self.ensure_one()
        if not self.format_id.is_sheet:
            return []
        return self.env['epg.sticker.sheet'].parse(self.sheet_cells, self.format_id.columns * self.format_id.rows)

    @api.model
    def _default_partners(self):
        """Chosen partners, or the partners behind whatever the wizard was opened on."""
        ctx = self.env.context
        if ctx.get('active_model') == 'res.partner':
            return self.env['res.partner'].browse(ctx.get('active_ids') or []).exists().ids
        orders = self.env['sale.order'].browse(self._default_orders())
        if not orders and ctx.get('active_model') == 'account.move':
            # an invoice billed without an order still has a clinic to send to
            return self._invoices_from_context().partner_id.ids
        return orders.partner_id.ids

    @api.model
    def _default_mode(self):
        """One sticker per order (client, 2026-10-20) - except where the wizard opens
        on clinics with no order behind them (Contacts, or an invoice billed without
        an order): one-per-order needs an order for its number, product and patient,
        so it would have nothing to print there, and the clinic label is the point."""
        if not self._default_orders() and self._default_partners():
            return 'customer'
        return 'order'

    @api.model
    def _default_orders(self):
        ctx = self.env.context
        if ctx.get('active_model') == 'sale.order':
            ids = ctx.get('active_ids') or ([ctx['active_id']] if ctx.get('active_id') else [])
            return self.env['sale.order'].browse(ids).exists().ids
        if ctx.get('active_model') == 'stock.picking':
            return self.env['stock.picking'].browse(ctx.get('active_ids') or []).sale_id.ids
        if ctx.get('active_model') == 'account.move':
            # an invoice's parcels are the orders it bills (client, 2026-10-14: "print
            # sticker in the invoices' action, in accounting")
            return self._invoices_from_context().invoice_line_ids.sale_line_ids.order_id.filtered(
                lambda o: o.state != 'cancel').ids
        return []

    @api.model
    def _invoices_from_context(self):
        ctx = self.env.context
        ids = ctx.get('active_ids') or ([ctx['active_id']] if ctx.get('active_id') else [])
        return self.env['account.move'].browse(ids).exists()

    # ------------------------------------------------------------ choosing orders
    @api.depends('partner_ids', 'order_ids')
    def _compute_order_choice(self):
        Order = self.env['sale.order']
        for wiz in self:
            domain = [('state', '!=', 'cancel')]
            if wiz.partner_ids:
                # child_of, not in: a clinic's orders are often booked against one of
                # its contacts, and the counter thinks of those as the same clinic.
                domain = [('partner_id', 'child_of', wiz.partner_ids.ids)] + domain
            wiz.order_domain = str(domain)
            wiz.available_count = Order.search_count(
                domain + [('id', 'not in', wiz.order_ids.ids)]) if wiz.partner_ids else 0

    @api.onchange('partner_ids')
    def _onchange_partner_ids(self):
        """Drop orders that no longer belong to anybody selected.

        Removing a doctor has to remove their parcels too - a sticker printed for a
        clinic the counter just took off the list is a parcel on the wrong van.
        """
        if not self.partner_ids:
            return
        allowed = self.env['res.partner'].search([('id', 'child_of', self.partner_ids.ids)])
        self.order_ids = self.order_ids.filtered(lambda o: o.partner_id.id in allowed.ids)

    def action_add_partner_orders(self):
        """Every open order of the chosen clinics, in one press."""
        self.ensure_one()
        if not self.partner_ids:
            raise UserError(_("Choose at least one doctor or clinic first."))
        found = self.env['sale.order'].search(
            [('partner_id', 'child_of', self.partner_ids.ids), ('state', '!=', 'cancel')])
        self.order_ids = found
        return self._reopen()

    def action_clear_orders(self):
        self.ensure_one()
        self.order_ids = [fields.Command.clear()]
        return self._reopen()

    def _reopen(self):
        """Stay on the wizard. A button returning nothing closes the dialog."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'views': [(self.env.ref('epg_sticker_print.view_sticker_print_wizard').id, 'form')],
            'target': self.env.context.get('epg_sticker_fullpage') and 'current' or 'new',
            'context': self.env.context,
        }

    def _options(self):
        self.ensure_one()
        return {
            'mode': self.mode, 'barcode_type': self.barcode_type, 'copies': self.copies,
            'show_route': self.show_route, 'show_phone': self.show_phone, 'show_patient': self.show_patient,
            'show_lines': self.show_lines, 'show_sender': self.show_sender,
        }

    def _can_print_from_partners(self):
        """True where the clinics themselves are enough - no orders needed.

        'One sticker per customer' / 'One sticker per sales route' only ever put
        the clinic's own address, phone and route on the label - all of which
        live on the partner. 'One sticker per order' needs an order for its
        number, product and patient, so it is excluded here on purpose.
        (client, 2026-08-29)
        """
        self.ensure_one()
        return self.mode != 'order' and bool(self.partner_ids)

    def _build_now(self, copies_override=None):
        """Stickers for this wizard's current choices - orders if any are picked,
        the clinics themselves otherwise (customer / route modes only)."""
        self.ensure_one()
        Engine = self.env['epg.sticker.engine']
        opts = dict(self._options())
        if copies_override is not None:
            opts['copies'] = copies_override
        if self.order_ids:
            return Engine.build(self.order_ids, opts, fmt=self.format_id)
        if self._can_print_from_partners():
            return Engine.build_from_partners(self.partner_ids, opts, fmt=self.format_id)
        return []

    @api.depends('order_ids', 'partner_ids', 'mode', 'format_id', 'copies', 'barcode_type',
                 'show_route', 'show_phone', 'show_patient', 'show_lines', 'show_sender')
    def _compute_preview(self):
        for wiz in self:
            fmt = wiz.format_id
            if fmt and fmt.is_sheet:
                wiz.format_hint = _('%(w)s x %(h)s mm, %(n)s per sheet (%(c)s x %(r)s)', w=fmt.width_mm,
                                    h=fmt.height_mm, n=fmt.columns * fmt.rows, c=fmt.columns, r=fmt.rows)
            else:
                wiz.format_hint = fmt and _('%(w)s x %(h)s mm, 1 per page', w=fmt.width_mm, h=fmt.height_mm) or ''
            if not wiz.order_ids and not wiz._can_print_from_partners():
                wiz.sticker_count = 0
                wiz.preview_html = Markup(
                    '<div class="text-muted fst-italic">Select at least one order, or pick the '
                    'clinics for a customer / route label, to see the sticker.</div>')
                continue
            stickers = wiz._build_now(copies_override=1)
            wiz.sticker_count = len(stickers) * max(wiz.copies or 1, 1)
            if not stickers:
                wiz.preview_html = Markup('<div class="text-muted fst-italic">Nothing to print.</div>')
                continue
            wiz.preview_html = wiz._preview_markup(stickers, fmt)

    def _preview_markup(self, stickers, fmt):
        """The preview IS the print: the run's stickers - up to PREVIEW_MAX of them - are
        rendered by wkhtmltopdf exactly as they will be printed, and each label is cut out
        of its page and shown as an image, captioned with whose it is.

        Drawing the same template straight into the form looked different — the web
        client's fonts and line heights are not wkhtmltopdf's, and mm boxes scale with
        the screen DPI — so the counter saw one layout on screen and another on the roll
        (client, 2026-08-19). Falls back to the HTML rendering if the PDF or the
        rasteriser is unavailable.
        """
        self.ensure_one()
        Engine = self.env['epg.sticker.engine']
        total = len(stickers)
        shown = stickers[:PREVIEW_MAX]
        use_partners = not self.order_ids and self._can_print_from_partners()
        try:
            report = self.env.ref('epg_sticker_print.action_report_sticker').with_context(
                epg_sticker_paperformat_id=fmt.paperformat_id.id)
            # the same run the print makes, cut to the stickers shown
            data = dict(self._options(), copies=1, format_id=fmt.id, limit=len(shown))
            if use_partners:
                data['partner_ids'] = self.partner_ids.ids
                pdf = report._render_qweb_pdf(report.report_name, [], data=data)[0]
            else:
                data['ids'] = self.order_ids.ids
                pdf = report._render_qweb_pdf(report.report_name, self.order_ids.ids, data=data)[0]
            pngs = self._label_pngs(pdf, fmt, len(shown))
        except Exception as exc:                                   # noqa: BLE001
            _logger.info("sticker preview: falling back to HTML (%s)", exc)
            pngs = None
        if total == 1:
            head = _('Preview of the sticker — exactly as it prints')
        elif total <= PREVIEW_MAX:
            head = _('Preview of all %s stickers — exactly as they print', total)
        else:
            head = _('The first %(n)s of %(total)s stickers — exactly as they print. Preview all shows every one.',
                     n=len(shown), total=total)
        if pngs and len(pngs) == len(shown):
            # one label at full size; a run as a grid the eye can walk down
            width_px = int(fmt.width_mm * (3.2 if len(pngs) == 1 else 2.2))
            cells = Markup('').join(
                Markup('<figure style="display:inline-block;margin:0 10px 12px 0;vertical-align:top;">'
                       '<div class="epg-sticker-preview" style="border:1px dashed #999;background:#fff;line-height:0;">'
                       '<img src="data:image/png;base64,%s" style="width:%spx;max-width:100%%;'
                       'image-rendering:-webkit-optimize-contrast;"/></div>'
                       '<figcaption class="text-muted small" style="max-width:%spx;white-space:nowrap;overflow:hidden;'
                       'text-overflow:ellipsis;">%s</figcaption></figure>')
                % (base64.b64encode(png).decode(), width_px, width_px, self._caption(i, sticker))
                for i, (png, sticker) in enumerate(zip(pngs, shown)))
            return Markup('<div class="text-muted small mb-2">%s</div><div>%s</div>') % (head, cells)
        layout = Engine.layout_vars(fmt)
        cells = Markup('').join(
            Markup('<figure style="display:inline-block;margin:0 10px 12px 0;vertical-align:top;">%s'
                   '<figcaption class="text-muted small">%s</figcaption></figure>') % (
                Markup(self.env['ir.qweb']._render('epg_sticker_print.sticker_preview_label',
                                                   {'sticker': sticker, 'layout': layout})),
                self._caption(i, sticker))
            for i, sticker in enumerate(shown))
        return Markup('<div class="text-muted small mb-2">%s</div><div>%s</div>') % (head, cells)

    @api.model
    def _caption(self, index, sticker):
        """'3 · SO280137 · DR ONE' - which parcel the label is for."""
        bits = [str(index + 1)]
        if sticker.get('kind') == 'order' and sticker.get('title'):
            bits.append(sticker['title'])
        if sticker.get('name'):
            bits.append(sticker['name'])
        if sticker.get('kind') in ('customer', 'route') and (sticker.get('count') or 0) > 1:
            bits.append(_('%s orders', sticker['count']))
        return ' · '.join(bits)

    @api.model
    def _label_pngs(self, pdf, fmt, count):
        """Every label of the first `count`, cut out of the pages of `pdf` (poppler's
        pdftoppm, then the label grid of the format): a roll is one label a page; a
        sheet holds columns x rows of them, side by side with the gap between columns."""
        import io
        import glob
        import shutil
        import subprocess
        import tempfile
        from PIL import Image
        if not shutil.which('pdftoppm') or count < 1:
            return None
        per_page = max(fmt.columns * fmt.rows, 1)
        pages = -(-count // per_page)
        dpi = 200 if count == 1 else 150
        mm = dpi / 25.4
        left, top = fmt._grid_offsets()
        out = []
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, 's.pdf')
            with open(src, 'wb') as fh:
                fh.write(pdf)
            subprocess.run(['pdftoppm', '-png', '-r', str(dpi), '-f', '1', '-l', str(pages), src, os.path.join(tmp, 'p')],
                           check=True, timeout=60, capture_output=True)
            files = sorted(glob.glob(os.path.join(tmp, 'p-*.png')), key=lambda f: int(f.rsplit('-', 1)[1][:-4]))
            for path in files[:pages]:
                with Image.open(path) as page:
                    for slot in range(per_page):
                        if len(out) == count:
                            break
                        col, row = slot % fmt.columns, slot // fmt.columns
                        x = (left + col * (fmt.width_mm + fmt.gap_mm)) * mm
                        y = (top + row * (fmt.height_mm + fmt.row_gap_mm)) * mm
                        box = tuple(int(round(v)) for v in (x, y, x + fmt.width_mm * mm, y + fmt.height_mm * mm))
                        buf = io.BytesIO()
                        page.crop(box).save(buf, format='PNG', optimize=True)
                        out.append(buf.getvalue())
        return out

    def _report_data(self):
        self.ensure_one()
        data = dict(self._options(), format_id=self.format_id.id)
        skip = self._skip_cells()
        if skip:
            data['skip_cells'] = skip
        if self.order_ids:
            data['ids'] = self.order_ids.ids
        elif self._can_print_from_partners():
            data['ids'] = []
            data['partner_ids'] = self.partner_ids.ids
        else:
            data['ids'] = []
        return data

    def _nothing_to_print_error(self):
        self.ensure_one()
        if self.mode == 'order':
            return UserError(_(
                "Select at least one order — 'One sticker per order' needs a real "
                "order for its number, product and patient."))
        return UserError(_(
            "Choose at least one doctor or clinic, or select their orders."))

    def action_print(self):
        self.ensure_one()
        if not self.order_ids and not self._can_print_from_partners():
            raise self._nothing_to_print_error()
        report = self.env.ref('epg_sticker_print.action_report_sticker')
        action = report.with_context(epg_sticker_paperformat_id=self.format_id.paperformat_id.id).report_action(
            self.order_ids, data=self._report_data(), config=False)
        action['context'] = dict(action.get('context') or {}, epg_sticker_paperformat_id=self.format_id.paperformat_id.id)
        # what this run takes off the tray sheet is what the next run starts from
        self.env['epg.sticker.sheet'].record_print(self.format_id, self._skip_cells(), self.sticker_count)
        return action

    def action_preview_html(self):
        """The full run as HTML in a new tab - check every sticker before feeding labels."""
        self.ensure_one()
        if not self.order_ids and not self._can_print_from_partners():
            raise self._nothing_to_print_error()
        import json
        from urllib.parse import quote
        data = self._report_data()
        ctx = {'epg_sticker_paperformat_id': self.format_id.paperformat_id.id, 'active_ids': self.order_ids.ids}
        url = '/report/html/epg_sticker_print.report_sticker?options=%s&context=%s' % (
            quote(json.dumps(data)), quote(json.dumps(ctx)))
        return {'type': 'ir.actions.act_url', 'url': url, 'target': 'new'}
