# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError

STATUS = [
    ('draft', 'New'),
    ('submitted', 'Submitted'),
    ('approved', 'Approved'),
    ('rejected', 'Rejected'),
    ('cancel', 'Cancelled'),
]


class DailySales(models.Model):
    _name = 'daily.sales'
    _inherit = ['mail.thread']
    _description = "Daily Sales"
    _order = "date desc, sales_person_id asc"
    _rec_name = 'sales_person_id'

    @api.model
    def _get_default_team(self):
        return self.env['crm.team']._get_default_team_id()

    sales_person_id = fields.Many2one(
        'res.users', string='Sales Officer', required=True, tracking=True,
        default=lambda self: self.env.user, readonly=True)
    # Business date (native create_date is kept as the audit timestamp).
    date = fields.Date(
        string='Date', tracking=True, copy=False,
        default=fields.Date.context_today)
    create_user_id = fields.Many2one(
        'res.users', string='Created By',
        default=lambda self: self.env.user, tracking=True)
    currency_id = fields.Many2one(
        'res.currency', related='company_id.currency_id',
        string="Company Currency", readonly=True)
    company_id = fields.Many2one(
        'res.company', string='Company', required=True, readonly=True,
        default=lambda self: self.env.company)
    state = fields.Selection(
        STATUS, string='Status', readonly=True, default='draft',
        tracking=True, copy=False)
    work_collected = fields.Integer(
        string='Work Collected', compute='_compute_all', store=True,
        tracking=True, readonly=False)
    work_delivered = fields.Integer(
        string='Work Delivered', compute='_compute_all', store=True,
        tracking=True, readonly=False)
    payment_amount = fields.Monetary(
        currency_field="currency_id", string='Payment', store=True,
        tracking=True, compute='_compute_all', readonly=False)
    team_id = fields.Many2one(
        'crm.team', string='Sales Team', default=_get_default_team,
        readonly=True)
    sales_line_ids = fields.One2many(
        'daily.sales.line', 'sales_id', string='Daily Sales Lines', copy=True)

    approved_by_id = fields.Many2one('res.users', string='Approved By', readonly=True, copy=False)
    approved_date = fields.Datetime(string='Approved On', readonly=True, copy=False)
    manager_note = fields.Char(string='Manager Note', copy=False,
                                help="Reason shown to the officer when rejected.")

    @api.depends('sales_line_ids.work_collected',
                 'sales_line_ids.work_delivered',
                 'sales_line_ids.payment_amount')
    def _compute_all(self):
        for record in self:
            record.work_collected = sum(record.sales_line_ids.mapped('work_collected'))
            record.work_delivered = sum(record.sales_line_ids.mapped('work_delivered'))
            record.payment_amount = sum(record.sales_line_ids.mapped('payment_amount'))

    def action_submit(self):
        """Officer submits their daily entry for manager review."""
        self.filtered(lambda r: r.state == 'draft').write({'state': 'submitted'})
        return True

    def _check_is_manager(self):
        if not self.env.user.has_group('sales_team.group_sale_manager'):
            raise UserError(_("Only a Sales Manager can approve or reject a daily sales entry."))

    def action_approve(self):
        self._check_is_manager()
        self.filtered(lambda r: r.state == 'submitted').write({
            'state': 'approved',
            'approved_by_id': self.env.user.id,
            'approved_date': fields.Datetime.now(),
            'manager_note': False,
        })
        return True

    def action_reject(self):
        self._check_is_manager()
        self.filtered(lambda r: r.state == 'submitted').write({'state': 'rejected'})
        return True

    def action_cancel(self):
        self.write({'state': 'cancel'})
        return True

    def action_draft(self):
        self.write({'state': 'draft', 'approved_by_id': False, 'approved_date': False})
        return True


class DailySalesLine(models.Model):
    _name = 'daily.sales.line'
    _description = "Daily Sales Lines"
    _order = "date desc, sales_person_id asc"
    _rec_name = 'sales_person_id'

    sales_id = fields.Many2one(
        'daily.sales', string='Daily Sales', required=True, ondelete='cascade')
    sales_person_id = fields.Many2one(
        'res.users', related='sales_id.sales_person_id', string='Sales Officer',
        store=True)
    date = fields.Date(
        related='sales_id.date', string='Date', store=True)
    create_user_id = fields.Many2one(
        'res.users', string='Created By', default=lambda self: self.env.user)
    currency_id = fields.Many2one(
        'res.currency', related='sales_id.currency_id',
        string="Company Currency", readonly=True)
    company_id = fields.Many2one(
        'res.company', related='sales_id.company_id', store=True, string='Company')
    partner_id = fields.Many2one('res.partner', string='Clinic')
    contact_id = fields.Many2one('res.partner', string='Doctor')
    work_collected = fields.Integer(string='Work Collected')
    work_delivered = fields.Integer(string='Work Delivered')
    is_new_clinic = fields.Boolean(string='New Clinic')
    payment_amount = fields.Monetary(currency_field="currency_id", string='Payment')
    team_id = fields.Many2one(
        'crm.team', related='sales_id.team_id', store=True, string='Sales Team')
