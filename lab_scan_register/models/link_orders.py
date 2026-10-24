# -*- coding: utf-8 -*-
"""Linking the orders that were made without the register.

A case is often booked straight into Sales - the doctor rang, the counter typed the
order - while its scans sit in a register that then shows no orders, waits for Create
Orders, and would make a second order for the same patient. (client, 2026-10-15: "a
button in scan register to map the SOs if the SOs are directly created, not from scan
registers")

Each patient still without an order gets a row, filled in where the clinic has exactly
the order for that patient - the same name, not linked to any register, near the
register's date - and open to any other order of the clinic. Linking writes both sides
(the patient's order, the order's register and patient), puts the scans on the order as
Create Orders would, and when every patient has an order the register is Ordered - unless
a plate of it is on a machine, when printing finishes first and Create Orders closes it.
"""
import re
from datetime import timedelta

from markupsafe import Markup

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError

SUGGEST_DAYS = 45       # an order this far either side of the register's arrival can be its patient's


def _name_key(name):
    """'  Tanvi  (UL) ' and 'tanvi' are one patient."""
    name = re.sub(r'\(.*?\)', ' ', (name or '').lower())
    return ' '.join(re.findall(r'[a-z0-9]+', name))


class ScanLinkOrders(models.TransientModel):
    _name = 'lab.scan.link.orders'
    _description = 'Link existing sale orders to a scan register'

    scan_id = fields.Many2one('lab.scan', required=True, readonly=True, ondelete='cascade')
    clinic_id = fields.Many2one('res.partner', compute='_compute_clinic')
    line_ids = fields.One2many('lab.scan.link.orders.line', 'wizard_id', string='Patients')
    copy_files = fields.Boolean('Put the scans on the orders', default=True,
                                help="Each patient's files and the shared ones go onto the order, as Create Orders "
                                     "does. A file the order already has is not added twice.")

    @api.depends('scan_id.partner_id')
    def _compute_clinic(self):
        for wiz in self:
            wiz.clinic_id = wiz.scan_id.partner_id.commercial_partner_id

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        scan = self.env['lab.scan'].browse(values.get('scan_id') or self.env.context.get('default_scan_id'))
        if scan and 'line_ids' in fields_list:
            suggested = scan._suggest_orders()
            values['line_ids'] = [Command.create({
                'scan_line_id': line.id, 'sale_order_id': suggested.get(line.id, False)})
                for line in scan.line_ids.filtered(lambda l: not l.sale_order_id)]
        return values

    def action_link(self):
        self.ensure_one()
        pairs = [(row.scan_line_id, row.sale_order_id) for row in self.line_ids if row.sale_order_id]
        if not pairs:
            raise UserError(_("Choose an order for at least one patient."))
        self.scan_id._link_orders(pairs, copy_files=self.copy_files)
        return {'type': 'ir.actions.act_window_close'}


class ScanLinkOrdersLine(models.TransientModel):
    _name = 'lab.scan.link.orders.line'
    _description = 'A patient and the order it was booked as'

    wizard_id = fields.Many2one('lab.scan.link.orders', required=True, ondelete='cascade')
    scan_line_id = fields.Many2one('lab.scan.line', string='Patient Line', required=True, readonly=True)
    patient_name = fields.Char(related='scan_line_id.patient_name')
    description = fields.Char(related='scan_line_id.description')
    clinic_id = fields.Many2one(related='wizard_id.clinic_id')
    sale_order_id = fields.Many2one(
        'sale.order', string='Order',
        # the register's clinic's orders - or, while it has none yet, any open order
        domain="[('state', '!=', 'cancel'), ('scan_line_id', '=', False), "
               "('partner_id.commercial_partner_id', '=?', clinic_id)]")
    order_patient = fields.Char(related='sale_order_id.patient', string='Patient on the Order')
    order_date = fields.Datetime(related='sale_order_id.date_order', string='Order Date')


class ScanLinkOrdersRegister(models.Model):
    _inherit = 'lab.scan'

    def action_open_link_orders(self):
        self.ensure_one()
        if self.state in ('done', 'cancel'):
            raise UserError(_("%s is ordered or cancelled - there is nothing to link.", self.name))
        if not self.line_ids.filtered(lambda l: not l.sale_order_id):
            raise UserError(_("Every patient of %s has an order already.", self.name))
        return {'type': 'ir.actions.act_window', 'name': _('Link Existing Orders'),
                'res_model': 'lab.scan.link.orders', 'view_mode': 'form', 'target': 'new',
                'context': {'default_scan_id': self.id}}

    def _suggest_orders(self):
        """{scan line id: sale order id} where the clinic has one unlinked order for that
        patient's name around the register's date - never one order for two patients."""
        self.ensure_one()
        clinic = self.partner_id.commercial_partner_id
        if not clinic:
            return {}
        around = self.date_received or self.create_date or fields.Datetime.now()
        orders = self.env['sale.order'].search([
            ('partner_id', 'child_of', clinic.id), ('state', '!=', 'cancel'), ('scan_line_id', '=', False),
            ('patient', '!=', False),
            ('date_order', '>=', around - timedelta(days=SUGGEST_DAYS)),
            ('date_order', '<=', around + timedelta(days=SUGGEST_DAYS))])
        by_name = {}
        for order in orders:
            by_name.setdefault(_name_key(order.patient), self.env['sale.order'])
            by_name[_name_key(order.patient)] |= order
        taken, out = set(), {}
        for line in self.line_ids.filtered(lambda l: not l.sale_order_id):
            found = by_name.get(_name_key(line.patient_name), self.env['sale.order']).filtered(
                lambda o: o.id not in taken)
            if len(found) == 1:
                out[line.id] = found.id
                taken.add(found.id)
        return out

    def _link_orders(self, pairs, copy_files=True):
        """Make each (patient, order) one case: both sides written, the scans on the order."""
        self.ensure_one()
        if self.state in ('done', 'cancel'):
            raise UserError(_("%s is ordered or cancelled - there is nothing to link.", self.name))
        orders = [order for _line, order in pairs]
        twice = {o for o in orders if orders.count(o) > 1}
        if twice:
            raise UserError(_("%s is chosen for two patients - one order is one patient.",
                              ', '.join(o.name for o in twice)))
        shared = self._shared_attachments()
        done = []
        for line, order in pairs:
            if line.scan_id != self:
                raise UserError(_("%s is not a patient of this register.", line.display_name))
            if line.sale_order_id:
                raise UserError(_("%(patient)s has an order already (%(order)s).",
                                  patient=line.patient_name, order=line.sale_order_id.name))
            if order.state == 'cancel':
                raise UserError(_("%s is cancelled.", order.name))
            if order.scan_line_id:
                raise UserError(_("%(order)s is already the order of %(patient)s in %(scan)s.",
                                  order=order.name, patient=order.scan_line_id.patient_name or '-',
                                  scan=order.scan_line_id.scan_id.name))
            line.with_context(scan_workflow=True).write({'sale_order_id': order.id})
            vals = {'scan_id': self.id, 'scan_line_id': line.id}
            printed = line.print_line_ids.filtered(lambda p: p.result == 'ok' and not p.superseded)
            if printed and not order.print_order_id:
                vals['print_order_id'] = printed.sorted('id')[-1].order_id.id
            order.write(vals)
            if copy_files:
                have = set(self.env['ir.attachment'].search(
                    [('res_model', '=', 'sale.order'), ('res_id', '=', order.id)]).mapped('checksum'))
                for att in line.attachment_ids | shared:
                    if att.checksum not in have:
                        att.copy({'res_model': 'sale.order', 'res_id': order.id})
                        have.add(att.checksum)
            order.message_post(body=_("Linked to scan register %(scan)s - patient %(patient)s.",
                                      scan=self._get_html_link(), patient=line.patient_name or '-'))
            done.append(order)
        self.message_post(body=Markup(_("Existing orders linked: %s")) % Markup(', ').join(
            o._get_html_link() for o in done))
        # a register still without its clinic takes the one its orders were booked for
        clinics = self.line_ids.sale_order_id.partner_id.commercial_partner_id
        if not self.partner_id and len(clinics) == 1:
            self._move({'partner_id': clinics.id})
        # every patient has an order: nothing is left to make - unless a plate of it is
        # on a machine, when printing ends first and Create Orders closes the register
        if all(l.sale_order_id for l in self.line_ids) and self.state not in ('printing', 'in_progress'):
            self._move({'state': 'done', 'date_ordered': fields.Datetime.now()})
        return True
