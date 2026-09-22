# -*- coding: utf-8 -*-
from odoo import fields, models, tools


class LabPerformance(models.Model):
    """One row per executive per month: effort, output and discipline together.

    Separate reports answer these separately — visits in one, travel in another,
    targets in a third. The interesting cases only appear in the combination: many
    visits and few cases is a coaching problem, many cases and poor location
    compliance is a reporting-integrity problem, and high travel with few visits is
    neither. A manager reviewing a person needs them on one line.

    Not stored with a cron: derived data that is stored goes stale the moment somebody
    back-dates a visit.
    """
    _name = 'lab.performance'
    _description = 'Field Performance'
    _auto = False
    _order = 'month desc, case_value desc'
    _rec_name = 'user_id'

    user_id = fields.Many2one('res.users', string='Executive', readonly=True)
    team_id = fields.Many2one('crm.team', string='Sales Team', readonly=True)
    beat_id = fields.Many2one('lab.beat', string='Beat', readonly=True)
    month = fields.Date(readonly=True)
    company_id = fields.Many2one('res.company', readonly=True)
    currency_id = fields.Many2one('res.currency', readonly=True)

    # effort
    planned = fields.Integer('Planned', readonly=True)
    done = fields.Integer('Visits Done', readonly=True)
    clinics = fields.Integer('Clinics Covered', readonly=True)
    completion = fields.Float('Completion %', readonly=True, aggregator='avg')

    # output
    cases = fields.Integer('Cases', readonly=True)
    case_value = fields.Monetary('Case Value', currency_field='currency_id', readonly=True)
    collected = fields.Monetary('Collected', currency_field='currency_id', readonly=True)
    cases_per_visit = fields.Float('Cases / Visit', readonly=True, aggregator='avg')

    # discipline
    at_clinic = fields.Integer('Checked in at Clinic', readonly=True)
    away = fields.Integer('Checked in Away', readonly=True)
    no_fix = fields.Integer('No Location', readonly=True)
    location_ok = fields.Float('Location %', readonly=True, aggregator='avg')

    # cost
    distance = fields.Float('Distance (km)', readonly=True)
    travel_cost = fields.Monetary('Travel', currency_field='currency_id', readonly=True)
    cost_per_case = fields.Monetary('Travel / Case', currency_field='currency_id',
                                    readonly=True, aggregator='avg')

    def _search(self, domain, offset=0, limit=None, order=None, **kwargs):
        self.env['lab.visit'].flush_model()
        self.env['lab.trip'].flush_model()
        return super()._search(domain, offset=offset, limit=limit, order=order, **kwargs)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute("""
            CREATE OR REPLACE VIEW %s AS (
                WITH v AS (
                    SELECT v.user_id, v.company_id,
                           date_trunc('month', v.date)::date AS month,
                           min(v.beat_id)                                   AS beat_id,
                           count(*)                                         AS planned,
                           count(*) FILTER (WHERE v.state = 'done')          AS done,
                           count(DISTINCT v.partner_id) FILTER (
                               WHERE v.state = 'done')                       AS clinics,
                           COALESCE(sum(v.order_count) FILTER (
                               WHERE v.state = 'done'), 0)                   AS cases,
                           COALESCE(sum(v.order_value) FILTER (
                               WHERE v.state = 'done'), 0)                   AS case_value,
                           COALESCE(sum(v.collected) FILTER (
                               WHERE v.state = 'done'), 0)                   AS collected,
                           count(*) FILTER (
                               WHERE v.state = 'done' AND v.gps_state = 'ok')  AS at_clinic,
                           count(*) FILTER (
                               WHERE v.state = 'done' AND v.gps_state = 'far') AS away,
                           count(*) FILTER (
                               WHERE v.state = 'done' AND v.gps_state = 'nofix') AS no_fix
                    FROM lab_visit v
                    WHERE v.state != 'cancel'
                    GROUP BY v.user_id, v.company_id, date_trunc('month', v.date)
                ),
                cc AS (
                    -- Collected at a door with no visit behind it. The same
                    -- money, so the same column. (client, 2026-09-12)
                    SELECT cc.user_id, cc.company_id,
                           date_trunc('month', cc.date)::date AS month,
                           COALESCE(sum(cc.amount), 0) AS collected
                    FROM lab_cash_collection cc
                    GROUP BY cc.user_id, cc.company_id, date_trunc('month', cc.date)
                ),
                t AS (
                    SELECT t.user_id, t.company_id,
                           date_trunc('month', t.date)::date AS month,
                           COALESCE(sum(t.distance), 0) AS distance,
                           COALESCE(sum(t.amount), 0)   AS travel_cost
                    FROM lab_trip t WHERE t.state != 'cancel'
                    GROUP BY t.user_id, t.company_id, date_trunc('month', t.date)
                ),
                keys AS (
                    SELECT user_id, company_id, month FROM v
                    UNION
                    SELECT user_id, company_id, month FROM t
                    UNION
                    SELECT user_id, company_id, month FROM cc
                )
                SELECT row_number() OVER (ORDER BY k.month, k.user_id) AS id,
                       k.user_id, k.company_id, k.month,
                       v.beat_id,
                       (SELECT st.id FROM crm_team st
                         WHERE st.user_id = k.user_id LIMIT 1)          AS team_id,
                       c.currency_id,
                       COALESCE(v.planned, 0)    AS planned,
                       COALESCE(v.done, 0)       AS done,
                       COALESCE(v.clinics, 0)    AS clinics,
                       CASE WHEN COALESCE(v.planned, 0) = 0 THEN 0.0
                            ELSE v.done::numeric / v.planned * 100 END  AS completion,
                       COALESCE(v.cases, 0)      AS cases,
                       COALESCE(v.case_value, 0) AS case_value,
                       COALESCE(v.collected, 0)
                           + COALESCE(cc.collected, 0)  AS collected,
                       CASE WHEN COALESCE(v.done, 0) = 0 THEN 0.0
                            ELSE v.cases::numeric / v.done END          AS cases_per_visit,
                       COALESCE(v.at_clinic, 0)  AS at_clinic,
                       COALESCE(v.away, 0)       AS away,
                       COALESCE(v.no_fix, 0)     AS no_fix,
                       -- Visits with no location at all are excluded from the ratio: a
                       -- refused permission is a different failure from checking in
                       -- somewhere else, and averaging them hides both.
                       CASE WHEN COALESCE(v.at_clinic, 0) + COALESCE(v.away, 0) = 0
                            THEN 0.0
                            ELSE v.at_clinic::numeric / (v.at_clinic + v.away) * 100 END
                                                                        AS location_ok,
                       COALESCE(t.distance, 0)   AS distance,
                       COALESCE(t.travel_cost, 0) AS travel_cost,
                       CASE WHEN COALESCE(v.cases, 0) = 0 THEN 0.0
                            ELSE COALESCE(t.travel_cost, 0) / v.cases END AS cost_per_case
                FROM keys k
                LEFT JOIN v ON v.user_id = k.user_id AND v.month = k.month
                           AND v.company_id IS NOT DISTINCT FROM k.company_id
                LEFT JOIN t ON t.user_id = k.user_id AND t.month = k.month
                           AND t.company_id IS NOT DISTINCT FROM k.company_id
                LEFT JOIN cc ON cc.user_id = k.user_id AND cc.month = k.month
                           AND cc.company_id IS NOT DISTINCT FROM k.company_id
                JOIN res_company c
                  ON c.id = COALESCE(k.company_id, (SELECT min(id) FROM res_company))
            )
        """ % self._table)
