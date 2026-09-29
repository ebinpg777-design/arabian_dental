# -*- coding: utf-8 -*-
"""What a customer owes, how they pay, and the reminders sent to them."""
import base64
from datetime import timedelta

from markupsafe import Markup, escape

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools import SQL, formatLang

from .followup import FOLLOWUP_STATES


class ResPartner(models.Model):
    _inherit = 'res.partner'

    ebshel_followup_level_id = fields.Many2one('ebshel.followup.level', string='Last reminder level', copy=False, tracking=True)
    ebshel_followup_next_date = fields.Date('Next reminder', copy=False)
    ebshel_followup_responsible_id = fields.Many2one('res.users', string='Follow-up responsible', copy=False)
    ebshel_followup_excluded = fields.Boolean('Never remind', copy=False, tracking=True)
    ebshel_on_hold = fields.Boolean('On hold', copy=False, tracking=True,
                                    help="Flagged by a follow-up level or by hand; shown on the desk and the form.")
    ebshel_followup_note = fields.Text('Collection notes', copy=False)
    ebshel_promise_date = fields.Date('Promised for', copy=False)
    ebshel_promise_amount = fields.Monetary('Promised amount', copy=False)
    ebshel_followup_action_ids = fields.One2many('ebshel.followup.action', 'partner_id', string='Follow-up log')
    ebshel_followup_action_count = fields.Integer(compute='_compute_ebshel_followup')
    ebshel_due_total = fields.Monetary(compute='_compute_ebshel_followup', string='Open receivable')
    ebshel_overdue_total = fields.Monetary(compute='_compute_ebshel_followup', string='Overdue')
    ebshel_overdue_days = fields.Integer(compute='_compute_ebshel_followup', string='Oldest overdue (days)')
    ebshel_followup_state = fields.Selection(FOLLOWUP_STATES, compute='_compute_ebshel_followup',
                                             search='_search_ebshel_followup_state', string='Collection status')
    ebshel_suggested_level_id = fields.Many2one('ebshel.followup.level', compute='_compute_ebshel_followup', string='Level reached')
    ebshel_avg_days_to_pay = fields.Float(compute='_compute_ebshel_pay_habits', string='Average days to pay', digits=(16, 1))
    ebshel_avg_days_late = fields.Float(compute='_compute_ebshel_pay_habits', string='Average days late', digits=(16, 1))

    # ------------------------------------------------------------------ ledger reads
    @api.model
    def _ebshel_followup_rows(self, company, today, partner_ids=None):
        """Per customer (commercial partner): what is open, overdue, how old."""
        self.env['account.move.line'].flush_model()
        where = SQL("l.company_id = %s", company.id)
        if partner_ids:
            where = SQL("%s AND COALESCE(p.commercial_partner_id, p.id) IN %s", where, tuple(partner_ids))
        rows = self.env.execute_query(SQL("""
            SELECT COALESCE(p.commercial_partner_id, p.id) AS partner,
                   SUM(l.amount_residual) AS due,
                   SUM(CASE WHEN l.amount_residual > 0 AND COALESCE(l.date_maturity, l.date) < %(today)s THEN l.amount_residual ELSE 0 END) AS overdue,
                   MIN(CASE WHEN l.amount_residual > 0 AND COALESCE(l.date_maturity, l.date) < %(today)s THEN COALESCE(l.date_maturity, l.date) END) AS oldest,
                   COUNT(*) FILTER (WHERE l.amount_residual > 0) AS invoices,
                   SUM(CASE WHEN l.amount_residual < 0 THEN -l.amount_residual ELSE 0 END) AS unallocated
              FROM account_move_line l
              JOIN account_account a ON a.id = l.account_id
              JOIN res_partner p ON p.id = l.partner_id
             WHERE %(where)s AND a.account_type = 'asset_receivable' AND l.parent_state = 'posted'
               AND NOT l.reconciled
             GROUP BY 1
            HAVING COUNT(*) FILTER (WHERE l.amount_residual > 0) > 0 OR ABS(SUM(l.amount_residual)) > 0.005
             ORDER BY 3 DESC, 2 DESC
        """, today=today, where=where))
        return [{'partner': r[0], 'due': float(r[1] or 0.0), 'overdue': float(r[2] or 0.0), 'oldest': r[3],
                 'invoices': int(r[4] or 0), 'unallocated': float(r[5] or 0.0)} for r in rows]

    @api.model
    def _ebshel_pay_habits(self, partner_ids, days=365):
        """How long each customer takes to pay, weighted by amount, over the last year."""
        if not partner_ids:
            return {}
        self.env['account.partial.reconcile'].flush_model()
        today = fields.Date.context_today(self)
        rows = self.env.execute_query(SQL("""
            SELECT COALESCE(p.commercial_partner_id, p.id),
                   SUM(pr.amount * (pr.max_date - m.invoice_date)) / NULLIF(SUM(pr.amount), 0),
                   SUM(pr.amount * (pr.max_date - COALESCE(dl.date_maturity, m.invoice_date))) / NULLIF(SUM(pr.amount), 0)
              FROM account_partial_reconcile pr
              JOIN account_move_line dl ON dl.id = pr.debit_move_id
              JOIN account_move m ON m.id = dl.move_id
              JOIN account_account a ON a.id = dl.account_id
              JOIN res_partner p ON p.id = dl.partner_id
             WHERE COALESCE(p.commercial_partner_id, p.id) IN %s AND a.account_type = 'asset_receivable'
               AND m.move_type = 'out_invoice' AND m.invoice_date IS NOT NULL AND pr.max_date >= %s
             GROUP BY 1
        """, tuple(partner_ids), today - timedelta(days=days)))
        return {r[0]: {'to_pay': round(float(r[1]), 1) if r[1] is not None else None,
                       'late': round(float(r[2]), 1) if r[2] is not None else None} for r in rows}

    def _ebshel_open_items(self):
        """The open receivable items of this customer's family."""
        self.ensure_one()
        family = self.commercial_partner_id | self.commercial_partner_id.child_ids | self
        return self.env['account.move.line'].search([
            ('partner_id', 'in', family.ids), ('account_id.account_type', '=', 'asset_receivable'),
            ('reconciled', '=', False), ('parent_state', '=', 'posted'), ('company_id', '=', self.env.company.id)],
            order='date_maturity, date, id')

    # ------------------------------------------------------------------ judgement
    def _ebshel_judge(self, due, overdue, oldest, today):
        """(state, level reached, days overdue) for one customer."""
        self.ensure_one()
        Level = self.env['ebshel.followup.level']
        company = self.env.company
        days = (today - oldest).days if oldest else 0
        reached = Level._for_days(days, company) if overdue > 0 else Level
        if self.ebshel_followup_excluded:
            return 'excluded', reached, days
        if overdue <= 0.005:
            return ('due' if due > 0.005 else 'none'), reached, days
        if self.ebshel_promise_date and self.ebshel_promise_date >= today:
            return 'promised', reached, days
        if self.ebshel_followup_next_date and self.ebshel_followup_next_date > today:
            return 'reminded', reached, days
        if overdue < (company.ebshel_followup_min_amount or 0.0):
            return 'overdue', reached, days
        if not reached:
            return 'overdue', reached, days
        last = self.ebshel_followup_level_id
        if not last or reached.days > last.days or (self.ebshel_followup_next_date and self.ebshel_followup_next_date <= today):
            return 'action', reached, days
        return 'overdue', reached, days

    @api.depends('ebshel_followup_level_id', 'ebshel_followup_next_date', 'ebshel_followup_excluded', 'ebshel_promise_date')
    def _compute_ebshel_followup(self):
        today = fields.Date.context_today(self)
        commercial = {p.id: (p.commercial_partner_id or p) for p in self}
        ids = list({c.id for c in commercial.values()})
        rows = {r['partner']: r for r in self._ebshel_followup_rows(self.env.company, today, ids)} if ids else {}
        for partner in self:
            c = commercial[partner.id]
            row = rows.get(c.id) or {}
            state, reached, days = c._ebshel_judge(row.get('due', 0.0), row.get('overdue', 0.0), row.get('oldest'), today)
            partner.ebshel_due_total = row.get('due', 0.0)
            partner.ebshel_overdue_total = row.get('overdue', 0.0)
            partner.ebshel_overdue_days = days
            partner.ebshel_followup_state = state
            partner.ebshel_suggested_level_id = reached
            partner.ebshel_followup_action_count = len(c.ebshel_followup_action_ids)

    def _search_ebshel_followup_state(self, operator, value):
        if operator not in ('=', '!=', 'in', 'not in'):
            return NotImplemented
        wanted = {value} if isinstance(value, str) else set(value)
        today = fields.Date.context_today(self)
        rows = self._ebshel_followup_rows(self.env.company, today)
        partners = self.browse([r['partner'] for r in rows])
        matching, with_rows = [], [r['partner'] for r in rows]
        for partner, row in zip(partners, rows):
            if partner._ebshel_judge(row['due'], row['overdue'], row['oldest'], today)[0] in wanted:
                matching.append(partner.id)
        if 'none' in wanted:
            domain = ['|', ('id', 'in', matching), ('id', 'not in', with_rows)]
        else:
            domain = [('id', 'in', matching)]
        if operator in ('!=', 'not in'):
            domain = ['!'] + domain
        return domain

    def _compute_ebshel_pay_habits(self):
        habits = self._ebshel_pay_habits([(p.commercial_partner_id or p).id for p in self])
        for partner in self:
            h = habits.get((partner.commercial_partner_id or partner).id, {})
            partner.ebshel_avg_days_to_pay = h.get('to_pay') or 0.0
            partner.ebshel_avg_days_late = h.get('late') or 0.0

    # ------------------------------------------------------------------ helpers
    def _ebshel_log(self, kind, level=None, note=None, amount=0.0, mail=None, promise_date=None):
        return self.env['ebshel.followup.action'].create({
            'partner_id': (self.commercial_partner_id or self).id, 'kind': kind, 'note': note or False,
            'level_id': level.id if level else False, 'amount': amount, 'mail_id': mail.id if mail else False,
            'promise_date': promise_date or False, 'company_id': self.env.company.id})

    def _ebshel_money(self, amount):
        return formatLang(self.env, amount, currency_obj=self.env.company.currency_id)

    def _ebshel_reminder_email(self):
        self.ensure_one()
        partner = self.commercial_partner_id or self
        return partner.email or (partner.child_ids.filtered('email')[:1].email or '')

    def _ebshel_items_table(self, items, today):
        rows = Markup('').join(
            Markup('<tr><td>%s</td><td>%s</td><td>%s</td><td style="text-align:right">%s</td><td style="text-align:right">%s</td></tr>') % (
                escape(l.move_id.name or ''), escape(fields.Date.to_string(l.date)),
                escape(fields.Date.to_string(l.date_maturity or l.date)),
                escape(str(max((today - (l.date_maturity or l.date)).days, 0))), escape(self._ebshel_money(l.amount_residual)))
            for l in items)
        total = sum(items.mapped('amount_residual'))
        return Markup('<table style="border-collapse:collapse;width:100%%;font-size:13px" cellpadding="4" border="1">'
                      '<tr style="background:#f3f4f6"><th>Invoice</th><th>Date</th><th>Due</th><th>Days late</th><th>Amount due</th></tr>'
                      '%s<tr><th colspan="4" style="text-align:right">Total</th><th style="text-align:right">%s</th></tr></table>') % (
            rows, escape(self._ebshel_money(total)))

    def _ebshel_attachments(self, level, items):
        """The statement of account and the overdue invoices as PDF attachments."""
        self.ensure_one()
        Attachment = self.env['ir.attachment']
        Report = self.env['ir.actions.report']
        attachments = Attachment
        today = fields.Date.context_today(self)
        if level.attach_statement:
            statement = self.env['ebshel.fin.report'].search([('key', '=', 'customer_statement')], limit=1)
            if statement:
                fy = self.env.company.compute_fiscalyear_dates(today)
                options = {'date': {'preset': 'custom', 'from': fields.Date.to_string(fy['date_from']),
                                    'to': fields.Date.to_string(today)},
                           'partners': [self.id], 'unfold_all': True}
                pdf, kind = Report._render_qweb_pdf('ebshel_account_reports.action_fin_report_pdf', res_ids=[statement.id],
                                                    data={'options': options, 'report_id': statement.id})
                attachments |= Attachment.create({
                    'name': _('Statement %s.%s', self.display_name, 'pdf' if kind == 'pdf' else 'html'),
                    'datas': base64.b64encode(pdf).decode(), 'res_model': 'res.partner', 'res_id': self.id,
                    'mimetype': 'application/pdf' if kind == 'pdf' else 'text/html'})
        if level.attach_invoices:
            invoices = items.mapped('move_id').filtered(lambda m: m.move_type == 'out_invoice')
            if invoices:
                pdf, kind = Report._render_qweb_pdf('account.account_invoices', res_ids=invoices.ids)
                attachments |= Attachment.create({
                    'name': _('Overdue invoices %s.%s', self.display_name, 'pdf' if kind == 'pdf' else 'html'),
                    'datas': base64.b64encode(pdf).decode(), 'res_model': 'res.partner', 'res_id': self.id,
                    'mimetype': 'application/pdf' if kind == 'pdf' else 'text/html'})
        return attachments

    def _ebshel_build_mail(self, level, note, items, email):
        """The reminder email, from the level's template or from its text."""
        self.ensure_one()
        today = fields.Date.context_today(self)
        attachments = self._ebshel_attachments(level, items)
        company = self.env.company
        if level.template_id:
            mail_id = level.template_id.with_context(ebshel_note=note or '', ebshel_level=level.name).send_mail(
                self.id, force_send=False, email_values={'email_to': email, 'auto_delete': False})
            mail = self.env['mail.mail'].browse(mail_id)
            if attachments:
                mail.write({'attachment_ids': [(4, a.id) for a in attachments]})
            return mail
        overdue = items.filtered(lambda l: l.amount_residual > 0 and (l.date_maturity or l.date) < today)
        total = sum(overdue.mapped('amount_residual'))
        body = Markup('<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#111">'
                      '<p>Dear %s,</p>%s<p>Our records show the following invoices are past their due date, '
                      'for a total of <b>%s</b>:</p>%s%s<p>%s<br/>%s</p></div>') % (
            escape(self.name or ''), level.text or Markup('<p>This is a reminder that the invoices below are overdue. '
                                                          'Please arrange payment or let us know if something is wrong.</p>'),
            escape(self._ebshel_money(total)), self._ebshel_items_table(overdue, today),
            Markup('<p>%s</p>') % escape(note) if note else Markup(''),
            escape(_('Kind regards,')), escape(company.name))
        return self.env['mail.mail'].create({
            'subject': _('%(company)s: payment reminder, %(amount)s overdue', company=company.name, amount=self._ebshel_money(total)),
            'email_from': self.env.user.email_formatted or company.email or False,
            'email_to': email, 'body_html': body, 'model': 'res.partner', 'res_id': self.id,
            'author_id': self.env.user.partner_id.id, 'auto_delete': False,
            'attachment_ids': [(4, a.id) for a in attachments]})

    def _ebshel_schedule_activity(self, level):
        self.ensure_one()
        user = self.ebshel_followup_responsible_id if level.responsible == 'followup' else self.user_id
        user = user or self.ebshel_followup_responsible_id or self.env.user
        act_type = level.activity_type_id
        xmlid = '' if act_type else ('mail.mail_activity_data_call' if level.action in ('call', 'email_call') else 'mail.mail_activity_data_todo')
        values = {'user_id': user.id}
        if act_type:
            values['activity_type_id'] = act_type.id
        return self.activity_schedule(xmlid, date_deadline=fields.Date.context_today(self),
                                      summary=_('Collections: %s', level.name),
                                      note=_('Overdue %s', self._ebshel_money(self.ebshel_overdue_total)), **values)

    def _ebshel_after_send(self, level):
        self.ensure_one()
        today = fields.Date.context_today(self)
        vals = {'ebshel_followup_level_id': level.id,
                'ebshel_followup_next_date': today + timedelta(days=level._wait_days())}
        if level.on_hold and not self.ebshel_on_hold:
            vals['ebshel_on_hold'] = True
            self._ebshel_log('hold', level, _('By level %s', level.name))
        self.write(vals)

    # ------------------------------------------------------------------ actions
    def action_ebshel_send_reminder(self, level_id=None, note=None):
        """Email (or record) the reminder for the level reached, with its attachments."""
        Level = self.env['ebshel.followup.level']
        results = {'sent': [], 'skipped': [], 'letters': []}
        today = fields.Date.context_today(self)
        done = set()
        for partner in self:
            partner = partner.commercial_partner_id or partner
            if partner.id in done:
                continue
            done.add(partner.id)
            level = Level.browse(level_id) if level_id else partner.ebshel_suggested_level_id
            if not level:
                level = Level._for_days(partner.ebshel_overdue_days, self.env.company) or Level.search(
                    [('company_id', '=', self.env.company.id)], order='days', limit=1)
            if not level:
                results['skipped'].append({'id': partner.id, 'name': partner.display_name, 'why': _('no follow-up level is defined')})
                continue
            items = partner._ebshel_open_items()
            if not items.filtered(lambda l: l.amount_residual > 0):
                results['skipped'].append({'id': partner.id, 'name': partner.display_name, 'why': _('nothing open')})
                continue
            if level.action in ('email', 'email_call'):
                email = partner._ebshel_reminder_email()
                if not email:
                    results['skipped'].append({'id': partner.id, 'name': partner.display_name, 'why': _('no email address')})
                    continue
                mail = partner._ebshel_build_mail(level, note, items, email)
                partner._ebshel_log('email', level, note, amount=partner.ebshel_overdue_total, mail=mail)
                partner.message_post(body=_('Reminder "%(level)s" emailed to %(email)s (%(n)d attachment(s)).',
                                            level=level.name, email=email, n=len(mail.attachment_ids)),
                                     subtype_xmlid='mail.mt_note')
            elif level.action == 'letter':
                partner._ebshel_log('letter', level, note, amount=partner.ebshel_overdue_total)
                results['letters'].append(partner.id)
            else:
                partner._ebshel_log('note', level, note or _('Level %s reached', level.name), amount=partner.ebshel_overdue_total)
            if level.action in ('email_call', 'call', 'todo') or level.activity_type_id:
                partner._ebshel_schedule_activity(level)
            partner._ebshel_after_send(level)
            results['sent'].append(partner.id)
        return results

    def action_ebshel_log_call(self, note, next_date=None):
        for partner in self:
            partner = partner.commercial_partner_id or partner
            partner._ebshel_log('call', partner.ebshel_followup_level_id, note, amount=partner.ebshel_overdue_total)
            if next_date:
                partner.ebshel_followup_next_date = fields.Date.to_date(next_date)
            partner.message_post(body=_('Collections call: %s', note or ''), subtype_xmlid='mail.mt_note')
        return True

    def action_ebshel_promise(self, date, amount=0.0, note=None):
        date = fields.Date.to_date(date)
        for partner in self:
            partner = partner.commercial_partner_id or partner
            partner.write({'ebshel_promise_date': date, 'ebshel_promise_amount': amount or partner.ebshel_overdue_total,
                           'ebshel_followup_next_date': date + timedelta(days=1)})
            partner._ebshel_log('promise', partner.ebshel_followup_level_id, note, amount=amount or partner.ebshel_overdue_total,
                                promise_date=date)
            partner.message_post(body=_('Promised %(amount)s for %(date)s. %(note)s',
                                        amount=self._ebshel_money(amount or partner.ebshel_overdue_total),
                                        date=fields.Date.to_string(date), note=note or ''), subtype_xmlid='mail.mt_note')
        return True

    def action_ebshel_snooze(self, days, note=None):
        today = fields.Date.context_today(self)
        for partner in self:
            partner = partner.commercial_partner_id or partner
            partner.ebshel_followup_next_date = today + timedelta(days=int(days))
            partner._ebshel_log('snooze', partner.ebshel_followup_level_id, note or _('%s days', days))
        return True

    def action_ebshel_set_exclusion(self, excluded, note=None):
        for partner in self:
            partner = partner.commercial_partner_id or partner
            partner.ebshel_followup_excluded = excluded
            partner._ebshel_log('exclude' if excluded else 'include', None, note)
        return True

    def action_ebshel_set_hold(self, on_hold, note=None):
        for partner in self:
            partner = partner.commercial_partner_id or partner
            partner.ebshel_on_hold = on_hold
            partner._ebshel_log('hold' if on_hold else 'release', None, note)
        return True

    def action_ebshel_print_letter(self, level_id=None):
        partners = self.mapped('commercial_partner_id') | self.filtered(lambda p: not p.commercial_partner_id)
        Level = self.env['ebshel.followup.level']
        for partner in partners:
            level = Level.browse(level_id) if level_id else partner.ebshel_suggested_level_id
            partner._ebshel_log('letter', level or None, None, amount=partner.ebshel_overdue_total)
            if level:
                partner._ebshel_after_send(level)
        action = self.env.ref('ebshel_account_advanced.action_report_followup_letter').report_action(partners)
        action['context'] = dict(action.get('context') or {}, ebshel_level_id=level_id)
        return action

    def action_ebshel_open_items(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': 'account.move.line', 'name': _('Open items'),
                'view_mode': 'list,form', 'domain': [('id', 'in', self._ebshel_open_items().ids)],
                'context': {'create': False}}

    def action_ebshel_open_desk(self):
        self.ensure_one()
        return {'type': 'ir.actions.client', 'tag': 'ebshel_followup_desk', 'name': _('Collections desk'),
                'params': {'partner_id': (self.commercial_partner_id or self).id}}

    def action_ebshel_open_log(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': 'ebshel.followup.action', 'name': _('Follow-up log'),
                'view_mode': 'list', 'domain': [('partner_id', '=', (self.commercial_partner_id or self).id)]}

    # letter helpers used by the QWeb template
    def _ebshel_letter_items(self):
        self.ensure_one()
        today = fields.Date.context_today(self)
        return self._ebshel_open_items().filtered(lambda l: l.amount_residual > 0), today

    def _ebshel_letter_level(self):
        self.ensure_one()
        level_id = self.env.context.get('ebshel_level_id')
        return self.env['ebshel.followup.level'].browse(level_id) if level_id else (self.ebshel_followup_level_id or self.ebshel_suggested_level_id)
