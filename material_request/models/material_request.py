# -*- coding: utf-8 -*-
"""A department's request for consumables from the main store.

The lab keeps its stock in one main store and hands it out to department stores
(ceramic, acrylic, CAD/CAM, orthodontic, metal, wax-up) and to the office and marketing.
Every hand-out starts as a request here; approving it creates the internal transfer
from the main store to the department, and the transfer's delivery closes the request.

Three things a storekeeper wants to know before approving are answered on the request
itself rather than by opening product after product: how much of each line is on hand
at the source store, which lines are short, and how much has already been delivered
against an approved request.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare, float_is_zero


class MaterialRequest(models.Model):
    _name = 'material.request'
    _description = 'Material Request'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'priority desc, date_request desc, id desc'

    name = fields.Char(
        string='Reference', required=True, copy=False, readonly=True,
        default=lambda self: _('New'), tracking=True)
    company_id = fields.Many2one(
        'res.company', required=True, default=lambda self: self.env.company, copy=False)
    user_id = fields.Many2one(
        'res.users', string='Requested By', required=True, tracking=True,
        default=lambda self: self.env.user, index=True)
    location_id = fields.Many2one(
        'stock.location', string='Deliver To', required=True, tracking=True, index=True,
        domain="[('usage', '=', 'internal'), ('company_id', 'in', (company_id, False))]",
        help="The department store, office or cabin the material is for.")
    source_location_id = fields.Many2one(
        'stock.location', string='From Store', compute='_compute_source_location',
        help="The main store the material is picked from: the source location of the "
             "operation type set in Inventory settings.")
    picking_type_id = fields.Many2one(
        'stock.picking.type', compute='_compute_source_location')
    date_request = fields.Date(
        string='Requested On', default=fields.Date.context_today, required=True, tracking=True)
    date_needed = fields.Date(
        string='Needed By', tracking=True,
        help="When the department needs it. The board and the list colour a request "
             "that is running late.")
    priority = fields.Selection(
        [('0', 'Normal'), ('1', 'Urgent')], default='0', tracking=True, index=True)
    reason = fields.Text(
        string='Purpose', help="What the material is for, when it is not the usual weekly top-up.")
    line_ids = fields.One2many('material.request.lines', 'material_request_id', string='Lines',
                               copy=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('confirm', 'Waiting Approval'),
        ('approved', 'Approved'),
        ('done', 'Delivered'),
        ('rejected', 'Rejected'),
        ('cancelled', 'Cancelled'),
    ], default='draft', readonly=True, tracking=True, index=True, copy=False)
    approver_id = fields.Many2one('res.users', string='Approved By', readonly=True, copy=False)
    date_approved = fields.Datetime(readonly=True, copy=False)
    rejection_reason = fields.Text(readonly=True, copy=False, tracking=True)
    picking_ids = fields.One2many('stock.picking', 'material_request_id', string='Transfers')
    transfer_count = fields.Integer(compute='_compute_transfer_count')

    # what the board and the list show at a glance
    line_count = fields.Integer(compute='_compute_totals', store=True)
    qty_requested = fields.Float(compute='_compute_totals', store=True, digits='Product Unit of Measure')
    qty_delivered = fields.Float(compute='_compute_totals', store=True, digits='Product Unit of Measure')
    progress = fields.Integer(
        string='Delivered %', compute='_compute_totals', store=True,
        help="Delivered quantity against the approved quantity, across all lines.")
    has_shortage = fields.Boolean(
        compute='_compute_shortage', search='_search_has_shortage',
        help="At least one line asks for more than the source store has on hand.")
    shortage_count = fields.Integer(compute='_compute_shortage')
    is_late = fields.Boolean(compute='_compute_is_late', search='_search_is_late')
    estimated_cost = fields.Monetary(
        compute='_compute_totals', store=True, currency_field='currency_id',
        help="Requested quantities at the products' cost price: what the department is "
             "drawing from the store, in money.")
    currency_id = fields.Many2one(related='company_id.currency_id')

    # ------------------------------------------------------------------ computes
    @api.depends('company_id')
    def _compute_source_location(self):
        for request in self:
            ptype = request.company_id.material_request_type_id
            request.picking_type_id = ptype
            request.source_location_id = ptype.default_location_src_id

    @api.depends('picking_ids')
    def _compute_transfer_count(self):
        for request in self:
            request.transfer_count = len(request.picking_ids)

    @api.depends('line_ids.quantity', 'line_ids.qty_approved', 'line_ids.qty_delivered',
                 'line_ids.product_id.standard_price', 'state')
    def _compute_totals(self):
        for request in self:
            lines = request.line_ids
            request.line_count = len(lines)
            request.qty_requested = sum(lines.mapped('quantity'))
            request.qty_delivered = sum(lines.mapped('qty_delivered'))
            request.estimated_cost = sum(l.quantity * l.product_id.standard_price for l in lines)
            basis = sum(lines.mapped('qty_approved')) if request.state in ('approved', 'done') \
                else sum(lines.mapped('quantity'))
            request.progress = int(round(100.0 * request.qty_delivered / basis)) if basis else 0

    @api.depends('line_ids.availability')
    def _compute_shortage(self):
        for request in self:
            short = request.line_ids.filtered(lambda l: l.availability in ('short', 'none'))
            request.shortage_count = len(short)
            request.has_shortage = bool(short)

    @staticmethod
    def _search_wants_true(operator, value):
        """Whether a boolean search asks for the True records.

        Odoo 19 hands ('field', '=', True) to a search method as ('in', [True]),
        so both spellings, and their negations, have to be read.
        """
        truthy = any(value) if isinstance(value, (list, tuple, set)) else bool(value)
        positive = operator in ('=', 'in')
        return positive == truthy

    def _search_has_shortage(self, operator, value):
        # Availability is live stock, never stored: answer from the lines.
        wanted = self._search_wants_true(operator, value)
        lines = self.env['material.request.lines'].search(
            [('material_request_id.state', 'in', ('draft', 'confirm'))])
        ids = lines.filtered(lambda l: l.availability in ('short', 'none')).mapped(
            'material_request_id').ids
        return [('id', 'in' if wanted else 'not in', ids)]

    @api.depends('date_needed', 'state')
    def _compute_is_late(self):
        today = fields.Date.context_today(self)
        for request in self:
            request.is_late = bool(request.date_needed and request.date_needed < today
                                   and request.state in ('draft', 'confirm', 'approved'))

    def _search_is_late(self, operator, value):
        today = fields.Date.context_today(self)
        domain = [('date_needed', '<', today), ('state', 'in', ('draft', 'confirm', 'approved'))]
        wanted = self._search_wants_true(operator, value)
        if wanted:
            return domain
        return ['|', ('date_needed', '=', False), '|', ('date_needed', '>=', today),
                ('state', 'not in', ('draft', 'confirm', 'approved'))]

    # ------------------------------------------------------------------ crud
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                seq = self.env['ir.sequence']
                if vals.get('company_id'):
                    seq = seq.with_company(vals['company_id'])
                vals['name'] = seq.next_by_code('material.req') or _('New')
        requests = super().create(vals_list)
        # the requester follows their own request: approval and delivery reach them
        for request in requests:
            if request.user_id.partner_id:
                request.message_subscribe(partner_ids=request.user_id.partner_id.ids)
        return requests

    def unlink(self):
        if any(r.state not in ('draft', 'cancelled', 'rejected') for r in self):
            raise UserError(_("Only draft, cancelled or rejected requests can be deleted. "
                              "Cancel the request instead, so its history stays."))
        return super().unlink()

    # ------------------------------------------------------------------ workflow
    def _check_lines(self):
        for request in self:
            if not request.line_ids:
                raise ValidationError(_("Add at least one line before going on."))
            if any(float_compare(l.quantity, 0.0, precision_digits=3) <= 0 for l in request.line_ids):
                raise ValidationError(_("Every line needs a quantity above zero."))

    def action_confirm(self):
        self._check_lines()
        for request in self:
            if request.state != 'draft':
                continue
            request.write({'state': 'confirm'})
            approver = request.company_id.material_request_approver_id
            if approver:
                request.activity_schedule(
                    'mail.mail_activity_data_todo', user_id=approver.id,
                    summary=_("Approve material request %s", request.name),
                    note=_("%(who)s asks for %(n)d item(s) for %(where)s.",
                           who=request.user_id.name, n=request.line_count,
                           where=request.location_id.display_name))
        return True

    def action_approve(self):
        """Approve what can go and raise the internal transfer for it.

        A line whose approved quantity is left at zero is approved for what was asked;
        a storekeeper who wants to give less types the smaller figure first. A line
        explicitly set to zero after that is left out of the transfer.
        """
        self._check_lines()
        for request in self:
            if request.state != 'confirm':
                raise UserError(_("%s is not waiting for approval.", request.name))
            ptype = request.picking_type_id
            if not ptype:
                raise UserError(_("Set the operation type for material requests in "
                                  "Inventory settings first."))
            source = ptype.default_location_src_id
            if not source:
                raise UserError(_("The operation type %s has no source location.", ptype.name))
            moves = []
            for line in request.line_ids:
                if not line.qty_approved_set:
                    line.qty_approved = line.quantity
                if float_compare(line.qty_approved, 0.0, precision_digits=3) <= 0:
                    continue
                moves.append((0, 0, {
                    'product_id': line.product_id.id,
                    'product_uom_qty': line.qty_approved,
                    'product_uom': line.product_uom_id.id,
                    'description_picking': line.product_id.display_name,
                    'location_id': source.id,
                    'location_dest_id': request.location_id.id,
                    'material_request_line_id': line.id,
                }))
            if not moves:
                raise UserError(_("Nothing was approved: every line is at zero."))
            picking = self.env['stock.picking'].sudo().create({
                'picking_type_id': ptype.id,
                'company_id': request.company_id.id,
                'origin': request.name,
                'material_request_id': request.id,
                'location_id': source.id,
                'location_dest_id': request.location_id.id,
                'move_ids': moves,
            })
            picking.action_confirm()
            picking.action_assign()
            request.write({'state': 'approved', 'approver_id': self.env.user.id,
                           'date_approved': fields.Datetime.now()})
            request.activity_feedback(['mail.mail_activity_data_todo'])
            request.message_post(body=_(
                "Approved. Transfer %s raised from %s.", picking.name, source.display_name))
        return True

    def action_reject(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': _('Reject Request'),
            'res_model': 'material.request.reject', 'view_mode': 'form', 'target': 'new',
            'context': {'default_request_id': self.id},
        }

    def _do_reject(self, reason):
        for request in self:
            if request.state not in ('draft', 'confirm'):
                raise UserError(_("%s can no longer be rejected.", request.name))
            request.write({'state': 'rejected', 'rejection_reason': reason,
                           'approver_id': self.env.user.id,
                           'date_approved': fields.Datetime.now()})
            request.activity_feedback(['mail.mail_activity_data_todo'])
            request.message_post(body=_("Rejected: %s", reason))

    def action_cancel(self):
        for request in self:
            live = request.picking_ids.filtered(lambda p: p.state not in ('cancel', 'draft'))
            if live.filtered(lambda p: p.state == 'done'):
                raise UserError(_("%s has been delivered; it cannot be cancelled.", request.name))
            live.action_cancel()
            request.write({'state': 'cancelled'})
            request.activity_unlink(['mail.mail_activity_data_todo'])
        return True

    def action_draft(self):
        for request in self:
            if request.picking_ids.filtered(lambda p: p.state == 'done'):
                raise UserError(_("%s has deliveries against it.", request.name))
            request.picking_ids.filtered(lambda p: p.state != 'cancel').action_cancel()
            request.write({'state': 'draft', 'approver_id': False, 'date_approved': False,
                           'rejection_reason': False})
        return True

    def action_mark_done(self):
        """Close by hand when the transfer was handled outside this screen."""
        for request in self:
            if request.state != 'approved':
                raise UserError(_("Only an approved request can be marked delivered."))
            request.write({'state': 'done'})
        return True

    def _check_delivered(self):
        """Close a request once every transfer against it has been validated."""
        for request in self:
            if request.state != 'approved':
                continue
            pickings = request.picking_ids.filtered(lambda p: p.state != 'cancel')
            if pickings and all(p.state == 'done' for p in pickings):
                request.write({'state': 'done'})
                request.message_post(body=_("Delivered to %s.", request.location_id.display_name))

    def action_reorder(self):
        """A new draft with the same lines: the weekly top-up is the same list every week."""
        self.ensure_one()
        new = self.copy({'date_request': fields.Date.context_today(self), 'date_needed': False,
                         'reason': self.reason})
        new.line_ids.write({'qty_approved': 0.0, 'qty_approved_set': False})
        return {
            'type': 'ir.actions.act_window', 'res_model': 'material.request',
            'res_id': new.id, 'view_mode': 'form', 'target': 'current',
        }

    def action_view_transfers(self):
        self.ensure_one()
        action = self.env['ir.actions.actions']._for_xml_id('stock.action_picking_tree_all')
        action['domain'] = [('material_request_id', '=', self.id)]
        action['context'] = {'create': False}
        if len(self.picking_ids) == 1:
            action['views'] = [(False, 'form')]
            action['res_id'] = self.picking_ids.id
        return action

    def action_print_slip(self):
        return self.env.ref('material_request.action_report_material_request').report_action(self)


class MaterialRequestLine(models.Model):
    _name = 'material.request.lines'
    _description = 'Material Request Line'
    _order = 'material_request_id, sequence, id'

    material_request_id = fields.Many2one(
        'material.request', string='Request', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    product_id = fields.Many2one(
        'product.product', required=True, domain="[('type', '=', 'consu')]",
        index=True)
    product_uom_id = fields.Many2one(related='product_id.uom_id')
    quantity = fields.Float(string='Requested', required=True, default=1.0,
                            digits='Product Unit of Measure')
    qty_approved = fields.Float(string='Approved', digits='Product Unit of Measure', copy=False)
    # Zero can mean "not decided yet" or "refused": the flag tells them apart.
    qty_approved_set = fields.Boolean(copy=False)
    qty_delivered = fields.Float(
        string='Delivered', compute='_compute_qty_delivered', store=True,
        digits='Product Unit of Measure')
    qty_available = fields.Float(
        string='On Hand', compute='_compute_availability', digits='Product Unit of Measure',
        help="On hand at the source store right now.")
    availability = fields.Selection([
        ('ok', 'Available'), ('short', 'Partly'), ('none', 'Out of stock'),
    ], compute='_compute_availability')
    note = fields.Char()
    state = fields.Selection(related='material_request_id.state', store=True)
    location_id = fields.Many2one(related='material_request_id.location_id', store=True)
    date_request = fields.Date(related='material_request_id.date_request', store=True)
    user_id = fields.Many2one(related='material_request_id.user_id', store=True)
    company_id = fields.Many2one(related='material_request_id.company_id', store=True)
    move_ids = fields.One2many('stock.move', 'material_request_line_id')

    @api.depends('move_ids.state', 'move_ids.quantity', 'move_ids.product_uom_qty')
    def _compute_qty_delivered(self):
        for line in self:
            done = line.move_ids.filtered(lambda m: m.state == 'done')
            line.qty_delivered = sum(done.mapped('quantity'))

    @api.depends('product_id', 'quantity', 'material_request_id.source_location_id')
    def _compute_availability(self):
        for line in self:
            source = line.material_request_id.source_location_id
            if not line.product_id or not source:
                line.qty_available = 0.0
                line.availability = False
                continue
            # sudo: on-hand is computed through purchase and manufacturing, which a
            # department user has no rights to read - and needs none of.
            on_hand = line.product_id.sudo().with_context(location=source.id).qty_available
            line.qty_available = on_hand
            if float_is_zero(on_hand, precision_digits=3) or on_hand <= 0:
                line.availability = 'none'
            elif float_compare(on_hand, line.quantity, precision_digits=3) < 0:
                line.availability = 'short'
            else:
                line.availability = 'ok'

    @api.onchange('qty_approved')
    def _onchange_qty_approved(self):
        for line in self:
            line.qty_approved_set = True

    @api.constrains('quantity')
    def _check_quantity(self):
        for line in self:
            if float_compare(line.quantity, 0.0, precision_digits=3) < 0:
                raise ValidationError(_("A requested quantity cannot be negative."))
