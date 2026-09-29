# -*- coding: utf-8 -*-
"""The month-end close: a period, its checklist, the live counts behind each
check, and the lock date set when it is closed."""
from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError

CHECKS = [
    ('draft_entries', 'Draft journal entries in the period'),
    ('draft_invoices', 'Draft invoices and bills in the period'),
    ('bank_unmatched', 'Bank lines not matched'),
    ('suspense', 'Items still on a suspense account'),
    ('payments_unmatched', 'Payments not reconciled'),
    ('no_partner', 'Receivable or payable items without a partner'),
    ('assets_due', 'Depreciation still to post'),
    ('deferrals_due', 'Deferral months still to recognise'),
    ('followups_due', 'Customers with a reminder due'),
    ('findings_open', 'Open ledger findings'),
    ('future_dated', 'Entries posted after the period end'),
]


class ClosePeriod(models.Model):
    _name = 'ebshel.close.period'
    _inherit = ['mail.thread']
    _description = 'Close period'
    _order = 'date_from desc, id desc'

    name = fields.Char(required=True)
    code = fields.Char(readonly=True, copy=False, default=lambda self: _('New'))
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    date_from = fields.Date(required=True)
    date_to = fields.Date(required=True)
    state = fields.Selection([('open', 'Open'), ('review', 'In review'), ('closed', 'Closed')], default='open', tracking=True, copy=False)
    user_id = fields.Many2one('res.users', string='Owner', default=lambda self: self.env.user)
    lock_books = fields.Boolean('Set the lock date on close', default=lambda self: self.env.company.ebshel_close_lock)
    closed_on = fields.Datetime(readonly=True, copy=False)
    closed_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    note = fields.Html()
    task_ids = fields.One2many('ebshel.close.task', 'period_id', copy=False)
    task_count = fields.Integer(compute='_compute_progress')
    done_count = fields.Integer(compute='_compute_progress')
    blocking_count = fields.Integer(compute='_compute_progress', help="Checks that still find something and are not skipped.")
    progress_pct = fields.Float(compute='_compute_progress')

    _dates_ok = models.Constraint('CHECK(date_to >= date_from)', 'The period must end after it starts.')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('code') or vals['code'] == _('New'):
                vals['code'] = self.env['ir.sequence'].next_by_code('ebshel.close.period') or _('New')
        periods = super().create(vals_list)
        for period in periods.filtered(lambda p: not p.task_ids):
            period.action_generate_tasks()
        return periods

    @api.depends('task_ids.state', 'task_ids.count')
    def _compute_progress(self):
        for period in self:
            tasks = period.task_ids
            period.task_count = len(tasks)
            period.done_count = len(tasks.filtered(lambda t: t.state in ('done', 'skipped')))
            period.blocking_count = len(tasks.filtered(lambda t: t.kind == 'check' and t.state == 'todo' and t.count))
            period.progress_pct = (period.done_count / len(tasks) * 100.0) if tasks else 0.0

    def action_generate_tasks(self):
        Template = self.env['ebshel.close.task.template']
        for period in self:
            templates = Template.search(['|', ('company_id', '=', False), ('company_id', '=', period.company_id.id)])
            existing = set(period.task_ids.mapped('name'))
            period.write({'task_ids': [(0, 0, {'name': t.name, 'sequence': t.sequence, 'kind': t.kind, 'check_key': t.check_key,
                                               'user_id': t.user_id.id or period.user_id.id, 'note': t.note})
                                       for t in templates if t.name not in existing]})
            period.action_refresh()
        return True

    def action_refresh(self):
        for task in self.mapped('task_ids').filtered(lambda t: t.kind == 'check'):
            task.action_run_check()
        return True

    def action_review(self):
        self.action_refresh()
        self.write({'state': 'review'})

    def action_close(self):
        for period in self:
            period.action_refresh()
            todo = period.task_ids.filtered(lambda t: t.state == 'todo')
            if todo:
                raise UserError(_("%d task(s) are still to do: %s", len(todo), ', '.join(todo.mapped('name')[:6])))
            if period.lock_books:
                if not self.env.su and not self.env.user.has_group('account.group_account_manager'):
                    raise UserError(_("Only an accounting manager can set the lock date."))
                company = period.company_id
                if not company.fiscalyear_lock_date or company.fiscalyear_lock_date < period.date_to:
                    company.sudo().write({'fiscalyear_lock_date': period.date_to})
                    period.message_post(body=_('Books locked up to %s.', fields.Date.to_string(period.date_to)))
            period.write({'state': 'closed', 'closed_on': fields.Datetime.now(), 'closed_by_id': self.env.user.id})
        return True

    def action_reopen(self):
        for period in self:
            period.write({'state': 'open', 'closed_on': False, 'closed_by_id': False})
            period.message_post(body=_('Reopened. The lock date was left where it is; move it in the accounting settings if needed.'))
        return True

    def action_cockpit(self):
        self.ensure_one()
        return {'type': 'ir.actions.client', 'tag': 'ebshel_close_cockpit', 'name': _('Close cockpit'),
                'params': {'period_id': self.id}}


class CloseTaskTemplate(models.Model):
    _name = 'ebshel.close.task.template'
    _description = 'Close task template'
    _order = 'sequence, id'

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    kind = fields.Selection([('check', 'Automatic check'), ('manual', 'Manual task')], default='manual', required=True)
    check_key = fields.Selection(CHECKS)
    user_id = fields.Many2one('res.users', string='Default owner')
    note = fields.Text('Instructions', translate=True)
    company_id = fields.Many2one('res.company', help="Empty: every company.")
    active = fields.Boolean(default=True)


class CloseTask(models.Model):
    _name = 'ebshel.close.task'
    _description = 'Close task'
    _order = 'period_id, sequence, id'

    period_id = fields.Many2one('ebshel.close.period', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='period_id.company_id', store=True)
    sequence = fields.Integer(default=10)
    name = fields.Char(required=True)
    kind = fields.Selection([('check', 'Automatic check'), ('manual', 'Manual task')], default='manual', required=True)
    check_key = fields.Selection(CHECKS)
    state = fields.Selection([('todo', 'To do'), ('done', 'Done'), ('skipped', 'Skipped')], default='todo')
    count = fields.Integer('Found', help="What the check found the last time it ran.")
    amount = fields.Monetary(currency_field='currency_id')
    currency_id = fields.Many2one(related='company_id.currency_id')
    model = fields.Char()
    res_ids = fields.Json()
    user_id = fields.Many2one('res.users', string='Owner')
    done_on = fields.Datetime(readonly=True)
    done_by_id = fields.Many2one('res.users', readonly=True)
    note = fields.Text()
    checked_on = fields.Datetime(readonly=True)

    def action_run_check(self):
        Checks = self.env['ebshel.close.checks']
        for task in self.filtered(lambda t: t.kind == 'check' and t.check_key):
            count, amount, model, ids = Checks.run(task.check_key, task.period_id)
            vals = {'count': count, 'amount': amount, 'model': model, 'res_ids': ids[:500], 'checked_on': fields.Datetime.now()}
            if task.state == 'done' and count:
                vals['state'] = 'todo'                 # it came back
            elif task.state == 'todo' and not count:
                vals.update({'state': 'done', 'done_on': fields.Datetime.now(), 'done_by_id': False})
            task.write(vals)
        return True

    def action_done(self):
        self.write({'state': 'done', 'done_on': fields.Datetime.now(), 'done_by_id': self.env.user.id})

    def action_skip(self):
        self.write({'state': 'skipped', 'done_on': fields.Datetime.now(), 'done_by_id': self.env.user.id})

    def action_reset(self):
        self.write({'state': 'todo', 'done_on': False, 'done_by_id': False})

    def action_open(self):
        self.ensure_one()
        if not self.model:
            return False
        return {'type': 'ir.actions.act_window', 'res_model': self.model, 'name': self.name,
                'view_mode': 'list,form', 'domain': [('id', 'in', self.res_ids or [])], 'context': {'create': False}}


class CloseChecks(models.AbstractModel):
    """Every automatic check: (count, amount, model, ids) for one period."""
    _name = 'ebshel.close.checks'
    _description = 'Close checks'

    @api.model
    def run(self, key, period):
        self.env.flush_all()
        method = getattr(self, '_check_' + key, None)
        if not method:
            return 0, 0.0, False, []
        model, records, amount = method(period)
        return len(records), amount, model, records.ids

    def _check_draft_entries(self, period):
        moves = self.env['account.move'].search([('company_id', '=', period.company_id.id), ('state', '=', 'draft'),
                                                 ('date', '>=', period.date_from), ('date', '<=', period.date_to),
                                                 ('move_type', '=', 'entry')])
        return 'account.move', moves, sum(moves.mapped('amount_total_signed'))

    def _check_draft_invoices(self, period):
        moves = self.env['account.move'].search([('company_id', '=', period.company_id.id), ('state', '=', 'draft'),
                                                 ('move_type', '!=', 'entry'), '|',
                                                 '&', ('invoice_date', '>=', period.date_from), ('invoice_date', '<=', period.date_to),
                                                 '&', ('invoice_date', '=', False), ('date', '<=', period.date_to)])
        return 'account.move', moves, sum(moves.mapped('amount_total'))

    def _check_bank_unmatched(self, period):
        lines = self.env['account.bank.statement.line'].search([('company_id', '=', period.company_id.id), ('is_reconciled', '=', False),
                                                                 ('date', '<=', period.date_to)])
        return 'account.bank.statement.line', lines, sum(lines.mapped('amount'))

    def _check_suspense(self, period):
        journals = self.env['account.journal'].search([('company_id', '=', period.company_id.id), ('suspense_account_id', '!=', False)])
        accounts = journals.mapped('suspense_account_id')
        lines = self.env['account.move.line'].search([('company_id', '=', period.company_id.id), ('account_id', 'in', accounts.ids),
                                                      ('parent_state', '=', 'posted'), ('reconciled', '=', False),
                                                      ('date', '<=', period.date_to)]) if accounts else self.env['account.move.line']
        return 'account.move.line', lines, sum(lines.mapped('balance'))

    def _check_payments_unmatched(self, period):
        payments = self.env['account.payment'].search([('company_id', '=', period.company_id.id), ('state', 'in', ('in_process', 'paid', 'posted')),
                                                       ('is_reconciled', '=', False), ('date', '<=', period.date_to)])
        return 'account.payment', payments, sum(payments.mapped('amount'))

    def _check_no_partner(self, period):
        lines = self.env['account.move.line'].search([('company_id', '=', period.company_id.id), ('parent_state', '=', 'posted'),
                                                      ('account_id.account_type', 'in', ('asset_receivable', 'liability_payable')),
                                                      ('partner_id', '=', False), ('date', '>=', period.date_from), ('date', '<=', period.date_to)])
        return 'account.move.line', lines, sum(lines.mapped('balance'))

    def _check_assets_due(self, period):
        if 'ebshel.asset.line' not in self.env:
            return 'ebshel.asset.line', self.env['ebshel.close.task'].browse(), 0.0
        lines = self.env['ebshel.asset.line'].search([('asset_id.company_id', '=', period.company_id.id), ('state', '=', 'draft'),
                                                      ('asset_id.state', '=', 'running'), ('date', '<=', period.date_to)])
        return 'ebshel.asset.line', lines, sum(lines.mapped('amount'))

    def _check_deferrals_due(self, period):
        lines = self.env['ebshel.deferral.line'].search([('deferral_id.company_id', '=', period.company_id.id), ('state', '=', 'draft'),
                                                         ('deferral_id.state', '=', 'running'), ('date', '<=', period.date_to)])
        return 'ebshel.deferral.line', lines, sum(lines.mapped('amount'))

    def _check_followups_due(self, period):
        partners = self.env['res.partner'].with_company(period.company_id).search([('ebshel_followup_state', '=', 'action')])
        return 'res.partner', partners, sum(partners.mapped('ebshel_overdue_total'))

    def _check_findings_open(self, period):
        findings = self.env['ebshel.ledger.finding'].search([('company_id', '=', period.company_id.id), ('state', '=', 'open'),
                                                             ('severity', 'in', ('warn', 'error'))])
        return 'ebshel.ledger.finding', findings, sum(findings.mapped('amount'))

    def _check_future_dated(self, period):
        moves = self.env['account.move'].search([('company_id', '=', period.company_id.id), ('state', '=', 'posted'),
                                                 ('date', '>', period.date_to), ('create_date', '<=', fields.Datetime.to_datetime(period.date_to).replace(hour=23, minute=59))])
        return 'account.move', moves, sum(moves.mapped('amount_total_signed'))


class CloseCockpit(models.AbstractModel):
    _name = 'ebshel.close.cockpit'
    _description = 'Close cockpit'

    @api.model
    def _check(self, write=False):
        if self.env.su:
            return
        user = self.env.user
        if not user.has_groups('account.group_account_readonly,account.group_account_invoice,account.group_account_user'):
            raise AccessError(_("The close cockpit is for the accounting team."))
        if write and not user.has_groups('account.group_account_user,account.group_account_manager'):
            raise AccessError(_("Only accountants can work the close checklist."))

    @api.model
    def get_data(self, period_id=None):
        self._check()
        company = self.env.company
        Period = self.env['ebshel.close.period']
        periods = Period.search([('company_id', '=', company.id)], limit=24)
        period = Period.browse(period_id) if period_id else (periods.filtered(lambda p: p.state != 'closed')[:1] or periods[:1])
        if period and period.state != 'closed':
            period.action_refresh()
        tasks = []
        for t in (period.task_ids if period else []):
            tasks.append({'id': t.id, 'name': t.name, 'kind': t.kind, 'check_key': t.check_key, 'state': t.state, 'count': t.count,
                          'amount': t.amount, 'model': t.model, 'owner': t.user_id.name or '', 'owner_id': t.user_id.id,
                          'note': t.note or '', 'done_by': t.done_by_id.name or '',
                          'done_on': fields.Datetime.to_string(t.done_on) if t.done_on else None,
                          'blocking': t.kind == 'check' and t.state == 'todo' and bool(t.count)})
        return {
            'periods': [{'id': p.id, 'name': p.name, 'state': p.state, 'from': fields.Date.to_string(p.date_from),
                         'to': fields.Date.to_string(p.date_to), 'progress': round(p.progress_pct)} for p in periods],
            'period': {'id': period.id, 'name': period.name, 'code': period.code, 'state': period.state,
                       'from': fields.Date.to_string(period.date_from), 'to': fields.Date.to_string(period.date_to),
                       'progress': round(period.progress_pct), 'done': period.done_count, 'total': period.task_count,
                       'blocking': period.blocking_count, 'lock_books': period.lock_books, 'owner': period.user_id.name or '',
                       'closed_on': fields.Datetime.to_string(period.closed_on) if period.closed_on else None,
                       'closed_by': period.closed_by_id.name or ''} if period else None,
            'tasks': tasks,
            'locks': {'fiscal': fields.Date.to_string(company.fiscalyear_lock_date) if company.fiscalyear_lock_date else None,
                      'tax': fields.Date.to_string(company.tax_lock_date) if company.tax_lock_date else None,
                      'sale': fields.Date.to_string(company.sale_lock_date) if company.sale_lock_date else None,
                      'purchase': fields.Date.to_string(company.purchase_lock_date) if company.purchase_lock_date else None},
            'users': [{'id': u.id, 'name': u.name} for u in self.env['res.users'].search([('share', '=', False)], order='name', limit=80)],
            'currency': {'symbol': company.currency_id.symbol, 'position': company.currency_id.position, 'decimals': company.currency_id.decimal_places},
            'can_write': self.env.su or self.env.user.has_groups('account.group_account_user,account.group_account_manager'),
            'can_lock': self.env.su or self.env.user.has_group('account.group_account_manager'),
        }

    @api.model
    def set_task(self, task_id, state, note=None):
        self._check(write=True)
        task = self.env['ebshel.close.task'].browse(task_id)
        if note is not None:
            task.note = note
        {'done': task.action_done, 'skipped': task.action_skip, 'todo': task.action_reset}[state]()
        return True

    @api.model
    def assign(self, task_id, user_id):
        self._check(write=True)
        self.env['ebshel.close.task'].browse(task_id).write({'user_id': user_id or False})
        return True

    @api.model
    def create_period(self, date_from, date_to, name=None):
        self._check(write=True)
        d_from, d_to = fields.Date.to_date(date_from), fields.Date.to_date(date_to)
        period = self.env['ebshel.close.period'].create({'name': name or d_from.strftime('%B %Y'), 'date_from': d_from, 'date_to': d_to})
        return period.id

    @api.model
    def close(self, period_id):
        self._check(write=True)
        self.env['ebshel.close.period'].browse(period_id).action_close()
        return True

    @api.model
    def reopen(self, period_id):
        self._check(write=True)
        self.env['ebshel.close.period'].browse(period_id).action_reopen()
        return True

    @api.model
    def open_task(self, task_id):
        self._check()
        return self.env['ebshel.close.task'].browse(task_id).action_open()
