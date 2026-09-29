# -*- coding: utf-8 -*-
"""The matching desk: open items paired by amount, reference or sum with a
confidence score, applied in one click; bank lines matched to the invoices
they pay."""
import re
from itertools import combinations

from odoo import api, fields, models, Command, _
from odoo.exceptions import AccessError, UserError
from odoo.tools import float_compare, float_is_zero


class MatchDesk(models.AbstractModel):
    _name = 'ebshel.match.desk'
    _description = 'Matching desk'

    @api.model
    def _check(self, write=False):
        if self.env.su:
            return
        user = self.env.user
        if not user.has_groups('account.group_account_readonly,account.group_account_invoice,account.group_account_user'):
            raise AccessError(_("The matching desk is for the accounting team."))
        if write and not user.has_groups('account.group_account_user,account.group_account_manager'):
            raise AccessError(_("Only accountants can match items."))

    @api.model
    def _currency(self):
        c = self.env.company.currency_id
        return {'symbol': c.symbol, 'position': c.position, 'decimals': c.decimal_places}

    @api.model
    def _open_items(self, kind, partner_ids=None):
        atype = 'asset_receivable' if kind == 'receivable' else 'liability_payable'
        domain = [('account_id.account_type', '=', atype), ('reconciled', '=', False), ('parent_state', '=', 'posted'),
                  ('company_id', '=', self.env.company.id), ('partner_id', '!=', False)]
        if partner_ids:
            domain.append(('partner_id.commercial_partner_id', 'in', partner_ids))
        return self.env['account.move.line'].search(domain, order='date, id')

    @api.model
    def _item(self, line):
        return {'id': line.id, 'move_id': line.move_id.id, 'name': line.move_id.name or line.name or '',
                'label': line.name or '', 'ref': line.move_id.ref or line.move_id.payment_reference or '',
                'date': fields.Date.to_string(line.date), 'due': fields.Date.to_string(line.date_maturity or line.date),
                'residual': line.amount_residual, 'balance': line.balance, 'journal': line.journal_id.code or '',
                'partner_id': line.partner_id.commercial_partner_id.id,
                'partner': line.partner_id.commercial_partner_id.display_name,
                'account': line.account_id.display_name}

    @api.model
    def _tokens(self, *texts):
        out = set()
        for text in texts:
            for tok in re.findall(r'[A-Za-z0-9][A-Za-z0-9/\-]{3,}', text or ''):
                out.add(tok.upper())
        return out

    @api.model
    def _subset(self, cands, amount, rounding, max_items=4):
        small = [d for d in cands if d.amount_residual <= amount + rounding][:12]
        for n in range(2, min(max_items, len(small)) + 1):
            for combo in combinations(small, n):
                if float_compare(sum(d.amount_residual for d in combo), amount, precision_rounding=rounding) == 0:
                    return list(combo)
        return None

    @api.model
    def _suggest_for_partner(self, lines, rounding):
        """Pair one partner's credits (payments, credit notes) with its debits (invoices)."""
        debits = sorted([l for l in lines if l.amount_residual > 0], key=lambda l: (l.date_maturity or l.date, l.id))
        credits = sorted([l for l in lines if l.amount_residual < 0], key=lambda l: (l.date, l.id))
        used, out = set(), []
        for c in credits:
            amount = -c.amount_residual
            c_tokens = self._tokens(c.name, c.move_id.ref, c.move_id.payment_reference, c.move_id.name)
            cands = [d for d in debits if d.id not in used]
            if not cands:
                break
            by_ref = [d for d in cands if (d.move_id.name and d.move_id.name.upper() in c_tokens)
                      or (d.move_id.payment_reference and d.move_id.payment_reference.upper() in c_tokens)]
            exact = [d for d in cands if float_compare(d.amount_residual, amount, precision_rounding=rounding) == 0]
            both = [d for d in by_ref if d in exact]
            pick, conf, reason = [], 0, ''
            if both:
                pick, conf, reason = [both[0]], 98, _('same amount and the invoice number is quoted')
            elif by_ref:
                if float_compare(sum(d.amount_residual for d in by_ref), amount, precision_rounding=rounding) == 0:
                    pick, conf, reason = by_ref, 95, _('the quoted invoices add up to it')
                else:
                    pick, conf, reason = by_ref[:1], 85, _('the invoice number is quoted, the amount differs (partial)')
            elif exact:
                pick, conf, reason = [exact[0]], 90, _('same amount as one invoice')
            else:
                combo = self._subset(cands, amount, rounding)
                if combo:
                    pick, conf, reason = combo, 80, _('%d invoices add up to it', len(combo))
                else:
                    left = amount
                    for d in cands:
                        if left <= rounding / 2:
                            break
                        pick.append(d)
                        left -= d.amount_residual
                    conf, reason = 50, _('oldest invoices first (on account)')
            if not pick:
                continue
            used.update(d.id for d in pick)
            out.append({'key': 'c%d' % c.id, 'credit': self._item(c), 'debits': [self._item(d) for d in pick],
                        'aml_ids': [c.id] + [d.id for d in pick],
                        'amount': min(amount, sum(d.amount_residual for d in pick)), 'confidence': conf, 'reason': reason})
        return out

    @api.model
    def get_data(self, options=None):
        self._check()
        self.env.flush_all()
        options = options or {}
        kind = options.get('kind') or 'receivable'
        if kind == 'bank':
            return self._bank_data(options)
        rounding = self.env.company.currency_id.rounding
        partner_ids = [options['partner_id']] if options.get('partner_id') else None
        lines = self._open_items(kind, partner_ids)
        by_partner = {}
        for line in lines:
            by_partner.setdefault(line.partner_id.commercial_partner_id, []).append(line)
        partners, pairs = [], []
        for partner, pl in by_partner.items():
            debit = sum(l.amount_residual for l in pl if l.amount_residual > 0)
            credit = -sum(l.amount_residual for l in pl if l.amount_residual < 0)
            row = {'id': partner.id, 'name': partner.display_name, 'items': len(pl), 'debit': debit, 'credit': credit,
                   'net': debit - credit, 'offsettable': min(debit, credit), 'pairs': 0}
            if credit > 0 and debit > 0:
                found = self._suggest_for_partner(pl, rounding)
                for s in found:
                    s.update({'partner_id': partner.id, 'partner': partner.display_name})
                pairs.extend(found)
                row['pairs'] = len(found)
            partners.append(row)
        search = (options.get('search') or '').strip().lower()
        if search:
            partners = [p for p in partners if search in p['name'].lower()]
            pairs = [p for p in pairs if search in p['partner'].lower()]
        min_conf = options.get('min_confidence') or 0
        pairs = [p for p in pairs if p['confidence'] >= min_conf]
        partners.sort(key=lambda p: (-p['offsettable'], -p['items']))
        pairs.sort(key=lambda p: (-p['confidence'], -p['amount']))
        limit = options.get('limit') or 200
        return {
            'kind': kind,
            'kpis': {'items': len(lines), 'partners': len(by_partner), 'debit': sum(p['debit'] for p in partners),
                     'credit': sum(p['credit'] for p in partners), 'offsettable': sum(p['offsettable'] for p in partners),
                     'pairs': len(pairs), 'sure': sum(1 for p in pairs if p['confidence'] >= 90),
                     'amount': sum(p['amount'] for p in pairs)},
            'partners': partners[:limit], 'pairs': pairs[:limit],
            'bank_journals': self._bank_journals(), 'currency': self._currency(),
            'can_write': self.env.su or self.env.user.has_groups('account.group_account_user,account.group_account_manager'),
        }

    @api.model
    def _bank_journals(self):
        return [{'id': j.id, 'name': j.name} for j in self.env['account.journal'].search(
            [('type', 'in', ('bank', 'cash')), ('company_id', '=', self.env.company.id)])]

    @api.model
    def apply(self, pairs):
        """Reconcile each group of items; returns how many groups were done and fully cleared."""
        self._check(write=True)
        AML = self.env['account.move.line']
        done, full, skipped = 0, 0, []
        for pair in pairs:
            lines = AML.browse(pair.get('aml_ids') or []).exists().filtered(lambda l: not l.reconciled)
            if len(lines) < 2 or len(lines.mapped('account_id')) != 1:
                skipped.append(pair.get('key'))
                continue
            lines.reconcile()
            done += 1
            if all(l.reconciled for l in lines):
                full += 1
        return {'done': done, 'full': full, 'skipped': skipped}

    @api.model
    def auto_apply(self, kind='receivable', min_confidence=90):
        self._check(write=True)
        data = self.get_data({'kind': kind, 'min_confidence': min_confidence, 'limit': 1000})
        return self.apply(data['pairs'])

    # ------------------------------------------------------------------ bank
    @api.model
    def _bank_candidates(self, st):
        """Open items that could be what this bank line paid, best first."""
        rounding = st.company_id.currency_id.rounding
        amount = st.amount
        if float_is_zero(amount, precision_rounding=rounding):
            return []
        atype = 'asset_receivable' if amount > 0 else 'liability_payable'
        AML = self.env['account.move.line']
        base = [('account_id.account_type', '=', atype), ('reconciled', '=', False), ('parent_state', '=', 'posted'),
                ('company_id', '=', st.company_id.id)]
        tokens = list(self._tokens(st.payment_ref))
        out, seen = [], set()

        def add(line, conf, reason):
            if line.id in seen or line.amount_residual * amount <= 0:
                return
            seen.add(line.id)
            item = self._item(line)
            item.update({'confidence': conf, 'reason': reason})
            out.append(item)

        if tokens:
            by_ref = AML.search(base + ['|', ('move_id.name', 'in', tokens), ('move_id.payment_reference', 'in', tokens)])
            for line in by_ref:
                same = float_compare(line.amount_residual, amount, precision_rounding=rounding) == 0
                add(line, 97 if same else 88, _('its number is in the bank reference') + (_(' and the amount matches') if same else ''))
        partner = st.partner_id.commercial_partner_id
        if partner:
            mine = AML.search(base + [('partner_id.commercial_partner_id', '=', partner.id)], order='date_maturity, date, id')
            exact = [l for l in mine if float_compare(l.amount_residual, amount, precision_rounding=rounding) == 0]
            for line in exact:
                add(line, 92, _('same partner and amount'))
            combo = self._subset([l for l in mine if l.id not in seen and amount > 0] if amount > 0 else [], amount, rounding) if amount > 0 else None
            if combo:
                for line in combo:
                    add(line, 82, _('same partner, %d invoices add up to it', len(combo)))
            for line in mine:
                add(line, 55, _('same partner, oldest first'))
        else:
            for line in AML.search(base + [('amount_residual', '=', amount)], limit=6):
                add(line, 70, _('same amount, no partner on the bank line'))
        out.sort(key=lambda i: (-i['confidence'], i['due']))
        return out[:10]

    @api.model
    def _bank_data(self, options):
        company = self.env.company
        domain = [('is_reconciled', '=', False), ('company_id', '=', company.id), ('move_id.state', '=', 'posted')]
        if options.get('journal_id'):
            domain.append(('journal_id', '=', options['journal_id']))
        search = (options.get('search') or '').strip()
        if search:
            domain += ['|', ('payment_ref', 'ilike', search), ('partner_id', 'ilike', search)]
        st_lines = self.env['account.bank.statement.line'].search(domain, order='date desc, id desc', limit=options.get('limit') or 100)
        rows = []
        for st in st_lines:
            cands = self._bank_candidates(st)
            rows.append({'id': st.id, 'date': fields.Date.to_string(st.date), 'ref': st.payment_ref or '', 'amount': st.amount,
                         'partner_id': st.partner_id.commercial_partner_id.id, 'partner': st.partner_id.display_name or '',
                         'journal': st.journal_id.name, 'candidates': cands, 'best': cands[0] if cands else None})
        return {
            'kind': 'bank', 'rows': rows,
            'kpis': {'lines': len(st_lines), 'inflow': sum(r['amount'] for r in rows if r['amount'] > 0),
                     'outflow': -sum(r['amount'] for r in rows if r['amount'] < 0),
                     'sure': sum(1 for r in rows if r['best'] and r['best']['confidence'] >= 90),
                     'none': sum(1 for r in rows if not r['best'])},
            'bank_journals': self._bank_journals(), 'currency': self._currency(),
            'can_write': self.env.su or self.env.user.has_groups('account.group_account_user,account.group_account_manager'),
        }

    @api.model
    def match_bank(self, st_line_id, aml_ids):
        """Replace the bank line's suspense entry with the chosen open items and reconcile them."""
        self._check(write=True)
        st = self.env['account.bank.statement.line'].browse(st_line_id).exists()
        amls = self.env['account.move.line'].browse(aml_ids).exists().filtered(lambda l: not l.reconciled)
        if not st or not amls:
            raise UserError(_("Nothing left to match."))
        company_currency = st.company_id.currency_id
        rounding = company_currency.rounding
        if (st.foreign_currency_id and st.foreign_currency_id != company_currency) or \
                (st.journal_id.currency_id and st.journal_id.currency_id != company_currency):
            raise UserError(_("Foreign-currency bank lines are matched from the statement itself."))
        liquidity, suspense, other = st._seek_for_lines()
        if not suspense:
            raise UserError(_("This bank line is already matched."))
        left = -sum(suspense.mapped('balance'))
        base_vals = st._prepare_move_line_default_vals()[1]
        base_vals.pop('move_id', None)
        creates, pairs = [], []
        for aml in amls.sorted(lambda l: (l.date_maturity or l.date, l.id)):
            if float_is_zero(left, precision_rounding=rounding):
                break
            residual = aml.amount_residual
            if residual * left <= 0:
                continue
            take = residual if abs(residual) <= abs(left) else left
            vals = dict(base_vals, account_id=aml.account_id.id, partner_id=aml.partner_id.id,
                        name=aml.move_id.name or aml.name or st.payment_ref, debit=max(-take, 0.0), credit=max(take, 0.0),
                        amount_currency=-take, currency_id=company_currency.id)
            creates.append(vals)
            pairs.append((aml, vals))
            left -= take
        if not pairs:
            raise UserError(_("None of the chosen items is on the same side as the bank line."))
        if not float_is_zero(left, precision_rounding=rounding):
            creates.append(dict(base_vals, debit=max(-left, 0.0), credit=max(left, 0.0), amount_currency=-left,
                                currency_id=company_currency.id))
        move = st.move_id.with_context(force_delete=True, skip_readonly_check=True)
        move.write({'line_ids': [Command.delete(l.id) for l in (suspense | other)] + [Command.create(v) for v in creates]})
        new_lines = move.line_ids - liquidity
        plan, used = [], set()
        for aml, vals in pairs:
            target = vals['debit'] - vals['credit']
            match = new_lines.filtered(lambda l: l.id not in used and l.account_id.id == vals['account_id']
                                       and l.partner_id.id == vals['partner_id'] and abs(l.balance - target) < rounding / 2)
            if not match:
                continue
            used.add(match[0].id)
            plan.append(match[0] + aml)
        self.env['account.move.line']._reconcile_plan(plan)
        st.invalidate_recordset(['is_reconciled'])
        return {'reconciled': st.is_reconciled, 'left': left, 'pairs': len(plan)}
