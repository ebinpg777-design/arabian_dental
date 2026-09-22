# -*- coding: utf-8 -*-
from odoo import fields, models, tools


class LabOutstandingReport(models.Model):
    """Doctor-wise / invoice-wise outstanding and monthly payment monitoring (gap 23).

    A SQL view over the open receivable journal items rather than a computed
    model: the lab wants to pivot thousands of open invoices by clinic, by ageing
    bucket and by month, and that has to be one query, not one per row.
    """
    _name = 'lab.outstanding.report'
    _description = 'Doctor-wise / Invoice-wise Outstanding'
    _auto = False
    _order = 'date_due, partner_id'
    _rec_name = 'move_name'

    move_id = fields.Many2one('account.move', string='Invoice', readonly=True)
    move_name = fields.Char('Invoice Number', readonly=True)
    move_line_id = fields.Many2one('account.move.line', string='Journal Item', readonly=True)
    partner_id = fields.Many2one('res.partner', string='Clinic', readonly=True)
    commercial_partner_id = fields.Many2one(
        'res.partner', string='Invoicing Entity', readonly=True)
    user_id = fields.Many2one('res.users', string='Salesperson', readonly=True)
    team_id = fields.Many2one('crm.team', string='Sales Team', readonly=True)
    journal_id = fields.Many2one('account.journal', string='Journal', readonly=True)
    company_id = fields.Many2one('res.company', readonly=True)
    currency_id = fields.Many2one('res.currency', readonly=True)

    date_invoice = fields.Date('Invoice Date', readonly=True)
    date_due = fields.Date('Due Date', readonly=True)
    invoice_month = fields.Date('Invoice Month', readonly=True)
    days_overdue = fields.Integer(readonly=True)
    age_bucket = fields.Selection(
        [('not_due', 'Not Due'), ('b_0_30', '0-30 Days'), ('b_31_60', '31-60 Days'),
         ('b_61_90', '61-90 Days'), ('b_90_plus', '90+ Days')],
        string='Ageing', readonly=True)

    amount_total = fields.Monetary(
        'Invoice Total', currency_field='currency_id', readonly=True)
    amount_residual = fields.Monetary(
        'Outstanding', currency_field='currency_id', readonly=True)
    amount_paid = fields.Monetary(
        'Paid', currency_field='currency_id', readonly=True)

    def _search(self, domain, offset=0, limit=None, order=None, **kwargs):
        """Read through the ORM cache before querying the view.

        This is a SQL view over account_move_line, and PostgreSQL only sees what
        has been written. Odoo's flush machinery cannot know that, because a view
        model declares no dependency on the tables underneath it — so an invoice
        or a reconciliation made earlier in the same transaction would simply be
        missing from the report. The EOD report reads this model straight after
        clearing cheques, which is exactly that situation.
        """
        self.env['account.move'].flush_model()
        self.env['account.move.line'].flush_model()
        return super()._search(domain, offset=offset, limit=limit, order=order, **kwargs)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(f"""
            CREATE OR REPLACE VIEW {self._table} AS (
                SELECT
                    aml.id                                  AS id,
                    aml.id                                  AS move_line_id,
                    am.id                                   AS move_id,
                    am.name                                 AS move_name,
                    am.partner_id                           AS partner_id,
                    am.commercial_partner_id                AS commercial_partner_id,
                    am.invoice_user_id                      AS user_id,
                    am.team_id                              AS team_id,
                    am.journal_id                           AS journal_id,
                    am.company_id                           AS company_id,
                    am.currency_id                          AS currency_id,
                    am.invoice_date                         AS date_invoice,
                    COALESCE(aml.date_maturity, am.invoice_date) AS date_due,
                    DATE_TRUNC('month', am.invoice_date)::date   AS invoice_month,
                    GREATEST(0, (CURRENT_DATE
                        - COALESCE(aml.date_maturity, am.invoice_date))::int) AS days_overdue,
                    CASE
                        WHEN COALESCE(aml.date_maturity, am.invoice_date) >= CURRENT_DATE
                            THEN 'not_due'
                        WHEN (CURRENT_DATE
                            - COALESCE(aml.date_maturity, am.invoice_date))::int <= 30
                            THEN 'b_0_30'
                        WHEN (CURRENT_DATE
                            - COALESCE(aml.date_maturity, am.invoice_date))::int <= 60
                            THEN 'b_31_60'
                        WHEN (CURRENT_DATE
                            - COALESCE(aml.date_maturity, am.invoice_date))::int <= 90
                            THEN 'b_61_90'
                        ELSE 'b_90_plus'
                    END                                     AS age_bucket,
                    (aml.debit - aml.credit)                AS amount_total,
                    aml.amount_residual                     AS amount_residual,
                    ((aml.debit - aml.credit) - aml.amount_residual) AS amount_paid
                FROM account_move_line aml
                JOIN account_move am ON am.id = aml.move_id
                JOIN account_account aa ON aa.id = aml.account_id
                WHERE am.move_type IN ('out_invoice', 'out_refund')
                  AND am.state = 'posted'
                  AND aa.account_type = 'asset_receivable'
                  AND aml.parent_state = 'posted'
                  AND aml.reconciled IS NOT TRUE
                  AND aml.amount_residual != 0
            )
        """)
