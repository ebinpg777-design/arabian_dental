# -*- coding: utf-8 -*-
"""Digital Rx — a doctor sends a case in through the portal.

Ported from the earlier portal build, but NOT copied: that version was written against
a data model this lab no longer has (`lab.appliance.type`, an `arch` field on the
order). It has been rebuilt on what the lab actually uses today — real products, and
the U/L per line that `sale_custom` already understands — so a converted request looks
exactly like a case an executive would have entered.

The request is deliberately its own model rather than a draft sale order. A doctor's
submission is a *proposal*: it has not been priced, the products they picked may not be
what the lab makes, and nobody has checked it. Landing it straight in the order pipeline
would put unverified outside input where the lab's own confirmed work lives.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class LabCaseRequest(models.Model):
    _name = 'lab.case.request'
    _description = 'Portal Case Request (Digital Rx)'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc, id desc'

    name = fields.Char(default=lambda s: _('New'), copy=False, readonly=True)
    partner_id = fields.Many2one(
        'res.partner', string='Doctor / Clinic', required=True, index=True,
        tracking=True)
    company_id = fields.Many2one('res.company', default=lambda s: s.env.company,
                                 required=True)

    patient = fields.Char(required=True, tracking=True)
    age = fields.Integer()
    gender = fields.Selection([('male', 'Male'), ('female', 'Female')])

    line_ids = fields.One2many('lab.case.request.line', 'request_id',
                               string='Requested Work')
    line_count = fields.Integer(compute='_compute_line_count')

    modification = fields.Char('Modifications')
    instruction = fields.Char('Special Instructions')
    note = fields.Text('Notes from the doctor')

    # STL / intra-oral scan uploads, which is half the point of a digital Rx.
    attachment_ids = fields.Many2many(
        'ir.attachment', 'lab_case_request_attachment_rel',
        'request_id', 'attachment_id', string='Scans / Files')

    state = fields.Selection(
        [('new', 'New'), ('reviewed', 'Reviewed'),
         ('converted', 'Converted'), ('rejected', 'Rejected')],
        default='new', required=True, tracking=True, copy=False)
    staff_note = fields.Char(help="Internal note — e.g. why it was rejected.")
    sale_order_id = fields.Many2one('sale.order', string='Case', readonly=True,
                                    copy=False)

    @api.depends('line_ids')
    def _compute_line_count(self):
        for request in self:
            request.line_count = len(request.line_ids)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'lab.case.request') or _('New')
        requests = super().create(vals_list)
        for request in requests:
            request._notify_lab()
        return requests

    def _notify_lab(self):
        """Tell the office a case arrived, rather than hoping somebody refreshes."""
        self.ensure_one()
        group = self.env.ref('lab_order_control.group_lab_order_checker',
                             raise_if_not_found=False)
        if not group:
            return
        partners = group.sudo().all_user_ids.filtered('active').partner_id
        if partners:
            self.message_notify(
                partner_ids=partners.ids,
                subject=_("New case request from %s", self.partner_id.display_name),
                body=_("%(doctor)s submitted a case for %(patient)s through the portal.",
                       doctor=self.partner_id.display_name, patient=self.patient or ''))

    # ------------------------------------------------------------------ workflow
    def action_mark_reviewed(self):
        self.filtered(lambda r: r.state == 'new').write({'state': 'reviewed'})
        return True

    def action_reject(self):
        for request in self:
            if request.state in ('converted',):
                raise UserError(_("%s has already become a case.", request.name))
            if not request.staff_note:
                raise UserError(_(
                    "Say why %s is being rejected — the doctor is told this, and "
                    "'rejected' with no reason just produces a phone call.",
                    request.name))
            request.write({'state': 'rejected'})
        return True

    def action_convert_to_case(self):
        """Turn the request into a real order — which then goes through verification.

        Not confirmed here on purpose. This is outside input: someone at the lab still
        has to price it and check the products are ones the lab actually makes, and the
        existing maker-checker gate is exactly the thing that enforces that.
        """
        self.ensure_one()
        if self.state == 'converted':
            raise UserError(_("%s has already been converted.", self.name))
        if not self.line_ids:
            raise UserError(_(
                "%s has no work lines — there is nothing to make.", self.name))

        order = self.env['sale.order'].create({
            'partner_id': self.partner_id.id,
            'company_id': self.company_id.id,
            'patient': self.patient,
            'age': self.age,
            'gender': self.gender,
            'modification': self.modification,
            'instruction': self.instruction,
            'order_line': [(0, 0, {
                'product_id': line.product_id.id,
                'product_uom_qty': line.quantity,
                'ul': line.ul,
            }) for line in self.line_ids],
        })
        # A portal submission is unverified by definition — send it to the queue the
        # lab already works, rather than inventing a second one.
        order.write({'verification_needed': True})
        order.action_submit_for_verification()

        # The scans go with the case: the people making it work from the order, and
        # never open the request again. Copied, not moved, so the request keeps its
        # own record of what the doctor sent. (Same content, so the filestore does not
        # store it twice.)
        Attachment = self.env['ir.attachment'].sudo()
        for attachment in self.attachment_ids.sudo():
            Attachment.create({
                'name': attachment.name, 'raw': attachment.raw,
                'mimetype': attachment.mimetype,
                'res_model': 'sale.order', 'res_id': order.id,
            })

        self.write({'state': 'converted', 'sale_order_id': order.id})
        order.message_post(body=_(
            "Created from portal case request %(name)s submitted by %(doctor)s.",
            name=self.name, doctor=self.partner_id.display_name))
        self.message_post(body=_("Converted to %s.", order.name))
        return self.action_open_case()

    def action_open_case(self):
        self.ensure_one()
        if not self.sale_order_id:
            return False
        return {
            'type': 'ir.actions.act_window', 'name': _('Case'),
            'res_model': 'sale.order', 'res_id': self.sale_order_id.id,
            'view_mode': 'form',
        }


class LabCaseRequestLine(models.Model):
    _name = 'lab.case.request.line'
    _description = 'Case Request Line'
    _order = 'request_id, id'

    request_id = fields.Many2one('lab.case.request', required=True,
                                 ondelete='cascade', index=True)
    product_id = fields.Many2one(
        'product.product', string='Appliance', required=True,
        domain="[('sale_ok', '=', True)]")
    ul = fields.Selection(
        [('upper', 'Upper'), ('lower', 'Lower'), ('ul', 'Upper & Lower')],
        string='Arch', default='upper', required=True)
    quantity = fields.Float(default=1.0, required=True)
    note = fields.Char()
