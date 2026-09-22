# -*- coding: utf-8 -*-
"""The print wizard.

It answers three questions before anything is rendered: which records, how many
copies of each, and with which pricelists.  Both render engines then work off the
same flat list of slots that :meth:`_collect_slots` produces, so a PDF and a ZPL
job from the same wizard contain exactly the same labels in the same order.
"""

import base64
import logging

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class EpgLabelPrint(models.TransientModel):
    _name = 'epg.label.print'
    _description = 'Print Labels'

    template_id = fields.Many2one(
        'epg.label.template', string='Label Template', required=True,
        default=lambda self: self._default_template())
    label_type = fields.Selection([
        ('product', 'Product'),
        ('partner', 'Contact / Address'),
        ('lot', 'Lot / Serial Number'),
    ], string='Label For', default='product', required=True)
    company_id = fields.Many2one(
        'res.company', string='Company', default=lambda self: self.env.company)

    # ------------------------------------------------------------------
    # what to print
    # ------------------------------------------------------------------
    product_ids = fields.Many2many('product.product', string='Product Variants')
    product_tmpl_ids = fields.Many2many('product.template', string='Products')
    partner_ids = fields.Many2many('res.partner', string='Contacts')
    lot_ids = fields.Many2many('stock.lot', string='Lots / Serial Numbers')
    line_ids = fields.One2many(
        'epg.label.print.line', 'wizard_id', string='Quantities')

    quantity_source = fields.Selection([
        ('fixed', 'Same number of copies for every record'),
        ('per_record', 'A different number per record'),
    ], string='Copies', default='fixed', required=True)
    quantity = fields.Integer(
        string='Copies per Record', default=1, required=True,
        help="How many identical labels to print for each selected record.")
    skip_labels = fields.Integer(
        string='Skip Labels', default=0,
        help="Leave this many label positions empty at the start of the first sheet, "
             "so a partly used sheet can be reloaded into the printer.")

    # ------------------------------------------------------------------
    # pricing
    # ------------------------------------------------------------------
    pricelist_id = fields.Many2one(
        'product.pricelist', string='Pricelist',
        default=lambda self: self._default_pricelist('pricelist_id'),
        help="Used by regular price elements.")
    promo_pricelist_id = fields.Many2one(
        'product.pricelist', string='Promotional Pricelist',
        default=lambda self: self._default_pricelist('promo_pricelist_id'),
        help="Used by promotional price and price difference elements.")
    price_quantity = fields.Float(
        string='Price for Quantity', default=1.0,
        help="Quantity used when asking the pricelist for a price, so that "
             "quantity-based rules resolve the way they will at the till.")

    # ------------------------------------------------------------------
    # output
    # ------------------------------------------------------------------
    output_format = fields.Selection([
        ('pdf', 'PDF'),
        ('zpl', 'ZPL (Zebra)'),
    ], string='Format', default='pdf', required=True)
    template_output = fields.Selection(related='template_id.output_type')
    show_cut_grid = fields.Boolean(
        string='Show Cutting Guides', default=False,
        help="Outline every label position with a dashed hairline. PDF only.")
    layout_mode = fields.Selection(related='template_id.layout_mode')
    labels_per_page = fields.Integer(related='template_id.labels_per_page')

    zpl_text = fields.Text(string='ZPL', readonly=True)
    zpl_data = fields.Binary(string='ZPL File', readonly=True, attachment=False)
    zpl_filename = fields.Char(readonly=True)
    printer_url = fields.Char(
        string='Printer Endpoint',
        default=lambda self: self._default_printer_url(),
        help="HTTP endpoint that accepts a raw ZPL body. Configure a default in "
             "Inventory > Configuration > Settings.")

    # ------------------------------------------------------------------
    # feedback
    # ------------------------------------------------------------------
    record_count = fields.Integer(compute='_compute_totals')
    label_count = fields.Integer(compute='_compute_totals', string='Labels')
    page_count = fields.Integer(compute='_compute_totals', string='Pages')
    preview_html = fields.Html(
        string='Preview', compute='_compute_preview_html', sanitize=False)
    warning = fields.Char(compute='_compute_totals')

    # ==================================================================
    # defaults
    # ==================================================================
    @api.model
    def _default_template(self):
        label_type = self.env.context.get('default_label_type') or self._guess_type()
        return self.env['epg.label.template']._default_template(label_type)

    @api.model
    def _guess_type(self):
        context = self.env.context
        if context.get('default_partner_ids') or context.get('active_model') == 'res.partner':
            return 'partner'
        if context.get('default_lot_ids') or context.get('active_model') == 'stock.lot':
            return 'lot'
        return 'product'

    @api.model
    def _default_pricelist(self, key):
        value = self.env['ir.config_parameter'].sudo().get_param(
            'epg_product_label.%s' % key)
        if value:
            return self.env['product.pricelist'].browse(int(value)).exists()
        return self.env['product.pricelist']

    @api.model
    def _default_printer_url(self):
        return self.env['ir.config_parameter'].sudo().get_param(
            'epg_product_label.zpl_printer_url') or False

    @api.model
    def default_get(self, fields_list):
        """Pick up whatever the calling list view had selected."""
        values = super().default_get(fields_list)
        context = self.env.context
        active_model, active_ids = context.get('active_model'), context.get('active_ids')
        mapping = {
            'product.product': 'product_ids',
            'product.template': 'product_tmpl_ids',
            'res.partner': 'partner_ids',
            'stock.lot': 'lot_ids',
        }
        target = mapping.get(active_model)
        if target and active_ids and not values.get(target) \
                and not context.get('default_%s' % target):
            values[target] = [(6, 0, active_ids)]
            # Overwrite rather than setdefault: the field's own default already put
            # 'product' in there, so a contact selection would otherwise be typed as
            # a product print and find no template.
            if not context.get('default_label_type'):
                values['label_type'] = {
                    'res.partner': 'partner',
                    'stock.lot': 'lot',
                }.get(active_model, 'product')
        if not values.get('label_type'):
            values['label_type'] = self._guess_type()
        return values

    # ==================================================================
    # computes
    # ==================================================================
    def _records(self):
        """The records selected for printing, in the wizard's own order."""
        self.ensure_one()
        if self.label_type == 'partner':
            return self.partner_ids
        if self.label_type == 'lot':
            return self.lot_ids
        return self.product_ids or self.product_tmpl_ids

    @api.depends('product_ids', 'product_tmpl_ids', 'partner_ids', 'lot_ids',
                 'quantity', 'quantity_source', 'line_ids.quantity',
                 'skip_labels', 'template_id', 'label_type')
    def _compute_totals(self):
        for wizard in self:
            records = wizard._records()
            wizard.record_count = len(records)
            if wizard.quantity_source == 'per_record' and wizard.line_ids:
                total = sum(line.quantity for line in wizard.line_ids)
            else:
                total = len(records) * max(0, wizard.quantity)
            wizard.label_count = total
            per_page = max(1, wizard.template_id.labels_per_page or 1)
            skip = min(wizard.skip_labels, per_page - 1) if per_page > 1 else 0
            wizard.page_count = ((total + skip - 1) // per_page + 1) if total else 0

            warning = False
            if wizard.template_id and records and \
                    wizard.template_id.label_type != wizard.label_type:
                warning = _("The selected template is not made for this kind of record.")
            elif wizard.output_format == 'zpl' and wizard.template_id \
                    and wizard.template_id.output_type == 'pdf':
                warning = _("This template is configured for PDF output only. "
                            "Set its Output to ZPL or to both on the Output tab.")
            elif wizard.skip_labels and per_page == 1:
                warning = _("Skipping has no effect on a one-label-per-page layout.")
            wizard.warning = warning

    @api.depends('template_id', 'product_ids', 'product_tmpl_ids', 'partner_ids',
                 'lot_ids', 'pricelist_id', 'promo_pricelist_id', 'price_quantity')
    def _compute_preview_html(self):
        for wizard in self:
            if not wizard.template_id:
                wizard.preview_html = False
                continue
            record = wizard._records()[:1] or wizard.template_id._get_preview_record()
            try:
                context = wizard._render_context()
                label = wizard.template_id._render_label_html(record, context)
                wizard.preview_html = Markup(
                    '<div style="padding:10px;background:#f1f3f5;display:flex;'
                    'justify-content:center;overflow:auto;">%s</div>') % label
            except Exception as error:
                _logger.warning("Label preview failed.", exc_info=True)
                wizard.preview_html = Markup(
                    '<div class="alert alert-warning">%s</div>'
                ) % _("Preview unavailable: %s", error)

    @api.onchange('label_type')
    def _onchange_label_type(self):
        """Keep the template and the selection in step when the type changes."""
        if self.template_id and self.template_id.label_type != self.label_type:
            self.template_id = self.env['epg.label.template']._default_template(
                self.label_type)
        return {'domain': {'template_id': [('label_type', '=', self.label_type)]}}

    @api.onchange('quantity_source', 'product_ids', 'product_tmpl_ids',
                  'partner_ids', 'lot_ids')
    def _onchange_quantity_source(self):
        """Materialise one quantity line per record when switching to per-record."""
        if self.quantity_source != 'per_record':
            return
        records = self._records()
        existing = {(line.res_model, line.res_id): line for line in self.line_ids}
        lines = []
        for record in records:
            key = (record._name, record.id)
            if key in existing:
                lines.append((4, existing[key].id))
            else:
                lines.append((0, 0, {
                    'res_model': record._name,
                    'res_id': record.id,
                    'display_name_stored': record.display_name,
                    'quantity': self.quantity or 1,
                }))
        removed = [(2, line.id) for key, line in existing.items()
                   if key not in {(r._name, r.id) for r in records}]
        self.line_ids = lines + removed

    # ==================================================================
    # slot expansion
    # ==================================================================
    def _render_context(self):
        self.ensure_one()
        return self.template_id._build_render_context(
            pricelist=self.pricelist_id,
            promo_pricelist=self.promo_pricelist_id,
            partner=self.partner_ids[:1] or None,
            company=self.company_id or self.env.company,
            quantity=self.price_quantity or 1.0)

    def _collect_slots(self):
        """Expand the selection into one ``(record, context)`` pair per printed label."""
        self.ensure_one()
        if not self.template_id:
            raise UserError(_("Choose a label template first."))
        base_context = self._render_context()
        slots = []
        if self.quantity_source == 'per_record' and self.line_ids:
            for line in self.line_ids:
                record = line._record()
                if not record:
                    continue
                slots.extend([(record, base_context)] * max(0, line.quantity))
        else:
            copies = max(0, self.quantity)
            if copies <= 0:
                raise UserError(_("The number of copies must be greater than zero."))
            for record in self._records():
                slots.extend([(record, base_context)] * copies)
        if not slots:
            raise UserError(_(
                "Nothing was selected to print. Pick at least one record, and check "
                "that archived records were unarchived first."))
        return slots

    def _check_ready(self):
        self.ensure_one()
        if not self.template_id:
            raise UserError(_("Choose a label template first."))
        if not self.template_id.element_ids:
            raise UserError(_(
                "The template '%s' has no elements yet, so every label would come "
                "out blank. Add at least one element in the designer.",
                self.template_id.name))
        if self.skip_labels < 0:
            raise UserError(_("The number of labels to skip cannot be negative."))

    # ==================================================================
    # actions
    # ==================================================================
    def action_print(self):
        """Render and hand back the label job in the chosen format."""
        self.ensure_one()
        self._check_ready()
        if self.output_format == 'zpl':
            return self.action_download_zpl()
        return self._pdf_report_action()

    def _pdf_report_action(self):
        self.ensure_one()
        if self.template_id.output_type == 'zpl':
            raise UserError(_(
                "The template '%s' is configured for ZPL output only.",
                self.template_id.name))
        self.env['epg.label.print.log']._log_print(self, 'pdf')
        # The paperformat travels in the context: report_action copies it into the
        # action, and ir.actions.report.get_paperformat picks it up again.
        report = self.env.ref('epg_product_label.action_report_epg_label')
        action = report.with_context(
            epg_label_paperformat_id=self.template_id.paperformat_id.id,
        ).report_action(self, data={'wizard_id': self.id}, config=False)
        action['close_on_report_download'] = True
        return action

    def action_preview(self):
        """Open the rendered labels as HTML, without downloading anything."""
        self.ensure_one()
        self._check_ready()
        self.env['epg.label.print.log']._log_print(self, 'preview')
        return {
            'type': 'ir.actions.act_url',
            'url': '/report/html/epg_product_label.report_label/%s' % self.id,
            'target': 'new',
        }

    def action_build_zpl(self):
        """Generate the ZPL and keep it on the wizard so it can be reviewed."""
        self.ensure_one()
        self._check_ready()
        zpl = self._build_zpl()
        self.env['epg.label.print.log']._log_print(self, 'zpl')
        self.write({
            'zpl_text': zpl,
            'zpl_data': base64.b64encode(zpl.encode('utf-8')),
            'zpl_filename': '%s.zpl' % (
                self.template_id.code or self.template_id.name or 'labels'
            ).replace(' ', '_').lower(),
            'output_format': 'zpl',
        })
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
            'context': dict(self.env.context, epg_show_zpl=True),
        }

    def action_download_zpl(self):
        self.ensure_one()
        if not self.zpl_data:
            self.action_build_zpl()
        return {
            'type': 'ir.actions.act_url',
            'url': '/web/content/%s/%s/zpl_data/%s?download=true' % (
                self._name, self.id, self.zpl_filename or 'labels.zpl'),
            'target': 'self',
        }

    def action_direct_print(self):
        """POST the ZPL to the configured printer endpoint (IoT Box or print server)."""
        self.ensure_one()
        self._check_ready()
        endpoint = self.printer_url or self._default_printer_url()
        if not endpoint:
            raise UserError(_(
                "No printer endpoint is configured. Set one on this wizard, or a "
                "default under Inventory > Configuration > Settings, or use "
                "Download instead."))
        zpl = self._build_zpl()
        try:
            import requests
            response = requests.post(
                endpoint, data=zpl.encode('utf-8'),
                headers={'Content-Type': 'text/plain; charset=utf-8'}, timeout=20)
            response.raise_for_status()
        except Exception as error:
            raise UserError(_(
                "The printer at %(url)s could not be reached: %(error)s",
                url=endpoint, error=error)) from error
        self.env['epg.label.print.log']._log_print(self, 'zpl')
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'success',
                'title': _("Sent to printer"),
                'message': _("%s label(s) were sent to %s.",
                             self.label_count, endpoint),
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }

    def _build_zpl(self):
        self.ensure_one()
        if self.template_id.output_type == 'pdf':
            raise UserError(_(
                "The template '%s' is configured for PDF output only. Set its "
                "Output to ZPL or to both on the Output tab.",
                self.template_id.name))
        return self.template_id._render_zpl(self._collect_slots())

    def action_open_designer(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'epg.label.template',
            'res_id': self.template_id.id,
            'view_mode': 'form',
            'target': 'current',
        }


class EpgLabelPrintLine(models.TransientModel):
    _name = 'epg.label.print.line'
    _description = 'Label Print Quantity'
    _order = 'id'

    wizard_id = fields.Many2one(
        'epg.label.print', required=True, ondelete='cascade', index=True)
    res_model = fields.Char(required=True)
    res_id = fields.Integer(required=True)
    display_name_stored = fields.Char(string='Record', readonly=True)
    quantity = fields.Integer(string='Copies', default=1, required=True)

    def _record(self):
        self.ensure_one()
        if not self.res_model or not self.res_id:
            return None
        model = self.env.get(self.res_model)
        if model is None:
            return None
        return model.browse(self.res_id).exists()
