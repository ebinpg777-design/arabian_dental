# -*- coding: utf-8 -*-
"""The statements on screen, from the same filters the wizard prints with.

Every method takes the partners and the report `data` the PDF is rendered from, so
what is looked at, printed, exported and e-mailed from this screen can never
disagree with each other or with the wizard. The screen carries that data in its
own action params, so it outlives the transient wizard and survives a reload.
(client, 2026-09-30)
"""
import base64

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .statement_engine import STATEMENT_TYPES


class PartnerStatementView(models.AbstractModel):
    _inherit = 'epg.partner.statement'

    # ------------------------------------------------------------------ helpers
    @api.model
    def _view_guard(self):
        # whoever may run the wizard may look at what it would print
        self.env['epg.partner.statement.wizard'].check_access('create')

    @api.model
    def _view_partners(self, partner_ids):
        """The partners in the order they were handed over (route order, from the wizard)."""
        found = self.env['res.partner'].browse([int(i) for i in partner_ids or []]).exists()
        by_id = {p.id: p for p in found}
        return self.env['res.partner'].concat(*[by_id[i] for i in partner_ids if i in by_id]) \
            if found else found

    @api.model
    def _view_currency(self, currency):
        return {'id': currency.id, 'name': currency.name, 'symbol': currency.symbol,
                'position': currency.position, 'digits': currency.decimal_places}

    @api.model
    def _view_block(self, block, with_lines):
        out = {
            'currency': self._view_currency(block['currency']),
            'opening': block['opening'], 'closing': block['closing'],
            'total_debit': block['total_debit'], 'total_credit': block['total_credit'],
            'overdue': block['overdue_amount'], 'open_count': block['open_count'],
            'credits_open': block['credits_open'],
            'ageing': [[label, amount, late] for label, amount, late in block['ageing_rows']],
            'ageing_total': block['ageing_total'], 'line_count': len(block['lines']),
        }
        if with_lines:
            out['lines'] = [{
                'date': fields.Date.to_string(line['doc_date']),
                'move': line['move'], 'entry': line['entry'], 'move_id': line.get('move_id'),
                'ref': line['ref'], 'journal': line['journal'], 'kind': line['kind'],
                'name': line['name'], 'patient': line['patient'],
                'due': fields.Date.to_string(line['due']), 'days_overdue': line['days_overdue'],
                'debit': line['debit'], 'credit': line['credit'], 'balance': line['balance'],
                'residual': line['residual'], 'paid': bool(line['paid']),
            } for line in block['lines']]
        return out

    @api.model
    def _view_route(self, partner):
        if 'team_id' not in partner._fields:
            return ''
        return (partner.team_id or partner.commercial_partner_id.team_id).name or ''

    # ------------------------------------------------------------------ reading
    @api.model
    def view_summary(self, partner_ids, data):
        """Every selected partner's totals - no lines, so a long run stays light."""
        self._view_guard()
        options = self.normalize_options(data)
        partners = self._view_partners(partner_ids)
        statements = self.compute(partners, options)
        rows, totals = [], {}
        for partner in partners:
            st = statements[partner.id]
            blocks = [self._view_block(b, False) for b in st['blocks']] if st['has_data'] else []
            rows.append({
                'id': partner.id, 'name': partner.display_name, 'route': self._view_route(partner),
                'email': partner.email or '', 'city': partner.city or '',
                'has_data': st['has_data'], 'blocks': blocks,
            })
            for b in blocks:
                t = totals.setdefault(b['currency']['id'], {
                    'currency': b['currency'], 'opening': 0.0, 'debit': 0.0, 'credit': 0.0,
                    'closing': 0.0, 'overdue': 0.0, 'overdue_partners': 0})
                t['opening'] += b['opening']
                t['debit'] += b['total_debit']
                t['credit'] += b['total_credit']
                t['closing'] += b['closing']
                t['overdue'] += b['overdue']
                t['overdue_partners'] += 1 if b['overdue'] > 0.005 else 0
        return {
            'type_label': dict(STATEMENT_TYPES)[options['statement_type']],
            'date_from': fields.Date.to_string(options['date_from']),
            'date_to': fields.Date.to_string(options['date_to']),
            'open_items_only': options['open_items_only'],
            'show_ageing': bool(options['show_ageing']),
            'entered_from': fields.Datetime.to_string(options['created_from']) if options.get('created_from') else False,
            'entered_to': fields.Datetime.to_string(options['created_to']) if options.get('created_to') else False,
            'labels': self.labels(options['statement_type']),
            'company': options['company'].name,
            'partners': rows,
            'totals': list(totals.values()),
        }

    @api.model
    def view_statement(self, partner_id, data):
        """One partner's statement, line by line."""
        self._view_guard()
        options = self.normalize_options(data)
        partner = self.env['res.partner'].browse(int(partner_id)).exists()
        if not partner:
            raise UserError(_("This partner no longer exists."))
        st = self.compute(partner, options)[partner.id]
        return {
            'id': partner.id, 'name': partner.display_name, 'route': self._view_route(partner),
            'email': partner.email or '', 'phone': partner.phone or '',
            'address': st['address'], 'identifiers': st['identifiers'],
            'has_data': st['has_data'],
            'blocks': [self._view_block(b, True) for b in st['blocks']] if st['has_data'] else [],
        }

    # ------------------------------------------------------------------ acting
    @api.model
    def view_print(self, partner_ids, data):
        """The same PDF the wizard prints, for these partners."""
        self._view_guard()
        partners = self._view_partners(partner_ids)
        if not partners:
            raise UserError(_("No partner to print."))
        options = self.normalize_options(data)
        return self.env.ref('epg_partner_statement.action_report_partner_statement').report_action(
            partners, data={
                'ids': partners.ids, 'statement_type': options['statement_type'],
                'date_from': str(options['date_from']), 'date_to': str(options['date_to']),
                'company_id': options['company'].id, 'open_items_only': options['open_items_only'],
                'show_ageing': options['show_ageing'], **self.entered_data(options)},
            config=False)

    @api.model
    def view_xlsx(self, partner_ids, data):
        self._view_guard()
        partners = self._view_partners(partner_ids)
        if not partners:
            raise UserError(_("No partner to export."))
        options = self.normalize_options(data)
        content = self.render_xlsx(partners, options)
        name = _('Statements %(start)s to %(end)s.xlsx', start=options['date_from'], end=options['date_to'])
        if len(partners) == 1:
            name = _('Statement %(name)s %(start)s to %(end)s.xlsx', name=partners.name,
                     start=options['date_from'], end=options['date_to'])
        attachment = self.env['ir.attachment'].create({
            'name': name, 'type': 'binary', 'datas': base64.b64encode(content),
            'mimetype': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        })
        return {'type': 'ir.actions.act_url', 'target': 'self',
                'url': '/web/content/%s?download=true' % attachment.id}

    @api.model
    def view_email(self, partner_ids, data):
        self._view_guard()
        partners = self._view_partners(partner_ids)
        options = self.normalize_options(data)
        sent = self.send_by_email(partners, options)
        skipped = partners - sent
        return {'sent': len(sent), 'skipped': skipped.mapped('display_name')[:8],
                'skipped_count': len(skipped)}
