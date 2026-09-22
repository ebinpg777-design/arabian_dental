# -*- coding: utf-8 -*-
"""A label template: the sheet or roll geometry, plus the elements printed on it.

The template owns three things the elements do not: where each label sits on the
page, the paper format that has to follow that geometry, and the ZPL printer
settings.  It is also the entry point for both render engines and for the XML
import/export used to move designs between databases.
"""

import base64
import logging
from lxml import etree

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .label_units import ZPL_DENSITY_SELECTION, css, to_mm
from .label_zpl import ZplBuilder, encode_image_b64

_logger = logging.getLogger(__name__)

# Printable area of the paper sizes we offer, in millimetres.
PAGE_SIZES = {
    'A4': (210.0, 297.0),
    'A5': (148.0, 210.0),
    'Letter': (215.9, 279.4),
    'Legal': (215.9, 355.6),
}

# Unsaved element values the designer canvas may preview.  ``condition`` is left out
# on purpose: it is evaluated as Python, and a preview must not run code that was
# never saved through the element's access rules.
DESIGNER_PATCH_FIELDS = frozenset((
    'pos_x', 'pos_y', 'width', 'height', 'rotation', 'z_index', 'opacity',
    'font_size', 'font_bold', 'text_align', 'name', 'element_type',
    'field_path', 'text_content', 'active',
))

LABEL_MODELS = {
    'product': 'product.template',
    'partner': 'res.partner',
    'lot': 'stock.lot',
}


class EpgLabelTemplate(models.Model):
    _name = 'epg.label.template'
    _description = 'Label Template'
    _inherit = ['mail.thread']
    _order = 'sequence, name'

    name = fields.Char(required=True, tracking=True)
    code = fields.Char(
        string='Reference', copy=False, index=True,
        help="Short unique code, handy when importing or exporting templates.")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True, tracking=True)
    note = fields.Text(string='Internal Notes')
    company_id = fields.Many2one(
        'res.company', string='Company', default=lambda self: self.env.company,
        help="Leave empty to share the template across every company.")
    is_predefined = fields.Boolean(
        string='Predefined', default=False, copy=False, readonly=True,
        help="Shipped with the module. Duplicate it rather than editing it, so a "
             "module update does not overwrite your changes.")

    label_type = fields.Selection([
        ('product', 'Product'),
        ('partner', 'Contact / Address'),
        ('lot', 'Lot / Serial Number'),
    ], string='Label For', default='product', required=True, tracking=True)
    model_name = fields.Char(compute='_compute_model_name', store=True)

    # ------------------------------------------------------------------
    # geometry
    # ------------------------------------------------------------------
    unit = fields.Selection(
        [('mm', 'Millimetres'), ('in', 'Inches')],
        string='Unit', default='mm', required=True)
    label_width = fields.Float(string='Label Width', default=57.0, required=True)
    label_height = fields.Float(string='Label Height', default=38.0, required=True)

    layout_mode = fields.Selection([
        ('sheet', 'Label sheet (grid on a page)'),
        ('roll', 'Roll / continuous (one label per page)'),
    ], string='Layout', default='sheet', required=True,
        help="Sheets print a grid of labels on ordinary paper. Roll prints one label "
             "per page, sized to the label - the mode to use for thermal printers.")

    stock_id = fields.Many2one(
        'epg.label.stock', string='Label Stock',
        help="Pick the stock you actually load into the printer and every size, "
             "margin and gap below is filled in from the manufacturer's spec.")
    stock_mismatch = fields.Boolean(
        compute='_compute_stock_mismatch',
        help="True once the geometry has been edited away from the chosen stock.")

    page_format = fields.Selection(
        [(key, key) for key in PAGE_SIZES] + [('custom', 'Custom')],
        string='Paper Size', default='A4', required=True)
    page_width = fields.Float(string='Paper Width', compute='_compute_page_size',
                              store=True, readonly=False)
    page_height = fields.Float(string='Paper Height', compute='_compute_page_size',
                               store=True, readonly=False)
    orientation = fields.Selection(
        [('Portrait', 'Portrait'), ('Landscape', 'Landscape')],
        string='Orientation', default='Portrait', required=True)

    margin_top = fields.Float(string='Top Margin', default=8.0)
    margin_left = fields.Float(string='Left Margin', default=5.0)
    margin_right = fields.Float(string='Right Margin', default=5.0)
    margin_bottom = fields.Float(string='Bottom Margin', default=8.0)
    column_gap = fields.Float(string='Horizontal Gap', default=2.0)
    row_gap = fields.Float(string='Vertical Gap', default=0.0)

    auto_grid = fields.Boolean(
        string='Fit Grid Automatically', default=True,
        help="Work out how many labels fit on the page from the label and paper "
             "sizes. Uncheck to set the number of columns and rows yourself.")
    # No `default` on these two: a default counts as an explicit value, which would
    # stop the compute from ever running on a new template.
    columns = fields.Integer(string='Columns', compute='_compute_grid',
                             store=True, readonly=False)
    rows = fields.Integer(string='Rows', compute='_compute_grid',
                          store=True, readonly=False)
    labels_per_page = fields.Integer(
        compute='_compute_labels_per_page', store=True)

    # ------------------------------------------------------------------
    # look
    # ------------------------------------------------------------------
    default_font_family = fields.Selection([
        ('Arial, Helvetica, sans-serif', 'Arial / Helvetica'),
        ('"Times New Roman", Times, serif', 'Times New Roman'),
        ('"Courier New", Courier, monospace', 'Courier New'),
        ('Verdana, Geneva, sans-serif', 'Verdana'),
        ('Tahoma, Geneva, sans-serif', 'Tahoma'),
        ('Georgia, serif', 'Georgia'),
    ], string='Default Font', default='Arial, Helvetica, sans-serif', required=True)
    default_font_size = fields.Float(string='Default Font Size (pt)', default=8.0)
    label_bg_color = fields.Char(string='Background Colour', default='#FFFFFF')
    label_bg_transparent = fields.Boolean(string='Transparent Background', default=True)
    label_border = fields.Boolean(
        string='Draw Label Border', default=False,
        help="Outline every label. Useful while designing and when cutting by hand.")
    label_border_color = fields.Char(string='Border Colour', default='#000000')
    label_border_width = fields.Float(string='Border Width', default=0.2)
    label_border_radius = fields.Float(string='Corner Radius', default=0.0)

    bg_image_source = fields.Selection([
        ('none', 'None'),
        ('static', 'Uploaded Image'),
        ('field', 'Field on the Record'),
    ], string='Background Image', default='none', required=True)
    bg_image = fields.Binary(string='Image', attachment=True)
    bg_image_filename = fields.Char()
    bg_image_field = fields.Char(
        string='Image Field Path',
        help="Dotted path to a binary field, e.g. 'image_1920' or 'categ_id.image'.")
    bg_image_fit = fields.Selection([
        ('contain', 'Fit inside'), ('cover', 'Fill and crop'), ('fill', 'Stretch'),
    ], string='Image Scaling', default='cover', required=True)
    bg_image_opacity = fields.Float(string='Image Opacity', default=1.0)

    # ------------------------------------------------------------------
    # elements
    # ------------------------------------------------------------------
    element_ids = fields.One2many(
        'epg.label.element', 'template_id', string='Elements', copy=True)
    element_count = fields.Integer(compute='_compute_element_count')

    # ------------------------------------------------------------------
    # output
    # ------------------------------------------------------------------
    output_type = fields.Selection([
        ('pdf', 'PDF only'),
        ('zpl', 'ZPL only'),
        ('both', 'PDF and ZPL'),
    ], string='Output', default='pdf', required=True)
    pdf_dpi = fields.Integer(
        string='PDF DPI', default=96,
        help="Leave at 96 unless the printed size drifts: that is the resolution CSS "
             "millimetres assume.")

    zpl_use_global = fields.Boolean(
        string='Use Global ZPL Settings', default=True,
        help="Take print density, rotation and encoding from Inventory > "
             "Configuration > Settings.")
    zpl_density = fields.Selection(
        ZPL_DENSITY_SELECTION, string='Print Density', default='8')
    zpl_rotation = fields.Selection([
        ('0', 'None'), ('90', '90°'), ('180', '180°'), ('270', '270°'),
    ], string='ZPL Rotation', default='0')
    zpl_encoding = fields.Selection([
        ('utf8', 'UTF-8 (^CI28)'),
        ('cp850', 'CP850'),
        ('ascii', 'ASCII only'),
    ], string='Character Encoding', default='utf8')
    zpl_darkness = fields.Integer(
        string='Darkness (^MD)', default=0,
        help="Relative media darkness, -30 to 30. 0 leaves the printer setting alone.")
    zpl_speed = fields.Integer(
        string='Print Speed (^PR)', default=0,
        help="Inches per second, 1 to 14. 0 leaves the printer setting alone.")
    zpl_prefix = fields.Text(
        string='ZPL Prefix', help="Raw ZPL inserted before every label.")
    zpl_suffix = fields.Text(
        string='ZPL Suffix', help="Raw ZPL inserted after every label.")

    paperformat_id = fields.Many2one(
        'report.paperformat', string='Paper Format', copy=False, readonly=True,
        help="Maintained automatically from the layout settings above.")

    # ------------------------------------------------------------------
    # access
    # ------------------------------------------------------------------
    user_ids = fields.Many2many(
        'res.users', 'epg_label_template_users_rel', 'template_id', 'user_id',
        string='Restricted to Users',
        help="Leave empty to make the template available to everyone. Administrators "
             "always see every template.")
    default_user_ids = fields.One2many(
        'res.users', 'epg_default_label_template_id',
        string='Default for Users', readonly=True,
        help="Users whose print wizard opens on this template.")

    # ------------------------------------------------------------------
    # preview
    # ------------------------------------------------------------------
    preview_product_id = fields.Many2one(
        'product.product', string='Preview Product',
        help="Record used for the designer preview. Falls back to the one set in "
             "the general settings, then to any product.")
    preview_partner_id = fields.Many2one('res.partner', string='Preview Contact')
    preview_pricelist_id = fields.Many2one(
        'product.pricelist', string='Preview Pricelist')
    preview_promo_pricelist_id = fields.Many2one(
        'product.pricelist', string='Preview Promotional Pricelist')
    preview_html = fields.Html(
        string='Preview', compute='_compute_preview_html', sanitize=False)
    thumbnail_html = fields.Html(
        string='Thumbnail', compute='_compute_thumbnail_html', sanitize=False,
        help="The label rendered small enough for a gallery card.")
    designer_data = fields.Char(
        compute='_compute_designer_data',
        help="Serialised geometry consumed by the drag-and-drop canvas.")

    # ------------------------------------------------------------------
    # usage
    # ------------------------------------------------------------------
    print_log_ids = fields.One2many(
        'epg.label.print.log', 'template_id', string='Print History')
    print_log_count = fields.Integer(compute='_compute_print_log_count')
    printed_label_count = fields.Integer(
        compute='_compute_print_log_count', string='Labels Printed')
    last_print_date = fields.Datetime(
        compute='_compute_print_log_count', string='Last Printed')

    _unique_code = models.Constraint(
        'UNIQUE(code)', 'A label template with this reference already exists.')

    # ==================================================================
    # computes
    # ==================================================================
    @api.depends('label_type')
    def _compute_model_name(self):
        for template in self:
            template.model_name = LABEL_MODELS.get(template.label_type, 'product.template')

    @api.depends('element_ids')
    def _compute_element_count(self):
        for template in self:
            template.element_count = len(template.element_ids)

    @api.depends('print_log_ids')
    def _compute_print_log_count(self):
        grouped = self.env['epg.label.print.log']._read_group(
            [('template_id', 'in', self.ids)], ['template_id'],
            ['__count', 'label_count:sum', 'print_date:max'])
        data = {template.id: row for template, *row in grouped}
        for template in self:
            count, labels, last = data.get(template.id, (0, 0, False))
            template.print_log_count = count
            template.printed_label_count = labels or 0
            template.last_print_date = last

    @api.depends('stock_id', 'label_width', 'label_height', 'unit', 'layout_mode',
                 'columns', 'rows', 'margin_top', 'margin_left', 'page_format')
    def _compute_stock_mismatch(self):
        """Warn when the geometry has drifted from the stock it claims to be.

        Editing the size after choosing a stock is legitimate, but silently
        printing 'Avery L7160' onto something that is no longer L7160 is how a
        whole box of sheets gets wasted.
        """
        for template in self:
            stock = template.stock_id
            if not stock:
                template.stock_mismatch = False
                continue
            template.stock_mismatch = any(
                abs(float(template[name] or 0) - float(stock[name] or 0)) > 0.01
                if isinstance(stock[name], float) else template[name] != stock[name]
                for name in ('unit', 'label_width', 'label_height', 'layout_mode',
                             'columns', 'rows'))

    @api.onchange('stock_id')
    def _onchange_stock_id(self):
        """Copy the manufacturer's geometry onto the template."""
        for template in self:
            if not template.stock_id:
                continue
            values = template.stock_id.geometry_values()
            if not template.name:
                values['name'] = template.stock_id.name
            if isinstance(template.id, int) and template.id:
                # A saved record has to take the geometry in a single write.
                # Assigning field by field would let the size check see a label
                # that has already grown on a page that has not, and reject a
                # combination that is perfectly valid once both have landed.
                template.write(values)
            else:
                template.update(values)

    @api.depends('page_format', 'orientation', 'layout_mode',
                 'label_width', 'label_height', 'unit',
                 'margin_top', 'margin_bottom', 'margin_left', 'margin_right')
    def _compute_page_size(self):
        for template in self:
            if template.layout_mode == 'roll':
                # The page *is* the label, plus whatever margin the user asked for.
                template.page_width = (template.label_width
                                       + template.margin_left + template.margin_right)
                template.page_height = (template.label_height
                                        + template.margin_top + template.margin_bottom)
            elif template.page_format in PAGE_SIZES:
                width, height = PAGE_SIZES[template.page_format]
                if template.orientation == 'Landscape':
                    width, height = height, width
                if template.unit == 'in':
                    width, height = width / 25.4, height / 25.4
                template.page_width, template.page_height = width, height
            else:
                # Custom size: keep whatever the designer typed, but never leave the
                # page at zero - a compute has to assign something to every record.
                template.page_width = template.page_width or 210.0
                template.page_height = template.page_height or 297.0

    @api.depends('auto_grid', 'layout_mode', 'page_width', 'page_height',
                 'label_width', 'label_height', 'column_gap', 'row_gap',
                 'margin_top', 'margin_bottom', 'margin_left', 'margin_right')
    def _compute_grid(self):
        for template in self:
            if template.layout_mode == 'roll':
                template.columns = template.rows = 1
            elif template.auto_grid:
                template.columns = template._fit(
                    template.page_width - template.margin_left - template.margin_right,
                    template.label_width, template.column_gap)
                template.rows = template._fit(
                    template.page_height - template.margin_top - template.margin_bottom,
                    template.label_height, template.row_gap)
            else:
                # Manual grid: leave the designer's numbers alone, but every branch
                # of a compute still has to assign, so fall back to a single label.
                template.columns = template.columns or 1
                template.rows = template.rows or 1

    @api.depends('columns', 'rows')
    def _compute_labels_per_page(self):
        for template in self:
            template.labels_per_page = max(
                1, (template.columns or 1) * (template.rows or 1))

    @staticmethod
    def _fit(available, size, gap):
        """How many ``size`` cells, separated by ``gap``, fit into ``available``."""
        if size <= 0:
            return 1
        count = 1
        while (count + 1) * size + count * gap <= available + 1e-6:
            count += 1
        return max(1, count)

    @api.depends('element_ids', 'element_ids.pos_x', 'element_ids.pos_y',
                 'element_ids.width', 'element_ids.height', 'element_ids.name',
                 'element_ids.element_type', 'element_ids.active',
                 'label_width', 'label_height', 'unit')
    def _compute_designer_data(self):
        import json
        for template in self:
            template.designer_data = json.dumps({
                'unit': template.unit,
                'label_width': template.label_width,
                'label_height': template.label_height,
                'elements': [{
                    'id': element.id,
                    'name': element.name,
                    'type': element.element_type,
                    'x': element.pos_x,
                    'y': element.pos_y,
                    'width': element.width,
                    'height': element.height,
                } for element in template.element_ids if element.active],
            })

    @api.depends('element_ids', 'label_width', 'label_height', 'unit',
                 'label_bg_color', 'label_bg_transparent', 'label_border',
                 'label_border_color', 'label_border_width', 'label_border_radius',
                 'bg_image_source', 'bg_image', 'bg_image_field', 'bg_image_fit',
                 'default_font_family', 'default_font_size',
                 'preview_product_id', 'preview_partner_id',
                 'preview_pricelist_id', 'preview_promo_pricelist_id')
    def _compute_preview_html(self):
        for template in self:
            try:
                record = template._get_preview_record()
                context = template._build_render_context(
                    pricelist=template.preview_pricelist_id,
                    promo_pricelist=template.preview_promo_pricelist_id)
                label = template._render_label_html(record, context)
                template.preview_html = Markup(
                    '<div class="epg_label_preview_wrapper">%s%s</div>'
                ) % (Markup(template._preview_style()), label)
            except Exception as error:  # a broken element must not break the form
                _logger.warning("Label preview failed for %s.", template.name,
                                exc_info=True)
                template.preview_html = Markup(
                    '<div class="alert alert-warning">%s</div>'
                ) % _("Preview unavailable: %s", error)

    def _preview_style(self):
        """Scoped CSS so the preview in the form looks like the printed label."""
        return (
            '<style>'
            '.epg_label_preview_wrapper{padding:12px;background:#f1f3f5;'
            'display:flex;justify-content:center;overflow:auto;}'
            '.epg_label_preview_wrapper .epg_label{box-shadow:0 1px 6px rgba(0,0,0,.25);}'
            '</style>'
        )

    # Longest edge, in CSS pixels, that a gallery thumbnail may occupy.
    THUMBNAIL_SIZE = 190

    @api.depends('element_ids', 'label_width', 'label_height', 'unit',
                 'label_bg_color', 'label_bg_transparent', 'label_border',
                 'bg_image_source', 'bg_image')
    def _compute_thumbnail_html(self):
        """Render the real label, then shrink it with a CSS transform.

        Re-laying the design out at thumbnail scale would mean a second renderer to
        keep in step with the first.  Scaling the genuine article instead means the
        card in the gallery cannot disagree with what comes out of the printer.
        """
        for template in self:
            try:
                record = template._get_preview_record()
                context = template._build_render_context(
                    pricelist=template.preview_pricelist_id,
                    promo_pricelist=template.preview_promo_pricelist_id,
                    extra={'thumbnail': True})
                label = template._render_label_html(record, context)
            except Exception:
                _logger.debug("Thumbnail failed for %s.", template.name, exc_info=True)
                template.thumbnail_html = False
                continue
            # 1 mm is about 3.78 CSS px; work out the factor that fits the card.
            px_per_unit = 3.7795 if template.unit == 'mm' else 96.0
            width_px = max(1.0, template.label_width * px_per_unit)
            height_px = max(1.0, template.label_height * px_per_unit)
            scale = min(self.THUMBNAIL_SIZE / width_px,
                        self.THUMBNAIL_SIZE / height_px, 1.6)
            template.thumbnail_html = Markup(
                '<div class="epg_label_thumb" style="width:%spx;height:%spx;">'
                '<div class="epg_label_thumb_inner" style="transform:scale(%s);">'
                '%s</div></div>'
            ) % (css(width_px * scale), css(height_px * scale), css(scale), label)

    # ==================================================================
    # designer canvas
    # ==================================================================
    @api.model
    def render_designer(self, template_id, element_ids=None, values=None):
        """Render elements the way they will print, for the drag-and-drop canvas.

        The canvas is an ordinary DOM tree, so it can show the true label instead
        of grey placeholder boxes - a barcode that will not fit its box is worth
        seeing while you are still dragging it, not after a test print.

        :param element_ids: restrict to these elements; ``None`` renders them all
        :param values: unsaved field values, keyed by element id, applied on top of
            what is stored so the canvas keeps up with an edit in the same form
        :return: ``{'label_style': str, 'elements': {id: html}, 'background': html}``
        """
        if not self.env.user.has_group('epg_product_label.group_label_designer'):
            raise AccessError(_("Only label designers can use the label designer."))
        template = self.browse(template_id).exists()
        if not template:
            return {'label_style': '', 'elements': {}, 'background': ''}
        template.check_access('read')
        elements = template.element_ids
        if element_ids:
            elements = elements.filtered(lambda e: e.id in set(element_ids))
        record = template._get_preview_record()
        context = template._build_render_context(
            pricelist=template.preview_pricelist_id,
            promo_pricelist=template.preview_promo_pricelist_id,
            extra={'designer': True})
        rendered = {}
        for element in elements:
            patch = (values or {}).get(str(element.id)) or (values or {}).get(element.id)
            patch = {name: value for name, value in (patch or {}).items()
                     if name in DESIGNER_PATCH_FIELDS}
            live = element
            if patch:
                # ``new`` gives a throw-away in-memory record: the canvas can preview
                # a change the user has not saved without touching the database.
                live = element.new(
                    {**element._designer_snapshot(), **patch}, origin=element)
            try:
                rendered[element.id] = str(live._render_html(record, context))
            except Exception:
                _logger.debug("Designer render failed for element %s.", element.id,
                              exc_info=True)
                rendered[element.id] = ''
        return {
            'label_style': template._label_style(record, context),
            'background': str(template._background_image_html(record, context)),
            'elements': rendered,
            'record_name': record.display_name if record else '',
        }

    # ==================================================================
    # constraints
    # ==================================================================
    @api.constrains('label_width', 'label_height', 'page_width', 'page_height')
    def _check_sizes(self):
        for template in self:
            if template.label_width <= 0 or template.label_height <= 0:
                raise ValidationError(_("The label size must be greater than zero."))
            if template.layout_mode == 'sheet':
                usable_width = (template.page_width - template.margin_left
                                - template.margin_right)
                usable_height = (template.page_height - template.margin_top
                                 - template.margin_bottom)
                if template.label_width > usable_width + 1e-6:
                    raise ValidationError(_(
                        "A %(label)s wide label does not fit in the %(page)s of "
                        "printable width left by the margins.",
                        label=template._display_size(template.label_width),
                        page=template._display_size(usable_width)))
                if template.label_height > usable_height + 1e-6:
                    raise ValidationError(_(
                        "A %(label)s tall label does not fit in the %(page)s of "
                        "printable height left by the margins.",
                        label=template._display_size(template.label_height),
                        page=template._display_size(usable_height)))

    def _display_size(self, value):
        return '%s %s' % (css(value), self.unit)

    @api.constrains('zpl_darkness', 'zpl_speed')
    def _check_zpl_ranges(self):
        for template in self:
            if not -30 <= template.zpl_darkness <= 30:
                raise ValidationError(_("ZPL darkness must be between -30 and 30."))
            if template.zpl_speed and not 1 <= template.zpl_speed <= 14:
                raise ValidationError(_("ZPL print speed must be between 1 and 14."))

    # ==================================================================
    # CRUD - keep the paper format in step with the layout
    # ==================================================================
    PAPERFORMAT_TRIGGERS = (
        'name', 'layout_mode', 'page_format', 'orientation', 'unit',
        'page_width', 'page_height', 'label_width', 'label_height',
        'margin_top', 'margin_bottom', 'margin_left', 'margin_right', 'pdf_dpi',
    )

    @api.model_create_multi
    def create(self, vals_list):
        vals_list = [self._apply_stock_geometry(vals) for vals in vals_list]
        templates = super().create(vals_list)
        templates._sync_paperformat()
        return templates

    def _apply_stock_geometry(self, vals):
        """Fill the geometry from ``stock_id`` for anything the caller left out.

        The onchange covers the form, but a template can also arrive from an
        import, a data file or another module - and a stock reference that
        quietly failed to size the label would only show up on the first
        misaligned sheet.
        """
        if not vals.get('stock_id'):
            return vals
        stock = self.env['epg.label.stock'].browse(vals['stock_id']).exists()
        if not stock:
            return vals
        # Explicit values win: passing both means the caller wants the override.
        return {**stock.geometry_values(), **vals}

    def write(self, vals):
        if vals.get('stock_id') and not any(
                key in vals for key in ('label_width', 'label_height', 'columns', 'rows')):
            stock = self.env['epg.label.stock'].browse(vals['stock_id']).exists()
            if stock:
                vals = {**vals, **stock.geometry_values()}
        result = super().write(vals)
        if any(key in vals for key in self.PAPERFORMAT_TRIGGERS):
            self._sync_paperformat()
        return result

    def copy_data(self, default=None):
        vals_list = super().copy_data(default=default)
        for template, values in zip(self, vals_list):
            values.setdefault('name', _('%s (copy)', template.name))
            values['is_predefined'] = False
            values['code'] = False
            values['paperformat_id'] = False
        return vals_list

    @api.ondelete(at_uninstall=False)
    def _unlink_except_predefined(self):
        if any(template.is_predefined for template in self) \
                and not self.env.user.has_group('base.group_system'):
            raise UserError(_(
                "Predefined templates cannot be deleted. Archive them instead."))

    def unlink(self):
        formats = self.paperformat_id.sudo()
        result = super().unlink()
        # Only ours to remove, and only once nothing else points at them.  sudo: the
        # paper format ACL is admin-only, the template unlink above is the real check.
        formats.filtered(lambda fmt: not fmt.report_ids).unlink()
        return result

    def _sync_paperformat(self):
        """Create or update the report.paperformat that matches this layout.

        wkhtmltopdf sizes the page, not the CSS; a 100x100 roll label printed on an
        A4 paper format comes out as a 100x100 mark in the corner of an A4 sheet.
        """
        Paperformat = self.env['report.paperformat'].sudo()
        for template in self:
            width_mm = to_mm(template.page_width, template.unit)
            height_mm = to_mm(template.page_height, template.unit)
            values = {
                'name': _('Label: %s', template.name),
                'default': False,
                'format': 'custom',
                'page_width': int(round(width_mm)),
                'page_height': int(round(height_mm)),
                'orientation': 'Portrait',
                # Every offset is handled in CSS so the two engines agree; letting
                # wkhtmltopdf add its own margins on top would double them.
                'margin_top': 0.0,
                'margin_bottom': 0.0,
                'margin_left': 0.0,
                'margin_right': 0.0,
                'header_line': False,
                'header_spacing': 0,
                'dpi': template.pdf_dpi or 96,
                'disable_shrinking': True,
            }
            if template.paperformat_id:
                # Only Settings admins may write paper formats; a designer's right to
                # change the template was checked by the write that brought us here.
                template.paperformat_id.sudo().write(values)
            else:
                template.paperformat_id = Paperformat.create(values)

    # ==================================================================
    # render context
    # ==================================================================
    def _build_render_context(self, pricelist=None, promo_pricelist=None,
                              partner=None, company=None, quantity=1.0, extra=None):
        """Everything the elements need that does not live on the record itself."""
        self.ensure_one()
        company = company or self.company_id or self.env.company
        context = {
            'pricelist': pricelist or self.env['product.pricelist'],
            'promo_pricelist': promo_pricelist or self.env['product.pricelist'],
            'partner': partner,
            'company': company,
            'currency': (pricelist.currency_id if pricelist else None)
                        or company.currency_id,
            'price_quantity': quantity or 1.0,
            'print_date': fields.Date.context_today(self),
            'template': self,
        }
        if extra:
            context.update(extra)
        return context

    def _get_preview_record(self):
        """Pick a sensible record for the designer preview."""
        self.ensure_one()
        parameters = self.env['ir.config_parameter'].sudo()
        if self.label_type == 'partner':
            partner = self.preview_partner_id
            if not partner:
                partner_id = parameters.get_param('epg_product_label.preview_partner_id')
                partner = self.env['res.partner'].browse(
                    int(partner_id)).exists() if partner_id else None
            return partner or self.env.company.partner_id
        if self.label_type == 'lot':
            return self.env['stock.lot'].search([], limit=1)
        product = self.preview_product_id
        if not product:
            product_id = parameters.get_param('epg_product_label.preview_product_id')
            product = self.env['product.product'].browse(
                int(product_id)).exists() if product_id else None
        return product or self.env['product.product'].search(
            [('barcode', '!=', False)], limit=1) \
            or self.env['product.product'].search([], limit=1)

    # ==================================================================
    # HTML rendering
    # ==================================================================
    def _label_style(self, record=None, context=None):
        """CSS for one label box, without its position on the page."""
        self.ensure_one()
        suffix = 'mm' if self.unit == 'mm' else 'in'
        style = [
            'width:%s%s' % (css(self.label_width), suffix),
            'height:%s%s' % (css(self.label_height), suffix),
            'position:relative',
            'overflow:hidden',
            'box-sizing:border-box',
            'font-family:%s' % self.default_font_family,
            'font-size:%spt' % css(self.default_font_size or 8.0),
            'line-height:1.1',
        ]
        if not self.label_bg_transparent:
            style.append('background-color:%s' % (self.label_bg_color or '#FFFFFF'))
        if self.label_border and self.label_border_width:
            style.append('border:%s%s solid %s' % (
                css(self.label_border_width), suffix,
                self.label_border_color or '#000000'))
        if self.label_border_radius:
            style.append('border-radius:%s%s' % (css(self.label_border_radius), suffix))
        return ';'.join(style)

    def _background_image_html(self, record, context):
        """The label background image layer, if the template defines one."""
        self.ensure_one()
        data = None
        if self.bg_image_source == 'static':
            data = encode_image_b64(self.bg_image)
        elif self.bg_image_source == 'field' and self.bg_image_field and record:
            element = self.env['epg.label.element'].new({
                'template_id': self.id, 'name': 'bg'})
            data = encode_image_b64(
                element._resolve_path(record, self.bg_image_field))
        if not data:
            return Markup('')
        size = {'contain': 'contain', 'cover': 'cover',
                'fill': '100% 100%'}[self.bg_image_fit]
        source = 'data:image/png;base64,%s' % base64.b64encode(data).decode()
        return Markup(
            '<div style="position:absolute;inset:0;left:0;top:0;right:0;bottom:0;'
            'background-image:url(\'%s\');background-size:%s;'
            'background-position:center;background-repeat:no-repeat;'
            'opacity:%s;z-index:0;"></div>'
        ) % (Markup(source), Markup(size), css(self.bg_image_opacity or 1.0))

    def _render_label_html(self, record, context, extra_style=''):
        """Render a single label - background layer plus every visible element."""
        self.ensure_one()
        body = [self._background_image_html(record, context)]
        for element in self.element_ids.sorted(lambda e: (e.z_index, e.sequence, e.id)):
            body.append(element._render_html(record, context))
        style = self._label_style(record, context)
        if extra_style:
            style = '%s;%s' % (style, extra_style)
        return Markup('<div class="epg_label" style="%s">%s</div>') % (
            Markup(style), Markup('').join(body))

    def _render_pages_html(self, slots, context, skip=0):
        """Lay ``slots`` out on pages and return one HTML block per page.

        ``slots`` is a flat list of ``(record, per_label_context)`` pairs, already
        expanded by copy count.  ``skip`` leaves that many positions empty at the
        start of the first page, which is how you use a part-consumed label sheet.
        """
        self.ensure_one()
        suffix = 'mm' if self.unit == 'mm' else 'in'
        per_page = self.labels_per_page
        columns = max(1, self.columns)
        positions = [None] * min(skip, per_page - 1 if per_page > 1 else 0)
        positions.extend(slots)

        pages = []
        for start in range(0, max(len(positions), 1), per_page):
            chunk = positions[start:start + per_page]
            cells = []
            for index, slot in enumerate(chunk):
                if slot is None:
                    continue
                record, slot_context = slot
                column, row = index % columns, index // columns
                left = self.margin_left + column * (self.label_width + self.column_gap)
                top = self.margin_top + row * (self.label_height + self.row_gap)
                cells.append(self._render_label_html(
                    record, slot_context,
                    extra_style='position:absolute;left:%s%s;top:%s%s' % (
                        css(left), suffix, css(top), suffix)))
            pages.append(Markup('').join(cells))
        return pages

    def _page_style(self):
        suffix = 'mm' if self.unit == 'mm' else 'in'
        return (
            'position:relative;box-sizing:border-box;overflow:hidden;'
            'width:%s%s;height:%s%s;margin:0;padding:0;' % (
                css(self.page_width), suffix, css(self.page_height), suffix))

    # ==================================================================
    # ZPL rendering
    # ==================================================================
    def _zpl_settings(self):
        """Resolve the ZPL settings, falling back to the global configuration."""
        self.ensure_one()
        if not self.zpl_use_global:
            return {
                'density': self.zpl_density or '8',
                'rotation': int(self.zpl_rotation or '0'),
                'encoding': self.zpl_encoding or 'utf8',
                'darkness': self.zpl_darkness or None,
                'speed': self.zpl_speed or None,
            }
        parameters = self.env['ir.config_parameter'].sudo()
        return {
            'density': parameters.get_param(
                'epg_product_label.zpl_density', '8'),
            'rotation': int(parameters.get_param(
                'epg_product_label.zpl_rotation', '0') or 0),
            'encoding': parameters.get_param(
                'epg_product_label.zpl_encoding', 'utf8'),
            'darkness': int(parameters.get_param(
                'epg_product_label.zpl_darkness', '0') or 0) or None,
            'speed': int(parameters.get_param(
                'epg_product_label.zpl_speed', '0') or 0) or None,
        }

    def _render_label_zpl(self, record, context):
        """Return the ZPL for a single label, ``^XA`` through ``^XZ``."""
        self.ensure_one()
        settings = self._zpl_settings()
        builder = ZplBuilder(
            to_mm(self.label_width, self.unit),
            to_mm(self.label_height, self.unit),
            density=settings['density'],
            rotation=settings['rotation'],
            encoding=settings['encoding'],
            darkness=settings['darkness'],
            print_speed=settings['speed'])
        if self.zpl_prefix:
            builder.raw(self.zpl_prefix.strip())
        if self.label_border and self.label_border_width:
            builder.box(0, 0,
                        to_mm(self.label_width, self.unit),
                        to_mm(self.label_height, self.unit),
                        thickness_mm=to_mm(self.label_border_width, self.unit))
        if self.bg_image_source != 'none':
            element = self.env['epg.label.element'].new({
                'template_id': self.id, 'name': 'bg'})
            data = encode_image_b64(self.bg_image) if self.bg_image_source == 'static' \
                else encode_image_b64(element._resolve_path(record, self.bg_image_field))
            if data:
                builder.image(0, 0, data,
                              to_mm(self.label_width, self.unit),
                              to_mm(self.label_height, self.unit))
        for element in self.element_ids.sorted(lambda e: (e.z_index, e.sequence, e.id)):
            element._render_zpl(builder, record, context)
        if self.zpl_suffix:
            builder.raw(self.zpl_suffix.strip())
        return builder.render()

    def _render_zpl(self, slots, context=None):
        """Concatenate the ZPL for every slot into one printer job."""
        self.ensure_one()
        return ''.join(
            self._render_label_zpl(record, slot_context or context or {})
            for record, slot_context in slots if record)

    # ==================================================================
    # XML import / export
    # ==================================================================
    # Fields that must not travel: they are local ids, derived values, or handled
    # separately (binaries go out base64-encoded in their own node).
    EXPORT_SKIP = {
        'id', 'paperformat_id', 'is_predefined', 'company_id',
        'preview_product_id', 'preview_partner_id',
        'preview_pricelist_id', 'preview_promo_pricelist_id',
    }
    EXPORT_SKIP_TYPES = ('one2many', 'many2many', 'many2one', 'binary')

    @api.model
    def _export_fields(self, model):
        """Stored, writable, non-relational fields worth carrying to another database."""
        return sorted(
            name for name, field in model._fields.items()
            if field.store and not field.readonly and not field.compute
            and field.type not in self.EXPORT_SKIP_TYPES
            and name not in self.EXPORT_SKIP
            and not name.startswith('message_')
            and not name.startswith('activity_'))

    def action_export_xml(self):
        """Serialise the selected templates and hand back a downloadable file."""
        root = etree.Element('epg_label_templates', version='1.0')
        Element = self.env['epg.label.element']
        template_fields = self._export_fields(self)
        element_fields = self._export_fields(Element)
        for template in self:
            node = etree.SubElement(root, 'template')
            for name in template_fields:
                self._append_value_node(node, name, template[name], template._fields[name])
            binary = etree.SubElement(node, 'binary', name='bg_image')
            if template.bg_image:
                binary.text = (template.bg_image or b'').decode() \
                    if isinstance(template.bg_image, bytes) else template.bg_image
            elements = etree.SubElement(node, 'elements')
            for element in template.element_ids:
                element_node = etree.SubElement(elements, 'element')
                for name in element_fields:
                    self._append_value_node(
                        element_node, name, element[name], element._fields[name])
                if element.image_data:
                    image = etree.SubElement(element_node, 'binary', name='image_data')
                    image.text = element.image_data.decode() \
                        if isinstance(element.image_data, bytes) else element.image_data
        payload = etree.tostring(
            root, pretty_print=True, xml_declaration=True, encoding='UTF-8')
        attachment = self.env['ir.attachment'].create({
            'name': 'label_templates.xml',
            'type': 'binary',
            'datas': base64.b64encode(payload),
            'mimetype': 'application/xml',
        })
        return {
            'type': 'ir.actions.act_url',
            'url': '/web/content/%s?download=true' % attachment.id,
            'target': 'self',
        }

    @staticmethod
    def _append_value_node(parent, name, value, field):
        node = etree.SubElement(parent, 'field', name=name, type=field.type)
        if value is False and field.type == 'boolean':
            node.text = '0'
        elif value is False or value is None:
            node.text = ''
        elif field.type == 'boolean':
            node.text = '1' if value else '0'
        else:
            node.text = str(value)

    @api.model
    def _import_from_xml(self, payload, overwrite=False):
        """Recreate templates from an :meth:`action_export_xml` payload."""
        try:
            root = etree.fromstring(payload)
        except etree.XMLSyntaxError as error:
            raise UserError(_("This is not a valid XML file: %s", error)) from error
        if root.tag != 'epg_label_templates':
            raise UserError(_(
                "This file was not exported from the label builder."))

        Element = self.env['epg.label.element']
        imported = self.browse()
        for node in root.findall('template'):
            values = self._read_value_nodes(node, self)
            binary = node.find("binary[@name='bg_image']")
            if binary is not None and binary.text:
                values['bg_image'] = binary.text.strip()
            existing = self.search(
                [('code', '=', values.get('code'))], limit=1) if values.get('code') else None
            if existing and overwrite:
                existing.element_ids.unlink()
                existing.write(values)
                template = existing
            else:
                if existing:
                    values['code'] = self._free_code(values['code'])
                    values['name'] = _('%s (imported)', values.get('name') or '')
                template = self.create(values)
            element_values = []
            for element_node in node.findall('elements/element'):
                element_data = self._read_value_nodes(element_node, Element)
                image = element_node.find("binary[@name='image_data']")
                if image is not None and image.text:
                    element_data['image_data'] = image.text.strip()
                element_values.append((0, 0, element_data))
            if element_values:
                template.write({'element_ids': element_values})
            imported |= template
        return imported

    @api.model
    def _free_code(self, code):
        """Return ``code`` with a numeric suffix that no template is using yet.

        Importing the same file twice in one day has to keep working, so this counts
        up rather than stamping a date and hoping.
        """
        candidate, index = '%s-copy' % code, 1
        while self.with_context(active_test=False).search_count(
                [('code', '=', candidate)], limit=1):
            index += 1
            candidate = '%s-copy%d' % (code, index)
        return candidate

    @staticmethod
    def _read_value_nodes(node, model):
        """Cast the ``<field>`` children of ``node`` back to Odoo values."""
        values = {}
        for child in node.findall('field'):
            name = child.get('name')
            field = model._fields.get(name)
            if not field or field.readonly or field.compute:
                continue
            text = (child.text or '').strip()
            if field.type == 'boolean':
                values[name] = text in ('1', 'True', 'true')
            elif not text:
                values[name] = False
            elif field.type == 'integer':
                values[name] = int(float(text))
            elif field.type == 'float':
                values[name] = float(text)
            else:
                values[name] = text
        return values

    # ==================================================================
    # actions
    # ==================================================================
    def action_open_print_wizard(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Print Labels'),
            'res_model': 'epg.label.print',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_template_id': self.id,
                'default_label_type': self.label_type,
            },
        }

    def action_preview_pdf(self):
        """Render the template once, with the preview record, as a PDF."""
        self.ensure_one()
        record = self._get_preview_record()
        if not record:
            raise UserError(_(
                "There is no record to preview with. Pick a preview product or "
                "contact on the Preview tab."))
        wizard = self.env['epg.label.print'].create({
            'template_id': self.id,
            'label_type': self.label_type,
            'quantity': 1,
            'pricelist_id': self.preview_pricelist_id.id,
            'promo_pricelist_id': self.preview_promo_pricelist_id.id,
            'output_format': 'pdf',
            **self._preview_wizard_values(record),
        })
        return wizard.action_print()

    def _preview_wizard_values(self, record):
        if record._name == 'res.partner':
            return {'partner_ids': [(6, 0, record.ids)]}
        if record._name == 'stock.lot':
            return {'lot_ids': [(6, 0, record.ids)]}
        if record._name == 'product.template':
            return {'product_tmpl_ids': [(6, 0, record.ids)]}
        return {'product_ids': [(6, 0, record.ids)]}

    def action_open_assistant(self):
        """Open the layout assistant on this template."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Layout Assistant'),
            'res_model': 'epg.label.assistant',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_template_id': self.id, 'active_id': self.id},
        }

    def action_view_print_log(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Print History of %s', self.name),
            'res_model': 'epg.label.print.log',
            'view_mode': 'list,graph,pivot,form',
            'domain': [('template_id', '=', self.id)],
            'context': {'search_default_filter_real': 1},
        }

    def action_view_elements(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Elements of %s', self.name),
            'res_model': 'epg.label.element',
            'view_mode': 'list,form',
            'domain': [('template_id', '=', self.id)],
            'context': {'default_template_id': self.id},
        }

    def action_duplicate_template(self):
        copies = self.browse()
        for template in self:
            copies |= template.copy()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'epg.label.template',
            'view_mode': 'form',
            'res_id': copies[:1].id,
        }

    # ==================================================================
    # lookup helpers used by the wizards and by the product actions
    # ==================================================================
    @api.model
    def _available_domain(self, label_type=None):
        """Templates the current user is allowed to print with."""
        domain = [
            '|', ('company_id', '=', False),
            ('company_id', 'in', self.env.companies.ids),
        ]
        if label_type:
            domain.append(('label_type', '=', label_type))
        if not self.env.user.has_group('base.group_system'):
            domain.extend([
                '|', ('user_ids', '=', False),
                ('user_ids', 'in', self.env.user.ids),
            ])
        return domain

    @api.model
    def _default_template(self, label_type='product'):
        """The template a print wizard should open on."""
        user_default = self.env.user.epg_default_label_template_id
        if user_default and user_default.label_type == label_type \
                and user_default.active:
            return user_default
        available = self.search(self._available_domain(label_type), limit=1)
        return available

    @api.model
    def name_search(self, name='', args=None, operator='ilike', limit=100):
        args = (args or []) + self._available_domain()
        return super().name_search(name, args, operator, limit)
