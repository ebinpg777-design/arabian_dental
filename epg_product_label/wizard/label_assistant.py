# -*- coding: utf-8 -*-
"""The layout assistant.

Starting a label from an empty rectangle is the slowest part of the job: you know
what a retail price tag looks like, you just do not want to place nine boxes by
hand.  The assistant holds a handful of proven layouts as *proportions* rather
than millimetres, so the same design lands correctly on a 25 mm shelf tag and on
a 100 mm shipping label.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..models.label_units import mm_to_pt, to_mm

# Layouts are written in fractions of the label: x/y/w/h in 0..1, and `f` as the
# share of the element's own height to spend on type.  Scaling is then one
# multiplication and the design survives any label size.
FRACTION_KEYS = ('x', 'y', 'w', 'h')


class EpgLabelAssistant(models.TransientModel):
    _name = 'epg.label.assistant'
    _description = 'Label Layout Assistant'

    template_id = fields.Many2one(
        'epg.label.template', string='Template', required=True, ondelete='cascade')
    label_type = fields.Selection(related='template_id.label_type')
    label_width = fields.Float(related='template_id.label_width')
    label_height = fields.Float(related='template_id.label_height')
    unit = fields.Selection(related='template_id.unit')

    style = fields.Selection([
        ('retail_tag', 'Retail price tag'),
        ('shelf_edge', 'Shelf-edge label'),
        ('promo', 'Promotional price tag'),
        ('barcode_only', 'Barcode only'),
        ('product_card', 'Product card with image'),
        ('asset_tag', 'Asset tag with QR code'),
        ('address', 'Shipping address'),
        ('address_vcard', 'Contact card with vCard QR'),
        ('lot_trace', 'Lot / serial traceability'),
    ], string='Layout', required=True, default='retail_tag')

    theme = fields.Selection([
        ('clean', 'Clean - plain type, no rules'),
        ('boxed', 'Boxed - framed sections'),
        ('bold', 'Bold - heavy type, inverted header'),
    ], string='Style', default='clean', required=True)

    include_name = fields.Boolean(string='Product Name', default=True)
    include_reference = fields.Boolean(string='Internal Reference', default=True)
    include_price = fields.Boolean(string='Price', default=True)
    include_barcode = fields.Boolean(string='Barcode', default=True)
    include_image = fields.Boolean(string='Product Image', default=False)
    include_logo = fields.Boolean(string='Company Logo', default=False)
    include_attributes = fields.Boolean(string='Variant Attributes', default=False)
    include_date = fields.Boolean(string='Print Date', default=False)

    replace_existing = fields.Boolean(
        string='Replace Current Elements', default=True,
        help="Clear the label before drawing. Uncheck to add the generated "
             "elements on top of what is already there.")
    element_preview = fields.Integer(
        string='Elements', compute='_compute_element_preview')
    warning = fields.Char(compute='_compute_element_preview')

    # ==================================================================
    # defaults / computes
    # ==================================================================
    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        template = self.env['epg.label.template'].browse(
            values.get('template_id') or self.env.context.get('active_id'))
        if template.exists():
            values['template_id'] = template.id
            # Offer a layout that matches what the template is actually for.
            values.setdefault('style', {
                'partner': 'address',
                'lot': 'lot_trace',
            }.get(template.label_type, 'retail_tag'))
        return values

    @api.onchange('template_id', 'label_type')
    def _onchange_label_type(self):
        """Keep the chosen layout compatible with the template's record type.

        A domain would be the natural way to say this, but domains do not apply
        to selection fields, so the incompatible choice is corrected instead.
        """
        allowed = self._styles_for(self.label_type)
        if self.style not in allowed:
            self.style = allowed[0]

    def _has_expiry(self):
        """Whether lots carry an expiry date in this database."""
        return (self.label_type == 'lot'
                and 'expiration_date' in self.env['stock.lot']._fields)

    @staticmethod
    def _styles_for(label_type):
        if label_type == 'partner':
            return ['address', 'address_vcard', 'asset_tag', 'barcode_only']
        if label_type == 'lot':
            return ['lot_trace', 'barcode_only', 'asset_tag', 'retail_tag']
        return ['retail_tag', 'shelf_edge', 'promo', 'barcode_only',
                'product_card', 'asset_tag', 'lot_trace']

    @api.depends('style', 'theme', 'include_name', 'include_reference',
                 'include_price', 'include_barcode', 'include_image',
                 'include_logo', 'include_attributes', 'include_date',
                 'template_id')
    def _compute_element_preview(self):
        for wizard in self:
            try:
                specs = wizard._build_specs()
            except Exception:
                specs, warning = [], _("This layout cannot be built for this template.")
            else:
                warning = wizard._layout_warning(specs)
            wizard.element_preview = len(specs)
            wizard.warning = warning

    def _layout_warning(self, specs):
        """Flag layouts that will be too cramped to read once printed."""
        self.ensure_one()
        if not specs:
            return _("Nothing selected - tick at least one section to include.")
        height_mm = to_mm(self.template_id.label_height, self.template_id.unit)
        smallest = min(
            (spec['h'] * height_mm for spec in specs if spec.get('kind') != 'shape'),
            default=height_mm)
        if smallest < 2.2:
            return _(
                "This label is small for the chosen layout: some text will print "
                "below 6 pt. Consider a simpler layout or fewer sections.")
        if self.style in ('product_card',) and height_mm < 25:
            return _("A product card needs about 25 mm of height to breathe.")
        return False

    # ==================================================================
    # generation
    # ==================================================================
    def action_generate(self):
        self.ensure_one()
        template = self.template_id
        if not template:
            raise UserError(_("No template to draw on."))
        specs = self._build_specs()
        if not specs:
            raise UserError(_(
                "Nothing was selected to draw. Tick at least one section."))
        if self.replace_existing:
            template.element_ids.unlink()
        template.write({'element_ids': [
            (0, 0, values) for values in self._scale(specs)]})
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'epg.label.template',
            'res_id': template.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def _scale(self, specs):
        """Turn fractional specs into real element values in the template's unit."""
        self.ensure_one()
        template = self.template_id
        width, height = template.label_width, template.label_height
        unit = template.unit
        # Font sizes are derived from the box the text sits in, so they scale with
        # the label instead of being hard-coded points that overflow small stock.
        values_list = []
        for index, spec in enumerate(specs):
            spec = dict(spec)
            font_share = spec.pop('f', 0.72)
            kind = spec.pop('kind', 'text')
            box_height = spec['h'] * height
            values = {
                'sequence': (index + 1) * 10,
                'z_index': spec.pop('z', 1),
                'pos_x': round(spec.pop('x') * width, 3),
                'pos_y': round(spec.pop('y') * height, 3),
                'width': round(spec.pop('w') * width, 3),
                'height': round(spec.pop('h') * height, 3),
            }
            if kind == 'text':
                values['font_size'] = max(
                    4.0, round(mm_to_pt(to_mm(box_height, unit)) * font_share, 1))
            values.update(spec)
            values_list.append(values)
        return values_list

    def _build_specs(self):
        """Dispatch to the generator for the selected layout."""
        self.ensure_one()
        builder = getattr(self, '_specs_%s' % self.style, None)
        if builder is None:
            return []
        return [spec for spec in builder() if spec]

    # ------------------------------------------------------------------
    # shared fragments
    # ------------------------------------------------------------------
    @property
    def _accent(self):
        return {'clean': '#000000', 'boxed': '#000000', 'bold': '#000000'}[self.theme]

    def _name_spec(self, x, y, w, h, size=0.62, bold=None, align='left'):
        if not self.include_name:
            return None
        return {
            'name': _('Product Name'), 'element_type': 'field',
            'field_path': 'name', 'max_length': 60,
            'x': x, 'y': y, 'w': w, 'h': h, 'f': size,
            'font_bold': self.theme == 'bold' if bold is None else bold,
            'text_align': align, 'vertical_align': 'center',
            'text_wrap': True,
        }

    def _reference_spec(self, x, y, w, h, align='left'):
        if not self.include_reference:
            return None
        return {
            'name': _('Internal Reference'), 'element_type': 'field',
            'field_path': 'default_code', 'prefix': '',
            'x': x, 'y': y, 'w': w, 'h': h, 'f': 0.66,
            'text_align': align, 'vertical_align': 'center',
            'text_wrap': False,
            'condition': 'object.default_code',
        }

    def _price_spec(self, x, y, w, h, size=0.68, align='right', promo=False):
        if not self.include_price:
            return None
        return {
            'name': _('Promotional Price') if promo else _('Price'),
            'element_type': 'price_promo' if promo else 'price',
            'price_base': 'promo_pricelist' if promo else 'pricelist',
            'price_show_currency': True,
            'x': x, 'y': y, 'w': w, 'h': h, 'f': size,
            'font_bold': True, 'text_align': align, 'vertical_align': 'center',
            'text_wrap': False,
        }

    def _barcode_spec(self, x, y, w, h, human=True):
        if not self.include_barcode:
            return None
        return {
            'name': _('Barcode'), 'element_type': 'barcode',
            'barcode_source': 'barcode', 'barcode_type': 'auto',
            'barcode_humanreadable': human,
            'x': x, 'y': y, 'w': w, 'h': h,
            'kind': 'barcode',
            'condition': 'object.barcode',
        }

    def _image_spec(self, x, y, w, h):
        if not self.include_image:
            return None
        return {
            'name': _('Product Image'), 'element_type': 'image',
            'image_source': 'record', 'image_fit': 'contain',
            'x': x, 'y': y, 'w': w, 'h': h, 'kind': 'image',
        }

    def _logo_spec(self, x, y, w, h):
        if not self.include_logo:
            return None
        return {
            'name': _('Company Logo'), 'element_type': 'image',
            'image_source': 'company_logo', 'image_fit': 'contain',
            'x': x, 'y': y, 'w': w, 'h': h, 'kind': 'image',
        }

    def _attributes_spec(self, x, y, w, h):
        if not self.include_attributes:
            return None
        return {
            'name': _('Attributes'), 'element_type': 'attributes',
            'attribute_separator': ' · ', 'attribute_show_name': False,
            'x': x, 'y': y, 'w': w, 'h': h, 'f': 0.68,
            'text_align': 'left', 'vertical_align': 'center', 'text_wrap': False,
        }

    def _date_spec(self, x, y, w, h, align='right'):
        if not self.include_date:
            return None
        return {
            'name': _('Print Date'), 'element_type': 'date',
            'date_format': '%d/%m/%Y',
            'x': x, 'y': y, 'w': w, 'h': h, 'f': 0.68,
            'text_align': align, 'vertical_align': 'center', 'text_wrap': False,
        }

    def _rule_spec(self, x, y, w):
        """A hairline separator - only drawn by the themes that use rules."""
        if self.theme == 'clean':
            return None
        return {
            'name': _('Separator'), 'element_type': 'line',
            'x': x, 'y': y, 'w': w, 'h': 0.012, 'kind': 'shape',
            'fill': True, 'bg_color': '#000000', 'bg_transparent': False, 'z': 0,
        }

    def _frame_spec(self):
        if self.theme != 'boxed':
            return None
        return {
            'name': _('Frame'), 'element_type': 'box',
            'x': 0.01, 'y': 0.02, 'w': 0.98, 'h': 0.96, 'kind': 'shape',
            'border_width': 0.3, 'border_color': '#000000', 'z': 0,
        }

    def _header_band_spec(self, height=0.28):
        """The inverted strip the bold theme puts the product name on."""
        if self.theme != 'bold':
            return None
        return {
            'name': _('Header Band'), 'element_type': 'box',
            'x': 0.0, 'y': 0.0, 'w': 1.0, 'h': height, 'kind': 'shape',
            'fill': True, 'bg_color': '#000000', 'bg_transparent': False, 'z': 0,
        }

    # ------------------------------------------------------------------
    # layouts
    # ------------------------------------------------------------------
    def _specs_retail_tag(self):
        """Name across the top, price large on the right, barcode along the foot."""
        bold = self.theme == 'bold'
        name = self._name_spec(0.04, 0.04, 0.92, 0.24, size=0.60, bold=True)
        if name and bold:
            name.update({'text_color': '#FFFFFF', 'text_align': 'center'})
        return [
            self._frame_spec(),
            self._header_band_spec(0.30),
            name,
            self._reference_spec(0.04, 0.30, 0.44, 0.13),
            self._attributes_spec(0.04, 0.43, 0.44, 0.13),
            self._price_spec(0.46, 0.30, 0.50, 0.28, size=0.72),
            self._rule_spec(0.04, 0.60, 0.92),
            self._barcode_spec(0.06, 0.63, 0.88, 0.33),
        ]

    def _specs_shelf_edge(self):
        """Wide format: description on the left, the price owning the right third."""
        return [
            self._frame_spec(),
            self._name_spec(0.03, 0.06, 0.60, 0.30, size=0.58, bold=True),
            self._attributes_spec(0.03, 0.36, 0.60, 0.16),
            self._reference_spec(0.03, 0.52, 0.32, 0.16),
            self._barcode_spec(0.03, 0.66, 0.58, 0.30),
            self._rule_spec(0.645, 0.08, 0.005),
            self._price_spec(0.66, 0.10, 0.32, 0.44, size=0.74),
            {
                'name': _('Price per Unit'), 'element_type': 'price_uom',
                'price_uom_qty': 1.0,
                'x': 0.66, 'y': 0.56, 'w': 0.32, 'h': 0.14, 'f': 0.66,
                'text_align': 'right', 'vertical_align': 'center', 'text_wrap': False,
            } if self.include_price else None,
            self._date_spec(0.66, 0.80, 0.32, 0.14),
        ]

    def _specs_promo(self):
        """Old price struck through, new price large, and a savings badge."""
        specs = [
            self._frame_spec(),
            self._name_spec(0.04, 0.04, 0.92, 0.22, size=0.58, bold=True),
            {
                'name': _('Regular Price'), 'element_type': 'price',
                'price_base': 'pricelist', 'price_strikethrough': True,
                'x': 0.04, 'y': 0.28, 'w': 0.40, 'h': 0.16, 'f': 0.64,
                'text_align': 'left', 'vertical_align': 'center',
                'prefix': '', 'text_wrap': False,
            } if self.include_price else None,
            self._price_spec(0.04, 0.44, 0.58, 0.30, size=0.78, align='left',
                             promo=True),
            {
                'name': _('You Save'), 'element_type': 'price_diff',
                'price_diff_mode': 'percent', 'digits': 0,
                'x': 0.64, 'y': 0.28, 'w': 0.32, 'h': 0.24, 'f': 0.62,
                'font_bold': True, 'text_align': 'center', 'vertical_align': 'center',
                'bg_transparent': False, 'bg_color': '#000000',
                'text_color': '#FFFFFF', 'border_radius': 1.0, 'text_wrap': False,
            } if self.include_price else None,
            {
                'name': _('Offer Validity'), 'element_type': 'pricelist_rule',
                'rule_info': 'validity', 'rule_pricelist': 'promo_pricelist',
                'x': 0.64, 'y': 0.54, 'w': 0.32, 'h': 0.14, 'f': 0.62,
                'text_align': 'center', 'vertical_align': 'center', 'text_wrap': False,
            } if self.include_price else None,
            self._barcode_spec(0.06, 0.76, 0.88, 0.22, human=False),
        ]
        return specs

    def _specs_barcode_only(self):
        """The whole label is the symbol; everything else gets out of the way."""
        has_caption = self.include_name or self.include_reference
        barcode_height = 0.72 if has_caption else 0.92
        return [
            self._frame_spec(),
            self._barcode_spec(0.05, 0.04, 0.90, barcode_height),
            self._name_spec(0.05, 0.78, 0.90, 0.18, size=0.60, align='center')
            if self.include_name else
            self._reference_spec(0.05, 0.78, 0.90, 0.18, align='center'),
        ]

    def _specs_product_card(self):
        """Picture left, description right, price and barcode along the bottom."""
        has_image = self.include_image
        text_x = 0.34 if has_image else 0.04
        text_w = 0.62 if has_image else 0.92
        return [
            self._frame_spec(),
            self._image_spec(0.04, 0.06, 0.27, 0.52) if has_image else None,
            self._name_spec(text_x, 0.06, text_w, 0.24, size=0.56, bold=True),
            self._attributes_spec(text_x, 0.30, text_w, 0.14),
            self._reference_spec(text_x, 0.44, text_w, 0.14),
            self._price_spec(text_x, 0.58, text_w, 0.20, size=0.70),
            self._rule_spec(0.04, 0.80, 0.92),
            self._barcode_spec(0.04, 0.82, 0.62, 0.16, human=False),
            self._logo_spec(0.70, 0.82, 0.26, 0.16),
        ]

    def _specs_asset_tag(self):
        """A QR code that survives a scuffed asset, with the identity beside it."""
        return [
            self._frame_spec(),
            {
                'name': _('QR Code'), 'element_type': 'qrcode',
                'barcode_source': 'text',
                'text_content': '{{ display_name }} | {{ default_code }}',
                'qr_error_correction': 'Q',
                'x': 0.04, 'y': 0.10, 'w': 0.34, 'h': 0.80, 'kind': 'barcode',
            },
            self._logo_spec(0.42, 0.06, 0.30, 0.20),
            self._name_spec(0.42, 0.28, 0.54, 0.30, size=0.52, bold=True),
            self._reference_spec(0.42, 0.58, 0.54, 0.16),
            self._date_spec(0.42, 0.74, 0.54, 0.14, align='left'),
        ]

    def _specs_address(self):
        """A shipping block: sender small at the top, recipient large below it."""
        return [
            self._frame_spec(),
            self._logo_spec(0.04, 0.04, 0.24, 0.18),
            {
                'name': _('Sender'), 'element_type': 'text',
                'text_content': '{{ env.company.name }}',
                'x': 0.30, 'y': 0.05, 'w': 0.66, 'h': 0.14, 'f': 0.60,
                'text_align': 'right', 'vertical_align': 'center', 'text_wrap': False,
            },
            self._rule_spec(0.04, 0.24, 0.92),
            {
                'name': _('Deliver To'), 'element_type': 'text',
                'text_content': 'DELIVER TO', 'letter_spacing': 0.4,
                'x': 0.04, 'y': 0.27, 'w': 0.50, 'h': 0.10, 'f': 0.66,
                'font_bold': True, 'text_align': 'left', 'vertical_align': 'center',
                'text_wrap': False,
            },
            {
                'name': _('Address'), 'element_type': 'address',
                'address_parts': 'name,street,street2,city,state,zip,country',
                'x': 0.04, 'y': 0.38, 'w': 0.68, 'h': 0.50, 'f': 0.20,
                'text_align': 'left', 'vertical_align': 'flex-start',
                'line_height': 1.25, 'text_wrap': True,
            },
            {
                'name': _('vCard QR'), 'element_type': 'vcard',
                'qr_error_correction': 'M',
                'x': 0.74, 'y': 0.38, 'w': 0.22, 'h': 0.46, 'kind': 'barcode',
            },
            self._date_spec(0.04, 0.90, 0.92, 0.08, align='left'),
        ]

    def _specs_address_vcard(self):
        """A contact card: identity on the left, a scannable vCard on the right."""
        return [
            self._frame_spec(),
            {
                'name': _('Contact Name'), 'element_type': 'field',
                'field_path': 'name',
                'x': 0.05, 'y': 0.10, 'w': 0.58, 'h': 0.20, 'f': 0.58,
                'font_bold': True, 'text_align': 'left', 'vertical_align': 'center',
                'text_wrap': False,
            },
            {
                'name': _('Job Position'), 'element_type': 'field',
                'field_path': 'function', 'condition': 'object.function',
                'x': 0.05, 'y': 0.30, 'w': 0.58, 'h': 0.12, 'f': 0.66,
                'text_align': 'left', 'vertical_align': 'center', 'text_wrap': False,
            },
            {
                'name': _('Address'), 'element_type': 'address',
                'address_parts': 'company,street,city,zip,country',
                'x': 0.05, 'y': 0.43, 'w': 0.58, 'h': 0.32, 'f': 0.24,
                'text_align': 'left', 'vertical_align': 'flex-start',
                'line_height': 1.2, 'text_wrap': True,
            },
            {
                'name': _('Contact Details'), 'element_type': 'text',
                'text_content': '{{ phone }}  ·  {{ email }}',
                'x': 0.05, 'y': 0.78, 'w': 0.90, 'h': 0.12, 'f': 0.62,
                'text_align': 'left', 'vertical_align': 'center', 'text_wrap': False,
            },
            {
                'name': _('vCard QR'), 'element_type': 'vcard',
                'qr_error_correction': 'M',
                'x': 0.68, 'y': 0.12, 'w': 0.27, 'h': 0.60, 'kind': 'barcode',
            },
            self._logo_spec(0.68, 0.76, 0.27, 0.16),
        ]

    def _specs_lot_trace(self):
        """Lot and serial work: the number is the point, so it gets the room."""
        return [
            self._frame_spec(),
            self._header_band_spec(0.24),
            {
                'name': _('Product'), 'element_type': 'field',
                'field_path': 'product_id.name'
                              if self.label_type == 'lot' else 'name',
                'x': 0.04, 'y': 0.03, 'w': 0.92, 'h': 0.20, 'f': 0.58,
                'font_bold': True, 'text_align': 'left', 'vertical_align': 'center',
                'text_color': '#FFFFFF' if self.theme == 'bold' else '#000000',
                'text_wrap': False,
            },
            {
                'name': _('Lot / Serial'), 'element_type': 'field',
                'field_path': 'name' if self.label_type == 'lot' else 'default_code',
                'prefix': '', 'default_value': '-',
                'x': 0.04, 'y': 0.26, 'w': 0.60, 'h': 0.22, 'f': 0.70,
                'font_bold': True, 'text_align': 'left', 'vertical_align': 'center',
                'text_wrap': False,
            },
            self._date_spec(0.64, 0.26, 0.32, 0.14),
            # Expiry only exists once product_expiry is installed.  Deciding that
            # here, rather than with a condition on the element, keeps a generated
            # layout free of elements that could never resolve.
            {
                'name': _('Expiry Date'), 'element_type': 'field',
                'field_path': 'expiration_date', 'value_format': 'date',
                'prefix': 'EXP ', 'condition': 'object.expiration_date',
                'x': 0.64, 'y': 0.40, 'w': 0.32, 'h': 0.12, 'f': 0.66,
                'text_align': 'right', 'vertical_align': 'center', 'text_wrap': False,
            } if self._has_expiry() else None,
            self._rule_spec(0.04, 0.52, 0.92),
            {
                'name': _('Lot Barcode'), 'element_type': 'barcode',
                'barcode_source': 'field' if self.label_type == 'lot' else 'barcode',
                'field_path': 'name', 'barcode_type': 'Code128',
                'barcode_humanreadable': True,
                'x': 0.06, 'y': 0.56, 'w': 0.88, 'h': 0.40, 'kind': 'barcode',
            },
        ]
