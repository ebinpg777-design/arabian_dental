# -*- coding: utf-8 -*-
"""Journal entries typed like a spreadsheet: a grid, paste from Excel, one key to
balance, templates that split an amount by percentage, and the checks run before
anything is posted."""
from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError
from odoo.tools import float_compare, float_is_zero


class EntryTemplate(models.Model):
    _name = 'ebshel.entry.template'
    _description = 'Journal entry template'
    _order = 'sequence, name'

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    journal_id = fields.Many2one('account.journal', check_company=True)
    ref = fields.Char('Reference')
    note = fields.Char()
    line_ids = fields.One2many('ebshel.entry.template.line', 'template_id', copy=True)
    use_count = fields.Integer(readonly=True)


class EntryTemplateLine(models.Model):
    _name = 'ebshel.entry.template.line'
    _description = 'Journal entry template line'
    _order = 'sequence, id'

    template_id = fields.Many2one('ebshel.entry.template', required=True, ondelete='cascade')
    sequence = fields.Integer(default=10)
    account_id = fields.Many2one('account.account', required=True)
    partner_id = fields.Many2one('res.partner')
    label = fields.Char()
    side = fields.Selection([('debit', 'Debit'), ('credit', 'Credit')], required=True, default='debit')
    share = fields.Float('Share %', help="Of the amount entered when the template is used. 0 with a fixed amount below.")
    fixed = fields.Float('Fixed amount')
    analytic_account_id = fields.Many2one('account.analytic.account')


class EntryStudio(models.AbstractModel):
    _name = 'ebshel.entry.studio'
    _description = 'Journal entry studio'

    @api.model
    def _check(self, write=False):
        if self.env.su:
            return
        user = self.env.user
        if not user.has_groups('account.group_account_readonly,account.group_account_invoice'):
            raise AccessError(_("Journal entries are for the accounting team."))
        if write and not user.has_groups('account.group_account_user,account.group_account_manager'):
            raise AccessError(_("Only accountants can write journal entries."))

    @api.model
    def get_setup(self):
        self._check()
        company = self.env.company
        journals = self.env['account.journal'].search([('company_id', '=', company.id)], order='type desc, sequence')
        recent = self.env['account.move'].search([('company_id', '=', company.id), ('move_type', '=', 'entry'),
                                                   ('create_uid', '=', self.env.uid)], order='id desc', limit=8)
        c = company.currency_id
        lock = company.fiscalyear_lock_date
        return {
            'journals': [{'id': j.id, 'name': j.name, 'code': j.code, 'type': j.type} for j in journals],
            'default_journal': (journals.filtered(lambda j: j.type == 'general')[:1] or journals[:1]).id,
            'templates': [{'id': t.id, 'name': t.name, 'journal_id': t.journal_id.id, 'lines': len(t.line_ids),
                           'uses_share': any(l.share for l in t.line_ids)} for t in self.env['ebshel.entry.template'].search([])],
            'recent': [{'id': m.id, 'name': m.name, 'date': fields.Date.to_string(m.date), 'ref': m.ref or '',
                        'amount': m.amount_total, 'state': m.state} for m in recent],
            'lock_date': fields.Date.to_string(lock) if lock else None,
            'analytic': self.env.user.has_group('analytic.group_analytic_accounting'),
            'currency': {'symbol': c.symbol, 'position': c.position, 'decimals': c.decimal_places},
            'today': fields.Date.to_string(fields.Date.context_today(self)),
            'can_write': self.env.su or self.env.user.has_groups('account.group_account_user,account.group_account_manager'),
        }

    @api.model
    def find(self, model, query, limit=12):
        """Autocomplete for the grid: accounts by code or name, partners, analytic accounts."""
        self._check()
        allowed = {'account.account': [('company_ids', 'in', self.env.company.id)], 'res.partner': [],
                   'account.analytic.account': []}
        if model not in allowed:
            raise UserError(_("Not searchable here."))
        Model = self.env[model]
        domain = list(allowed[model])
        query = (query or '').strip()
        if query:
            if model == 'account.account':
                domain += ['|', ('code', '=like', query + '%'), ('name', 'ilike', query)]
            else:
                domain.append(('display_name', 'ilike', query) if model != 'res.partner' else ('name', 'ilike', query))
        return [{'id': r.id, 'name': r.display_name} for r in Model.search(domain, limit=limit)]

    @api.model
    def resolve_accounts(self, codes):
        """Pasted account codes (or names) to ids."""
        self._check()
        Account = self.env['account.account']
        out = {}
        for code in set(filter(None, (c.strip() for c in codes or []))):
            acc = Account.search([('code', '=', code.split(' ')[0]), ('company_ids', 'in', self.env.company.id)], limit=1) or \
                Account.search([('name', '=ilike', code), ('company_ids', 'in', self.env.company.id)], limit=1)
            if acc:
                out[code] = {'id': acc.id, 'name': acc.display_name}
        return out

    @api.model
    def check(self, vals):
        """Everything that would stop this entry, before trying."""
        problems = []
        lines = vals.get('lines') or []
        cur = self.env.company.currency_id
        debit = sum(float(l.get('debit') or 0) for l in lines)
        credit = sum(float(l.get('credit') or 0) for l in lines)
        if not lines:
            problems.append(_("Add at least two lines."))
        if float_compare(debit, credit, precision_rounding=cur.rounding) != 0:
            problems.append(_("Debits and credits differ by %s.", round(debit - credit, 2)))
        if any(not l.get('account_id') for l in lines if float(l.get('debit') or 0) or float(l.get('credit') or 0)):
            problems.append(_("A line with an amount has no account."))
        if any(float(l.get('debit') or 0) and float(l.get('credit') or 0) for l in lines):
            problems.append(_("A line has both a debit and a credit."))
        date = fields.Date.to_date(vals.get('date')) if vals.get('date') else None
        lock = self.env.company.fiscalyear_lock_date
        if date and lock and date <= lock:
            problems.append(_("The date is on or before the lock date %s.", fields.Date.to_string(lock)))
        if not vals.get('journal_id'):
            problems.append(_("Choose a journal."))
        needs_partner = self.env['account.account'].browse([l['account_id'] for l in lines if l.get('account_id')]).filtered(
            lambda a: a.account_type in ('asset_receivable', 'liability_payable'))
        for line in lines:
            if line.get('account_id') in needs_partner.ids and not line.get('partner_id'):
                problems.append(_("A receivable or payable line has no partner."))
                break
        return {'problems': problems, 'debit': debit, 'credit': credit}

    @api.model
    def create_entry(self, vals, post=False):
        self._check(write=True)
        result = self.check(vals)
        if result['problems']:
            raise UserError('\n'.join(result['problems']))
        lines = []
        for line in vals['lines']:
            debit, credit = float(line.get('debit') or 0), float(line.get('credit') or 0)
            if float_is_zero(debit - credit, precision_rounding=self.env.company.currency_id.rounding) and not (debit or credit):
                continue
            item = {'account_id': int(line['account_id']), 'name': line.get('label') or vals.get('ref') or '/',
                    'debit': debit, 'credit': credit, 'partner_id': line.get('partner_id') or False}
            if line.get('analytic_account_id'):
                item['analytic_distribution'] = {str(line['analytic_account_id']): 100.0}
            lines.append((0, 0, item))
        move = self.env['account.move'].create({'move_type': 'entry', 'journal_id': int(vals['journal_id']),
                                                'date': vals.get('date') or fields.Date.context_today(self),
                                                'ref': vals.get('ref') or False, 'line_ids': lines})
        if post:
            move.action_post()
        if vals.get('template_id'):
            tpl = self.env['ebshel.entry.template'].browse(int(vals['template_id']))
            tpl.use_count += 1
        return {'id': move.id, 'name': move.name, 'state': move.state}

    @api.model
    def save_template(self, name, vals):
        self._check(write=True)
        total = sum(float(l.get('debit') or 0) for l in vals.get('lines') or []) or 1.0
        tpl = self.env['ebshel.entry.template'].create({
            'name': name, 'journal_id': vals.get('journal_id') or False, 'ref': vals.get('ref') or False,
            'line_ids': [(0, 0, {'sequence': i, 'account_id': int(l['account_id']), 'partner_id': l.get('partner_id') or False,
                                 'label': l.get('label') or False, 'analytic_account_id': l.get('analytic_account_id') or False,
                                 'side': 'debit' if float(l.get('debit') or 0) else 'credit',
                                 'share': round((float(l.get('debit') or 0) or float(l.get('credit') or 0)) / total * 100, 4)})
                         for i, l in enumerate(vals.get('lines') or []) if l.get('account_id')]})
        return {'id': tpl.id, 'name': tpl.name}

    @api.model
    def load_template(self, template_id, amount=0.0):
        """The template's lines for an amount: shares of it, plus any fixed amounts."""
        self._check()
        tpl = self.env['ebshel.entry.template'].browse(int(template_id)).exists()
        if not tpl:
            raise UserError(_("That template no longer exists."))
        cur = self.env.company.currency_id
        amount = float(amount or 0.0)
        lines = []
        for l in tpl.line_ids:
            value = cur.round(amount * l.share / 100.0) if l.share else l.fixed
            lines.append({'account_id': l.account_id.id, 'account': l.account_id.display_name,
                          'partner_id': l.partner_id.id or False, 'partner': l.partner_id.display_name or '',
                          'label': l.label or '', 'analytic_account_id': l.analytic_account_id.id or False,
                          'analytic': l.analytic_account_id.display_name or '',
                          'debit': value if l.side == 'debit' else 0.0, 'credit': value if l.side == 'credit' else 0.0})
        # rounding leftovers go to the largest line of the smaller side, so the entry balances
        d = sum(x['debit'] for x in lines)
        c = sum(x['credit'] for x in lines)
        gap = cur.round(d - c)
        if lines and gap and abs(gap) < 1:
            side = 'credit' if gap > 0 else 'debit'
            target = max((x for x in lines if x[side]), key=lambda x: x[side], default=None)
            if target:
                target[side] = cur.round(target[side] + abs(gap))
        return {'journal_id': tpl.journal_id.id, 'ref': tpl.ref or tpl.name, 'lines': lines}


class AnalyticRedistribute(models.TransientModel):
    """Give many journal items a new analytic distribution in one go - posted ones too;
    Odoo rewrites their analytic items."""
    _name = 'ebshel.analytic.redistribute'
    _inherit = ['analytic.mixin']
    _description = 'Redistribute analytic accounts'

    line_ids = fields.Many2many('account.move.line', string='Journal items')
    mode = fields.Selection([('replace', 'Replace the distribution'), ('fill', 'Only where there is none'),
                             ('clear', 'Remove the distribution')], default='replace', required=True)
    count = fields.Integer(compute='_compute_summary')
    total = fields.Float(compute='_compute_summary')
    with_distribution = fields.Integer(compute='_compute_summary')

    @api.model
    def default_get(self, fields_list):
        vals = super().default_get(fields_list)
        if self.env.context.get('active_model') == 'account.move.line' and self.env.context.get('active_ids'):
            vals['line_ids'] = [(6, 0, self.env.context['active_ids'])]
        return vals

    @api.depends('line_ids')
    def _compute_summary(self):
        for w in self:
            w.count = len(w.line_ids)
            w.total = sum(abs(b) for b in w.line_ids.mapped('balance'))
            w.with_distribution = len(w.line_ids.filtered('analytic_distribution'))

    def action_apply(self):
        self.ensure_one()
        if not self.env.su and not self.env.user.has_groups('account.group_account_user,account.group_account_manager'):
            raise AccessError(_("Only accountants can change analytic distributions."))
        lines = self.line_ids.filtered(lambda l: l.display_type not in ('line_section', 'line_note'))
        if self.mode == 'fill':
            lines = lines.filtered(lambda l: not l.analytic_distribution)
        if self.mode != 'clear' and not self.analytic_distribution:
            raise UserError(_("Choose the analytic distribution to apply."))
        value = False if self.mode == 'clear' else self.analytic_distribution
        lines.with_context(validate_analytic=True).write({'analytic_distribution': value})
        return {'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'title': _('Analytic distribution'), 'type': 'success',
                           'message': _('%s journal item(s) updated; their analytic items were rewritten.', len(lines)),
                           'next': {'type': 'ir.actions.act_window_close'}}}
