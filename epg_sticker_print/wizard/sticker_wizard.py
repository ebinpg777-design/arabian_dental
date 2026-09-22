# -*- coding: utf-8 -*-
import os
import logging
import base64
from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..models.sticker_engine import BARCODES, MODES


_logger = logging.getLogger(__name__)


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
    mode = fields.Selection(MODES, string='One sticker per', required=True, default='customer')
    format_id = fields.Many2one('epg.sticker.format', string='Label Format', required=True,
                                default=lambda self: self.env['epg.sticker.format'].get_default())
    copies = fields.Integer(default=1, required=True)
    barcode_type = fields.Selection(BARCODES, string='Code', default='code128', required=True)
    # A DESPATCH sticker, not a case sheet: the address and the phone are what
    # the courier needs, and everything else printed by default was being
    # unticked one box at a time before every run. Only the phone starts on;
    # the rest are there for whoever wants them. (client, 2026-09-12)
    show_route = fields.Boolean(string='Sales route', default=False)
    show_phone = fields.Boolean(string='Phone', default=True)
    show_patient = fields.Boolean(string='Patient', default=False)
    show_lines = fields.Boolean(string='Work lines (order stickers)', default=False)
    show_sender = fields.Boolean(string='Sender (company)', default=False)
    sticker_count = fields.Integer(compute='_compute_preview')
    preview_html = fields.Html(compute='_compute_preview', sanitize=False)
    format_hint = fields.Char(compute='_compute_preview')

    @api.model
    def _default_partners(self):
        """Chosen partners, or the partners behind whatever the wizard was opened on."""
        ctx = self.env.context
        if ctx.get('active_model') == 'res.partner':
            return self.env['res.partner'].browse(ctx.get('active_ids') or []).exists().ids
        orders = self.env['sale.order'].browse(self._default_orders())
        return orders.partner_id.ids

    @api.model
    def _default_orders(self):
        ctx = self.env.context
        if ctx.get('active_model') == 'sale.order':
            ids = ctx.get('active_ids') or ([ctx['active_id']] if ctx.get('active_id') else [])
            return self.env['sale.order'].browse(ids).exists().ids
        if ctx.get('active_model') == 'stock.picking':
            return self.env['stock.picking'].browse(ctx.get('active_ids') or []).sale_id.ids
        return []

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
            wiz.format_hint = fmt and _('%(w)s x %(h)s mm, %(n)s per page', w=fmt.width_mm, h=fmt.height_mm,
                                        n=fmt.columns * fmt.rows) or ''
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
        """The preview IS the print: the first label is rendered by wkhtmltopdf exactly as
        it will be printed, and the first page of that PDF is shown as an image.

        Drawing the same template straight into the form looked different — the web
        client's fonts and line heights are not wkhtmltopdf's, and mm boxes scale with
        the screen DPI — so the counter saw one layout on screen and another on the roll
        (client, 2026-08-19). Falls back to the HTML rendering if the PDF or the
        rasteriser is unavailable.
        """
        self.ensure_one()
        Engine = self.env['epg.sticker.engine']
        total = len(stickers)
        # A preview of ONE label, rendered the real way, same as the order path
        # always has: the first order, or - with no orders picked - the first
        # clinic. Passing every partner through here would preview correctly
        # too, but one label is the point; the rest are the same layout.
        use_partners = not self.order_ids and self._can_print_from_partners()
        first = self.partner_ids[:1] if use_partners else self.order_ids[:1]
        try:
            report = self.env.ref('epg_sticker_print.action_report_sticker').with_context(
                epg_sticker_paperformat_id=fmt.paperformat_id.id)
            data = dict(self._options(), copies=1, format_id=fmt.id)
            if use_partners:
                data['partner_ids'] = first.ids
                pdf = report._render_qweb_pdf(report.report_name, [], data=data)[0]
            else:
                data['ids'] = first.ids
                pdf = report._render_qweb_pdf(report.report_name, first.ids, data=data)[0]
            png = self._first_page_png(pdf, fmt)
        except Exception as exc:                                   # noqa: BLE001
            _logger.info("sticker preview: falling back to HTML (%s)", exc)
            png = None
        if png:
            width_px = int(fmt.width_mm * 3.2)                        # ~81 dpi on screen
            return Markup(
                '<div class="epg-sticker-preview" style="display:inline-block;border:1px dashed #999;'
                'background:#fff;line-height:0;"><img src="data:image/png;base64,%s" '
                'style="width:%spx;max-width:100%%;image-rendering:-webkit-optimize-contrast;"/></div>'
                '<div class="text-muted small mt-1">Preview of sticker 1 of %s — exactly as it prints</div>'
            ) % (base64.b64encode(png).decode(), width_px, total)
        layout = Engine.layout_vars(fmt)
        html = self.env['ir.qweb']._render('epg_sticker_print.sticker_preview', {
            'sticker': stickers[0], 'layout': layout, 'total': total})
        return Markup(html)

    @api.model
    def _first_page_png(self, pdf, fmt):
        """Rasterise page 1 of `pdf` with poppler (pdftoppm), cropped to the first label."""
        import shutil
        import subprocess
        import tempfile
        if not shutil.which('pdftoppm'):
            return None
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, 's.pdf')
            with open(src, 'wb') as fh:
                fh.write(pdf)
            dpi = 200
            # crop to the first label on sheet formats; rolls are one label per page
            w_px = int(fmt.width_mm / 25.4 * dpi)
            h_px = int(fmt.height_mm / 25.4 * dpi)
            cmd = ['pdftoppm', '-png', '-r', str(dpi), '-f', '1', '-l', '1', '-singlefile',
                   '-x', '0', '-y', '0', '-W', str(w_px), '-H', str(h_px), src, os.path.join(tmp, 'p')]
            subprocess.run(cmd, check=True, timeout=30, capture_output=True)
            out = os.path.join(tmp, 'p.png')
            if not os.path.exists(out):
                return None
            with open(out, 'rb') as fh:
                return fh.read()

    def _report_data(self):
        self.ensure_one()
        data = dict(self._options(), format_id=self.format_id.id)
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
