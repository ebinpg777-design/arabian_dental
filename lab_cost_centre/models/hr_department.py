# -*- coding: utf-8 -*-
"""The department: the lab's own unit of responsibility.

Odoo already has a department tree, used for staff. This puts the rest of the
lab on the same tree - the benches that belong to it, the store it draws from,
and the cost centre everything it spends lands on - so that "Ceramic" means one
thing in the payroll, on the floor, in the stores and in the ledger.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class HrDepartment(models.Model):
    _inherit = 'hr.department'

    code = fields.Char(
        string='Code', index=True, copy=False,
        help="Short code used to name the department's cost centre and to read "
             "it on a printed sheet: PRD-CR, SCM-ST.")
    dept_type = fields.Selection([
        ('production', 'Production'),
        ('quality', 'Quality'),
        ('commercial', 'Commercial'),
        ('supply', 'Supply Chain'),
        ('admin', 'Administration'),
    ], string='Kind', default='production', index=True,
        help="What this department is for. Production departments are the ones a "
             "job passes through; the rest support them.")

    # THE COST CENTRE. A department is who is responsible; a cost centre is where
    # the money lands. They are one to one here on purpose - the lab has never
    # needed a cost centre that is not a department - but they stay two records,
    # because Odoo reports on analytic accounts and manages people on departments,
    # and neither can be taught to be the other.
    cost_centre_id = fields.Many2one(
        'account.analytic.account', string='Cost Centre', copy=False, index=True,
        domain="[('plan_id.is_cost_centre_plan', '=', True)]",
        help="The analytic account every cost and revenue of this department is "
             "posted against.")

    # WHERE ITS MATERIAL SITS. The lab keeps a sub-store per department and issues
    # from the main store into it; what a bench consumes leaves from there.
    store_location_id = fields.Many2one(
        'stock.location', string='Department Store',
        domain="[('usage', '=', 'internal')]",
        help="The sub-store this department draws its material into.")
    consumption_location_id = fields.Many2one(
        'stock.location', string='Consumption Account',
        domain="[('usage', '=', 'inventory')]",
        help="Where material is written off when this department consumes it.")

    workcenter_ids = fields.One2many(
        'mrp.workcenter', 'department_id', string='Work Centres')
    workcenter_count = fields.Integer(compute='_compute_counts')
    employee_count = fields.Integer(compute='_compute_counts')

    _code_unique = models.Constraint(
        'UNIQUE(code)', "Two departments cannot share one code.")

    @api.depends('workcenter_ids', 'member_ids')
    def _compute_counts(self):
        # One grouped read for the whole list rather than a count per row: the
        # department list is opened with twenty-odd rows on it.
        benches = dict(self.env['mrp.workcenter']._read_group(
            [('department_id', 'in', self.ids)], ['department_id'], ['__count']))
        for department in self:
            department.workcenter_count = benches.get(department, 0)
            department.employee_count = len(department.member_ids)

    @api.depends('code', 'complete_name')
    def _compute_display_name(self):
        # The code first, because that is how the floor says it and how it prints.
        # Odoo 19 reads display_name, not name_get.
        for department in self:
            department.display_name = '%s %s' % (department.code, department.complete_name) \
                if department.code else department.complete_name

    # ------------------------------------------------------------------ actions
    def action_create_cost_centre(self):
        """Give each selected department a cost centre, named after it.

        Written as an action rather than a create() hook: a department is also an
        HR record, and somebody adding "Night Shift" to the staff structure should
        not silently open a new line in the ledger.
        """
        plan = self.env['account.analytic.plan']._lab_cost_centre_plan()
        made = self.env['account.analytic.account']
        for department in self:
            if department.cost_centre_id:
                continue
            if not department.code:
                raise UserError(_(
                    "%s needs a code before it can have a cost centre.",
                    department.display_name))
            centre = self.env['account.analytic.account'].create({
                'name': department.complete_name,
                'code': department.code,
                'plan_id': plan.id,
                'company_id': department.company_id.id or self.env.company.id,
            })
            department.cost_centre_id = centre
            made |= centre
        return made

    def action_view_analytic_items(self):
        """Everything posted against this department's cost centre."""
        self.ensure_one()
        if not self.cost_centre_id:
            raise UserError(_("%s has no cost centre yet.", self.display_name))
        return {
            'type': 'ir.actions.act_window',
            'name': _('%s — analytic items', self.display_name),
            'res_model': 'account.analytic.line',
            'view_mode': 'list,pivot,graph',
            # See `cost_centre_board.open_items`: a caller that goes
            # through `call_kw` gets no `views` unless they are written out.
            'views': [(False, 'list'), (False, 'pivot'), (False, 'graph'),
                      (False, 'form')],
            'domain': [('auto_account_id', '=', self.cost_centre_id.id)],
            # `analytic_plan_id`: the Analytic Account column on a line is
            # computed from whichever plan the context names. Without it the
            # column reads blank on every row, and a list of amounts with no
            # account beside them is unreadable.
            'context': {'search_default_group_date': 1,
                        'analytic_plan_id': self.cost_centre_id.plan_id.id},
        }

    def action_view_workcenters(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Work centres of %s', self.display_name),
            'res_model': 'mrp.workcenter',
            'view_mode': 'list,form',
            'views': [(False, 'list'), (False, 'form')],
            'domain': [('department_id', '=', self.id)],
            'context': {'default_department_id': self.id},
        }

    # ------------------------------------------------------------------ helpers
    @api.model
    def _lab_department_of(self, record):
        """The department a record belongs to, or an empty recordset.

        One place, so that every document derives it the same way and a rule
        changed here changes everywhere.
        """
        if not record:
            return self.browse()
        for field in ('department_id', 'workcenter_id', 'categ_id', 'location_id'):
            if field not in record._fields:
                continue
            value = record[field]
            if field == 'department_id':
                return value
            if value and 'department_id' in value._fields and value.department_id:
                return value.department_id
        return self.browse()
