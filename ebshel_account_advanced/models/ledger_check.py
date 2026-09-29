# -*- coding: utf-8 -*-
"""Ledger health: a scanner that looks for the things an auditor would ask
about, keeps each as a finding until it is resolved or ignored, and scores
the books."""
import hashlib
import re
from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import AccessError
from odoo.tools import SQL

SEVERITIES = [('info', 'Information'), ('warn', 'Warning'), ('error', 'Error')]
WEIGHTS = {'error': 8, 'warn': 3, 'info': 1}
CHECKS = [
    ('duplicate_bills', 'Possible duplicate vendor bills'),
    ('sequence_gaps', 'Gaps in invoice numbering'),
    ('old_drafts', 'Draft entries older than 30 days'),
    ('locked_drafts', 'Draft entries dated before the lock date'),
    ('payments_unmatched', 'Payments not reconciled after 60 days'),
    ('suspense_items', 'Items sitting on a suspense account'),
    ('no_partner', 'Receivable or payable items without a partner'),
    ('stale_receivables', 'Receivables overdue by more than 180 days'),
    ('unusual_amounts', 'Unusually large amounts on an account'),
    ('weekend_entries', 'Manual entries dated on a weekend'),
    ('round_entries', 'Large round-number manual entries'),
    ('future_dated', 'Entries dated more than 30 days ahead'),
    ('negative_cash', 'Bank or cash account below zero'),
]


class LedgerFinding(models.Model):
    _name = 'ebshel.ledger.finding'
    _description = 'Ledger finding'
    _order = 'severity desc, found_on desc, id desc'

    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')
    key = fields.Selection(CHECKS, required=True)
    severity = fields.Selection(SEVERITIES, required=True, default='warn')
    name = fields.Char(required=True)
    detail = fields.Text()
    model = fields.Char()
    res_ids = fields.Json()
    count = fields.Integer()
    amount = fields.Monetary()
    state = fields.Selection([('open', 'Open'), ('resolved', 'Resolved'), ('ignored', 'Ignored')], default='open', index=True)
    found_on = fields.Datetime(default=fields.Datetime.now)
    resolved_on = fields.Datetime()
    user_id = fields.Many2one('res.users', string='Handled by')
    note = fields.Text()
    fingerprint = fields.Char(index=True)

    def action_open_records(self):
        self.ensure_one()
        if not self.model:
            return False
        return {'type': 'ir.actions.act_window', 'res_model': self.model, 'name': self.name,
                'view_mode': 'list,form', 'domain': [('id', 'in', self.res_ids or [])], 'context': {'create': False}}

    def action_resolve(self):
        self.write({'state': 'resolved', 'resolved_on': fields.Datetime.now(), 'user_id': self.env.user.id})

    def action_ignore(self):
        self.write({'state': 'ignored', 'resolved_on': fields.Datetime.now(), 'user_id': self.env.user.id})

    def action_reopen(self):
        self.write({'state': 'open', 'resolved_on': False})


class LedgerScanner(models.AbstractModel):
    _name = 'ebshel.ledger.scanner'
    _description = 'Ledger scanner'

    @api.model
    def _check(self, write=False):
        if self.env.su:
            return
        user = self.env.user
        if not user.has_groups('account.group_account_readonly,account.group_account_invoice,account.group_account_user'):
            raise AccessError(_("Ledger health is for the accounting team."))
        if write and not user.has_groups('account.group_account_user,account.group_account_manager'):
            raise AccessError(_("Only accountants can run the scan or close findings."))

    # ------------------------------------------------------------------ the checks
    def _finding(self, key, severity, name, model, ids, amount=0.0, detail='', extra=''):
        ids = sorted(set(ids))
        return {'key': key, 'severity': severity, 'name': name, 'model': model, 'res_ids': ids, 'count': len(ids),
                'amount': amount, 'detail': detail,
                'fingerprint': key + ':' + hashlib.sha1((extra or ','.join(map(str, ids))).encode()).hexdigest()[:16]}

    def _c_duplicate_bills(self, company, today):
        rows = self.env.execute_query(SQL("""
            SELECT ARRAY_AGG(m.id ORDER BY m.id), m.commercial_partner_id, LOWER(TRIM(m.ref)), m.amount_total
              FROM account_move m
             WHERE m.company_id = %s AND m.move_type = 'in_invoice' AND m.state = 'posted'
               AND COALESCE(m.ref, '') <> '' AND m.invoice_date >= %s
             GROUP BY 2, 3, 4 HAVING COUNT(*) > 1
        """, company.id, today - timedelta(days=365)))
        out = []
        for ids, partner_id, ref, total in rows:
            partner = self.env['res.partner'].browse(partner_id)
            out.append(self._finding('duplicate_bills', 'error',
                                     _('%(n)d bills from %(partner)s with reference "%(ref)s" for %(amount).2f',
                                       n=len(ids), partner=partner.display_name, ref=ref, amount=total),
                                     'account.move', ids, amount=float(total) * (len(ids) - 1),
                                     detail=_('Same vendor, same reference, same total. Check that one is not a repeat.'),
                                     extra='%s|%s|%s' % (partner_id, ref, total)))
        return out

    def _c_sequence_gaps(self, company, today):
        out = []
        fy = company.compute_fiscalyear_dates(today)
        for journal in self.env['account.journal'].search([('company_id', '=', company.id), ('type', 'in', ('sale', 'purchase'))]):
            names = self.env.execute_query(SQL("""
                SELECT name FROM account_move WHERE journal_id = %s AND state = 'posted' AND date >= %s AND date <= %s
                   AND move_type IN ('out_invoice', 'in_invoice') AND name IS NOT NULL
            """, journal.id, fy['date_from'], fy['date_to']))
            by_prefix = {}
            for (name,) in names:
                m = re.match(r'^(.*?)(\d+)$', name)
                if m:
                    by_prefix.setdefault(m.group(1), []).append(int(m.group(2)))
            gaps = []
            for prefix, nums in by_prefix.items():
                if len(nums) < 5:
                    continue
                have = set(nums)
                missing = [n for n in range(min(nums), max(nums) + 1) if n not in have]
                if 0 < len(missing) <= 50:
                    gaps.append('%s: %s' % (prefix, ', '.join(str(n) for n in missing[:20]) + (' …' if len(missing) > 20 else '')))
            if gaps:
                out.append(self._finding('sequence_gaps', 'warn', _('%s: numbers missing this fiscal year', journal.name),
                                         'account.journal', [journal.id], detail='\n'.join(gaps), extra='%s|%s' % (journal.id, '|'.join(gaps))))
        return out

    def _c_old_drafts(self, company, today):
        moves = self.env['account.move'].search([('company_id', '=', company.id), ('state', '=', 'draft'),
                                                 ('date', '<', today - timedelta(days=30))])
        return [self._finding('old_drafts', 'warn', _('%d draft entries older than 30 days', len(moves)), 'account.move', moves.ids,
                              amount=sum(moves.mapped('amount_total')), extra='all')] if moves else []

    def _c_locked_drafts(self, company, today):
        if not company.fiscalyear_lock_date:
            return []
        moves = self.env['account.move'].search([('company_id', '=', company.id), ('state', '=', 'draft'),
                                                 ('date', '<=', company.fiscalyear_lock_date)])
        return [self._finding('locked_drafts', 'error', _('%d draft entries dated in a locked period', len(moves)), 'account.move',
                              moves.ids, amount=sum(moves.mapped('amount_total')), extra='all')] if moves else []

    def _c_payments_unmatched(self, company, today):
        payments = self.env['account.payment'].search([('company_id', '=', company.id), ('is_reconciled', '=', False),
                                                       ('state', 'in', ('in_process', 'paid', 'posted')),
                                                       ('date', '<', today - timedelta(days=60))])
        return [self._finding('payments_unmatched', 'warn', _('%d payments not reconciled after 60 days', len(payments)),
                              'account.payment', payments.ids, amount=sum(payments.mapped('amount')), extra='all')] if payments else []

    def _c_suspense_items(self, company, today):
        accounts = self.env['account.journal'].search([('company_id', '=', company.id)]).mapped('suspense_account_id')
        if not accounts:
            return []
        lines = self.env['account.move.line'].search([('company_id', '=', company.id), ('account_id', 'in', accounts.ids),
                                                      ('parent_state', '=', 'posted'), ('reconciled', '=', False)])
        return [self._finding('suspense_items', 'warn', _('%d items on suspense accounts', len(lines)), 'account.move.line',
                              lines.ids, amount=sum(lines.mapped('balance')), extra='all')] if lines else []

    def _c_no_partner(self, company, today):
        lines = self.env['account.move.line'].search([('company_id', '=', company.id), ('parent_state', '=', 'posted'),
                                                      ('account_id.account_type', 'in', ('asset_receivable', 'liability_payable')),
                                                      ('partner_id', '=', False)])
        return [self._finding('no_partner', 'warn', _('%d receivable/payable items without a partner', len(lines)),
                              'account.move.line', lines.ids, amount=sum(lines.mapped('balance')), extra='all')] if lines else []

    def _c_stale_receivables(self, company, today):
        lines = self.env['account.move.line'].search([('company_id', '=', company.id), ('parent_state', '=', 'posted'),
                                                      ('account_id.account_type', '=', 'asset_receivable'), ('reconciled', '=', False),
                                                      ('amount_residual', '>', 0), ('date_maturity', '<', today - timedelta(days=180))])
        return [self._finding('stale_receivables', 'info', _('%d invoices overdue by more than 180 days', len(lines)),
                              'account.move.line', lines.ids, amount=sum(lines.mapped('amount_residual')), extra='all')] if lines else []

    def _c_unusual_amounts(self, company, today):
        rows = self.env.execute_query(SQL("""
            WITH stats AS (
                SELECT account_id, AVG(ABS(balance)) AS mean, STDDEV_POP(ABS(balance)) AS sd, COUNT(*) AS n
                  FROM account_move_line WHERE company_id = %s AND parent_state = 'posted' AND date >= %s
                 GROUP BY account_id HAVING COUNT(*) >= 30)
            SELECT l.id, l.account_id, l.balance FROM account_move_line l JOIN stats s ON s.account_id = l.account_id
             WHERE l.company_id = %s AND l.parent_state = 'posted' AND l.date >= %s AND s.sd > 0
               AND ABS(l.balance) > s.mean + 3 * s.sd AND ABS(l.balance) > 1000
             ORDER BY ABS(l.balance) DESC LIMIT 50
        """, company.id, today - timedelta(days=365), company.id, today - timedelta(days=90)))
        if not rows:
            return []
        by_account = {}
        for lid, aid, bal in rows:
            by_account.setdefault(aid, []).append((lid, float(bal)))
        out = []
        for aid, items in by_account.items():
            account = self.env['account.account'].browse(aid)
            out.append(self._finding('unusual_amounts', 'info', _('%(n)d unusually large entries on %(account)s', n=len(items), account=account.display_name),
                                     'account.move.line', [i[0] for i in items], amount=sum(abs(i[1]) for i in items),
                                     detail=_('More than three standard deviations above the account\'s average over the last year.'),
                                     extra='%s|%s' % (aid, ','.join(str(i[0]) for i in items))))
        return out

    def _c_weekend_entries(self, company, today):
        moves = self.env['account.move'].search([('company_id', '=', company.id), ('state', '=', 'posted'), ('move_type', '=', 'entry'),
                                                 ('journal_id.type', '=', 'general'), ('date', '>=', today - timedelta(days=90))])
        weekend = moves.filtered(lambda m: m.date.weekday() >= 5)
        return [self._finding('weekend_entries', 'info', _('%d manual entries dated on a weekend', len(weekend)), 'account.move',
                              weekend.ids, amount=sum(weekend.mapped('amount_total')), extra='all')] if weekend else []

    def _c_round_entries(self, company, today):
        moves = self.env['account.move'].search([('company_id', '=', company.id), ('state', '=', 'posted'), ('move_type', '=', 'entry'),
                                                 ('journal_id.type', '=', 'general'), ('date', '>=', today - timedelta(days=90)),
                                                 ('amount_total', '>=', 10000)])
        rounds = moves.filtered(lambda m: abs(m.amount_total % 1000) < 0.005)
        return [self._finding('round_entries', 'info', _('%d large round-number manual entries', len(rounds)), 'account.move',
                              rounds.ids, amount=sum(rounds.mapped('amount_total')), extra='all')] if rounds else []

    def _c_future_dated(self, company, today):
        moves = self.env['account.move'].search([('company_id', '=', company.id), ('state', '=', 'posted'),
                                                 ('date', '>', today + timedelta(days=30))])
        return [self._finding('future_dated', 'warn', _('%d posted entries dated more than 30 days ahead', len(moves)), 'account.move',
                              moves.ids, amount=sum(moves.mapped('amount_total')), extra='all')] if moves else []

    def _c_negative_cash(self, company, today):
        _total, detail = self.env['ebshel.cash.forecast']._cash_balance(company, today)
        out = []
        for row in detail:
            if row['balance'] < -0.005:
                out.append(self._finding('negative_cash', 'error', _('%s is below zero (%.2f)', row['name'], row['balance']),
                                         'account.account', [row['id']], amount=-row['balance'], extra=str(row['id'])))
        return out

    # ------------------------------------------------------------------ running
    @api.model
    def scan(self, company=None, keys=None):
        """Run every check (or the given keys); keep findings in step with what is there now."""
        self._check(write=True)
        self.env.flush_all()
        company = company or self.env.company
        today = fields.Date.context_today(self)
        keys = keys or [k for k, _l in CHECKS]
        Finding = self.env['ebshel.ledger.finding']
        found = []
        for key in keys:
            found.extend(getattr(self, '_c_' + key)(company, today))
        existing = Finding.search([('company_id', '=', company.id), ('key', 'in', keys)], order='id desc')
        by_fp = {}
        for f in existing:
            by_fp.setdefault(f.fingerprint, f)               # the latest record per fingerprint
        created = updated = 0
        seen = set()
        for f in found:
            seen.add(f['fingerprint'])
            old = by_fp.get(f['fingerprint'])
            if old:
                vals = {'count': f['count'], 'amount': f['amount'], 'res_ids': f['res_ids'], 'detail': f['detail'], 'name': f['name']}
                if old.state == 'resolved':                # it came back: reopen the same record
                    vals.update({'state': 'open', 'resolved_on': False, 'found_on': fields.Datetime.now()})
                old.write(vals)
                updated += 1
            else:
                Finding.create(dict(f, company_id=company.id))
                created += 1
        gone = existing.filtered(lambda f: f.state == 'open' and f.fingerprint not in seen)
        gone.write({'state': 'resolved', 'resolved_on': fields.Datetime.now()})
        self.env['ir.config_parameter'].sudo().set_param('ebshel_account_advanced.last_scan.%d' % company.id, fields.Datetime.to_string(fields.Datetime.now()))
        return {'created': created, 'updated': updated, 'resolved': len(gone)}

    @api.model
    def _cron_scan(self):
        for company in self.env['res.company'].search([]):
            self.with_company(company).scan(company)
        return True

    @api.model
    def get_data(self):
        self._check()
        company = self.env.company
        findings = self.env['ebshel.ledger.finding'].search([('company_id', '=', company.id), ('state', '=', 'open')])
        penalty = sum(WEIGHTS[f.severity] for f in findings)
        rows = [{'id': f.id, 'key': f.key, 'label': dict(CHECKS)[f.key], 'severity': f.severity, 'name': f.name, 'detail': f.detail or '',
                 'count': f.count, 'amount': f.amount, 'model': f.model, 'found_on': fields.Datetime.to_string(f.found_on),
                 'state': f.state} for f in findings]
        recent = self.env['ebshel.ledger.finding'].search([('company_id', '=', company.id), ('state', '!=', 'open')], limit=20)
        last = self.env['ir.config_parameter'].sudo().get_param('ebshel_account_advanced.last_scan.%d' % company.id)
        return {
            'score': max(100 - penalty, 0), 'last_scan': last,
            'counts': {s: sum(1 for f in findings if f.severity == s) for s in ('error', 'warn', 'info')},
            'findings': rows,
            'recent': [{'id': f.id, 'name': f.name, 'state': f.state, 'severity': f.severity, 'user': f.user_id.name or '',
                        'on': fields.Datetime.to_string(f.resolved_on) if f.resolved_on else ''} for f in recent],
            'checks': [{'key': k, 'label': l} for k, l in CHECKS],
            'currency': {'symbol': company.currency_id.symbol, 'position': company.currency_id.position, 'decimals': company.currency_id.decimal_places},
            'can_write': self.env.su or self.env.user.has_groups('account.group_account_user,account.group_account_manager'),
        }

    @api.model
    def set_state(self, finding_id, state, note=None):
        self._check(write=True)
        f = self.env['ebshel.ledger.finding'].browse(finding_id)
        if note is not None:
            f.note = note
        {'resolved': f.action_resolve, 'ignored': f.action_ignore, 'open': f.action_reopen}[state]()
        return True

    @api.model
    def open_finding(self, finding_id):
        self._check()
        return self.env['ebshel.ledger.finding'].browse(finding_id).action_open_records()
