# -*- coding: utf-8 -*-
from odoo import api, fields, models, tools

DUE_AFTER, OVERDUE_AFTER, AT_RISK_AFTER = 30, 60, 90


class LabCoverage(models.Model):
    """Which clinics are we quietly losing?

    A field force reports on what it did — visits made, cases collected. The number that
    predicts revenue is the one nobody logs: the clinic not seen for eleven weeks that
    has stopped sending work. This inverts the reporting so the gap is the record.

    A SQL view, not computed fields: a lab pivots hundreds of clinics by beat and by
    coverage band, and that has to be one query.
    """
    _name = 'lab.coverage'
    _description = 'Clinic Coverage'
    _auto = False
    _order = 'days_since desc nulls first, value_90d desc'
    _rec_name = 'partner_id'

    partner_id = fields.Many2one('res.partner', string='Clinic', readonly=True)
    beat_id = fields.Many2one('lab.beat', string='Beat', readonly=True)
    user_id = fields.Many2one('res.users', string='Last Visited By', readonly=True)
    company_id = fields.Many2one('res.company', readonly=True)
    currency_id = fields.Many2one('res.currency', readonly=True)

    last_visit = fields.Date(readonly=True)
    days_since = fields.Integer('Days Since Visit', readonly=True)
    visits_90d = fields.Integer('Visits (90d)', readonly=True)
    cases_90d = fields.Integer('Cases (90d)', readonly=True)
    value_90d = fields.Monetary('Case Value (90d)', currency_field='currency_id',
                                readonly=True)
    value_prev_90d = fields.Monetary('Previous 90d', currency_field='currency_id',
                                     readonly=True)
    declining = fields.Boolean(readonly=True)

    # Ordering behaviour, which is the half of "coverage" this lab actually has.
    # lab.visit holds 12 rows; sale_order holds 26,309, so a coverage report that
    # only knows about visits calls 6,855 clinics "Never Visited" and the manager's
    # rescue alert — which reads visit status — matches nothing at all. These say the
    # same thing from the ledger instead. (client, 2026-08-29)
    last_order = fields.Date('Last Order', readonly=True)
    days_since_order = fields.Integer('Days Since Order', readonly=True)
    value_drop = fields.Monetary('Value Lost', currency_field='currency_id',
                                 readonly=True,
                                 help="How much less this clinic has ordered in the "
                                      "last 90 days than in the 90 before it.")
    order_state = fields.Selection(
        [('stopped', 'Stopped Ordering'), ('slipping', 'Ordering Less'),
         ('steady', 'Still Ordering'), ('none', 'No Orders')],
        string='Ordering', readonly=True,
        help="Stopped: ordered in the previous quarter and nothing since. "
             "Ordering less: still ordering, but under half what they were.")

    status = fields.Selection(
        [('never', 'Never Visited'), ('current', 'Current'), ('due', 'Due'),
         ('overdue', 'Overdue'), ('at_risk', 'At Risk')],
        string='Coverage', readonly=True)

    def _search(self, domain, offset=0, limit=None, order=None, **kwargs):
        # A SQL view only sees what PostgreSQL has been told, and a view model declares
        # no dependency for Odoo's flush machinery to follow.
        self.env['lab.visit'].flush_model()
        self.env['sale.order'].flush_model()
        self.env['res.partner'].flush_model()
        return super()._search(domain, offset=offset, limit=limit, order=order, **kwargs)

    @api.model
    def _bands(self):
        """The coverage bands, from settings. int() is not cosmetic — these values are
        interpolated into SQL, and a string from ir.config_parameter is user input."""
        get = self.env['ir.config_parameter'].sudo().get_param
        def band(key, fallback):
            try:
                return max(1, int(float(get('lab_fieldwork.%s' % key) or fallback)))
            except (TypeError, ValueError):
                return fallback
        return (band('due_days', DUE_AFTER), band('overdue_days', OVERDUE_AFTER),
                band('at_risk_days', AT_RISK_AFTER))

    def init(self):
        due, overdue, at_risk = self._bands()
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute("""
            CREATE OR REPLACE VIEW %s AS (
                WITH v AS (
                    SELECT partner_id,
                           max(date) AS last_visit,
                           count(*) FILTER (
                               WHERE date >= CURRENT_DATE - 90) AS visits_90d
                    FROM lab_visit WHERE state = 'done'
                    GROUP BY partner_id
                ),
                last_by AS (
                    SELECT DISTINCT ON (partner_id) partner_id, user_id, beat_id
                    FROM lab_visit WHERE state = 'done'
                    ORDER BY partner_id, date DESC, id DESC
                ),
                o AS (
                    SELECT s.partner_id,
                           count(*) FILTER (
                               WHERE s.date_order >= now() - interval '90 days') AS cases_90d,
                           COALESCE(sum(s.amount_untaxed) FILTER (
                               WHERE s.date_order >= now() - interval '90 days'), 0) AS v90,
                           COALESCE(sum(s.amount_untaxed) FILTER (
                               WHERE s.date_order >= now() - interval '180 days'
                                 AND s.date_order < now() - interval '90 days'), 0) AS vprev
                           , max(s.date_order) AS last_order
                    FROM sale_order s
                    WHERE s.state NOT IN ('draft', 'sent', 'cancel')
                    GROUP BY s.partner_id
                )
                SELECT p.id AS id, p.id AS partner_id,
                       lb.beat_id, lb.user_id,
                       p.company_id, c.currency_id,
                       v.last_visit,
                       CASE WHEN v.last_visit IS NULL THEN NULL
                            ELSE (CURRENT_DATE - v.last_visit) END AS days_since,
                       COALESCE(v.visits_90d, 0) AS visits_90d,
                       COALESCE(o.cases_90d, 0)  AS cases_90d,
                       COALESCE(o.v90, 0)        AS value_90d,
                       COALESCE(o.vprev, 0)      AS value_prev_90d,
                       (COALESCE(o.v90, 0) < COALESCE(o.vprev, 0)) AS declining,
                       o.last_order::date AS last_order,
                       CASE WHEN o.last_order IS NULL THEN NULL
                            ELSE (CURRENT_DATE - o.last_order::date) END
                            AS days_since_order,
                       GREATEST(COALESCE(o.vprev, 0) - COALESCE(o.v90, 0), 0)
                            AS value_drop,
                       CASE
                           WHEN COALESCE(o.vprev, 0) > 0 AND COALESCE(o.v90, 0) = 0
                                THEN 'stopped'
                           WHEN COALESCE(o.v90, 0) > 0
                                AND COALESCE(o.v90, 0) < COALESCE(o.vprev, 0) / 2
                                THEN 'slipping'
                           WHEN COALESCE(o.v90, 0) > 0 THEN 'steady'
                           ELSE 'none'
                       END AS order_state,
                       CASE
                           WHEN v.last_visit IS NULL THEN 'never'
                           WHEN (CURRENT_DATE - v.last_visit) > %s THEN 'at_risk'
                           WHEN (CURRENT_DATE - v.last_visit) > %s THEN 'overdue'
                           WHEN (CURRENT_DATE - v.last_visit) > %s THEN 'due'
                           ELSE 'current'
                       END AS status
                FROM res_partner p
                JOIN res_company c
                  ON c.id = COALESCE(p.company_id, (SELECT min(id) FROM res_company))
                LEFT JOIN v       ON v.partner_id  = p.id
                LEFT JOIN last_by lb ON lb.partner_id = p.id
                LEFT JOIN o       ON o.partner_id  = p.id
                -- Clinics, not the address book. Without this the report is 6,862
                -- partners including vendors, staff and contacts under a practice.
                -- `is_clinic` is filled in from the order ledger by the 19.0.3.22.0
                -- migration and kept true on every order confirmation.
                WHERE p.active = TRUE AND p.is_clinic = TRUE
            )
        """ % (self._table, at_risk, overdue, due))
