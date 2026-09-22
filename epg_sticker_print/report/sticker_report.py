# -*- coding: utf-8 -*-
from odoo import api, models


class ReportSticker(models.AbstractModel):
    _name = 'report.epg_sticker_print.report_sticker'
    _description = 'Stickers (PDF)'

    @api.model
    def _get_report_values(self, docids, data=None):
        data = data or {}
        # A report action carrying `data` is downloaded without the ids in the URL: Odoo's
        # client passes them in the context instead. Take them from wherever they are.
        if not docids:
            docids = data.get('ids') or (data.get('context') or {}).get('active_ids') or []
        orders = self.env['sale.order'].browse(docids).exists()
        Engine = self.env['epg.sticker.engine']
        options = {k: data.get(k) for k in ('mode', 'barcode_type', 'copies', 'show_route', 'show_phone',
                                            'show_patient', 'show_lines', 'show_sender') if k in data}
        fmt = self.env['epg.sticker.format'].browse(data.get('format_id')).exists() if data.get('format_id') \
            else self.env['epg.sticker.format'].get_default()
        if orders:
            stickers = Engine.build(orders, options, fmt=fmt)
            doc_ids, doc_model, docs = orders.ids, 'sale.order', orders
        else:
            # Customer / route labels straight from the clinics, no orders picked:
            # the wizard sends the chosen partners in `data` for exactly this case.
            # 'One sticker per order' has no partner-only meaning - there is no
            # order number, product or patient without an order - so it prints
            # nothing rather than a label for the wrong thing. (client, 2026-08-29)
            partners = self.env['res.partner'].browse(data.get('partner_ids') or []).exists()
            stickers = (Engine.build_from_partners(partners, options, fmt=fmt)
                       if partners and options.get('mode') != 'order' else [])
            doc_ids, doc_model, docs = partners.ids, 'res.partner', partners
        layout = Engine.layout_vars(fmt)
        per_page = layout['per_page']
        pages = [stickers[i:i + per_page] for i in range(0, len(stickers), per_page)] or [[]]
        return {
            'doc_ids': doc_ids, 'doc_model': doc_model, 'docs': docs,
            'stickers': stickers, 'pages': pages, 'layout': layout, 'fmt': fmt,
        }
