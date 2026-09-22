# -*- coding: utf-8 -*-
from odoo import api, fields, models

CR_SEQUENCE_XMLID = 'lab_reports.seq_partner_cr_number'
CR_PADDING = 4


class ResCompany(models.Model):
    _inherit = "res.company"

    excel_logo = fields.Binary(string="Excel Logo")


class ResPartner(models.Model):
    _inherit = "res.partner"

    cr_number = fields.Char(string="CR No.", copy=False)
    contact_person = fields.Char(string="Contact Person")
    handled_by = fields.Many2one('res.users', string="Handled By")
    sourced_by = fields.Many2one('res.users', string="Sourced By")
    hospital_reg_no = fields.Char(string="Hospital Registration No.")
    last_sale_date = fields.Date(compute="_compute_lab_sale", store=True)
    vendor_code = fields.Char(string="Vendor Code")
    evaluation_period = fields.Char(string="Evaluation Period")
    total_supplies = fields.Char(string="Total No. Of Supplies")
    qty_rejected = fields.Float(string="Qty Rejected")
    no_qty_rejected = fields.Float(string="No. of Rejected Qty per Supply")
    delivery_performance = fields.Char(string="Delivery Performance")
    invoicing = fields.Char(string="Invoicing")
    rating = fields.Char(string="Rating")
    quality_certificate = fields.Text(string="Quality Certificates")
    major_clients = fields.Text(string="Major Clients")
    approved_by = fields.Char(string="Approved By")
    activity_type = fields.Char(string="Type of Activity")
    iso_status = fields.Char(string="ISO Status")
    vendor_status = fields.Char(string="Vendor Status")
    credit_period = fields.Char(string="Credit Period")
    no_employees = fields.Float(string="No. of Employees")

    def _cr_number_max(self):
        """Highest existing numeric CR number (0 if none)."""
        self.env.cr.execute(
            "SELECT max(cr_number::int) FROM res_partner WHERE cr_number ~ '^[0-9]+$'")
        return self.env.cr.fetchone()[0] or 0

    def _cr_sequence(self):
        """The CR number sequence, lifted past the highest number already in use.

        Numbers come from the sequence, which PostgreSQL hands out atomically: two
        users saving clinics at the same moment used to read the same unlocked max()
        and get the same CR number. max() is still read, but only to keep the
        sequence from lagging behind numbers typed in or imported by hand.
        """
        sequence = self.env.ref(CR_SEQUENCE_XMLID, raise_if_not_found=False)
        if not sequence:
            return sequence
        sequence = sequence.sudo()
        # Both halves read fresh: max() is raw SQL, so numbers given out earlier
        # in this transaction must be written first, and number_next_actual is a
        # cached compute that a _next() a moment ago has already made stale -
        # comparing against the stale one wound the sequence BACK onto numbers
        # just handed out.
        self.env['res.partner'].flush_model(['cr_number'])
        sequence.invalidate_recordset(['number_next', 'number_next_actual'])
        floor = self._cr_number_max() + 1
        if sequence.number_next_actual < floor:
            sequence.write({'number_next': floor})
        return sequence

    def _assign_cr_numbers(self, partners):
        """Assign sequential CR numbers to clinics that lack one."""
        to_number = partners.filtered(lambda p: p.is_clinic and not p.cr_number)
        if not to_number:
            return
        sequence = self._cr_sequence()
        counter = None
        for partner in to_number:
            number = sequence and sequence._next()
            if not number:
                # The sequence record was deleted: fall back to max + 1 rather than
                # leave a clinic unnumbered.
                counter = (self._cr_number_max() if counter is None else counter) + 1
                number = str(counter).zfill(CR_PADDING)
            partner.cr_number = number
        if sequence:
            sequence.invalidate_recordset(['number_next', 'number_next_actual'])

    @api.model_create_multi
    def create(self, vals_list):
        partners = super().create(vals_list)
        self._assign_cr_numbers(partners)
        return partners

    def write(self, vals):
        res = super().write(vals)
        if vals.get('is_clinic'):
            self._assign_cr_numbers(self)
        return res

    @api.depends('sale_order_ids.date_order')
    def _compute_lab_sale(self):
        """Latest order date per clinic, in one grouped query.

        The record-by-record version loaded every order a clinic had ever placed
        just to take a max - and because the trigger fires on `date_order`, which
        core stamps at confirmation, that ran on every confirmation, for a clinic
        with hundreds of orders behind it. One `_read_group` answers the whole
        batch without building a single order record.
        """
        latest = dict(self.env['sale.order']._read_group(
            [('partner_id', 'in', self.ids)], ['partner_id'], ['date_order:max']))
        for rec in self:
            when = latest.get(rec)
            rec.last_sale_date = when.date() if when else False
