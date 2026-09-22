# -*- coding: utf-8 -*-
"""Who opened a new door this month.

A field executive is judged on two things: the work they bring back, and the clinics
they open. The lab could see the first everywhere and the second nowhere — a new doctor
appeared as one more row in a 6,763-line contact list and nobody could say who brought
them in, or how many the round produced last month.

The month is read from `create_date`, which is when the clinic was registered — the act
the executive is actually credited for. Whether that clinic has since sent any work is a
separate fact and is shown beside it, because a door opened and never used is not the
same achievement as one that is now sending cases every week.
"""
from datetime import date

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models

from .local_day import local_midnight_utc, local_now, tz_name

# A screenful. The count beside the heading always states the real total.
CLINIC_ROWS = 60


class LabNewClinics(models.AbstractModel):
    _name = 'lab.new.clinics'
    _description = 'New Clinics This Month'

    # ------------------------------------------------------------------ scoping
    @api.model
    def _viewer_routes(self):
        """The routes this viewer may see, or None for "everything".

        A manager, an administrator or whoever runs the lab sees the whole company; an
        executive sees the routes they work. Same shape as the collections screen, and
        for the same reason: one method, scoped by who is asking, rather than two
        screens that drift apart.
        """
        user = self.env.user
        wide = ('lab_fieldwork.group_fieldwork_manager',
                'lab_fieldwork.group_fieldwork_admin',
                'lab_ceo_dashboard.group_lab_executive',
                'base.group_system')
        for group in wide:
            if self.env.ref(group, raise_if_not_found=False) and user.has_group(group):
                return None
        if user.has_group('lab_fieldwork.group_fieldwork_executive'):
            return user.fw_route_ids.ids or [0]
        return None

    @api.model
    def _month_bounds(self, month=None):
        """(first day, first day of next month) for a 'YYYY-MM' string."""
        today = local_now(self.env).date()
        if month:
            try:
                year, mon = (int(part) for part in str(month).split('-')[:2])
                start = date(year, mon, 1)
            except (TypeError, ValueError):
                start = today.replace(day=1)
        else:
            start = today.replace(day=1)
        # Never the future: a month that has not begun has no clinics in it, and the
        # stepper would otherwise walk forward for ever.
        if start > today.replace(day=1):
            start = today.replace(day=1)
        return start, start + relativedelta(months=1)

    @api.model
    def _clinic_domain(self, start, end):
        """Doors opened in [start, end): top-level partners on a route.

        The days are the lab's local days - create_date is UTC, and comparing
        it with a naive midnight moved every clinic opened before 05:30 into
        the day before. A doctor added under a clinic is a contact, not a new
        door, so children are left out. (2026-09-15)
        """
        return [('create_date', '>=', local_midnight_utc(self.env, start)),
                ('create_date', '<', local_midnight_utc(self.env, end)),
                ('team_id', '!=', False),
                ('parent_id', '=', False)]

    # ------------------------------------------------------------------ the data
    @api.model
    def count_new_clinics(self, month=None):
        """Just the number, for the widgets that show it as one figure.

        get_new_clinics() builds the whole screen — four grouped reads — and both
        My Day and the Control Tower load on every page view. They only want the
        count, so they do not pay for the rest.
        """
        start, end = self._month_bounds(month)
        routes = self._viewer_routes()
        domain = self._clinic_domain(start, end)
        if routes is not None:
            domain.append(('team_id', 'in', routes))
        return self.env['res.partner'].sudo().search_count(domain)

    # @api.model: the screen calls this with [month], and without it the month
    # arrived as record ids and was dropped - the month arrows always reloaded
    # the current month. (2026-09-15)
    @api.model
    def get_new_clinics(self, month=None):
        start, end = self._month_bounds(month)
        routes = self._viewer_routes()

        domain = self._clinic_domain(start, end)
        if routes is not None:
            domain.append(('team_id', 'in', routes))

        Partner = self.env['res.partner'].sudo()
        total = Partner.search_count(domain)
        clinics = Partner.search(domain, order='create_date desc, id desc',
                                 limit=CLINIC_ROWS)

        # What each of them has sent since. One grouped read, not one per clinic: a
        # sixty-row list would otherwise cost sixty searches.
        orders = dict(self.env['sale.order'].sudo()._read_group(
            [('partner_id', 'in', clinics.ids), ('state', 'in', ('sale', 'done'))],
            ['partner_id'], ['__count']))
        value = dict(self.env['sale.order'].sudo()._read_group(
            [('partner_id', 'in', clinics.ids), ('state', 'in', ('sale', 'done'))],
            ['partner_id'], ['amount_total:sum']))
        first = {}
        for partner, earliest in self.env['sale.order'].sudo()._read_group(
                [('partner_id', 'in', clinics.ids), ('state', 'in', ('sale', 'done'))],
                ['partner_id'], ['date_order:min']):
            first[partner.id] = earliest

        local = self.with_context(tz=tz_name(self.env))
        rows = []
        for clinic in clinics:
            started = first.get(clinic.id)
            rows.append({
                'id': clinic.id,
                'name': clinic.display_name,
                'city': clinic.city or '',
                'phone': clinic.phone or '',
                'route': clinic.team_id.name or '',
                'route_id': clinic.team_id.id,
                'opened': fields.Date.to_string(fields.Datetime.context_timestamp(
                    local, clinic.create_date).date()),
                'by': clinic.create_uid.name or '',
                'orders': orders.get(clinic, 0),
                'value': value.get(clinic, 0.0),
                # A door opened and never used is not the same as one now sending work.
                'first_order': fields.Date.to_string(
                    fields.Date.to_date(started)) if started else '',
                'ordering': bool(orders.get(clinic, 0)),
            })

        return {
            'month': start.strftime('%Y-%m'),
            'month_label': start.strftime('%B %Y'),
            'is_this_month': start == local_now(self.env).date().replace(day=1),
            'prev_month': (start - relativedelta(months=1)).strftime('%Y-%m'),
            'next_month': (start + relativedelta(months=1)).strftime('%Y-%m'),
            'total': total,
            'shown': len(rows),
            'ordering': sum(1 for r in rows if r['ordering']),
            'rows': rows,
            'by_route': self._by_route(domain),
            'trend': self._trend(start, routes),
            'scoped': routes is not None,
            'currency_id': self.env.company.currency_id.id,
        }

    @api.model
    def _by_route(self, domain):
        """Which rounds are opening doors, biggest first."""
        groups = self.env['res.partner'].sudo()._read_group(
            domain, ['team_id'], ['__count'])
        rows = [{'name': team.name or _('No route'), 'id': team.id, 'count': count}
                for team, count in groups]
        rows.sort(key=lambda r: -r['count'])
        peak = rows[0]['count'] if rows else 0
        for row in rows:
            row['pct'] = round(row['count'] / peak * 100, 1) if peak else 0.0
        return rows

    @api.model
    def _trend(self, start, routes, months=6):
        """The last few months side by side, so one month is read in context."""
        out = []
        for back in range(months - 1, -1, -1):
            first_day = start - relativedelta(months=back)
            domain = self._clinic_domain(first_day,
                                         first_day + relativedelta(months=1))
            if routes is not None:
                domain.append(('team_id', 'in', routes))
            out.append({'label': first_day.strftime('%b'),
                        'month': first_day.strftime('%Y-%m'),
                        'count': self.env['res.partner'].sudo().search_count(domain)})
        peak = max((row['count'] for row in out), default=0)
        for row in out:
            row['pct'] = round(row['count'] / peak * 100, 1) if peak else 0.0
        return out

    @api.model
    def open_clinics(self, month=None, route_id=None):
        """The same set, in a list the reader can sort and export."""
        start, end = self._month_bounds(month)
        routes = self._viewer_routes()
        domain = self._clinic_domain(start, end)
        if route_id:
            domain.append(('team_id', '=', int(route_id)))
        elif routes is not None:
            domain.append(('team_id', 'in', routes))
        return {
            'type': 'ir.actions.act_window',
            'name': _('New clinics — %s', start.strftime('%B %Y')),
            'res_model': 'res.partner',
            'view_mode': 'list,kanban,form',
            'views': [(False, 'list'), (False, 'kanban'), (False, 'form')],
            'domain': domain,
            'context': {'create': False},
        }
