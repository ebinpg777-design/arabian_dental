# -*- coding: utf-8 -*-
"""The label sheet sitting in the printer tray.

A sheet of 24 labels is rarely used up in one run: the counter prints three stickers,
peels them off, and the sheet goes back in the tray with 21 labels left - or, as in the
photo that asked for this (client, 2026-10-19), with three. The next run must land on
labels that are still there. So the wizard shows the sheet as a grid, the cells already
peeled off are marked, and the run fills the free ones; and after each print the cells
it took are remembered here, per format and company, so the next run starts from the
sheet as it really is without anybody having to count.
"""
import json

from odoo import api, fields, models


class StickerSheet(models.Model):
    _name = 'epg.sticker.sheet'
    _description = 'Label Sheet in the Tray'
    _rec_name = 'format_id'

    format_id = fields.Many2one('epg.sticker.format', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    used_cells = fields.Char(default='[]', help="JSON list of the cells already peeled off, 0 = top left.")
    used_count = fields.Integer(compute='_compute_used_count')
    printed_on = fields.Datetime(readonly=True)
    printed_by = fields.Many2one('res.users', readonly=True)

    _one_per_format = models.Constraint('UNIQUE (format_id, company_id)',
                                        'One tray sheet per format and company.')

    @api.depends('used_cells')
    def _compute_used_count(self):
        for sheet in self:
            sheet.used_count = len(sheet.cells())

    @api.model
    def parse(self, value, per_page):
        """The cell list in `value` - a JSON text (the wizard field) or a list (report
        data) - kept to the sheet: sorted, unique, in range."""
        if isinstance(value, (list, tuple, set, range)):
            raw = list(value)
        else:
            try:
                raw = json.loads(value or '[]')
            except (TypeError, ValueError):
                raw = []
        if not isinstance(raw, list):
            raw = []
        cells = set()
        for value in raw:
            try:
                slot = int(value)
            except (TypeError, ValueError):
                continue
            if 0 <= slot < per_page:
                cells.add(slot)
        return sorted(cells)

    def cells(self):
        self.ensure_one()
        return self.parse(self.used_cells, self.format_id.columns * self.format_id.rows)

    @api.model
    def _for(self, fmt):
        """The tray sheet of `fmt` in the current company: none while nothing was ever
        printed on that format, in which case the sheet is taken as fresh."""
        if not fmt or not fmt.is_sheet:
            return self.browse()
        return self.search([('format_id', '=', fmt.id), ('company_id', '=', self.env.company.id)], limit=1)

    @api.model
    def cells_for(self, fmt):
        sheet = self._for(fmt)
        return sheet.cells() if sheet else []

    @api.model
    def after_print(self, per_page, used, count):
        """The cells used on the tray sheet once `count` stickers are printed on a sheet
        whose `used` cells were already gone.

        The run fills the free cells in reading order. While it fits, those join the
        used ones. Once it runs over, the tray sheet is finished and the run continues
        onto fresh sheets: whole ones are used up, and the last, partly used one is
        what is in the tray afterwards - its first `count % per_page` cells gone, or
        a fresh sheet when the last one was filled exactly.
        """
        used = set(used)
        free = [slot for slot in range(per_page) if slot not in used]
        if count < len(free):
            return sorted(used | set(free[:count]))
        # the tray sheet is finished (exactly, or with fresh sheets after it)
        return list(range((count - len(free)) % per_page))

    @api.model
    def record_print(self, fmt, used, count):
        """Remember what the print just took from the tray sheet."""
        if not fmt or not fmt.is_sheet or count <= 0:
            return self.browse()
        per_page = fmt.columns * fmt.rows
        cells = self.after_print(per_page, self.parse(used, per_page), count)
        vals = {'used_cells': json.dumps(cells), 'printed_on': fields.Datetime.now(),
                'printed_by': self.env.user.id}
        sheet = self._for(fmt)
        if sheet:
            sheet.write(vals)
        else:
            sheet = self.create(dict(vals, format_id=fmt.id))
        return sheet
