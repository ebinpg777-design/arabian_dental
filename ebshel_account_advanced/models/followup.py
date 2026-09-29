# -*- coding: utf-8 -*-
"""Collections: the follow-up levels, the log of what was done for each
customer, the nightly job, and the desk that puts every customer who owes
money on one screen."""
import logging
from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import AccessError
from odoo.modules import module as odoo_module
from odoo.tools import SQL

_logger = logging.getLogger(__name__)

FOLLOWUP_STATES = [
    ('none', 'Nothing due'),
    ('due', 'Due, not overdue'),
    ('overdue', 'Overdue, below the first level'),
    ('action', 'Reminder due'),
    ('reminded', 'Reminded, waiting'),
    ('promised', 'Payment promised'),
    ('excluded', 'Excluded'),
]


class FollowupLevel(models.Model):
    _name = 'ebshel.followup.level'
    _description = 'Follow-up level'
    _order = 'company_id, days, id'

    name = fields.Char(required=True, translate=True)
    days = fields.Integer('Days overdue', required=True, default=0,
                          help="Reached once the oldest open invoice is this many days past its due date.")
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    action = fields.Selection([('email', 'Send an email'), ('email_call', 'Email, then a call'),
                               ('call', 'A call to make'), ('letter', 'Print a letter'), ('todo', 'A to-do')],
                              default='email', required=True)
    template_id = fields.Many2one('mail.template', string='Email template', domain="[('model', '=', 'res.partner')]")
    attach_statement = fields.Boolean('Attach the statement of account', default=True)
    attach_invoices = fields.Boolean('Attach the overdue invoices', default=True)
    auto = fields.Boolean('Automatic', help="The nightly job sends this level itself when it falls due.")
    on_hold = fields.Boolean('Put the customer on hold', help="Flags the customer on the desk and on their form.")
    repeat_days = fields.Integer('Repeat after (days)', default=30,
                                 help="For the last level: how long to wait before reminding again.")
    text = fields.Html('Reminder text', translate=True, sanitize=True,
                       help="Used in the email when no template is set, and in the printed letter.")
    activity_type_id = fields.Many2one('mail.activity.type', string='Schedule an activity')
    responsible = fields.Selection([('followup', 'Follow-up responsible'), ('salesperson', 'Salesperson')],
                                   default='followup', required=True)
    active = fields.Boolean(default=True)
    partner_count = fields.Integer(compute='_compute_partner_count')

    _days_positive = models.Constraint('CHECK(days >= 0)', 'Days overdue cannot be negative.')

    def _compute_partner_count(self):
        for level in self:
            level.partner_count = self.env['res.partner'].search_count([('ebshel_followup_level_id', '=', level.id)])

    @api.model
    def _for_days(self, days_overdue, company):
        """The level reached after this many days overdue: the highest one at or below."""
        return self.search([('company_id', '=', company.id), ('days', '<=', max(days_overdue, 0))],
                           order='days desc, id desc', limit=1)

    def _next(self):
        self.ensure_one()
        return self.search([('company_id', '=', self.company_id.id), ('days', '>', self.days)], order='days, id', limit=1)

    def _wait_days(self):
        """How long after this level before the next reminder is due."""
        self.ensure_one()
        nxt = self._next()
        return max((nxt.days - self.days) if nxt else self.repeat_days, 1)

    def action_open_partners(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': 'res.partner', 'name': self.name,
                'view_mode': 'list,form', 'domain': [('ebshel_followup_level_id', '=', self.id)]}


class FollowupAction(models.Model):
    _name = 'ebshel.followup.action'
    _description = 'Follow-up action'
    _order = 'date desc, id desc'

    partner_id = fields.Many2one('res.partner', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')
    date = fields.Datetime(default=fields.Datetime.now, required=True)
    user_id = fields.Many2one('res.users', default=lambda self: self.env.user)
    level_id = fields.Many2one('ebshel.followup.level', ondelete='set null')
    kind = fields.Selection([('email', 'Email sent'), ('letter', 'Letter printed'), ('call', 'Call logged'),
                             ('promise', 'Promise to pay'), ('snooze', 'Snoozed'), ('note', 'Note'),
                             ('exclude', 'Excluded'), ('include', 'Included again'), ('hold', 'Put on hold'),
                             ('release', 'Released')], required=True)
    note = fields.Text()
    amount = fields.Monetary(currency_field='currency_id')
    promise_date = fields.Date()
    mail_id = fields.Many2one('mail.mail', ondelete='set null')


class FollowupDesk(models.AbstractModel):
    """The screen: every customer with money open, what to do next, and the
    buttons that do it."""
    _name = 'ebshel.followup.desk'
    _description = 'Collections desk'

    @api.model
    def _check(self, write=False):
        if self.env.su:
            return
        user = self.env.user
        if not (user.has_group('account.group_account_readonly') or user.has_group('account.group_account_invoice')):
            raise AccessError(_("The collections desk is for the accounting team."))
        if write and not user.has_group('account.group_account_invoice'):
            raise AccessError(_("Only the invoicing team can act from the collections desk."))

    @api.model
    def _currency_info(self, currency):
        return {'symbol': currency.symbol, 'position': currency.position,
                'decimals': currency.decimal_places, 'name': currency.name}

    @api.model
    def _buckets(self, company, today):
        """The overdue amount by age."""
        edges = [(1, 30), (31, 60), (61, 90), (91, 180), (181, None)]
        cases = SQL(", ").join(
            SQL("SUM(CASE WHEN %s - COALESCE(l.date_maturity, l.date) BETWEEN %s AND %s THEN l.amount_residual ELSE 0 END)",
                today, lo, hi if hi else 100000) for lo, hi in edges)
        row = self.env.execute_query(SQL("""
            SELECT %s FROM account_move_line l JOIN account_account a ON a.id = l.account_id
             WHERE l.company_id = %s AND a.account_type = 'asset_receivable' AND l.parent_state = 'posted'
               AND NOT l.reconciled AND l.amount_residual > 0 AND COALESCE(l.date_maturity, l.date) < %s
        """, cases, company.id, today))[0]
        labels = ['1–30', '31–60', '61–90', '91–180', '180+']
        return [{'label': lab, 'value': float(v or 0.0)} for lab, v in zip(labels, row)]

    @api.model
    def _sales_per_day(self, company, today):
        row = self.env.execute_query(SQL("""
            SELECT COALESCE(SUM(amount_total_signed), 0) FROM account_move
             WHERE company_id = %s AND state = 'posted' AND move_type IN ('out_invoice', 'out_refund')
               AND invoice_date > %s AND invoice_date <= %s
        """, company.id, today - timedelta(days=90), today))[0]
        return float(row[0] or 0.0) / 90.0

    @api.model
    def get_data(self, options=None):
        self._check()
        self.env.flush_all()
        options = options or {}
        company = self.env.company
        today = fields.Date.context_today(self)
        Partner = self.env['res.partner']
        rows = Partner._ebshel_followup_rows(company, today)
        partners = Partner.browse([r['partner'] for r in rows])
        habits = Partner._ebshel_pay_habits(partners.ids)
        Action = self.env['ebshel.followup.action']
        last = {}
        for act in Action.search([('partner_id', 'in', partners.ids)], order='date desc, id desc'):
            last.setdefault(act.partner_id.id, act)
        wanted = set(options.get('state') or [])
        responsible = options.get('responsible_id')
        search = (options.get('search') or '').strip().lower()
        out, kpis = [], {'receivable': 0.0, 'overdue': 0.0, 'action': 0, 'promised': 0.0, 'on_hold': 0, 'unallocated': 0.0}
        for partner, row in zip(partners, rows):
            state, suggested, days = partner._ebshel_judge(row['due'], row['overdue'], row['oldest'], today)
            kpis['receivable'] += row['due']
            kpis['overdue'] += row['overdue']
            kpis['unallocated'] += row['unallocated']
            if state == 'action':
                kpis['action'] += 1
            if state == 'promised':
                kpis['promised'] += partner.ebshel_promise_amount or row['overdue']
            if partner.ebshel_on_hold:
                kpis['on_hold'] += 1
            if wanted and state not in wanted:
                continue
            if responsible and partner.ebshel_followup_responsible_id.id != responsible:
                continue
            if search and search not in (partner.display_name or '').lower():
                continue
            act = last.get(partner.id)
            habit = habits.get(partner.id, {})
            out.append({
                'id': partner.id, 'name': partner.display_name, 'due': row['due'], 'overdue': row['overdue'],
                'unallocated': row['unallocated'], 'invoices': row['invoices'], 'days': days,
                'state': state, 'state_label': dict(FOLLOWUP_STATES)[state],
                'level': partner.ebshel_followup_level_id.name or '',
                'suggested': {'id': suggested.id, 'name': suggested.name, 'action': suggested.action} if suggested else None,
                'next_date': fields.Date.to_string(partner.ebshel_followup_next_date) if partner.ebshel_followup_next_date else None,
                'promise_date': fields.Date.to_string(partner.ebshel_promise_date) if partner.ebshel_promise_date else None,
                'promise_amount': partner.ebshel_promise_amount,
                'responsible_id': partner.ebshel_followup_responsible_id.id,
                'responsible': partner.ebshel_followup_responsible_id.name or '',
                'last_action': {'kind': act.kind, 'label': dict(act._fields['kind'].selection)[act.kind],
                                'date': fields.Date.to_string(act.date.date()), 'user': act.user_id.name or '',
                                'note': act.note or ''} if act else None,
                'email': bool(partner.email or partner.child_ids.filtered('email')),
                'phone': partner.phone or '',
                'on_hold': partner.ebshel_on_hold, 'excluded': partner.ebshel_followup_excluded,
                'days_to_pay': habit.get('to_pay'), 'days_late': habit.get('late'),
            })
        order = options.get('sort') or 'overdue'
        reverse = order in ('overdue', 'due', 'days')
        out.sort(key=lambda r: (r.get(order) or 0) if order != 'name' else r['name'], reverse=reverse)
        limit = options.get('limit') or 300
        per_day = self._sales_per_day(company, today)
        kpis['dso'] = round(kpis['receivable'] / per_day, 0) if per_day else None
        return {
            'today': fields.Date.to_string(today),
            'kpis': kpis,
            'buckets': self._buckets(company, today),
            'rows': out[:limit], 'total_rows': len(out),
            'levels': [{'id': l.id, 'name': l.name, 'days': l.days, 'action': l.action, 'auto': l.auto}
                       for l in self.env['ebshel.followup.level'].search([('company_id', '=', company.id)])],
            'states': [{'key': k, 'label': v} for k, v in FOLLOWUP_STATES],
            'users': [{'id': u.id, 'name': u.name} for u in self.env['res.users'].search([('share', '=', False)], order='name', limit=80)],
            'currency': self._currency_info(company.currency_id),
            'can_write': self.env.su or self.env.user.has_group('account.group_account_invoice'),
            'auto': company.ebshel_followup_auto,
        }

    @api.model
    def partner_detail(self, partner_id):
        self._check()
        self.env.flush_all()
        partner = self.env['res.partner'].browse(partner_id)
        today = fields.Date.context_today(self)
        items = []
        for line in partner._ebshel_open_items():
            due = line.date_maturity or line.date
            items.append({'id': line.id, 'move_id': line.move_id.id, 'name': line.move_id.name, 'ref': line.move_id.ref or '',
                          'date': fields.Date.to_string(line.date), 'due': fields.Date.to_string(due),
                          'days': (today - due).days, 'amount': line.balance, 'residual': line.amount_residual,
                          'kind': 'invoice' if line.amount_residual > 0 else 'credit'})
        items.sort(key=lambda i: (i['kind'] != 'invoice', i['due']))
        acts = []
        for act in self.env['ebshel.followup.action'].search([('partner_id', '=', partner.id)], limit=30):
            acts.append({'id': act.id, 'kind': act.kind, 'label': dict(act._fields['kind'].selection)[act.kind],
                         'date': fields.Datetime.to_string(act.date), 'user': act.user_id.name or '',
                         'level': act.level_id.name or '', 'note': act.note or '', 'amount': act.amount,
                         'promise_date': fields.Date.to_string(act.promise_date) if act.promise_date else None})
        habit = partner._ebshel_pay_habits([partner.id]).get(partner.id, {})
        emails = [c.email for c in (partner | partner.child_ids) if c.email]
        return {'id': partner.id, 'name': partner.display_name, 'items': items, 'actions': acts,
                'emails': emails, 'phone': partner.phone or '',
                'note': partner.ebshel_followup_note or '', 'habit': habit,
                'suggested_id': partner.ebshel_suggested_level_id.id,
                'statement_key': 'customer_statement'}

    # ------------------------------------------------------------------ actions
    @api.model
    def send(self, partner_ids, level_id=None, note=None):
        self._check(write=True)
        return self.env['res.partner'].browse(partner_ids).action_ebshel_send_reminder(level_id=level_id, note=note)

    @api.model
    def log_call(self, partner_id, note, next_date=None):
        self._check(write=True)
        return self.env['res.partner'].browse(partner_id).action_ebshel_log_call(note, next_date=next_date)

    @api.model
    def promise(self, partner_id, date, amount=0.0, note=None):
        self._check(write=True)
        return self.env['res.partner'].browse(partner_id).action_ebshel_promise(date, amount=amount, note=note)

    @api.model
    def snooze(self, partner_id, days, note=None):
        self._check(write=True)
        return self.env['res.partner'].browse(partner_id).action_ebshel_snooze(days, note=note)

    @api.model
    def set_flag(self, partner_id, flag, value, note=None):
        self._check(write=True)
        partner = self.env['res.partner'].browse(partner_id)
        if flag == 'excluded':
            return partner.action_ebshel_set_exclusion(bool(value), note=note)
        if flag == 'on_hold':
            return partner.action_ebshel_set_hold(bool(value), note=note)
        return False

    @api.model
    def set_responsible(self, partner_id, user_id):
        self._check(write=True)
        self.env['res.partner'].browse(partner_id).write({'ebshel_followup_responsible_id': user_id or False})
        return True

    @api.model
    def save_note(self, partner_id, note):
        self._check(write=True)
        self.env['res.partner'].browse(partner_id).write({'ebshel_followup_note': note})
        return True

    @api.model
    def letter(self, partner_ids, level_id=None):
        self._check(write=True)
        return self.env['res.partner'].browse(partner_ids).action_ebshel_print_letter(level_id=level_id)

    # ------------------------------------------------------------------ cron
    @api.model
    def _cron_followups(self):
        """Send the automatic levels that fell due; one customer per transaction."""
        today = fields.Date.context_today(self)
        for company in self.env['res.company'].search([('ebshel_followup_auto', '=', True)]):
            Partner = self.env['res.partner'].with_company(company)
            rows = Partner._ebshel_followup_rows(company, today)
            for row in rows:
                partner = Partner.browse(row['partner'])
                state, level, _days = partner._ebshel_judge(row['due'], row['overdue'], row['oldest'], today)
                if state != 'action' or not level or not level.auto or level.action not in ('email', 'email_call'):
                    continue
                try:
                    with self.env.cr.savepoint():
                        partner.action_ebshel_send_reminder(level_id=level.id, note=_('Sent automatically'))
                except Exception:                                      # noqa: BLE001
                    _logger.exception("automatic reminder for %s failed", partner.display_name)
                if not odoo_module.current_test:
                    self.env.cr.commit()
        return True
