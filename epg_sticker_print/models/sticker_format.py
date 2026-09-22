# -*- coding: utf-8 -*-
"""A label format = the physical label + how many fit on a page.

Rolls are 1 x 1 (one label per page, the printer feeds the next). Sheets (A4 with 2 x 5
labels, ...) place `columns x rows` labels per page. Each format owns the
`report.paperformat` wkhtmltopdf prints with, kept in sync automatically.
"""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class StickerFormat(models.Model):
    _name = 'epg.sticker.format'
    _description = 'Sticker Format'
    _order = 'sequence, id'

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    width_mm = fields.Float(string='Label Width (mm)', required=True, default=100)
    height_mm = fields.Float(string='Label Height (mm)', required=True, default=60)
    margin_mm = fields.Float(string='Inner Margin (mm)', default=2.0,
                             help="Free space kept inside the label edge.")
    columns = fields.Integer(default=1, required=True, help="Labels side by side on one page (1 for a roll).")
    rows = fields.Integer(default=1, required=True, help="Label rows on one page (1 for a roll).")
    gap_mm = fields.Float(string='Gap between labels (mm)', default=0.0)
    page_width_mm = fields.Float(string='Page Width (mm)', compute='_compute_page_size', store=True)
    page_height_mm = fields.Float(string='Page Height (mm)', compute='_compute_page_size', store=True)
    is_sheet = fields.Boolean(compute='_compute_page_size', store=True)
    is_default = fields.Boolean(string='Default')
    dpi = fields.Integer(default=96)
    paperformat_id = fields.Many2one('report.paperformat', string='Paper Format', readonly=True, ondelete='set null')
    note = fields.Char(string='Notes')

    _positive = models.Constraint('CHECK (width_mm > 0 AND height_mm > 0 AND columns > 0 AND rows > 0)',
                                  'A label needs a positive size and at least one row and column.')

    @api.depends('width_mm', 'height_mm', 'columns', 'rows', 'gap_mm')
    def _compute_page_size(self):
        for fmt in self:
            fmt.is_sheet = fmt.columns * fmt.rows > 1
            fmt.page_width_mm = fmt.width_mm * fmt.columns + fmt.gap_mm * max(fmt.columns - 1, 0)
            fmt.page_height_mm = fmt.height_mm * fmt.rows + fmt.gap_mm * max(fmt.rows - 1, 0)

    @api.constrains('is_default')
    def _check_single_default(self):
        defaults = self.search([('is_default', '=', True)])
        if len(defaults) > 1:
            raise ValidationError(_("Only one sticker format can be the default."))

    def _paperformat_vals(self):
        self.ensure_one()
        return {
            'name': _('Sticker %s', self.name),
            'format': 'custom',
            'page_width': int(round(self.page_width_mm)),
            'page_height': int(round(self.page_height_mm)),
            # Portrait on purpose: wkhtmltopdf applies its orientation flag ON TOP of the
            # explicit page size, so "Landscape" would rotate a 100x60 label into 60x100.
            'orientation': 'Portrait',
            'margin_top': 0, 'margin_bottom': 0, 'margin_left': 0, 'margin_right': 0,
            'header_line': False, 'header_spacing': 0, 'dpi': self.dpi or 96,
            # wkhtmltopdf's "smart shrinking" rescales the page to a 1024 px viewport,
            # which printed every label ~18 % smaller than its mm sizes said and left the
            # right and bottom of the sticker blank (2026-08-19). With it off, a mm is a mm
            # and the engine's fit is what comes out of the printer.
            'disable_shrinking': True,
        }

    def _sync_paperformat(self):
        # report.paperformat is writable by Settings admins only; the caller's right to
        # change this format was already checked by the write/create that got us here.
        Paper = self.env['report.paperformat'].sudo()
        for fmt in self:
            vals = fmt._paperformat_vals()
            if fmt.paperformat_id:
                fmt.paperformat_id.sudo().write(vals)
            else:
                fmt.paperformat_id = Paper.create(vals)

    @api.model_create_multi
    def create(self, vals_list):
        formats = super().create(vals_list)
        formats._sync_paperformat()
        return formats

    def write(self, vals):
        res = super().write(vals)
        if any(k in vals for k in ('name', 'width_mm', 'height_mm', 'columns', 'rows', 'gap_mm', 'dpi')):
            self._sync_paperformat()
        return res

    @api.model
    def get_default(self):
        return (self.search([('is_default', '=', True)], limit=1)
                or self.search([], limit=1))
