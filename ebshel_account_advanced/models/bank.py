# -*- coding: utf-8 -*-
"""Bank reconciliation, for books that import statements and for books that don't.

Two roads to the same place:

  statement   Bank statement lines (pasted, uploaded or typed) are matched to open
              invoices and bills, written off to an account (charges, interest), or
              recognised as money already in the books.
  ticking     Book items on the bank account are ticked "cleared" with the date the
              bank shows them. Most small companies post receipts and cheques straight
              to the bank account and never import a statement; for them this IS the
              reconciliation.

One paste feeds both: every pasted row that equals an uncleared book item ticks it,
and only the rest become statement lines to match. The reconciliation statement
(balance per books, deposits not yet credited, payments not yet presented, balance
per bank) is read at any date, against the closing balance the bank printed.
"""
import re
from datetime import timedelta

from odoo import api, fields, models, Command, _
from odoo.exceptions import AccessError, UserError
from odoo.tools import SQL, float_compare, float_is_zero

BOOK_WINDOW_BEFORE = 45        # a cheque can take weeks to reach the bank
BOOK_WINDOW_AFTER = 5          # and a statement can show a transfer a little before it is booked


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    ebshel_cleared_date = fields.Date('Cleared by the bank on', copy=False, index=True,
                                      help="The date the bank statement shows this item. Empty: not yet seen by the bank.")
    ebshel_bank_ref = fields.Char('Bank reference', copy=False, help="The line on the bank statement that cleared it.")
    ebshel_cleared_by_id = fields.Many2one('res.users', string='Ticked by', copy=False)


class BankStatementLine(models.Model):
    _inherit = 'account.bank.statement.line'

    ebshel_rule_id = fields.Many2one('ebshel.bank.rule', string='Matched by rule', copy=False, readonly=True)
    ebshel_note = fields.Char('Reconciliation note', copy=False)


class BankRule(models.Model):
    """"When the bank says X, it is Y." Learned from the user's own choices."""
    _name = 'ebshel.bank.rule'
    _inherit = ['analytic.mixin']
    _description = 'Bank reconciliation rule'
    _order = 'sequence, id'

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    journal_ids = fields.Many2many('account.journal', string='Bank journals', domain="[('type', 'in', ('bank', 'cash'))]",
                                   help="Empty: every bank and cash journal.")
    keywords = fields.Char('Label contains', help="Words from the bank's label, separated by commas.")
    keyword_mode = fields.Selection([('any', 'Any of the words'), ('all', 'All of the words')], default='any', required=True)
    direction = fields.Selection([('both', 'Money in or out'), ('in', 'Money in'), ('out', 'Money out')], default='both', required=True)
    amount_min = fields.Float('Amount from')
    amount_max = fields.Float('Amount up to', help="0: no upper limit.")
    action = fields.Selection([('account', 'Post to an account'), ('partner', 'Match the partner\'s open items')],
                              default='account', required=True)
    account_id = fields.Many2one('account.account', string='Account', check_company=True)
    label = fields.Char('Entry label', help="Empty: the bank's own label.")
    partner_id = fields.Many2one('res.partner', string='Partner')
    auto_apply = fields.Boolean('Apply by itself', default=True, help="Reconcile matching lines as soon as they arrive.")
    hits = fields.Integer('Used', readonly=True)
    last_used = fields.Date(readonly=True)

    @api.constrains('action', 'account_id', 'partner_id')
    def _check_target(self):
        for rule in self:
            if rule.action == 'account' and not rule.account_id:
                raise UserError(_("Rule '%s' posts to an account: choose the account.", rule.name))
            if rule.action == 'partner' and not rule.partner_id:
                raise UserError(_("Rule '%s' matches a partner's open items: choose the partner.", rule.name))

    def _words(self):
        self.ensure_one()
        return [w.strip().lower() for w in (self.keywords or '').split(',') if w.strip()]

    def _matches(self, st):
        self.ensure_one()
        if self.journal_ids and st.journal_id not in self.journal_ids:
            return False
        if self.direction == 'in' and st.amount <= 0 or self.direction == 'out' and st.amount >= 0:
            return False
        size = abs(st.amount)
        if self.amount_min and size < self.amount_min or self.amount_max and size > self.amount_max:
            return False
        words = self._words()
        text = (st.payment_ref or '').lower()
        if words:
            hit = [w in text for w in words]
            if not (all(hit) if self.keyword_mode == 'all' else any(hit)):
                return False
        return bool(words or self.amount_min or self.amount_max)


class BankCheckpoint(models.Model):
    """The closing balance the bank printed on a date - what the books must explain."""
    _name = 'ebshel.bank.checkpoint'
    _description = 'Bank statement balance'
    _order = 'date desc, id desc'

    journal_id = fields.Many2one('account.journal', required=True, domain="[('type', 'in', ('bank', 'cash'))]")
    company_id = fields.Many2one(related='journal_id.company_id', store=True)
    currency_id = fields.Many2one(related='company_id.currency_id')
    date = fields.Date(required=True, default=fields.Date.context_today)
    balance = fields.Monetary('Balance per bank', required=True)
    note = fields.Char()
    difference = fields.Monetary(compute='_compute_difference', help="Balance per bank less what the books explain.")

    def _compute_difference(self):
        Desk = self.env['ebshel.bank.desk'].sudo()
        for cp in self:
            if not cp.journal_id or not cp.date:
                cp.difference = 0.0
                continue
            brs = Desk._brs_numbers(cp.journal_id, cp.date)
            cp.difference = cp.balance - brs['bank_expected']


class BankDesk(models.AbstractModel):
    """Everything the reconciliation workspace asks the server."""
    _name = 'ebshel.bank.desk'
    _description = 'Bank reconciliation workspace'

    # ------------------------------------------------------------------ access
    @api.model
    def _check(self, write=False):
        if self.env.su:
            return
        user = self.env.user
        if not user.has_groups('account.group_account_readonly,account.group_account_invoice'):
            raise AccessError(_("Bank reconciliation is for the accounting team."))
        if write and not user.has_groups('account.group_account_user,account.group_account_manager'):
            raise AccessError(_("Only accountants can reconcile the bank."))

    @api.model
    def _currency(self):
        c = self.env.company.currency_id
        return {'symbol': c.symbol, 'position': c.position, 'decimals': c.decimal_places}

    @api.model
    def _journal(self, journal_id):
        journal = self.env['account.journal'].browse(int(journal_id)).exists()
        if not journal or journal.type not in ('bank', 'cash'):
            raise UserError(_("Choose a bank or cash journal."))
        if not journal.default_account_id:
            raise UserError(_("%s has no bank account in the books.", journal.display_name))
        return journal

    # ------------------------------------------------------------------ the books side
    @api.model
    def _book_domain(self, journal, date=None):
        domain = [('account_id', '=', journal.default_account_id.id), ('parent_state', '=', 'posted'),
                  ('company_id', '=', journal.company_id.id)]
        if date:
            domain.append(('date', '<=', date))
        return domain

    @api.model
    def _brs_numbers(self, journal, date):
        """Balance per books at `date`, what the bank has not seen yet, and so the
        balance the bank should be showing. Items that came from a bank statement
        line are the bank's own, so cleared by definition."""
        self.env['account.move.line'].flush_model()
        self.env['account.move'].flush_model(['statement_line_id'])
        row = self.env.execute_query(SQL("""
            SELECT COALESCE(SUM(l.balance), 0),
                   COALESCE(SUM(l.balance) FILTER (WHERE l.balance > 0 AND m.statement_line_id IS NULL
                                                   AND (l.ebshel_cleared_date IS NULL OR l.ebshel_cleared_date > %(d)s)), 0),
                   COALESCE(SUM(-l.balance) FILTER (WHERE l.balance < 0 AND m.statement_line_id IS NULL
                                                    AND (l.ebshel_cleared_date IS NULL OR l.ebshel_cleared_date > %(d)s)), 0),
                   COUNT(*) FILTER (WHERE m.statement_line_id IS NULL AND (l.ebshel_cleared_date IS NULL OR l.ebshel_cleared_date > %(d)s))
              FROM account_move_line l JOIN account_move m ON m.id = l.move_id
             WHERE l.account_id = %(acc)s AND l.parent_state = 'posted' AND l.company_id = %(co)s AND l.date <= %(d)s
        """, d=date, acc=journal.default_account_id.id, co=journal.company_id.id))[0]
        book, deposits, payments, count = (float(row[0]), float(row[1]), float(row[2]), int(row[3]))
        return {'book': book, 'deposits': deposits, 'payments': payments, 'uncleared': count,
                'bank_expected': book - deposits + payments}

    @api.model
    def _book_candidates(self, st, limit=6, exclude=()):
        """Uncleared items on the bank account that look like this statement line."""
        journal = st.journal_id
        if not journal.default_account_id:
            return []
        rounding = journal.company_id.currency_id.rounding
        lines = self.env['account.move.line'].search(self._book_domain(journal) + [
            ('ebshel_cleared_date', '=', False), ('move_id.statement_line_id', '=', False),
            ('balance', '>=', st.amount - rounding / 2), ('balance', '<=', st.amount + rounding / 2),
            ('date', '>=', st.date - timedelta(days=BOOK_WINDOW_BEFORE)), ('date', '<=', st.date + timedelta(days=BOOK_WINDOW_AFTER)),
            ('id', 'not in', list(exclude))], limit=50)
        tokens = self.env['ebshel.match.desk']._tokens(st.payment_ref)
        out = []
        for line in lines:
            ref_hit = bool(tokens & self.env['ebshel.match.desk']._tokens(line.name, line.ref, line.move_id.name, line.move_id.ref))
            days = abs((st.date - line.date).days)
            conf = min(99, (96 if ref_hit else 88) - min(days, 30) // 3)
            out.append({'id': line.id, 'move_id': line.move_id.id, 'name': line.move_id.name or '', 'label': line.name or '',
                        'ref': line.move_id.ref or '', 'date': fields.Date.to_string(line.date), 'amount': line.balance,
                        'partner': line.partner_id.display_name or '', 'confidence': conf,
                        'reason': _('already in the books on %(date)s%(ref)s', date=fields.Date.to_string(line.date),
                                    ref=_(', same reference') if ref_hit else '')})
        out.sort(key=lambda c: -c['confidence'])
        return out[:limit]

    # ------------------------------------------------------------------ overview
    @api.model
    def get_journals(self):
        self._check()
        out = []
        today = fields.Date.context_today(self)
        StLine = self.env['account.bank.statement.line']
        for journal in self.env['account.journal'].search([('type', 'in', ('bank', 'cash')), ('company_id', 'in', self.env.companies.ids)]):
            if not journal.default_account_id:
                continue
            brs = self._brs_numbers(journal, today)
            open_lines = StLine.search([('journal_id', '=', journal.id), ('is_reconciled', '=', False)])
            last_cp = self.env['ebshel.bank.checkpoint'].search([('journal_id', '=', journal.id)], limit=1)
            last_st = StLine.search([('journal_id', '=', journal.id)], order='date desc', limit=1)
            items = self.env['account.move.line'].search_count(self._book_domain(journal))
            out.append({'id': journal.id, 'name': journal.name, 'code': journal.code, 'type': journal.type,
                        'account': journal.default_account_id.display_name, 'book': brs['book'],
                        'bank_expected': brs['bank_expected'], 'uncleared': brs['uncleared'],
                        'deposits': brs['deposits'], 'payments': brs['payments'], 'items': items,
                        'to_match': len(open_lines), 'to_match_amount': sum(open_lines.mapped('amount')),
                        'last_statement': fields.Date.to_string(last_st.date) if last_st else None,
                        'checkpoint': {'date': fields.Date.to_string(last_cp.date), 'balance': last_cp.balance,
                                       'difference': last_cp.difference} if last_cp else None})
        out.sort(key=lambda j: (-j['items'], j['name']))
        return {'journals': out, 'currency': self._currency(), 'can_write': self._can_write(),
                'rules': self.env['ebshel.bank.rule'].search_count([])}

    @api.model
    def _can_write(self):
        return self.env.su or self.env.user.has_groups('account.group_account_user,account.group_account_manager')

    # ------------------------------------------------------------------ statement lines
    @api.model
    def _st_row(self, st, with_candidates=True):
        liquidity, suspense, other = st._seek_for_lines()
        row = {'id': st.id, 'date': fields.Date.to_string(st.date), 'label': st.payment_ref or '', 'amount': st.amount,
               'partner_id': st.partner_id.id, 'partner': st.partner_id.display_name or '', 'reconciled': st.is_reconciled,
               'residual': -sum(suspense.mapped('balance')) if suspense else 0.0,
               'rule': st.ebshel_rule_id.name or '', 'note': st.ebshel_note or '',
               'matched': [{'account': l.account_id.display_name, 'label': l.name or '', 'amount': -l.balance,
                            'partner': l.partner_id.display_name or '',
                            'with': ', '.join(l.matched_debit_ids.debit_move_id.move_id.mapped('name') +
                                              l.matched_credit_ids.credit_move_id.move_id.mapped('name'))} for l in other]}
        if with_candidates and not st.is_reconciled:
            cands = self._candidates(st)
            row['best'] = cands[0] if cands else None
        return row

    @api.model
    def _candidates(self, st):
        out = []
        for c in self._book_candidates(st, limit=3):
            out.append(dict(c, kind='book'))
        for c in self.env['ebshel.match.desk']._bank_candidates(st)[:6]:
            out.append(dict(c, kind='open'))
        rule = self._rule_for(st)
        if rule:
            out.append({'kind': 'rule', 'id': rule.id, 'name': rule.name, 'confidence': 93 if rule.action == 'account' else 80,
                        'reason': _('rule "%s"', rule.name), 'account': rule.account_id.display_name or '',
                        'amount': st.amount})
        out.sort(key=lambda c: -c['confidence'])
        return out

    @api.model
    def get_lines(self, journal_id, state='open', search='', limit=80, offset=0):
        self._check()
        journal = self._journal(journal_id)
        domain = [('journal_id', '=', journal.id)]
        if state == 'open':
            domain.append(('is_reconciled', '=', False))
        elif state == 'done':
            domain.append(('is_reconciled', '=', True))
        search = (search or '').strip()
        if search:
            try:
                amount = float(search.replace(',', ''))
                domain += ['|', ('amount', '=', amount), ('amount', '=', -amount)]
            except ValueError:
                domain += ['|', ('payment_ref', 'ilike', search), ('partner_id', 'ilike', search)]
        StLine = self.env['account.bank.statement.line']
        total = StLine.search_count(domain)
        lines = StLine.search(domain, order='date desc, id desc', limit=limit, offset=offset)
        return {'rows': [self._st_row(st, with_candidates=(state != 'done')) for st in lines], 'total': total,
                'journal': {'id': journal.id, 'name': journal.name}, 'currency': self._currency(), 'can_write': self._can_write()}

    @api.model
    def get_line(self, st_line_id):
        self._check()
        st = self.env['account.bank.statement.line'].browse(int(st_line_id)).exists()
        if not st:
            raise UserError(_("That bank line no longer exists."))
        row = self._st_row(st, with_candidates=False)
        row['candidates'] = self._candidates(st) if not st.is_reconciled else []
        row['accounts'] = [{'id': a.id, 'name': a.display_name} for a in self._quick_accounts(st)]
        return row

    @api.model
    def _quick_accounts(self, st):
        """Accounts this company usually writes bank lines off to: rules first, then the
        accounts that appear most on this bank's other entries."""
        accounts = self.env['ebshel.bank.rule'].search([('account_id', '!=', False)]).mapped('account_id')
        rows = self.env.execute_query(SQL("""
            SELECT l.account_id, COUNT(*) FROM account_move_line l
              JOIN account_move m ON m.id = l.move_id
              JOIN account_account a ON a.id = l.account_id
             WHERE m.journal_id = %s AND l.account_id <> %s AND a.account_type NOT IN ('asset_receivable', 'liability_payable')
               AND l.parent_state = 'posted'
             GROUP BY 1 ORDER BY 2 DESC LIMIT 8
        """, st.journal_id.id, st.journal_id.default_account_id.id))
        accounts |= self.env['account.account'].browse([r[0] for r in rows])
        return accounts[:10]

    @api.model
    def search_items(self, st_line_id, query='', kind='open'):
        """Manual search: open invoices/bills, or uncleared book items."""
        self._check()
        st = self.env['account.bank.statement.line'].browse(int(st_line_id))
        AML = self.env['account.move.line']
        query = (query or '').strip()
        if kind == 'book':
            domain = self._book_domain(st.journal_id) + [('ebshel_cleared_date', '=', False), ('move_id.statement_line_id', '=', False)]
        else:
            domain = [('account_id.account_type', 'in', ('asset_receivable', 'liability_payable')), ('reconciled', '=', False),
                      ('parent_state', '=', 'posted'), ('company_id', '=', st.company_id.id)]
        if query:
            try:
                amount = float(query.replace(',', ''))
                field = 'balance' if kind == 'book' else 'amount_residual'
                domain += ['|', (field, '=', amount), (field, '=', -amount)]
            except ValueError:
                domain += ['|', '|', '|', ('move_id.name', 'ilike', query), ('move_id.ref', 'ilike', query),
                           ('partner_id', 'ilike', query), ('name', 'ilike', query)]
        elif kind == 'open' and st.partner_id:
            domain.append(('partner_id.commercial_partner_id', '=', st.partner_id.commercial_partner_id.id))
        out = []
        for line in AML.search(domain, order='date desc, id desc', limit=40):
            out.append({'id': line.id, 'move_id': line.move_id.id, 'name': line.move_id.name or '', 'label': line.name or '',
                        'ref': line.move_id.ref or '', 'partner': line.partner_id.display_name or '',
                        'date': fields.Date.to_string(line.date), 'due': fields.Date.to_string(line.date_maturity or line.date),
                        'amount': line.balance if kind == 'book' else line.amount_residual, 'kind': kind})
        return out

    # ------------------------------------------------------------------ reconciling
    @api.model
    def reconcile(self, st_line_id, parts, partner_id=None, note=None, rule_id=None):
        """Replace the bank line's suspense entry with what it really was.

        parts: [{'kind': 'open', 'aml_id'} | {'kind': 'account', 'account_id', 'amount', 'label', 'analytic_distribution'}]
        Whatever is not covered stays on the suspense account, to be matched later."""
        self._check(write=True)
        st = self.env['account.bank.statement.line'].browse(int(st_line_id)).exists()
        if not st:
            raise UserError(_("That bank line no longer exists."))
        company_currency = st.company_id.currency_id
        if (st.foreign_currency_id and st.foreign_currency_id != company_currency) or \
                (st.journal_id.currency_id and st.journal_id.currency_id != company_currency):
            raise UserError(_("Foreign-currency bank lines are reconciled from the statement itself."))
        rounding = company_currency.rounding
        liquidity, suspense, other = st._seek_for_lines()
        if not suspense:
            raise UserError(_("This bank line is already reconciled. Undo it first."))
        left = -sum(suspense.mapped('balance'))
        base = st._prepare_move_line_default_vals()[1]
        base.pop('move_id', None)
        partner = self.env['res.partner'].browse(partner_id) if partner_id else st.partner_id
        creates, pairs = [], []
        AML = self.env['account.move.line']
        for part in parts or []:
            if float_is_zero(left, precision_rounding=rounding):
                break
            if part.get('kind') == 'open':
                aml = AML.browse(int(part['aml_id'])).exists()
                if not aml or aml.reconciled:
                    continue
                residual = aml.amount_residual
                if residual * left <= 0:
                    raise UserError(_("%s is on the wrong side for this bank line.", aml.move_id.name))
                take = residual if abs(residual) <= abs(left) else left
                vals = dict(base, account_id=aml.account_id.id, partner_id=aml.partner_id.id,
                            name=aml.move_id.name or aml.name or st.payment_ref,
                            debit=max(-take, 0.0), credit=max(take, 0.0), amount_currency=-take, currency_id=company_currency.id)
                creates.append(vals)
                pairs.append((aml, vals))
                left -= take
            elif part.get('kind') == 'account':
                account = self.env['account.account'].browse(int(part['account_id'])).exists()
                if not account:
                    raise UserError(_("Choose an account."))
                if account == st.journal_id.default_account_id:
                    raise UserError(_("That is the bank account itself. If the item is already in the books, tick it instead."))
                size = abs(float(part.get('amount') or 0.0)) or abs(left)
                take = (1 if left > 0 else -1) * min(size, abs(left))
                vals = dict(base, account_id=account.id, partner_id=(partner.id or False),
                            name=part.get('label') or st.payment_ref or account.name,
                            debit=max(-take, 0.0), credit=max(take, 0.0), amount_currency=-take, currency_id=company_currency.id)
                if part.get('analytic_distribution'):
                    vals['analytic_distribution'] = part['analytic_distribution']
                creates.append(vals)
                left -= take
        if not creates:
            raise UserError(_("Nothing to reconcile with."))
        if not float_is_zero(left, precision_rounding=rounding):
            creates.append(dict(base, partner_id=partner.id or False, debit=max(-left, 0.0), credit=max(left, 0.0),
                                amount_currency=-left, currency_id=company_currency.id))
        move = st.move_id.with_context(force_delete=True, skip_readonly_check=True)
        move.write({'line_ids': [Command.delete(l.id) for l in suspense] + [Command.create(v) for v in creates]})
        new_lines = move.line_ids - liquidity - other
        plan, used = [], set()
        for aml, vals in pairs:
            target = vals['debit'] - vals['credit']
            match = new_lines.filtered(lambda l: l.id not in used and l.account_id.id == vals['account_id']
                                       and l.partner_id.id == vals['partner_id'] and abs(l.balance - target) < rounding / 2)
            if match:
                used.add(match[0].id)
                plan.append(match[0] + aml)
        if plan:
            AML._reconcile_plan(plan)
        st_vals = {}
        if partner and partner != st.partner_id:
            st_vals['partner_id'] = partner.id
        if note is not None:
            st_vals['ebshel_note'] = note
        if rule_id:
            st_vals['ebshel_rule_id'] = rule_id
        if st_vals:
            st.with_context(skip_account_move_synchronization=True).write(st_vals)
        st.invalidate_recordset(['is_reconciled', 'amount_residual'])
        return {'reconciled': st.is_reconciled, 'left': left, 'row': self._st_row(st, with_candidates=False)}

    @api.model
    def undo(self, st_line_id):
        self._check(write=True)
        st = self.env['account.bank.statement.line'].browse(int(st_line_id)).exists()
        st.action_undo_reconciliation()
        st.with_context(skip_account_move_synchronization=True).write({'ebshel_rule_id': False, 'ebshel_note': False})
        return self._st_row(st)

    @api.model
    def already_booked(self, st_line_id, aml_id):
        """The bank line is money the books already hold: tick the book item with the
        bank's date and label, and drop the duplicate bank line."""
        self._check(write=True)
        st = self.env['account.bank.statement.line'].browse(int(st_line_id)).exists()
        line = self.env['account.move.line'].browse(int(aml_id)).exists()
        if not st or not line:
            raise UserError(_("Nothing to tick."))
        if line.account_id != st.journal_id.default_account_id:
            raise UserError(_("That item is not on this bank's account."))
        if float_compare(line.balance, st.amount, precision_rounding=st.company_id.currency_id.rounding) != 0:
            raise UserError(_("The amounts differ: %(a)s in the books, %(b)s at the bank.", a=line.balance, b=st.amount))
        if st.is_reconciled:
            raise UserError(_("This bank line is already reconciled. Undo it first."))
        line.write({'ebshel_cleared_date': st.date, 'ebshel_bank_ref': st.payment_ref or '', 'ebshel_cleared_by_id': self.env.uid})
        st.unlink()
        return True

    @api.model
    def create_rule(self, st_line_id, account_id, keywords, name=None, auto_apply=True, label=None):
        """"Always do this": a rule from the choice just made."""
        self._check(write=True)
        st = self.env['account.bank.statement.line'].browse(int(st_line_id))
        rule = self.env['ebshel.bank.rule'].create({
            'name': name or _('%(words)s → %(account)s', words=keywords, account=self.env['account.account'].browse(account_id).name),
            'keywords': keywords, 'account_id': account_id, 'action': 'account', 'auto_apply': auto_apply, 'label': label or False,
            'direction': 'in' if st.amount > 0 else 'out', 'journal_ids': [(6, 0, st.journal_id.ids)]})
        return {'id': rule.id, 'name': rule.name}

    @api.model
    def suggest_keywords(self, st_line_id):
        st = self.env['account.bank.statement.line'].browse(int(st_line_id))
        words = re.findall(r'[A-Za-z]{3,}', st.payment_ref or '')
        common = {'the', 'and', 'for', 'from', 'with', 'neft', 'imps', 'rtgs', 'upi', 'ref', 'txn', 'transfer', 'payment', 'inr'}
        words = [w for w in words if w.lower() not in common]
        return ', '.join(dict.fromkeys(w.lower() for w in words[:3]))

    @api.model
    def apply_rule(self, st_line_id, rule_id):
        self._check(write=True)
        st = self.env['account.bank.statement.line'].browse(int(st_line_id)).exists()
        rule = self.env['ebshel.bank.rule'].browse(int(rule_id)).exists()
        if not st or not rule:
            raise UserError(_("Nothing to apply."))
        if not self._apply_rule(st, rule):
            raise UserError(_("Rule '%s' found nothing to match for this line.", rule.name))
        return True

    @api.model
    def _rule_for(self, st):
        for rule in self.env['ebshel.bank.rule'].search([('company_id', '=', st.company_id.id)]):
            if rule._matches(st):
                return rule
        return self.env['ebshel.bank.rule']

    @api.model
    def _apply_rule(self, st, rule):
        if rule.action == 'account':
            self.reconcile(st.id, [{'kind': 'account', 'account_id': rule.account_id.id, 'amount': abs(st.amount),
                                    'label': rule.label or st.payment_ref, 'analytic_distribution': rule.analytic_distribution}],
                           partner_id=rule.partner_id.id or None, rule_id=rule.id)
        else:
            cands = [c for c in self.env['ebshel.match.desk']._bank_candidates(
                st.with_context(skip_account_move_synchronization=True)) if c.get('partner_id') == rule.partner_id.commercial_partner_id.id]
            if not cands:
                return False
            self.reconcile(st.id, [{'kind': 'open', 'aml_id': c['id']} for c in cands], partner_id=rule.partner_id.id, rule_id=rule.id)
        rule.write({'hits': rule.hits + 1, 'last_used': fields.Date.context_today(self)})
        return True

    @api.model
    def auto_reconcile(self, journal_id, min_confidence=95):
        """Apply every automatic rule, tick exact book twins, and match sure invoice pairs."""
        self._check(write=True)
        journal = self._journal(journal_id)
        done = {'rules': 0, 'booked': 0, 'invoices': 0}
        used = set()
        for st in self.env['account.bank.statement.line'].search([('journal_id', '=', journal.id), ('is_reconciled', '=', False)],
                                                                 order='date, id'):
            rule = self._rule_for(st)
            if rule and rule.auto_apply:
                with self.env.cr.savepoint():
                    if self._apply_rule(st, rule):
                        done['rules'] += 1
                        continue
            book = [c for c in self._book_candidates(st, limit=2, exclude=used)]
            if len(book) == 1 or (book and book[0]['confidence'] >= 96 and (len(book) < 2 or book[1]['confidence'] < 96)):
                used.add(book[0]['id'])
                self.already_booked(st.id, book[0]['id'])
                done['booked'] += 1
                continue
            cands = self.env['ebshel.match.desk']._bank_candidates(st)
            if cands and cands[0]['confidence'] >= min_confidence and (len(cands) < 2 or cands[1]['confidence'] < cands[0]['confidence']):
                with self.env.cr.savepoint():
                    self.reconcile(st.id, [{'kind': 'open', 'aml_id': cands[0]['id']}])
                    done['invoices'] += 1
        return done

    # ------------------------------------------------------------------ import
    @api.model
    def import_rows(self, journal_id, rows, tick=True):
        """Rows pasted or uploaded from the bank: [{date, label, amount}].
        Duplicates of lines already imported are skipped; rows that equal an uncleared
        book item tick it; the rest become bank lines, and automatic rules run on them."""
        self._check(write=True)
        journal = self._journal(journal_id)
        StLine = self.env['account.bank.statement.line']
        rounding = journal.company_id.currency_id.rounding
        result = {'ticked': 0, 'created': 0, 'duplicates': 0, 'ruled': 0, 'errors': []}
        used = set()
        created = StLine
        for i, row in enumerate(rows or []):
            try:
                day = fields.Date.to_date(row.get('date'))
                amount = round(float(row.get('amount') or 0.0), 2)
            except (TypeError, ValueError):
                result['errors'].append(_('row %(n)d: date or amount not understood', n=i + 1))
                continue
            if not day or float_is_zero(amount, precision_rounding=rounding):
                result['errors'].append(_('row %(n)d: no date or a zero amount', n=i + 1))
                continue
            label = (row.get('label') or '').strip() or _('Bank line')
            if StLine.search_count([('journal_id', '=', journal.id), ('date', '=', day), ('amount', '=', amount),
                                    ('payment_ref', '=', label)]):
                result['duplicates'] += 1
                continue
            if self.env['account.move.line'].search_count(self._book_domain(journal) + [
                    ('ebshel_cleared_date', '=', day), ('ebshel_bank_ref', '=', label), ('balance', '=', amount)]):
                result['duplicates'] += 1
                continue
            if tick:
                probe = StLine.new({'journal_id': journal.id, 'date': day, 'amount': amount, 'payment_ref': label})
                book = self._book_candidates(probe, limit=3, exclude=used)
                if book and (len(book) == 1 or book[0]['confidence'] > book[1]['confidence']):
                    target = self.env['account.move.line'].browse(book[0]['id'])
                    target.write({'ebshel_cleared_date': day, 'ebshel_bank_ref': label, 'ebshel_cleared_by_id': self.env.uid})
                    used.add(target.id)
                    result['ticked'] += 1
                    continue
            created |= StLine.create({'journal_id': journal.id, 'date': day, 'amount': amount, 'payment_ref': label})
            result['created'] += 1
        for st in created:
            rule = self._rule_for(st)
            if rule and rule.auto_apply:
                with self.env.cr.savepoint():
                    if self._apply_rule(st, rule):
                        result['ruled'] += 1
        return result

    # ------------------------------------------------------------------ ticking and the statement
    @api.model
    def get_brs(self, journal_id, date=None, show='uncleared', search='', limit=300):
        self._check()
        journal = self._journal(journal_id)
        day = fields.Date.to_date(date) if date else fields.Date.context_today(self)
        numbers = self._brs_numbers(journal, day)
        cp = self.env['ebshel.bank.checkpoint'].search([('journal_id', '=', journal.id), ('date', '<=', day)], limit=1)
        domain = self._book_domain(journal, day) + [('move_id.statement_line_id', '=', False)]
        if show == 'uncleared':
            domain += ['|', ('ebshel_cleared_date', '=', False), ('ebshel_cleared_date', '>', day)]
        elif show == 'cleared':
            domain += [('ebshel_cleared_date', '!=', False), ('ebshel_cleared_date', '<=', day)]
        search = (search or '').strip()
        if search:
            try:
                amount = float(search.replace(',', ''))
                domain += ['|', ('balance', '=', amount), ('balance', '=', -amount)]
            except ValueError:
                domain += ['|', '|', '|', ('move_id.name', 'ilike', search), ('move_id.ref', 'ilike', search),
                           ('partner_id', 'ilike', search), ('name', 'ilike', search)]
        AML = self.env['account.move.line']
        total = AML.search_count(domain)
        items = []
        for line in AML.search(domain, order='date desc, id desc', limit=limit):
            age = (day - line.date).days
            items.append({'id': line.id, 'move_id': line.move_id.id, 'date': fields.Date.to_string(line.date), 'name': line.move_id.name or '',
                          'label': line.name or '', 'ref': line.move_id.ref or '', 'partner': line.partner_id.display_name or '',
                          'amount': line.balance, 'age': age, 'stale': age > 90 and not line.ebshel_cleared_date,
                          'cleared': fields.Date.to_string(line.ebshel_cleared_date) if line.ebshel_cleared_date else None,
                          'bank_ref': line.ebshel_bank_ref or ''})
        first_open = AML.search(self._book_domain(journal, day) + [('ebshel_cleared_date', '=', False),
                                                                    ('move_id.statement_line_id', '=', False)], order='date', limit=1)
        return {'journal': {'id': journal.id, 'name': journal.name, 'account': journal.default_account_id.display_name},
                'date': fields.Date.to_string(day), 'numbers': numbers, 'items': items, 'total': total,
                'checkpoint': {'id': cp.id, 'date': fields.Date.to_string(cp.date), 'balance': cp.balance,
                               'difference': cp.balance - numbers['bank_expected'] if cp.date == day else None} if cp else None,
                'oldest_open': fields.Date.to_string(first_open.date) if first_open else None,
                'stale': sum(1 for i in items if i['stale']), 'currency': self._currency(), 'can_write': self._can_write()}

    @api.model
    def set_cleared(self, aml_ids, date=None, bank_ref=None):
        self._check(write=True)
        lines = self.env['account.move.line'].browse([int(i) for i in aml_ids]).exists()
        bad = lines.filtered(lambda l: l.journal_id.type not in ('bank', 'cash') and l.account_id.account_type not in ('asset_cash', 'liability_credit_card'))
        if bad:
            raise UserError(_("Only items on a bank or cash account can be ticked."))
        if date:
            lines.write({'ebshel_cleared_date': fields.Date.to_date(date), 'ebshel_cleared_by_id': self.env.uid,
                         **({'ebshel_bank_ref': bank_ref} if bank_ref else {})})
        else:
            lines.write({'ebshel_cleared_date': False, 'ebshel_bank_ref': False, 'ebshel_cleared_by_id': False})
        return len(lines)

    @api.model
    def baseline(self, journal_id, date):
        """Everything booked up to `date` counts as seen by the bank - the starting line
        for a company that has never ticked before."""
        self._check(write=True)
        journal = self._journal(journal_id)
        day = fields.Date.to_date(date)
        self.env['account.move.line'].flush_model()
        self.env.cr.execute(SQL("""
            UPDATE account_move_line l SET ebshel_cleared_date = l.date, ebshel_cleared_by_id = %s, ebshel_bank_ref = %s
              FROM account_move m
             WHERE m.id = l.move_id AND l.account_id = %s AND l.parent_state = 'posted' AND l.company_id = %s
               AND l.date <= %s AND l.ebshel_cleared_date IS NULL AND m.statement_line_id IS NULL
        """, self.env.uid, _('Baseline'), journal.default_account_id.id, journal.company_id.id, day))
        count = self.env.cr.rowcount
        self.env['account.move.line'].invalidate_model(['ebshel_cleared_date', 'ebshel_cleared_by_id', 'ebshel_bank_ref'])
        return count

    @api.model
    def save_checkpoint(self, journal_id, date, balance, note=None):
        self._check(write=True)
        journal = self._journal(journal_id)
        day = fields.Date.to_date(date)
        Checkpoint = self.env['ebshel.bank.checkpoint']
        cp = Checkpoint.search([('journal_id', '=', journal.id), ('date', '=', day)], limit=1)
        vals = {'balance': float(balance), 'note': note or False}
        if cp:
            cp.write(vals)
        else:
            cp = Checkpoint.create(dict(vals, journal_id=journal.id, date=day))
        return cp.id

    @api.model
    def brs_pdf(self, journal_id, date=None):
        self._check()
        journal = self._journal(journal_id)
        action = self.env.ref('ebshel_account_advanced.action_report_brs').report_action(journal)
        action['data'] = {'date': date or fields.Date.to_string(fields.Date.context_today(self)), 'journal_id': journal.id}
        return action


class BrsReport(models.AbstractModel):
    _name = 'report.ebshel_account_advanced.report_brs'
    _description = 'Bank reconciliation statement'

    @api.model
    def _get_report_values(self, docids, data=None):
        data = data or {}
        journal = self.env['account.journal'].browse(data.get('journal_id') or docids[:1])
        desk = self.env['ebshel.bank.desk']
        brs = desk.get_brs(journal.id, data.get('date'), show='uncleared', limit=2000)
        return {'doc_ids': journal.ids, 'doc_model': 'account.journal', 'docs': journal, 'brs': brs,
                'deposits': [i for i in brs['items'] if i['amount'] > 0], 'payments': [i for i in brs['items'] if i['amount'] < 0],
                'company': journal.company_id}
