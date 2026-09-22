# -*- coding: utf-8 -*-
"""One positioned thing on a label: a caption, a barcode, a price, a box.

An element knows how to turn itself into HTML (for the PDF engine) and into ZPL
commands (for a Zebra printer).  Both renderers read the same stored geometry and
the same resolved value, so a template designed once prints the same both ways.
"""

import base64
import io
import logging
import re
import types
from datetime import date, datetime

from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools.safe_eval import safe_eval

from .label_units import css, to_mm
from .label_zpl import ZplBuilder, encode_image_b64

_logger = logging.getLogger(__name__)

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None

class RecordView:
    """Read-only view of a record for element conditions.

    A condition is Python typed into a form, so what it can reach must be data, not
    the ORM: a real record carries ``.env`` and ``.sudo()``, and
    ``object.env['res.users'].sudo().write(...)`` would run as anyone who can print.
    Only field values come through here - relational ones wrapped again - and the
    record itself sits behind a name-mangled slot that safe_eval refuses to name
    (it rejects any name containing a double underscore).
    """

    __slots__ = ('__record',)

    def __init__(self, record):
        self.__record = record

    def __getattr__(self, name):
        record = self.__record
        field = None if name.startswith('_') else record._fields.get(name)
        if field is None:
            raise AttributeError(name)
        value = record[:1][name]
        if field.type == 'many2one':
            return RecordView(value)
        if field.type in ('one2many', 'many2many'):
            return tuple(RecordView(line) for line in value)
        return value

    def __setattr__(self, name, value):
        if name != '_RecordView__record':
            raise AttributeError(name)
        object.__setattr__(self, name, value)

    def __bool__(self):
        return bool(self.__record)

    def __eq__(self, other):
        if isinstance(other, RecordView):
            return self.__record == other.__record
        return NotImplemented

    def __hash__(self):
        return hash(self.__record)

    def __str__(self):
        return self.__record.display_name or ''


# ``str.format`` walks attributes named inside the format string - a constant that
# safe_eval's name check never sees - so '{0._RecordView__record.env}'.format(object)
# would read straight past RecordView.  A label condition has no use for it.
FORBIDDEN_CONDITION_NAMES = frozenset(('format', 'format_map'))


def condition_names(code):
    """Every name a compiled condition refers to, nested code objects included."""
    names = set(code.co_names)
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            names |= condition_names(const)
    return names


# ``{{ path.to.field }}`` placeholders inside a text element.
PLACEHOLDER_RE = re.compile(r'\{\{\s*([a-zA-Z_][\w.]*)\s*\}\}')

ELEMENT_TYPES = [
    ('text', 'Text'),
    ('field', 'Field'),
    ('barcode', 'Barcode'),
    ('qrcode', 'QR Code'),
    ('vcard', 'vCard QR Code'),
    ('image', 'Image'),
    ('price', 'Price'),
    ('price_promo', 'Promotional Price'),
    ('price_diff', 'Price Difference'),
    ('price_uom', 'Price per Unit'),
    ('pricelist_rule', 'Pricelist Rule'),
    ('attributes', 'Product Attributes'),
    ('address', 'Contact Address'),
    ('date', 'Date'),
    ('box', 'Box'),
    ('line', 'Line'),
    ('ellipse', 'Ellipse'),
    ('html', 'Custom HTML'),
]

# Element types that never carry a text value; the designer hides the content tab
# for these and the renderers take a shortcut.
SHAPE_TYPES = ('box', 'line', 'ellipse')


class EpgLabelElement(models.Model):
    _name = 'epg.label.element'
    _description = 'Label Element'
    _order = 'sequence, id'

    name = fields.Char(
        string='Label', required=True, default='Element',
        help="Designer-only name. It is never printed.")
    template_id = fields.Many2one(
        'epg.label.template', string='Template',
        required=True, ondelete='cascade', index=True)
    label_type = fields.Selection(related='template_id.label_type', store=True)
    unit = fields.Selection(related='template_id.unit')
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    element_type = fields.Selection(
        ELEMENT_TYPES, string='Type', required=True, default='text')

    # ------------------------------------------------------------------
    # geometry - stored in the template's unit, measured from the top-left
    # ------------------------------------------------------------------
    pos_x = fields.Float(string='X', default=1.0, digits=(16, 3))
    pos_y = fields.Float(string='Y', default=1.0, digits=(16, 3))
    width = fields.Float(string='Width', default=20.0, digits=(16, 3))
    height = fields.Float(string='Height', default=5.0, digits=(16, 3))
    rotation = fields.Selection(
        [('0', '0°'), ('90', '90°'), ('180', '180°'), ('270', '270°')],
        string='Rotation', default='0', required=True)
    z_index = fields.Integer(
        string='Layer', default=1,
        help="Higher layers are drawn on top of lower ones.")
    opacity = fields.Float(
        string='Opacity', default=1.0,
        help="0 is fully transparent, 1 fully opaque. PDF output only.")
    auto_height = fields.Boolean(
        string='Fit Height to Content', default=False,
        help="Let the element grow past its designed height when the value is long. "
             "PDF output only - ZPL always uses the designed box.")

    # ------------------------------------------------------------------
    # content
    # ------------------------------------------------------------------
    text_content = fields.Text(
        string='Text',
        help="Static text. Use {{ field.path }} to inject values from the record, "
             "for example: {{ name }} - {{ categ_id.name }}")
    field_path = fields.Char(
        string='Field Path',
        help="Dotted path evaluated on the printed record, e.g. 'categ_id.name'.")
    field_id = fields.Many2one(
        'ir.model.fields', string='Field',
        domain="[('model', '=', model_name), ('store', '=', True)]",
        help="Pick a field to fill in the path. You can then refine the path by hand.")
    model_name = fields.Char(compute='_compute_model_name')
    prefix = fields.Char(help="Printed immediately before the value.")
    suffix = fields.Char(help="Printed immediately after the value.")
    default_value = fields.Char(
        string='Fallback', help="Printed when the resolved value is empty.")
    value_format = fields.Selection([
        ('raw', 'As is'),
        ('float', 'Decimal'),
        ('integer', 'Integer'),
        ('monetary', 'Monetary'),
        ('percentage', 'Percentage'),
        ('date', 'Date'),
        ('datetime', 'Date & Time'),
        ('upper', 'UPPERCASE'),
        ('lower', 'lowercase'),
        ('title', 'Title Case'),
    ], string='Format', default='raw', required=True)
    digits = fields.Integer(string='Decimals', default=2)
    date_format = fields.Char(
        string='Date Pattern', default='%d/%m/%Y',
        help="Python strftime pattern, e.g. %d/%m/%Y")
    max_length = fields.Integer(
        string='Max Characters', default=0,
        help="Truncate longer values and append an ellipsis. 0 disables truncation.")
    html_content = fields.Html(
        string='HTML', sanitize=False,
        help="Raw HTML, rendered as-is in PDF output and ignored in ZPL output. "
             "{{ field.path }} placeholders are substituted.")

    # ------------------------------------------------------------------
    # barcode / QR
    # ------------------------------------------------------------------
    barcode_source = fields.Selection([
        ('barcode', 'Product Barcode'),
        ('default_code', 'Internal Reference'),
        ('name', 'Name'),
        ('field', 'Other Field'),
        ('static', 'Fixed Value'),
        ('text', 'Text Template'),
    ], string='Barcode Source', default='barcode', required=True)
    barcode_value = fields.Char(string='Fixed Barcode Value')
    barcode_type = fields.Selection([
        ('auto', 'Automatic'),
        ('EAN13', 'EAN-13'),
        ('EAN8', 'EAN-8'),
        ('UPCA', 'UPC-A'),
        ('UPCE', 'UPC-E'),
        ('Code128', 'Code 128'),
        ('Code39', 'Code 39'),
        ('ITF', 'ITF (Interleaved 2 of 5)'),
        ('Codabar', 'Codabar'),
        ('QR', 'QR Code'),
        ('DataMatrix', 'Data Matrix'),
    ], string='Symbology', default='auto', required=True)
    barcode_humanreadable = fields.Boolean(
        string='Show Value', default=False,
        help="Print the human-readable digits under the bars.")
    barcode_transparent = fields.Boolean(
        string='Transparent Barcode Background', default=False,
        help="Drop the white background so the label background shows through. "
             "PDF output only.")
    barcode_inverted = fields.Boolean(
        string='Inverted Colours', default=False,
        help="White bars on a black background. Some scanners need extra contrast; "
             "most do not read inverted 1D codes, so test before rolling this out.")
    barcode_quiet = fields.Boolean(
        string='Quiet Zone', default=True,
        help="Keep the mandatory blank margin around the symbol.")
    barcode_module_width = fields.Integer(
        string='Module Width (dots)', default=2,
        help="ZPL narrow-bar width. Ignored in PDF output.")
    barcode_ratio = fields.Float(string='Wide/Narrow Ratio', default=3.0)
    qr_error_correction = fields.Selection([
        ('L', 'Low (7%)'), ('M', 'Medium (15%)'),
        ('Q', 'Quartile (25%)'), ('H', 'High (30%)'),
    ], string='Error Correction', default='M', required=True)
    barcode_status = fields.Selection([
        ('ok', 'Valid'),
        ('warning', 'Check this'),
        ('error', 'Will not scan'),
        ('empty', 'No value'),
    ], string='Barcode Check', compute='_compute_barcode_status')
    barcode_status_message = fields.Char(compute='_compute_barcode_status')
    barcode_sample = fields.Char(
        string='Sample Value', compute='_compute_barcode_status',
        help="What this element would encode for the preview record.")

    # ------------------------------------------------------------------
    # image
    # ------------------------------------------------------------------
    image_source = fields.Selection([
        ('record', 'Record Image'),
        ('template_image', 'Product Template Image'),
        ('company_logo', 'Company Logo'),
        ('partner_image', 'Contact Image'),
        ('static', 'Uploaded Image'),
        ('field', 'Image Field'),
    ], string='Image Source', default='record', required=True)
    image_data = fields.Binary(string='Image', attachment=True)
    image_filename = fields.Char()
    image_fit = fields.Selection([
        ('contain', 'Fit inside (keep ratio)'),
        ('cover', 'Fill and crop'),
        ('fill', 'Stretch'),
    ], string='Scaling', default='contain', required=True)
    image_threshold = fields.Integer(
        string='B/W Threshold', default=128,
        help="Grey level (0-255) that separates black from white when the image is "
             "converted to a 1-bit ZPL bitmap.")

    # ------------------------------------------------------------------
    # prices
    # ------------------------------------------------------------------
    price_base = fields.Selection([
        ('pricelist', 'Regular Pricelist'),
        ('promo_pricelist', 'Promotional Pricelist'),
        ('list_price', 'Sales Price'),
        ('standard_price', 'Cost'),
        ('field', 'Other Field'),
    ], string='Price Source', default='pricelist', required=True)
    price_tax = fields.Selection([
        ('none', 'As stored'),
        ('included', 'Tax included'),
        ('excluded', 'Tax excluded'),
    ], string='Taxes', default='none', required=True)
    price_show_currency = fields.Boolean(string='Show Currency', default=True)
    price_currency_position = fields.Selection([
        ('before', 'Before amount'), ('after', 'After amount'),
    ], string='Currency Position', default='before', required=True)
    price_strikethrough = fields.Boolean(
        string='Strike Through', default=False,
        help="Cross the amount out - the usual way to show a superseded price.")
    price_uom_qty = fields.Float(
        string='Quantity', default=1.0,
        help="Show the price for this quantity, e.g. 100 for a price per 100 g.")
    price_uom_label = fields.Char(
        string='Unit Label',
        help="Printed after a price-per-unit, e.g. '/ 100 g'. Leave empty to use "
             "the product's unit of measure.")
    price_diff_mode = fields.Selection([
        ('amount', 'Amount saved'),
        ('percent', 'Percentage saved'),
        ('both', 'Amount and percentage'),
    ], string='Difference', default='percent', required=True)

    # ------------------------------------------------------------------
    # pricelist rule
    # ------------------------------------------------------------------
    rule_info = fields.Selection([
        ('validity', 'Validity Period'),
        ('date_start', 'Start Date'),
        ('date_end', 'End Date'),
        ('min_quantity', 'Minimum Quantity'),
        ('discount', 'Discount'),
        ('name', 'Rule Name'),
    ], string='Rule Detail', default='validity', required=True)
    rule_pricelist = fields.Selection([
        ('promo_pricelist', 'Promotional Pricelist'),
        ('pricelist', 'Regular Pricelist'),
    ], string='Rule From', default='promo_pricelist', required=True)

    # ------------------------------------------------------------------
    # attributes / address
    # ------------------------------------------------------------------
    attribute_ids = fields.Many2many(
        'product.attribute', string='Attributes',
        help="Leave empty to print every attribute of the variant.")
    attribute_separator = fields.Char(string='Separator', default=' | ')
    attribute_show_name = fields.Boolean(string='Show Attribute Names', default=False)
    address_parts = fields.Char(
        string='Address Parts', default='name,street,street2,city,state,zip,country',
        help="Comma-separated list from: name, contact_name, company, street, "
             "street2, city, state, zip, country, phone, mobile, email, website, vat.")

    # ------------------------------------------------------------------
    # typography and box styling
    # ------------------------------------------------------------------
    font_family = fields.Selection([
        ('inherit', 'Template default'),
        ('Arial, Helvetica, sans-serif', 'Arial / Helvetica'),
        ('"Times New Roman", Times, serif', 'Times New Roman'),
        ('"Courier New", Courier, monospace', 'Courier New'),
        ('Verdana, Geneva, sans-serif', 'Verdana'),
        ('Tahoma, Geneva, sans-serif', 'Tahoma'),
        ('Georgia, serif', 'Georgia'),
        ('"Trebuchet MS", Helvetica, sans-serif', 'Trebuchet MS'),
        ('Impact, Charcoal, sans-serif', 'Impact'),
    ], string='Font', default='inherit', required=True)
    font_size = fields.Float(string='Font Size (pt)', default=8.0)
    font_bold = fields.Boolean(string='Bold')
    font_italic = fields.Boolean(string='Italic')
    font_underline = fields.Boolean(string='Underline')
    font_strike = fields.Boolean(string='Strikethrough')
    text_align = fields.Selection([
        ('left', 'Left'), ('center', 'Center'),
        ('right', 'Right'), ('justify', 'Justified'),
    ], string='Horizontal Align', default='left', required=True)
    vertical_align = fields.Selection([
        ('flex-start', 'Top'), ('center', 'Middle'), ('flex-end', 'Bottom'),
    ], string='Vertical Align', default='flex-start', required=True)
    line_height = fields.Float(string='Line Height', default=1.1)
    letter_spacing = fields.Float(string='Letter Spacing (mm)', default=0.0)
    text_transform = fields.Selection([
        ('none', 'None'), ('uppercase', 'UPPERCASE'),
        ('lowercase', 'lowercase'), ('capitalize', 'Capitalize'),
    ], string='Case', default='none', required=True)
    text_wrap = fields.Boolean(
        string='Wrap Text', default=True,
        help="Uncheck to keep the value on a single line and let it clip.")
    text_color = fields.Char(string='Text Colour', default='#000000')
    bg_color = fields.Char(string='Background Colour', default='#FFFFFF')
    bg_transparent = fields.Boolean(string='Transparent Background', default=True)
    border_width = fields.Float(string='Border Width (mm)', default=0.0)
    border_style = fields.Selection([
        ('solid', 'Solid'), ('dashed', 'Dashed'),
        ('dotted', 'Dotted'), ('double', 'Double'),
    ], string='Border Style', default='solid', required=True)
    border_color = fields.Char(string='Border Colour', default='#000000')
    border_radius = fields.Float(string='Corner Radius (mm)', default=0.0)
    padding = fields.Float(string='Padding (mm)', default=0.0)
    fill = fields.Boolean(
        string='Filled', default=False,
        help="For shapes: fill with the background colour instead of drawing an outline.")

    # ------------------------------------------------------------------
    # visibility
    # ------------------------------------------------------------------
    condition = fields.Char(
        string='Condition',
        help="Python expression. The element is printed only when it is truthy. "
             "'object' is the printed record, e.g. object.barcode")

    _positive_size = models.Constraint(
        'CHECK(width >= 0 AND height >= 0)',
        'Element width and height cannot be negative.')

    # ==================================================================
    # compute / onchange
    # ==================================================================
    @api.depends('template_id.model_name')
    def _compute_model_name(self):
        for element in self:
            element.model_name = element.template_id.model_name or 'product.template'

    @api.depends('element_type', 'barcode_source', 'barcode_value', 'barcode_type',
                 'field_path', 'text_content', 'template_id.preview_product_id',
                 'template_id.preview_partner_id')
    def _compute_barcode_status(self):
        """Resolve the real payload for the preview record and check it scans.

        Barcode mistakes are invisible on screen and expensive on paper - a wrong
        check digit prints beautifully and fails at the till - so the answer
        belongs next to the field, while the design is still open.
        """
        for element in self:
            element.barcode_sample = False
            if element.element_type not in ('barcode', 'qrcode', 'vcard'):
                element.barcode_status = False
                element.barcode_status_message = False
                continue
            try:
                template = element.template_id
                record = template._get_preview_record() if template else None
                context = template._build_render_context() if template else {}
                if element.element_type == 'vcard':
                    value = element._get_vcard_value(record, context)
                    symbology = 'QR'
                else:
                    value = element._get_barcode_value(record, context)
                    symbology = 'QR' if element.element_type == 'qrcode' \
                        else element.barcode_type
                result = self.check_barcode_value(symbology, value)
            except Exception:
                _logger.debug("Barcode check failed.", exc_info=True)
                element.barcode_status = False
                element.barcode_status_message = False
                continue
            element.barcode_status = result['state']
            element.barcode_status_message = result['message']
            element.barcode_sample = (value or '')[:80] if value else False

    @api.onchange('field_id')
    def _onchange_field_id(self):
        if self.field_id:
            self.field_path = self.field_id.name

    @api.onchange('element_type')
    def _onchange_element_type(self):
        """Give each new element a size that is usable straight away."""
        defaults = {
            'barcode': (30.0, 12.0),
            'qrcode': (15.0, 15.0),
            'vcard': (18.0, 18.0),
            'image': (20.0, 20.0),
            'price': (25.0, 8.0),
            'price_promo': (25.0, 8.0),
            'line': (30.0, 0.3),
            'box': (30.0, 10.0),
            'ellipse': (15.0, 15.0),
        }
        if self.element_type in defaults and self.template_id:
            width, height = defaults[self.element_type]
            if self.template_id.unit == 'in':
                width, height = width / 25.4, height / 25.4
            self.width, self.height = width, height
        if self.element_type == 'price':
            self.font_size = 14.0
            self.font_bold = True
        if self.element_type in SHAPE_TYPES:
            self.bg_transparent = not self.fill

    @api.constrains('condition')
    def _check_condition(self):
        for element in self.filtered('condition'):
            try:
                code = compile(element.condition, '<condition>', 'eval')
            except SyntaxError as exc:
                raise ValidationError(_(
                    "The condition of element '%(name)s' is not a valid Python "
                    "expression: %(error)s", name=element.name, error=exc)) from exc
            forbidden = condition_names(code) & FORBIDDEN_CONDITION_NAMES
            if forbidden:
                raise ValidationError(_(
                    "The condition of element '%(name)s' cannot use %(names)s.",
                    name=element.name, names=', '.join(sorted(forbidden))))

    # ==================================================================
    # value resolution
    # ==================================================================
    def _resolve_path(self, record, path):
        """Walk a dotted ``path`` on ``record``, returning ``None`` on any miss.

        Templates outlive the fields they point at - a renamed field must degrade to
        a blank element, not a traceback in the middle of a print run.
        """
        if not record or not path:
            return None
        value = record
        for part in path.split('.'):
            if value is None:
                return None
            if isinstance(value, models.BaseModel):
                if not value:
                    return None
                value = value[:1]
                if part not in value._fields and not hasattr(value, part):
                    return None
                try:
                    value = getattr(value, part)
                except Exception:
                    return None
            else:
                value = getattr(value, part, None)
        return value

    def _format_value(self, value, ctx):
        """Apply the element's format, prefix/suffix and truncation to ``value``."""
        self.ensure_one()
        if isinstance(value, models.BaseModel):
            value = value.display_name if value else ''
        if value is None or value is False:
            text = ''
        elif self.value_format == 'monetary':
            text = self._format_amount(value, ctx)
        elif self.value_format == 'float':
            text = '%.*f' % (max(0, self.digits), float(value or 0.0))
        elif self.value_format == 'integer':
            text = '%d' % int(round(float(value or 0.0)))
        elif self.value_format == 'percentage':
            text = '%.*f%%' % (max(0, self.digits), float(value or 0.0))
        elif self.value_format in ('date', 'datetime') and isinstance(value, (date, datetime)):
            text = value.strftime(self.date_format or '%d/%m/%Y')
        elif self.value_format == 'upper':
            text = str(value).upper()
        elif self.value_format == 'lower':
            text = str(value).lower()
        elif self.value_format == 'title':
            text = str(value).title()
        elif isinstance(value, bool):
            text = _('Yes') if value else _('No')
        else:
            text = str(value)

        if not text and self.default_value:
            text = self.default_value
        if not text:
            return ''
        if self.max_length and len(text) > self.max_length:
            text = text[:max(1, self.max_length - 1)].rstrip() + '…'
        return '%s%s%s' % (self.prefix or '', text, self.suffix or '')

    def _format_amount(self, amount, ctx):
        """Format a number as money using the printing company's currency.

        Deliberately not ``formatLang``: a label is a fixed-width box, and thousands
        separators plus a non-breaking space are what push a price out of it. The
        element's own currency position wins over the currency default, because
        that is the setting the designer reached for.
        """
        currency = ctx.get('currency') or self.env.company.currency_id
        amount = float(amount or 0.0)
        formatted = '%.*f' % (currency.decimal_places, amount)
        symbol = currency.symbol or currency.name or ''
        if not self.price_show_currency or not symbol:
            return formatted
        if self.price_currency_position == 'after':
            return '%s %s' % (formatted, symbol)
        return '%s %s' % (symbol, formatted)

    def _render_placeholders(self, text, record, ctx, html=False):
        """Substitute ``{{ path }}`` occurrences inside ``text``.

        With ``html=True`` each substituted value is HTML-escaped: the template's own
        markup is the designer's, but a product called ``<img onerror=...>`` is data.
        """
        if html:
            plain = self._render_placeholders
            return PLACEHOLDER_RE.sub(
                lambda match: str(escape(plain(match.group(0), record, ctx))), text or '')

        def replace(match):
            value = self._resolve_path(record, match.group(1))
            if value is None or value is False:
                return ''
            if isinstance(value, models.BaseModel):
                return value.display_name or ''
            if isinstance(value, (date, datetime)):
                return value.strftime(self.date_format or '%d/%m/%Y')
            if isinstance(value, float):
                return '%.*f' % (max(0, self.digits), value)
            return str(value)
        return PLACEHOLDER_RE.sub(replace, text or '')

    def _is_visible(self, record, ctx):
        """Evaluate the optional condition against the record being printed."""
        self.ensure_one()
        if not self.active:
            return False
        if not self.condition:
            return True
        try:
            # Field values only - no env, no records, no render context (it holds
            # records too): see RecordView.
            code = compile(self.condition, '<condition>', 'eval')
            if condition_names(code) & FORBIDDEN_CONDITION_NAMES:
                # A row stored before the constraint existed.
                raise ValueError("string formatting is not allowed in a label condition")
            view = RecordView(record)
            return bool(safe_eval(self.condition, {
                'object': view,
                'record': view,
                'user': RecordView(self.env.user),
                'company': RecordView(ctx.get('company') or self.env.company),
            }))
        except Exception:
            _logger.warning(
                "Condition of label element %s failed to evaluate; hiding it.",
                self.display_name, exc_info=True)
            return False

    # ==================================================================
    # the printed value, per element type
    # ==================================================================
    def _get_text_value(self, record, ctx):
        """Return the plain-text value of this element, or '' for non-text types."""
        self.ensure_one()
        handler = getattr(self, '_value_%s' % self.element_type, None)
        if handler is None:
            return ''
        return handler(record, ctx) or ''

    def _value_text(self, record, ctx):
        return self._format_value(
            self._render_placeholders(self.text_content or '', record, ctx), ctx)

    def _value_field(self, record, ctx):
        return self._format_value(self._resolve_path(record, self.field_path), ctx)

    def _value_date(self, record, ctx):
        source = self._resolve_path(record, self.field_path) if self.field_path else None
        if source is None:
            source = ctx.get('print_date') or fields.Date.context_today(self)
        if isinstance(source, (date, datetime)):
            return '%s%s%s' % (
                self.prefix or '',
                source.strftime(self.date_format or '%d/%m/%Y'),
                self.suffix or '')
        return self._format_value(source, ctx)

    def _value_price(self, record, ctx):
        return self._format_value_price(self._compute_price(record, ctx, promo=False), ctx)

    def _value_price_promo(self, record, ctx):
        return self._format_value_price(self._compute_price(record, ctx, promo=True), ctx)

    def _value_price_uom(self, record, ctx):
        price = self._compute_price(record, ctx, promo=False)
        if price is None:
            return ''
        quantity = self.price_uom_qty or 1.0
        label = self.price_uom_label
        if not label:
            uom = self._resolve_path(record, 'uom_id')
            uom_name = uom.name if uom else ''
            label = '/ %s %s' % (
                ('%g' % quantity) if quantity != 1 else '', uom_name)
        text = self._format_amount(price * quantity, ctx)
        return '%s%s %s%s' % (self.prefix or '', text, label.strip(), self.suffix or '')

    def _value_price_diff(self, record, ctx):
        regular = self._compute_price(record, ctx, promo=False)
        promo = self._compute_price(record, ctx, promo=True)
        if regular is None or promo is None or regular <= 0:
            return ''
        difference = regular - promo
        if difference <= 0:
            return ''
        percent = difference / regular * 100.0
        if self.price_diff_mode == 'amount':
            body = self._format_amount(difference, ctx)
        elif self.price_diff_mode == 'percent':
            body = '-%.*f%%' % (max(0, self.digits) if self.digits else 0, percent)
        else:
            body = '%s (-%.0f%%)' % (self._format_amount(difference, ctx), percent)
        return '%s%s%s' % (self.prefix or '', body, self.suffix or '')

    def _format_value_price(self, price, ctx):
        if price is None:
            return self.default_value or ''
        return '%s%s%s' % (
            self.prefix or '', self._format_amount(price, ctx), self.suffix or '')

    def _compute_price(self, record, ctx, promo=False):
        """Resolve the price for ``record`` under the wizard's pricelist choices."""
        self.ensure_one()
        if self.price_base == 'field':
            value = self._resolve_path(record, self.field_path)
            return float(value) if isinstance(value, (int, float)) else None
        if self.price_base == 'standard_price':
            price = self._resolve_path(record, 'standard_price')
            return float(price or 0.0)

        base = self.price_base
        if self.element_type == 'price_promo' or promo:
            base = 'promo_pricelist'
        elif self.element_type == 'price' and base == 'promo_pricelist':
            base = 'pricelist'

        pricelist = ctx.get('promo_pricelist' if base == 'promo_pricelist' else 'pricelist')
        price = None
        if base in ('pricelist', 'promo_pricelist') and pricelist:
            price = self._pricelist_price(record, pricelist, ctx)
        if price is None:
            price = self._resolve_path(record, 'list_price')
            price = float(price or 0.0)
        return self._apply_taxes(record, price, ctx)

    def _pricelist_price(self, record, pricelist, ctx):
        """Ask the pricelist for a price, tolerating products it does not cover.

        Returning ``None`` rather than raising is deliberate: one product the
        pricelist cannot price must not abort a print run of several hundred labels.
        The caller falls back to the sales price.
        """
        if not record or record._name not in ('product.product', 'product.template'):
            return None
        try:
            return pricelist._get_product_price(
                record, ctx.get('price_quantity', 1.0) or 1.0,
                currency=ctx.get('currency') or None,
                date=ctx.get('print_date') or False)
        except Exception:
            _logger.info("Pricelist %s could not price %s; falling back to the "
                         "sales price.", pricelist.display_name, record.display_name)
            return None

    def _apply_taxes(self, record, price, ctx):
        """Convert between tax-included and tax-excluded on request."""
        if self.price_tax == 'none' or not price:
            return price
        taxes = self._resolve_path(record, 'taxes_id')
        if not taxes:
            return price
        company = ctx.get('company') or self.env.company
        taxes = taxes.filtered(lambda tax: tax.company_id == company) or taxes
        currency = ctx.get('currency') or company.currency_id
        try:
            computed = taxes.compute_all(
                price, currency=currency, quantity=1.0,
                product=record if record._name == 'product.product' else None)
        except Exception:
            _logger.debug("Tax computation failed for %s.", record, exc_info=True)
            return price
        return computed['total_included'] if self.price_tax == 'included' \
            else computed['total_excluded']

    def _value_pricelist_rule(self, record, ctx):
        rule = self._find_pricelist_rule(record, ctx)
        if not rule:
            return self.default_value or ''
        if self.rule_info == 'name':
            body = rule.display_name
        elif self.rule_info == 'min_quantity':
            body = '%g' % (rule.min_quantity or 0.0)
        elif self.rule_info == 'discount':
            if rule.compute_price == 'percentage':
                body = '-%g%%' % (rule.percent_price or 0.0)
            else:
                body = ''
        elif self.rule_info == 'date_start':
            body = rule.date_start.strftime(self.date_format or '%d/%m/%Y') \
                if rule.date_start else ''
        elif self.rule_info == 'date_end':
            body = rule.date_end.strftime(self.date_format or '%d/%m/%Y') \
                if rule.date_end else ''
        else:
            pattern = self.date_format or '%d/%m/%Y'
            start = rule.date_start.strftime(pattern) if rule.date_start else ''
            end = rule.date_end.strftime(pattern) if rule.date_end else ''
            if start and end:
                body = '%s - %s' % (start, end)
            elif end:
                body = _('until %s', end)
            elif start:
                body = _('from %s', start)
            else:
                body = ''
        if not body:
            return self.default_value or ''
        return '%s%s%s' % (self.prefix or '', body, self.suffix or '')

    def _find_pricelist_rule(self, record, ctx):
        """Return the pricelist item that actually priced this product, if any."""
        pricelist = ctx.get(
            'promo_pricelist' if self.rule_pricelist == 'promo_pricelist' else 'pricelist')
        if not pricelist or not record or record._name not in (
                'product.product', 'product.template'):
            return self.env['product.pricelist.item']
        try:
            rule_id = pricelist._get_product_rule(record, ctx.get('price_quantity', 1.0))
            if rule_id:
                return self.env['product.pricelist.item'].browse(rule_id).exists()
        except Exception:
            _logger.debug("Could not resolve a pricelist rule for %s.", record)
        return self.env['product.pricelist.item']

    def _value_attributes(self, record, ctx):
        values = self._resolve_path(record, 'product_template_variant_value_ids')
        if not values:
            values = self._resolve_path(record, 'attribute_line_ids')
            if values:
                parts = []
                for line in values:
                    if self.attribute_ids and line.attribute_id not in self.attribute_ids:
                        continue
                    names = ', '.join(line.value_ids.mapped('name'))
                    parts.append('%s: %s' % (line.attribute_id.name, names)
                                 if self.attribute_show_name else names)
                return (self.attribute_separator or ' | ').join(filter(None, parts))
            return self.default_value or ''
        parts = []
        for value in values:
            if self.attribute_ids and value.attribute_id not in self.attribute_ids:
                continue
            parts.append('%s: %s' % (value.attribute_id.name, value.name)
                         if self.attribute_show_name else value.name)
        return (self.attribute_separator or ' | ').join(parts) or (self.default_value or '')

    def _value_address(self, record, ctx):
        partner = self._address_partner(record, ctx)
        if not partner:
            return self.default_value or ''
        wanted = [part.strip() for part in (self.address_parts or '').split(',')]
        available = {
            'name': partner.name,
            'contact_name': partner.name if partner.parent_id else '',
            'company': partner.parent_id.name if partner.parent_id else partner.name,
            'street': partner.street,
            'street2': partner.street2,
            'city': partner.city,
            'state': partner.state_id.name if partner.state_id else '',
            'zip': partner.zip,
            'country': partner.country_id.name if partner.country_id else '',
            'phone': partner.phone,
            'mobile': getattr(partner, 'mobile', ''),
            'email': partner.email,
            'website': partner.website,
            'vat': partner.vat,
        }
        # City, state and zip belong on one line; everything else gets its own.
        lines, city_line = [], []
        for key in wanted:
            value = available.get(key)
            if not value:
                continue
            if key in ('city', 'state', 'zip'):
                city_line.append(value)
            else:
                if city_line:
                    lines.append(' '.join(city_line))
                    city_line = []
                lines.append(value)
        if city_line:
            lines.append(' '.join(city_line))
        return '\n'.join(lines)

    def _address_partner(self, record, ctx):
        if not record:
            return None
        if record._name == 'res.partner':
            return record
        partner = self._resolve_path(record, self.field_path) if self.field_path else None
        if isinstance(partner, models.BaseModel) and partner._name == 'res.partner':
            return partner
        return ctx.get('partner') or (ctx.get('company') or self.env.company).partner_id

    # ------------------------------------------------------------------
    # barcode payloads
    # ------------------------------------------------------------------
    def _get_barcode_value(self, record, ctx):
        self.ensure_one()
        if self.barcode_source == 'static':
            return self.barcode_value or ''
        if self.barcode_source == 'text':
            return self._render_placeholders(self.text_content or '', record, ctx)
        if self.barcode_source == 'field':
            value = self._resolve_path(record, self.field_path)
        else:
            value = self._resolve_path(record, self.barcode_source)
            if value in (None, False, '') and ctx.get('custom_barcode'):
                value = ctx['custom_barcode']
        if isinstance(value, models.BaseModel):
            value = value.display_name
        return str(value) if value not in (None, False) else ''

    def _get_vcard_value(self, record, ctx):
        """Build a vCard 3.0 payload - what a phone camera expects from an address label."""
        partner = self._address_partner(record, ctx)
        if not partner:
            return ''
        lines = ['BEGIN:VCARD', 'VERSION:3.0']
        name = partner.name or ''
        lines.append('N:%s;;;;' % name)
        lines.append('FN:%s' % name)
        if partner.parent_id:
            lines.append('ORG:%s' % partner.parent_id.name)
        elif partner.is_company:
            lines.append('ORG:%s' % name)
        if partner.function:
            lines.append('TITLE:%s' % partner.function)
        if partner.phone:
            lines.append('TEL;TYPE=WORK,VOICE:%s' % partner.phone)
        mobile = getattr(partner, 'mobile', None)
        if mobile:
            lines.append('TEL;TYPE=CELL:%s' % mobile)
        if partner.email:
            lines.append('EMAIL;TYPE=INTERNET:%s' % partner.email)
        if partner.website:
            lines.append('URL:%s' % partner.website)
        if partner.vat:
            lines.append('NOTE:VAT %s' % partner.vat)
        address = ';;%s;%s;%s;%s;%s' % (
            ' '.join(filter(None, [partner.street, partner.street2])) or '',
            partner.city or '',
            partner.state_id.name if partner.state_id else '',
            partner.zip or '',
            partner.country_id.name if partner.country_id else '')
        if address.strip(';'):
            lines.append('ADR;TYPE=WORK:%s' % address)
        lines.append('END:VCARD')
        return '\n'.join(lines)

    # ------------------------------------------------------------------
    # image payloads
    # ------------------------------------------------------------------
    def _get_image_bytes(self, record, ctx):
        """Return raw image bytes for this element, or ``None``."""
        self.ensure_one()
        source = self.image_source
        if source == 'static':
            return encode_image_b64(self.image_data)
        if source == 'company_logo':
            company = ctx.get('company') or self.env.company
            return encode_image_b64(company.logo)
        if source == 'partner_image':
            partner = self._address_partner(record, ctx)
            return encode_image_b64(partner.image_256) if partner else None
        if source == 'field':
            return encode_image_b64(self._resolve_path(record, self.field_path))
        if source == 'template_image':
            template = self._resolve_path(record, 'product_tmpl_id') or record
            return encode_image_b64(self._resolve_path(template, 'image_256'))
        for candidate in ('image_256', 'image_128', 'image_1920', 'image_512'):
            data = self._resolve_path(record, candidate)
            if data:
                return encode_image_b64(data)
        return None

    # ==================================================================
    # HTML rendering (PDF / preview)
    # ==================================================================
    def _render_html(self, record, ctx):
        """Return the absolutely-positioned HTML for this element."""
        self.ensure_one()
        if not self._is_visible(record, ctx):
            return Markup('')
        if self.element_type in SHAPE_TYPES:
            return self._html_wrapper(Markup(''), ctx, shape=True)
        if self.element_type in ('barcode', 'qrcode', 'vcard'):
            return self._html_wrapper(self._html_barcode(record, ctx), ctx)
        if self.element_type == 'image':
            return self._html_wrapper(self._html_image(record, ctx), ctx)
        if self.element_type == 'html':
            body = self._render_placeholders(self.html_content or '', record, ctx, html=True)
            return self._html_wrapper(Markup(body), ctx)
        value = self._get_text_value(record, ctx)
        if not value and not self.border_width and self.bg_transparent:
            return Markup('')
        return self._html_wrapper(self._html_text(value), ctx)

    def _html_text(self, value):
        """Wrap the value, turning newlines into breaks so addresses keep shape."""
        parts = [escape(line) for line in str(value).split('\n')]
        return Markup('<br/>').join(parts)

    def _html_barcode(self, record, ctx):
        if self.element_type == 'vcard':
            value, symbology = self._get_vcard_value(record, ctx), 'QR'
        elif self.element_type == 'qrcode':
            value, symbology = self._get_barcode_value(record, ctx), 'QR'
        else:
            value, symbology = self._get_barcode_value(record, ctx), self.barcode_type
        if not value:
            return Markup('')
        png = self._generate_barcode_png(value, symbology, ctx)
        if not png:
            # A symbology that rejects the value should show the value, not vanish -
            # a visibly wrong label is fixable, a silently empty one is not. A vCard
            # payload is far too long to fall back to, so it just drops out.
            return Markup('') if self.element_type == 'vcard' else self._html_text(value)
        src = 'data:image/png;base64,%s' % base64.b64encode(png).decode()
        # The alt text is a label, not the payload: a vCard would otherwise dump a
        # whole contact record into the HTML behind the image.
        return Markup(
            '<img src="%s" alt="%s" style="width:100%%;height:100%%;'
            'object-fit:contain;display:block;"/>'
        ) % (src, escape(self.name or symbology))

    def _generate_barcode_png(self, value, symbology, ctx):
        """Render the symbol via Odoo's barcode service, then post-process colours."""
        self.ensure_one()
        unit = self.template_id.unit or 'mm'
        # Aim for roughly 300 dpi so the symbol stays crisp after PDF scaling.  A
        # gallery thumbnail is a few centimetres of screen, so it drops to a
        # quarter of that: rasterising 30 full-resolution symbols to draw a grid of
        # cards costs seconds nobody gets back.
        pixels_per_mm = 3 if ctx.get('thumbnail') else 12
        width_px = max(60, int(to_mm(self.width, unit) * pixels_per_mm))
        height_px = max(30, int(to_mm(self.height, unit) * pixels_per_mm))
        if symbology in ('QR', 'DataMatrix'):
            width_px = height_px = max(width_px, height_px)
        options = {
            'width': width_px,
            'height': height_px,
            'humanreadable': 1 if self.barcode_humanreadable else 0,
            'quiet': 1 if self.barcode_quiet else 0,
        }
        try:
            png = self.env['ir.actions.report'].barcode(
                'QR' if symbology == 'DataMatrix' else symbology, value, **options)
        except UserError:
            # "This server cannot draw that symbology" is the one refusal that must
            # reach the person printing. Swallowed here it becomes a label with no
            # barcode on it at all, which is the silent-wrong-output failure the
            # refusal exists to prevent. (client, 2026-08-26)
            raise
        except Exception:
            _logger.info(
                "Barcode '%s' could not be rendered as %s.", value, symbology)
            return None
        if isinstance(png, str):
            png = png.encode('latin-1', 'ignore')
        if self.barcode_transparent or self.barcode_inverted:
            png = self._post_process_barcode(png)
        return png

    def _post_process_barcode(self, png):
        """Apply transparent and/or inverted rendering to a barcode PNG."""
        if Image is None or not png:
            return png
        try:
            image = Image.open(io.BytesIO(png)).convert('RGBA')
            pixels = image.load()
            width, height = image.size
            for x in range(width):
                for y in range(height):
                    red, green, blue, alpha = pixels[x, y]
                    is_light = (red + green + blue) / 3 > 127
                    if self.barcode_inverted:
                        red = green = blue = 0 if is_light else 255
                        is_light = not is_light
                    if self.barcode_transparent and is_light:
                        alpha = 0
                    pixels[x, y] = (red, green, blue, alpha)
            if self.barcode_inverted and not self.barcode_transparent:
                # Inverted codes need the dark field behind them to stay opaque.
                background = Image.new('RGBA', image.size, (0, 0, 0, 255))
                image = Image.alpha_composite(background, image)
            buffer = io.BytesIO()
            image.save(buffer, format='PNG')
            return buffer.getvalue()
        except Exception:
            _logger.warning("Barcode post-processing failed.", exc_info=True)
            return png

    def _html_image(self, record, ctx):
        data = self._get_image_bytes(record, ctx)
        if not data:
            return Markup('')
        src = 'data:image/png;base64,%s' % base64.b64encode(data).decode()
        # background-size beats object-fit here: wkhtmltopdf's engine honours it.
        size = {'contain': 'contain', 'cover': 'cover', 'fill': '100% 100%'}[self.image_fit]
        return Markup(
            '<div style="width:100%%;height:100%%;background-image:url(\'%s\');'
            'background-size:%s;background-position:center;'
            'background-repeat:no-repeat;"></div>'
        ) % (Markup(src), Markup(size))

    def _html_wrapper(self, body, ctx, shape=False):
        """Position ``body`` on the label and apply every style the element carries."""
        self.ensure_one()
        unit = self.template_id.unit or 'mm'
        suffix = 'mm' if unit == 'mm' else 'in'
        style = [
            'position:absolute',
            'left:%s%s' % (css(self.pos_x), suffix),
            'top:%s%s' % (css(self.pos_y), suffix),
            'width:%s%s' % (css(self.width), suffix),
            'z-index:%d' % (self.z_index or 1),
            'box-sizing:border-box',
            'overflow:hidden',
        ]
        style.append('min-height:%s%s' % (css(self.height), suffix)
                     if self.auto_height else 'height:%s%s' % (css(self.height), suffix))

        if self.element_type == 'ellipse':
            style.append('border-radius:50%')
        elif self.border_radius:
            style.append('border-radius:%s%s' % (css(self.border_radius), suffix))

        filled = shape and self.fill
        if filled:
            style.append('background-color:%s' % (self.bg_color or '#000000'))
        elif not self.bg_transparent:
            style.append('background-color:%s' % (self.bg_color or '#FFFFFF'))
        else:
            style.append('background-color:transparent')

        if self.border_width:
            style.append('border:%s%s %s %s' % (
                css(self.border_width), suffix, self.border_style,
                self.border_color or '#000000'))
        if self.padding:
            style.append('padding:%s%s' % (css(self.padding), suffix))
        if self.opacity and self.opacity < 1:
            style.append('opacity:%s' % css(self.opacity))
        if self.rotation != '0':
            style.append('transform:rotate(%sdeg)' % self.rotation)
            style.append('transform-origin:center center')

        if not shape and self.element_type not in ('image', 'barcode', 'qrcode', 'vcard'):
            style.extend(self._text_style())
            # Flexbox is what gives us reliable vertical centring inside a fixed box.
            style.append('display:flex')
            style.append('flex-direction:column')
            style.append('justify-content:%s' % self.vertical_align)
            align = {'left': 'flex-start', 'center': 'center',
                     'right': 'flex-end', 'justify': 'stretch'}[self.text_align]
            style.append('align-items:%s' % align)

        return Markup('<div class="epg_label_element" style="%s">%s</div>') % (
            Markup(';'.join(style)), body)

    def _text_style(self):
        """CSS fragments for the typography half of the element."""
        unit_suffix = 'mm' if (self.template_id.unit or 'mm') == 'mm' else 'in'
        style = [
            'font-size:%spt' % css(self.font_size or 8.0),
            'line-height:%s' % css(self.line_height or 1.1),
            'text-align:%s' % self.text_align,
            'color:%s' % (self.text_color or '#000000'),
        ]
        if self.font_family and self.font_family != 'inherit':
            style.append('font-family:%s' % self.font_family)
        if self.font_bold:
            style.append('font-weight:bold')
        if self.font_italic:
            style.append('font-style:italic')
        decorations = []
        if self.font_underline:
            decorations.append('underline')
        if self.font_strike or (
                self.element_type in ('price', 'price_promo') and self.price_strikethrough):
            decorations.append('line-through')
        if decorations:
            style.append('text-decoration:%s' % ' '.join(decorations))
        if self.text_transform != 'none':
            style.append('text-transform:%s' % self.text_transform)
        if self.letter_spacing:
            style.append('letter-spacing:%s%s' % (css(self.letter_spacing), unit_suffix))
        style.append('white-space:normal' if self.text_wrap else 'white-space:nowrap')
        if not self.text_wrap:
            style.append('text-overflow:ellipsis')
        return style

    # ==================================================================
    # ZPL rendering
    # ==================================================================
    def _render_zpl(self, builder, record, ctx, offset_x=0.0, offset_y=0.0):
        """Append this element's ZPL commands to ``builder``."""
        self.ensure_one()
        if not self._is_visible(record, ctx):
            return
        unit = self.template_id.unit or 'mm'
        x = to_mm(self.pos_x, unit) + offset_x
        y = to_mm(self.pos_y, unit) + offset_y
        width = to_mm(self.width, unit)
        height = to_mm(self.height, unit)
        rotation = int(self.rotation or '0')

        if self.element_type == 'line':
            builder.line(x, y, width, height,
                         thickness_mm=max(to_mm(self.border_width, unit), height or 0.3))
            return
        if self.element_type == 'box':
            builder.box(x, y, width, height,
                        thickness_mm=to_mm(self.border_width, unit) or 0.3,
                        rounding=min(8, int(to_mm(self.border_radius, unit))),
                        fill=self.fill)
            return
        if self.element_type == 'ellipse':
            builder.ellipse(x, y, width, height,
                            thickness_mm=to_mm(self.border_width, unit) or 0.3)
            return
        if self.element_type == 'image':
            data = self._get_image_bytes(record, ctx)
            if data:
                builder.image(x, y, data, width, height,
                              threshold=self.image_threshold or 128)
            return
        if self.element_type == 'html':
            # Raw HTML has no ZPL equivalent; print the text it contains.
            text = re.sub(r'<[^>]+>', ' ',
                          self._render_placeholders(self.html_content or '', record, ctx))
            builder.text(x, y, ' '.join(text.split()), self.font_size, width,
                         rotation, self.text_align, self.font_bold)
            return

        if self.element_type in ('barcode', 'qrcode', 'vcard'):
            self._render_zpl_barcode(builder, record, ctx, x, y, width, height, rotation)
            return

        value = self._get_text_value(record, ctx)
        if not value:
            if not self.bg_transparent and not self.fill:
                return
            return
        if not self.bg_transparent and (self.bg_color or '').upper() in ('#000000', '#000'):
            # A black box with white text: paint the box, then knock the glyphs out.
            builder.reverse_field(x, y, width, height)
            builder.text(x, y, value, self.font_size, width, rotation,
                         self.text_align, self.font_bold, reverse=True)
            return
        if self.border_width:
            builder.box(x, y, width, height,
                        thickness_mm=to_mm(self.border_width, unit))
        # Nudge the baseline so the glyph cell sits inside the designed box.
        padding = to_mm(self.padding, unit)
        builder.text(x + padding, y + padding, value, self.font_size,
                     max(width - 2 * padding, 1), rotation, self.text_align,
                     self.font_bold, max_lines=max(1, int(height / max(
                         0.1, height if not self.text_wrap else 3.0))) if not self.text_wrap else 8)

    def _render_zpl_barcode(self, builder, record, ctx, x, y, width, height, rotation):
        if self.element_type == 'vcard':
            value, symbology = self._get_vcard_value(record, ctx), 'QR'
        elif self.element_type == 'qrcode':
            value, symbology = self._get_barcode_value(record, ctx), 'QR'
        else:
            value, symbology = self._get_barcode_value(record, ctx), self.barcode_type
        if not value:
            return
        if self.barcode_inverted:
            builder.reverse_field(x, y, width, height)
        if symbology == 'QR':
            builder.qrcode(x, y, value, size_mm=min(width, height), rotation=rotation,
                           error_correction=self.qr_error_correction,
                           reverse=self.barcode_inverted)
        elif symbology == 'DataMatrix':
            builder.datamatrix(x, y, value, size_mm=min(width, height), rotation=rotation)
        else:
            # Leave room for the interpretation line so it does not overflow the box.
            bar_height = height * (0.78 if self.barcode_humanreadable else 1.0)
            builder.barcode(
                x, y, value, symbology, height_mm=bar_height, width_mm=width,
                rotation=rotation, human_readable=self.barcode_humanreadable,
                module_width=self.barcode_module_width or 2,
                ratio=self.barcode_ratio or 3.0, reverse=self.barcode_inverted)

    # ==================================================================
    # designer helpers
    # ==================================================================
    def _designer_snapshot(self):
        """Writable field values of this element, for building an in-memory copy."""
        self.ensure_one()
        snapshot = {}
        for name, field in self._fields.items():
            if not field.store or field.compute or field.related:
                continue
            if field.type in ('one2many', 'many2many'):
                continue
            value = self[name]
            if field.type == 'many2one':
                value = value.id
            snapshot[name] = value
        return snapshot

    @api.model
    def check_barcode_value(self, symbology, value, humanreadable=False):
        """Validate a barcode payload before it reaches a scanner.

        A label with an unscannable barcode looks perfect on screen and is
        discovered at the till, so the designer says up front what a given
        symbology will accept and what this value would actually encode as.

        :return: ``{'state': ok|warning|error|empty, 'message': str,
                    'effective': str}`` where ``effective`` is the symbology that
                  will really be used once ``auto`` and the fallbacks resolve.
        """
        value = (value or '').strip()
        if not value:
            return {'state': 'empty', 'message': _("No value to encode yet."),
                    'effective': symbology or 'auto'}

        effective = ZplBuilder._resolve_symbology(symbology or 'auto', value)
        digits_only = value.isdigit()

        # Fixed-length numeric symbologies: length and check digit are both fatal.
        fixed = {'EAN13': 13, 'EAN8': 8, 'UPCA': 12}
        if effective in fixed:
            expected = fixed[effective]
            if not digits_only:
                return {'state': 'error', 'effective': effective, 'message': _(
                    "%(symbology)s only encodes digits; this value has letters or "
                    "punctuation. It will fall back to Code 128.",
                    symbology=effective)}
            if len(value) != expected:
                return {'state': 'error', 'effective': effective, 'message': _(
                    "%(symbology)s needs exactly %(expected)s digits, this has "
                    "%(actual)s. It will fall back to Code 128.",
                    symbology=effective, expected=expected, actual=len(value))}
            try:
                from odoo.tools.barcode import check_barcode_encoding, \
                    get_barcode_check_digit
                if not check_barcode_encoding(value, effective.lower()):
                    correct = get_barcode_check_digit(value)
                    return {'state': 'error', 'effective': effective, 'message': _(
                        "The check digit is wrong: %(symbology)s expects "
                        "%(correct)s as the last digit, not %(given)s.",
                        symbology=effective, correct=correct, given=value[-1])}
            except ImportError:  # pragma: no cover - core helper always present
                pass
            return {'state': 'ok', 'effective': effective, 'message': _(
                "Valid %s.", effective)}

        if effective == 'Code39':
            allowed = set('0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ-. $/+%')
            bad = sorted(set(value.upper()) - allowed)
            if bad:
                return {'state': 'error', 'effective': effective, 'message': _(
                    "Code 39 cannot encode: %s", ' '.join(bad))}
            if value != value.upper():
                return {'state': 'warning', 'effective': effective, 'message': _(
                    "Code 39 has no lower case; the value will print in capitals.")}
        elif effective == 'ITF':
            if not digits_only:
                return {'state': 'error', 'effective': effective, 'message': _(
                    "ITF only encodes digits.")}
            if len(value) % 2:
                return {'state': 'warning', 'effective': effective, 'message': _(
                    "ITF encodes digits in pairs; an odd length gets padded with a "
                    "leading zero.")}
        elif effective == 'QR':
            if len(value) > 1200:
                return {'state': 'warning', 'effective': effective, 'message': _(
                    "%s characters is a dense QR code - print it large, or lower "
                    "the error correction level.", len(value))}
            return {'state': 'ok', 'effective': effective, 'message': _(
                "Valid QR code, %s characters.", len(value))}
        elif effective == 'Code128':
            if any(ord(char) > 127 for char in value):
                return {'state': 'error', 'effective': effective, 'message': _(
                    "Code 128 is ASCII only; remove the accented or non-Latin "
                    "characters.")}
            if len(value) > 48:
                return {'state': 'warning', 'effective': effective, 'message': _(
                    "%s characters makes a very wide Code 128; check it still fits "
                    "the element.", len(value))}

        message = _("Valid %s.", effective)
        if (symbology or 'auto') == 'auto':
            message = _("Automatic: this value encodes as %s.", effective)
        return {'state': 'ok', 'effective': effective, 'message': message}

    @api.model
    def field_suggestions(self, model_name, path='', query=''):
        """List the fields reachable at ``path``, for the field-path picker.

        Walking relations by hand ('is it categ_id.name or product_categ.name?')
        is the part of a dotted path people get wrong, so the picker resolves each
        hop against the real model and offers only what exists there.

        :return: ``{'model': str, 'path': str, 'valid': bool, 'fields': [...]}``
        """
        model = self.env.get(model_name)
        if model is None:
            return {'model': model_name, 'path': path, 'valid': False, 'fields': []}

        # Walk the relational prefix; the last segment is what the user is typing.
        parts = [part for part in (path or '').split('.') if part]
        current, walked = model, []
        for part in parts:
            field = current._fields.get(part)
            if field is None or not field.relational:
                break
            current = self.env[field.comodel_name]
            walked.append(part)

        prefix = '.'.join(walked)
        query = (query or '').lower()
        suggestions = []
        for name, field in current._fields.items():
            if not field.store and not field.compute:
                continue
            label = str(field.string or name)
            if query and query not in name.lower() and query not in label.lower():
                continue
            suggestions.append({
                'name': name,
                'path': '%s.%s' % (prefix, name) if prefix else name,
                'label': label,
                'type': field.type,
                'relational': bool(field.relational),
                'comodel': field.comodel_name if field.relational else False,
                'help': (field.help or '').split('\n')[0][:120] if field.help else '',
            })
        # Stored scalars first: they are what a label prints most of the time.
        suggestions.sort(key=lambda item: (item['relational'], item['label'].lower()))
        return {
            'model': current._name,
            'path': prefix,
            'valid': len(walked) == len(parts),
            'fields': suggestions[:80],
        }

    @api.model
    def resolve_field_path(self, model_name, path):
        """Describe where a dotted path lands, for the picker's status line."""
        model = self.env.get(model_name)
        if model is None or not path:
            return {'valid': False, 'label': '', 'type': ''}
        current, labels = model, []
        for part in path.split('.'):
            field = current._fields.get(part) if current is not None else None
            if field is None:
                return {'valid': False, 'label': ' › '.join(labels),
                        'type': '', 'error': _("'%s' does not exist here.", part)}
            labels.append(str(field.string or part))
            current = self.env[field.comodel_name] if field.relational else None
        return {'valid': True, 'label': ' › '.join(labels), 'type': field.type,
                'relational': bool(field.relational)}

    def action_move(self, pos_x, pos_y, width=None, height=None):
        """Called by the drag-and-drop canvas to write a new geometry back."""
        self.ensure_one()
        values = {'pos_x': round(float(pos_x), 3), 'pos_y': round(float(pos_y), 3)}
        if width is not None:
            values['width'] = max(0.1, round(float(width), 3))
        if height is not None:
            values['height'] = max(0.1, round(float(height), 3))
        self.write(values)
        return True

    def action_duplicate(self):
        self.ensure_one()
        unit = self.template_id.unit or 'mm'
        step = 2.0 if unit == 'mm' else 0.08
        copy = self.copy({
            'name': _('%s (copy)', self.name),
            'pos_x': self.pos_x + step,
            'pos_y': self.pos_y + step,
        })
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'epg.label.element',
            'res_id': copy.id,
            'view_mode': 'form',
            'target': 'new',
        }

    @api.ondelete(at_uninstall=False)
    def _unlink_except_locked_template(self):
        locked = self.filtered(lambda element: element.template_id.is_predefined
                               and not self.env.user.has_group('base.group_system'))
        if locked:
            raise UserError(_(
                "Predefined templates can only be edited by administrators. "
                "Duplicate the template first, then edit the copy."))
