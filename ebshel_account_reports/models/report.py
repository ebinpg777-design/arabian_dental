# -*- coding: utf-8 -*-
"""A report is a record: its kind names the handler that computes it, its lines
(for statement reports) say what to sum, and the record carries every switch the
screen offers. Users design their own the same way."""
import ast
import base64
import io
import json
import operator
import re

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

from .engine import BALANCE_MODES, COMPARISON_MODES, PRESETS

DISPLAYS = [('amount', 'Amount'), ('percent', 'Percentage'), ('ratio', 'Ratio'),
            ('days', 'Days'), ('count', 'Count'), ('check', 'Balance check')]
ACCOUNT_TYPES = [
    ('asset_receivable', 'Receivable'), ('asset_cash', 'Bank and Cash'),
    ('asset_current', 'Current Assets'), ('asset_non_current', 'Non-current Assets'),
    ('asset_prepayments', 'Prepayments'), ('asset_fixed', 'Fixed Assets'),
    ('liability_payable', 'Payable'), ('liability_credit_card', 'Credit Card'),
    ('liability_current', 'Current Liabilities'), ('liability_non_current', 'Non-current Liabilities'),
    ('equity', 'Equity'), ('equity_unaffected', 'Current Year Earnings'),
    ('income', 'Income'), ('income_other', 'Other Income'), ('expense', 'Expenses'),
    ('expense_depreciation', 'Depreciation'), ('expense_direct_cost', 'Cost of Revenue'),
    ('off_balance', 'Off-Balance Sheet'),
]

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.USub: operator.neg, ast.UAdd: operator.pos}
_FUNCS = {'abs': abs, 'min': min, 'max': max, 'round': round}


def evaluate_formula(expr, values):
    """A safe arithmetic over line codes: + - * / ( ) abs min max round, numbers,
    and the names in `values`. A division by zero reads as nothing."""
    tree = ast.parse(expr, mode='eval')

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id not in values:
                raise KeyError(node.id)
            return values[node.id]
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            left, right = ev(node.left), ev(node.right)
            if left is None or right is None:
                return None
            if isinstance(node.op, ast.Div) and not right:
                return None
            return _OPS[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
            value = ev(node.operand)
            return None if value is None else _OPS[type(node.op)](value)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS:
            args = [ev(a) for a in node.args]
            if any(a is None for a in args):
                return None
            return float(_FUNCS[node.func.id](*args))
        raise ValueError(_("Not allowed in a formula: %s", ast.dump(node)[:40]))
    return ev(tree)


def formula_names(expr):
    return sorted({n.id for n in ast.walk(ast.parse(expr, mode='eval')) if isinstance(n, ast.Name)})


class FinReport(models.Model):
    _name = 'ebshel.fin.report'
    _description = 'Dynamic financial report'
    _order = 'sequence, id'

    name = fields.Char(required=True, translate=True)
    key = fields.Char(required=True, help="Stable identifier, used by menus and by other modules.")
    kind = fields.Selection(selection='_kind_selection', required=True, default='statement',
                            help="Which engine computes the report.")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    custom = fields.Boolean(string='Designed here', default=True,
                            help="Built in the designer rather than shipped by a module.")
    description = fields.Text()
    company_id = fields.Many2one('res.company', help="Leave empty for every company.")
    line_ids = fields.One2many('ebshel.fin.report.line', 'report_id', string='Lines', copy=True)
    date_mode = fields.Selection([('range', 'A period'), ('single', 'As of a date')],
                                 default='range', required=True)
    default_preset = fields.Selection(PRESETS, default='this_month', required=True)
    allow_comparison = fields.Boolean(default=True)
    default_comparison = fields.Selection(COMPARISON_MODES, default='none')
    show_growth = fields.Boolean(string='Growth column', default=True)
    allow_journals = fields.Boolean(string='Journal filter', default=True)
    allow_partners = fields.Boolean(string='Partner filter', default=False)
    allow_analytic = fields.Boolean(string='Analytic filter', default=True)
    allow_accounts = fields.Boolean(string='Account search', default=False)
    allow_hierarchy = fields.Boolean(string='Account groups', default=False)
    allow_posted_toggle = fields.Boolean(string='Draft entries toggle', default=True)
    unfold_all_default = fields.Boolean(string='Unfold all by default', default=False)
    ledger_account_types = fields.Char(
        default='asset_receivable,liability_payable',
        help="Partner ledger: the account types it walks (comma separated).")
    aged_interval = fields.Integer(string='Ageing interval (days)', default=30)
    aged_buckets = fields.Integer(string='Ageing buckets', default=5)
    action_id = fields.Many2one('ir.actions.client', readonly=True, copy=False)
    menu_id = fields.Many2one('ir.ui.menu', readonly=True, copy=False)
    line_count = fields.Integer(compute='_compute_line_count')

    _key_uniq = models.Constraint('UNIQUE(key)', 'Report keys must be unique.')

    @api.model
    def _kind_selection(self):
        out = []
        for name in sorted(self.env.registry):
            if name.startswith('ebshel.fin.handler.'):
                out.append((name[len('ebshel.fin.handler.'):], self.env[name]._kind_label))
        return out

    @api.depends('line_ids')
    def _compute_line_count(self):
        for report in self:
            report.line_count = len(report.line_ids)

    def _handler(self):
        self.ensure_one()
        name = 'ebshel.fin.handler.%s' % self.kind
        if name not in self.env:
            raise UserError(_("No engine is installed for '%s' reports.", self.kind))
        return self.env[name]

    @api.model
    def by_key(self, key):
        report = self.with_context(active_test=False).search([('key', '=', key)], limit=1)
        if not report:
            raise UserError(_("There is no report with the key '%s'.", key))
        return report

    # ------------------------------------------------------------------ what the screen calls
    @api.model
    def open_by_key(self, key, options=None):
        """The viewer's first call: the report behind a menu, with its data."""
        return self.by_key(key).get_report_data(options)

    def action_send_statements(self, options):
        """Statements of account: one PDF per partner, by email."""
        self.ensure_one()
        self.env.flush_all()                      # the SQL must see pending ORM writes (parent_state, balance)
        self._check_report_access()
        handler = self._handler()
        if not hasattr(handler, 'send_statements'):
            raise UserError(_("This report is not a statement of account."))
        return handler.send_statements(self, options)

    def _check_report_access(self):
        # the superuser (crons, scheduled sends, tests) is always let in
        if not self.env.su and not self.env.user.has_groups('account.group_account_readonly,account.group_account_invoice'):
            raise UserError(_("Financial reports are for the accounting team."))

    def get_report_data(self, options=None):
        """Everything the viewer needs for one look at the report."""
        self.ensure_one()
        self.env.flush_all()                      # the SQL must see pending ORM writes (parent_state, balance)
        self._check_report_access()
        engine = self.env['ebshel.fin.engine']
        handler = self._handler()
        options = engine.normalize(self, options)
        options = handler.adjust_options(self, options)
        columns = handler.columns(self, options)
        lines = handler.lines(self, options, columns)
        currency = engine.currency(options)
        return {
            'report': {
                'id': self.id, 'name': self.name, 'kind': self.kind, 'key': self.key,
                'date_mode': self.date_mode, 'allow_comparison': self.allow_comparison,
                'allow_journals': self.allow_journals, 'allow_partners': self.allow_partners,
                'allow_analytic': self.allow_analytic, 'allow_accounts': self.allow_accounts,
                'allow_hierarchy': self.allow_hierarchy, 'allow_posted_toggle': self.allow_posted_toggle,
                'custom': self.custom, 'description': self.description or '',
                'extra_filters': handler.extra_filters(self, options),
            },
            'options': options,
            'columns': columns,
            'lines': lines,
            'choices': self._choices(options),
            'annotations': self.env['ebshel.fin.report.annotation'].for_report(self),
            'saved_views': self.env['ebshel.fin.report.view'].for_report(self),
            'currency': {'id': currency.id, 'symbol': currency.symbol,
                         'position': currency.position, 'decimals': currency.decimal_places},
            'can_design': self.env.user.has_group('account.group_account_manager'),
            'can_annotate': self.env.user.has_group('account.group_account_invoice'),
        }

    def _choices(self, options):
        Journal = self.env['account.journal'].sudo()
        journals = Journal.search([('company_id', 'in', options['companies'])], order='sequence, id')
        Analytic = self.env['account.analytic.account'].sudo()
        analytic = Analytic.search([('company_id', 'in', options['companies'] + [False])],
                                   order='plan_id, name', limit=500)
        partners = self.env['res.partner'].sudo().browse(options.get('partners') or [])
        return {
            'journals': [{'id': j.id, 'name': j.name, 'code': j.code, 'type': j.type} for j in journals],
            'analytic': [{'id': a.id, 'name': a.display_name, 'plan': a.plan_id.name or ''} for a in analytic],
            'partners': [{'id': p.id, 'name': p.display_name} for p in partners.exists()],
            'presets': PRESETS,
            'comparison_modes': COMPARISON_MODES,
            'account_types': ACCOUNT_TYPES,
        }

    def expand_line(self, options, line_id, offset=0):
        """The children of one folded line, page by page."""
        self.ensure_one()
        self.env.flush_all()                      # the SQL must see pending ORM writes (parent_state, balance)
        self._check_report_access()
        engine = self.env['ebshel.fin.engine']
        handler = self._handler()
        options = handler.adjust_options(self, engine.normalize(self, options))
        columns = handler.columns(self, options)
        return handler.expand(self, options, columns, str(line_id), int(offset or 0))

    def get_drill_action(self, options, line_id, column_key='p0'):
        """The journal items behind one figure, as a list."""
        self.ensure_one()
        self.env.flush_all()                      # the SQL must see pending ORM writes (parent_state, balance)
        self._check_report_access()
        engine = self.env['ebshel.fin.engine']
        handler = self._handler()
        options = handler.adjust_options(self, engine.normalize(self, options))
        columns = handler.columns(self, options)
        target = handler.drill(self, options, columns, str(line_id), column_key)
        if not target:
            return False
        if isinstance(target, dict) and target.get('type'):
            return target
        domain, title = target
        return {
            'type': 'ir.actions.act_window',
            'name': title,
            'res_model': 'account.move.line',
            'view_mode': 'list,pivot,graph',
            'views': [(self.env.ref('account.view_move_line_tree').id, 'list'), (False, 'pivot'), (False, 'graph')],
            'domain': domain,
            'context': {'create': False, 'search_default_group_by_account': 0},
            'target': 'current',
        }

    def explain_cell(self, options, line_id, column_key='p0'):
        """What a figure is made of: by account, by partner, by month."""
        self.ensure_one()
        self.env.flush_all()                      # the SQL must see pending ORM writes (parent_state, balance)
        self._check_report_access()
        engine = self.env['ebshel.fin.engine']
        handler = self._handler()
        options = handler.adjust_options(self, engine.normalize(self, options))
        columns = handler.columns(self, options)
        return handler.explain(self, options, columns, str(line_id), column_key)

    def get_trends(self, options, line_ids):
        """Twelve months of each line, for the sparklines."""
        self.ensure_one()
        self.env.flush_all()                      # the SQL must see pending ORM writes (parent_state, balance)
        self._check_report_access()
        engine = self.env['ebshel.fin.engine']
        handler = self._handler()
        options = handler.adjust_options(self, engine.normalize(self, options))
        return handler.trends(self, options, [str(i) for i in line_ids][:200])

    def export_xlsx(self, options):
        """The report as a workbook, fully unfolded."""
        self.ensure_one()
        self.env.flush_all()                      # the SQL must see pending ORM writes (parent_state, balance)
        self._check_report_access()
        try:
            import xlsxwriter
        except ImportError:                                           # pragma: no cover
            raise UserError(_("The xlsxwriter library is not installed on the server."))
        engine = self.env['ebshel.fin.engine']
        handler = self._handler()
        options = handler.adjust_options(self, engine.normalize(self, options))
        options['unfold_all'] = True
        columns = handler.columns(self, options)
        lines = handler.lines(self, options, columns, for_export=True)
        currency = engine.currency(options)
        output = io.BytesIO()
        book = xlsxwriter.Workbook(output, {'in_memory': True})
        sheet = book.add_worksheet(self.name[:31])
        title = book.add_format({'bold': True, 'font_size': 14})
        muted = book.add_format({'italic': True, 'font_color': '#666666'})
        head = book.add_format({'bold': True, 'bg_color': '#1f3a5f', 'font_color': '#ffffff',
                                'border': 1, 'align': 'center'})
        money = '#,##0.00;[Red]-#,##0.00'
        fmts = {}

        def style(level, bold, kind):
            key = (min(level, 6), bold, kind)
            if key not in fmts:
                spec = {'indent': min(level, 6), 'bold': bold}
                if kind == 'amount':
                    spec['num_format'] = money
                elif kind == 'growth' or kind == 'percent':
                    spec['num_format'] = '0.0"%"'
                if kind == 'text':
                    spec['align'] = 'left'
                fmts[key] = book.add_format(spec)
            return fmts[key]

        sheet.write(0, 0, self.name, title)
        sheet.write(1, 0, self.env['res.company'].browse(options['companies'][0]).name, muted)
        sheet.write(2, 0, self._options_summary(options), muted)
        row = 4
        sheet.write(row, 0, _('Line'), head)
        for c, col in enumerate(columns, start=1):
            sheet.write(row, c, col['label'], head)
        sheet.set_column(0, 0, 48)
        sheet.set_column(1, len(columns), 18)
        sheet.freeze_panes(row + 1, 1)
        for line in lines:
            row += 1
            bold = bool(line.get('bold'))
            sheet.write(row, 0, line['name'], style(line.get('level', 0), bold, 'text'))
            for c, (col, cell) in enumerate(zip(columns, line['columns']), start=1):
                value = cell.get('value')
                kind = 'growth' if col['type'] == 'growth' else (cell.get('display') or 'amount')
                if value is None:
                    sheet.write(row, c, cell.get('text') or '', style(0, bold, 'text'))
                elif kind in ('amount', 'growth', 'percent', 'ratio', 'days', 'count'):
                    sheet.write_number(row, c, value, style(0, bold, kind if kind in ('amount', 'growth', 'percent') else 'text'))
                else:
                    sheet.write(row, c, cell.get('text') or '', style(0, bold, 'text'))
        notes = self.env['ebshel.fin.report.annotation'].for_report(self)
        if notes:
            row += 2
            sheet.write(row, 0, _('Notes'), title)
            for line_key, items in notes.items():
                for note in items:
                    row += 1
                    sheet.write(row, 0, '%s — %s (%s)' % (note['line_name'] or line_key, note['text'], note['user']), muted)
        book.close()
        return {
            'filename': '%s - %s.xlsx' % (self.name, options['date']['to']),
            'content': base64.b64encode(output.getvalue()).decode(),
            'mimetype': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        }

    def get_pdf_action(self, options):
        self.ensure_one()
        self._check_report_access()
        action = self.env.ref('ebshel_account_reports.action_fin_report_pdf').read()[0]
        action['data'] = {'options': options, 'report_id': self.id}
        action['context'] = {'active_ids': [self.id]}
        return action

    def _options_summary(self, options):
        parts = ['%s → %s' % (options['date']['from'], options['date']['to'])]
        if options['comparison']['mode'] != 'none':
            parts.append(_('compared with %s', dict(COMPARISON_MODES)[options['comparison']['mode']].lower()))
        if options.get('journals'):
            parts.append(_('%s journal(s)', len(options['journals'])))
        if options.get('partners'):
            parts.append(_('%s partner(s)', len(options['partners'])))
        if options.get('analytic'):
            parts.append(_('%s analytic account(s)', len(options['analytic'])))
        parts.append(_('posted entries') if options['posted_only'] else _('posted and draft entries'))
        return ' · '.join(parts)

    # ------------------------------------------------------------------ designer
    def action_open_viewer(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.client',
            'tag': 'ebshel_fin_report',
            'name': self.name,
            'params': {'report_key': self.key},
        }

    def action_create_menu(self):
        """A menu entry under Reporting → Custom Reports for a designed report."""
        self.ensure_one()
        parent = self.env.ref('ebshel_account_reports.menu_custom_reports')
        if not self.action_id:
            self.action_id = self.env['ir.actions.client'].sudo().create({
                'name': self.name, 'tag': 'ebshel_fin_report',
                'params': {'report_key': self.key}})
        if not self.menu_id:
            self.menu_id = self.env['ir.ui.menu'].sudo().create({
                'name': self.name, 'parent_id': parent.id,
                'action': 'ir.actions.client,%d' % self.action_id.id,
                'sequence': self.sequence})
        return True

    def write(self, vals):
        res = super().write(vals)
        if 'name' in vals:
            for report in self:
                if report.menu_id:
                    report.menu_id.sudo().name = report.name
                if report.action_id:
                    report.action_id.sudo().name = report.name
        return res

    def unlink(self):
        (self.menu_id | self.env['ir.ui.menu']).sudo().unlink()
        (self.action_id | self.env['ir.actions.client']).sudo().unlink()
        return super().unlink()

    def copy_data(self, default=None):
        vals_list = super().copy_data(default=default)
        for report, vals in zip(self, vals_list):
            vals['key'] = '%s_copy_%s' % (report.key, self.env['ir.sequence'].sudo().next_by_code('ebshel.fin.report.copy') or fields.Datetime.now().strftime('%H%M%S'))
            vals['custom'] = True
            vals.pop('action_id', None)
            vals.pop('menu_id', None)
        return vals_list


class FinReportLine(models.Model):
    _name = 'ebshel.fin.report.line'
    _description = 'Financial report line'
    _order = 'sequence, id'
    _parent_name = 'parent_id'

    report_id = fields.Many2one('ebshel.fin.report', required=True, ondelete='cascade', index=True)
    parent_id = fields.Many2one('ebshel.fin.report.line', ondelete='cascade', index=True,
                                domain="[('report_id', '=', report_id)]")
    child_ids = fields.One2many('ebshel.fin.report.line', 'parent_id')
    sequence = fields.Integer(default=10)
    name = fields.Char(required=True, translate=True)
    code = fields.Char(help="Short name other lines' formulas refer to, e.g. REV.")
    kind = fields.Selection([
        ('sum', 'Sum of accounts'),
        ('formula', 'Formula over other lines'),
        ('header', 'Heading only'),
    ], default='sum', required=True)
    balance_mode = fields.Selection(BALANCE_MODES, default='flow', required=True)
    sign = fields.Selection([('1', 'As booked (debit positive)'), ('-1', 'Reversed (credit positive)')],
                            default='1', required=True,
                            help="Revenue, liabilities and equity are credit balances: reverse "
                                 "them so the report reads them as positive figures.")
    account_prefixes = fields.Char(help="Comma-separated code prefixes, e.g. 4,5")
    exclude_prefixes = fields.Char(help="Prefixes to leave out, e.g. 499")
    account_types = fields.Char(help="Comma-separated account types, e.g. income,income_other")
    account_ids = fields.Many2many('account.account', string='Specific accounts')
    account_tag_ids = fields.Many2many('account.account.tag', string='Account tags')
    domain = fields.Char(help="An extra journal-item domain, e.g. [('partner_id.country_id.code', '=', 'IN')]")
    formula = fields.Char(help="e.g. REV - COGS, or GP / REV * 100")
    display = fields.Selection(DISPLAYS, default='amount', required=True)
    bold = fields.Boolean()
    hidden = fields.Boolean(help="Computed for other lines' formulas, never shown.")
    hide_if_zero = fields.Boolean()
    unfold_accounts = fields.Boolean(string='Unfold to accounts', default=True,
                                     help="Fold open to the accounts that make the figure.")
    level = fields.Integer(compute='_compute_level', recursive=True)
    note = fields.Char()

    @api.depends('parent_id.level')
    def _compute_level(self):
        for line in self:
            line.level = (line.parent_id.level + 1) if line.parent_id else 0

    @api.constrains('formula', 'kind', 'code', 'report_id')
    def _check_formula(self):
        for line in self.filtered(lambda l: l.kind == 'formula'):
            if not line.formula:
                raise ValidationError(_("A formula line needs a formula: %s", line.name))
            try:
                names = formula_names(line.formula)
            except SyntaxError as exc:
                raise ValidationError(_("The formula of '%s' does not parse: %s", line.name, exc)) from exc
            codes = set(line.report_id.line_ids.mapped('code')) - {False, ''}
            unknown = [n for n in names if n not in codes and n != 'DAYS']
            # Shipped reports load line by line, a formula before the lines it
            # names: the check waits for the designer, where order is no excuse.
            if unknown and not self.env.context.get('install_mode'):
                raise ValidationError(_("'%s' refers to lines that do not exist: %s",
                                        line.name, ', '.join(unknown)))
            if line.code and line.code in names:
                raise ValidationError(_("'%s' refers to itself.", line.name))

    @api.constrains('code', 'report_id')
    def _check_code(self):
        for line in self.filtered('code'):
            if not re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', line.code):
                raise ValidationError(_("A line code is letters, digits and underscores: %s", line.code))
            twins = line.report_id.line_ids.filtered(lambda l: l.code == line.code and l.id != line.id)
            if twins:
                raise ValidationError(_("The code %s is used twice in %s.", line.code, line.report_id.name))

    @api.constrains('parent_id')
    def _check_parent(self):
        if self._has_cycle():
            raise ValidationError(_("A line cannot be its own ancestor."))
