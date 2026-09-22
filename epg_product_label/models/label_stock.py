# -*- coding: utf-8 -*-
"""Label stock presets.

Nobody measures a sheet of Avery L7160 with a ruler - they read the code off the
box.  A stock record turns that code into the eleven geometry fields a template
needs, so setting up a new template is a search-and-click instead of an
arithmetic exercise that goes wrong by half a millimetre.
"""

from odoo import _, api, fields, models

# Geometry fields a stock copies onto a template.  Listed once so the preset
# picker, the "create template" action and the tests cannot drift apart.
GEOMETRY_FIELDS = (
    'unit', 'label_width', 'label_height', 'layout_mode', 'page_format',
    'orientation', 'page_width', 'page_height', 'margin_top', 'margin_bottom',
    'margin_left', 'margin_right', 'column_gap', 'row_gap', 'auto_grid',
    'columns', 'rows',
)


class EpgLabelStock(models.Model):
    _name = 'epg.label.stock'
    _description = 'Label Stock Preset'
    _order = 'vendor, sequence, name'

    name = fields.Char(required=True, translate=True)
    code = fields.Char(
        string='Stock Code', index=True,
        help="Manufacturer's reference, e.g. L7160 or 99012.")
    vendor = fields.Selection([
        ('avery', 'Avery'),
        ('dymo', 'Dymo'),
        ('zebra', 'Zebra'),
        ('brother', 'Brother'),
        ('generic', 'Generic'),
    ], string='Vendor', default='generic', required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    note = fields.Char(string='Description', translate=True)

    unit = fields.Selection(
        [('mm', 'Millimetres'), ('in', 'Inches')], default='mm', required=True)
    label_width = fields.Float(string='Label Width', required=True)
    label_height = fields.Float(string='Label Height', required=True)

    layout_mode = fields.Selection([
        ('sheet', 'Label sheet'),
        ('roll', 'Roll / continuous'),
    ], string='Layout', default='sheet', required=True)
    page_format = fields.Selection([
        ('A4', 'A4'), ('A5', 'A5'), ('Letter', 'Letter'),
        ('Legal', 'Legal'), ('custom', 'Custom'),
    ], string='Paper Size', default='A4', required=True)
    orientation = fields.Selection(
        [('Portrait', 'Portrait'), ('Landscape', 'Landscape')],
        default='Portrait', required=True)
    page_width = fields.Float(string='Paper Width')
    page_height = fields.Float(string='Paper Height')

    margin_top = fields.Float(string='Top Margin')
    margin_bottom = fields.Float(string='Bottom Margin')
    margin_left = fields.Float(string='Left Margin')
    margin_right = fields.Float(string='Right Margin')
    column_gap = fields.Float(string='Horizontal Gap')
    row_gap = fields.Float(string='Vertical Gap')

    auto_grid = fields.Boolean(
        string='Fit Grid Automatically', default=False,
        help="Off for real stock: the manufacturer's column and row counts are "
             "authoritative, and re-deriving them from the margins can be one out.")
    columns = fields.Integer(string='Columns', default=1)
    rows = fields.Integer(string='Rows', default=1)
    labels_per_page = fields.Integer(compute='_compute_labels_per_page', store=True)

    template_count = fields.Integer(compute='_compute_template_count')
    display_size = fields.Char(compute='_compute_display_size')

    _unique_stock_code = models.Constraint(
        'UNIQUE(vendor, code)',
        'That stock code already exists for this vendor.')

    @api.depends('columns', 'rows', 'layout_mode')
    def _compute_labels_per_page(self):
        for stock in self:
            stock.labels_per_page = 1 if stock.layout_mode == 'roll' \
                else max(1, (stock.columns or 1) * (stock.rows or 1))

    @api.depends('label_width', 'label_height', 'unit')
    def _compute_display_size(self):
        for stock in self:
            stock.display_size = '%g × %g %s' % (
                stock.label_width, stock.label_height, stock.unit)

    def _compute_template_count(self):
        counts = dict(self.env['epg.label.template']._read_group(
            [('stock_id', 'in', self.ids)], ['stock_id'], ['__count']))
        for stock in self:
            stock.template_count = counts.get(stock, 0)

    @api.depends('name', 'code', 'label_width', 'label_height', 'unit')
    def _compute_display_name(self):
        for stock in self:
            parts = [stock.name]
            if stock.code:
                parts.append('(%s)' % stock.code)
            parts.append('- %g × %g %s' % (
                stock.label_width, stock.label_height, stock.unit))
            stock.display_name = ' '.join(parts)

    def geometry_values(self):
        """The template field values this stock stands for.

        The paper size is deliberately left out unless this stock states one.
        It is a stored editable compute on the template, so copying a blank over
        it would pin the page at zero and defeat the derivation from the paper
        format - which is how a sheet stock ends up "not fitting" its own paper.
        """
        self.ensure_one()
        values = {name: self[name] for name in GEOMETRY_FIELDS
                  if name not in ('page_width', 'page_height')}
        if self.page_format == 'custom' and self.page_width and self.page_height:
            values['page_width'] = self.page_width
            values['page_height'] = self.page_height
        return values

    def action_create_template(self):
        """Start a new template on this stock, ready for the layout assistant."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('New Label Template'),
            'res_model': 'epg.label.template',
            'view_mode': 'form',
            'context': {
                'default_name': self.name,
                'default_stock_id': self.id,
                **{'default_%s' % key: value
                   for key, value in self.geometry_values().items()},
            },
        }

    def action_view_templates(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Templates on %s', self.display_name),
            'res_model': 'epg.label.template',
            'view_mode': 'kanban,list,form',
            'domain': [('stock_id', '=', self.id)],
            'context': {'default_stock_id': self.id},
        }
