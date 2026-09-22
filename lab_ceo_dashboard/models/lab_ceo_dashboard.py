# -*- coding: utf-8 -*-

from datetime import timedelta

import logging

from odoo import _, api, fields, models
from odoo.exceptions import AccessError

_logger = logging.getLogger(__name__)


class LabCeoDashboard(models.AbstractModel):
    """The Management hub: one launcher per management screen, each with a live
    figure. The figure panels this board once carried (revenue, cash, top clinics,
    alerts) were asked to go; the screens behind the launchers answer those
    questions with their own comparisons. (client, 2026-08-28)"""
    _name = 'lab.ceo.dashboard'
    _description = 'Executive (CEO) Dashboard Data'

    # ------------------------------------------------------------------ periods
    @api.model
    def _receivables(self):
        """Open AR and how much of it is past due — the single most useful pair of
        numbers on this board.

        Read from the Collections countback, NOT from
        ``lab.outstanding.report.amount_residual``: that view sees only
        invoices, and this ledger's receipts are plain journal entries that are
        never reconciled, so a paid invoice still claims its face value. Reading
        it here put 10.31 M of "overdue" on this board against the Collections
        screen's 6.26 M and the ledger's own 6.26 M — the same money, two
        answers, in front of the client. (2026-08-31)
        """
        return self.env['lab.collection.performance'].outstanding_summary()

    # -------------------------------------------------------------------- cash
    @api.model
    def _cash(self):
        """Bank and cash balances, plus the float that is physically out with the field
        force — money the business owns but cannot spend today."""
        self.env['account.move.line'].flush_model()
        self.env.cr.execute("""
            SELECT COALESCE(sum(l.balance), 0)
            FROM account_move_line l
            JOIN account_move m ON m.id = l.move_id AND m.state = 'posted'
            JOIN account_account a ON a.id = l.account_id
            WHERE a.account_type = 'asset_cash' AND l.company_id IN %s
        """, (tuple(self.env.companies.ids),))
        liquid = round(self.env.cr.fetchone()[0] or 0.0, 2)
        floats = self.env['petty.cash.allocation'].sudo().search(
            [('state', '=', 'allocated')])
        return {
            'liquid': liquid,
            'petty': round(sum(floats.mapped('amount_balance')), 2),
            'petty_holders': len(floats),
        }

    # ------------------------------------------------------------------ trend
    # ------------------------------------------------------------------ launchers
    # The Management app's screens. They used to be submenus; the client asked for
    # the navigation bar to carry only the incentive screens and for everything
    # else to be reached from the hub - so the list lives here, each entry with the
    # action it opens and the one live figure that says whether it is worth opening
    # right now. Order is reading order. (client, 2026-08-28)
    LAUNCHERS = [
        # fa-money, not fa-inr: the figure already ends in the rupee sign,
        # and the tile read as .. Collections 64.4 L .. (client, 2026-09-02)
        ('collections', 'Collections', 'fa-money',
         'lab_collections.action_collection_dashboard',
         'Who owes what, and who is chasing it'),
        ('sales', 'Sales & Cases', 'fa-line-chart',
         'lab_ceo_dashboard.action_pulse_sales',
         'Orders, cases and clinics, week by week'),
        ('field', 'Field Force', 'fa-map-marker',
         'lab_ceo_dashboard.action_field_command',
         'Visits, rounds and coverage'),
        ('floor', 'Production Floor', 'fa-cogs',
         'lab_workcenter_scan.action_flow_board',
         'Where every live job is standing'),
        ('production', 'Production Reports', 'fa-users',
         'lab_workcenter_scan.action_mrp_report',
         'Who got through what, by week'),
        ('clinics', 'New Clinics', 'fa-hospital-o',
         'lab_fieldwork.action_new_clinics',
         'The doors the rounds opened'),
        ('redo', 'Redo Works', 'fa-refresh',
         'lab_workcenter_scan.action_mrp_redo',
         'Work started again, and why'),
        ('money', 'Money', 'fa-university',
         'lab_ceo_dashboard.action_pulse_money',
         'Revenue, cash and receivables'),
    ]

    # The Field Force tile is not one screen: it is whichever of these this
    # user's field work role earns, behind one switcher (the Field Command
    # wrapper, tag `lab_field_command`). Most specific role first - the
    # administrator implies both manager desks, so the order IS the default.
    # The analysis pulse is appended for everyone: gaining a desk must never
    # cost a manager the numbers they already had. (client, 2026-08-31)
    FIELD_SCREENS = [
        ('admin', 'lab_fieldwork.group_fieldwork_admin', 'lab_admin_desk',
         'Command Center', 'fa-building-o',
         'Both desks, the crons and the cover board'),
        ('ops', 'lab_fieldwork.group_fieldwork_ops_manager', 'lab_ops_desk',
         'Ops Desk', 'fa-inbox',
         'Your approval queue and the field, live'),
        ('mkt', 'lab_fieldwork.group_fieldwork_marketing_manager',
         'lab_marketing_desk', 'Marketing Desk', 'fa-star',
         'Activities to judge, and where to push'),
    ]

    @api.model
    def _field_screens(self):
        """The FIELD_SCREENS rows this user's roles earn.

        env.ref on the group before has_group: on a database without
        lab_fieldwork the lookup must read as 'no role', not raise.
        """
        rows = []
        for key, group_xmlid, tag, label, icon, hint in self.FIELD_SCREENS:
            group = self.env.ref(group_xmlid, raise_if_not_found=False)
            if group is not None and self.env.user.has_group(group_xmlid):
                rows.append((key, tag, label, icon, hint))
        return rows

    @api.model
    def _field_backlog(self, key):
        """What is waiting on the holder of one desk, right now.

        Sudo: the count must be the desk's truth, not what this user's record
        rules happen to show - and it is only ever computed for a user whose
        role has just been checked.
        """
        # 'flagged' counts for the administrator only: a day a desk raised is
        # waiting on THEM, and it left the desk that raised it.
        # (client, 2026-09-05)
        states = {'ops': ['submitted'], 'mkt': ['ops_approved', 'ops_checked'],
                  'admin': ['submitted', 'ops_approved', 'ops_checked',
                            'flagged']}.get(key)
        if not states or 'lab.daily.update' not in self.env:
            return 0
        return self.env['lab.daily.update'].sudo().search_count(
            [('state', 'in', states)])

    @api.model
    def get_field_command(self):
        """Everything behind the hub's Field Force tile, for this user.

        One list, in the order the switcher shows it: the desks their roles
        earn (most specific first - that one is the default), then the
        analysis pulse for everyone. Each screen carries its own live badge so
        the switcher says where the work is before anyone clicks.
        """
        if not self.env.user.has_group('lab_ceo_dashboard.group_lab_executive'):
            raise AccessError(_("The Management hub is for whoever runs the lab."))
        screens = [{
            'key': key, 'kind': 'client', 'tag': tag, 'label': label,
            'icon': icon, 'hint': hint, 'badge': self._field_backlog(key),
            'badge_title': _("day sheets waiting on this desk"),
        } for key, tag, label, icon, hint in self._field_screens()]
        today = fields.Date.context_today(self)
        try:
            visits = self.env['lab.visit'].sudo().search_count(
                [('date', '=', today), ('state', '=', 'done')])
        except Exception:                                          # noqa: BLE001
            visits = 0
        screens.append({
            'key': 'pulse', 'kind': 'client', 'tag': 'lab_mgmt_pulse',
            'params': {'section': 'field'}, 'label': _('Analysis'),
            'icon': 'fa-line-chart', 'hint': _('Visits, rounds and coverage'),
            'badge': visits, 'badge_title': _("visits done today"),
        })
        return {'screens': screens}

    @api.model
    def _launcher_figure(self, key):
        """One live number per screen, or nothing.

        Each is the cheapest honest count available and is wrapped so that a
        screen whose data is missing costs the hub a figure, never the hub itself.
        """
        today = fields.Date.context_today(self)
        month_start = today.replace(day=1)
        # Local midnight, not midnight UTC (05:30 here), for the datetime columns.
        utc_start = self.env['lab.mgmt.pulse']._utc_start
        try:
            if key == 'collections':
                ar = self._receivables()
                return ar['overdue'], _("overdue")
            if key == 'sales':
                n = self.env['sale.order'].search_count([
                    ('state', 'not in', ('draft', 'sent', 'cancel')),
                    ('date_order', '>=', fields.Datetime.to_string(
                        utc_start(month_start)))])
                return n, _("orders this month")
            if key == 'field':
                # A desk holder's honest number is what is waiting on THEM,
                # not how many rounds happened. Everyone else keeps the rounds.
                mine = self._field_screens()
                if mine:
                    return self._field_backlog(mine[0][0]), _("waiting on you")
                n = self.env['lab.visit'].search_count([
                    ('date', '=', today), ('state', '=', 'done')])
                return n, _("visits done today")
            if key == 'floor':
                Floor = self.env['report.lab.floor']
                n = self.env['mrp.production'].search_count(Floor._live_domain())
                return n, _("jobs on the floor")
            if key == 'production':
                monday = today - timedelta(days=today.weekday())
                n = self.env['lab.production.performance'].search_count([
                    ('handed_over_at', '>=', fields.Datetime.to_string(
                        utc_start(monday)))])
                return n, _("operations this week")
            if key == 'clinics':
                return self.env['lab.new.clinics'].count_new_clinics(), _("this month")
            if key == 'redo':
                n = self.env['lab.mrp.redo'].search_count([
                    ('date', '>=', fields.Datetime.to_string(
                        utc_start(month_start)))])
                return n, _("this month")
            # 'money' deliberately shows NO figure. The bank-and-cash balance
            # on a launcher put the lab's most sensitive number in front of
            # every management reader on every hub visit; it lives inside the
            # Money screen, one click away, where opening it is a choice.
            # (client, 2026-08-31)
        except Exception:                                              # noqa: BLE001
            _logger.exception("management hub: figure for %s failed", key)
        return None, ''

    @api.model
    def get_launchers(self):
        """The hub's screens, for this user, with their live figures.

        A screen whose action does not exist (module not installed) or whose
        records this user cannot read is left out, the way a menu would be.
        """
        if not self.env.user.has_group('lab_ceo_dashboard.group_lab_executive'):
            raise AccessError(_("The Management hub is for whoever runs the lab."))
        access = self.env['ir.model.access']
        out = []
        money_keys = {'collections', 'money'}
        for index, (key, name, icon, xmlid, hint) in enumerate(self.LAUNCHERS):
            if key == 'field':
                # The tile's subtitle names the reader's own desk; the screens
                # themselves live behind the Field Command switcher the
                # launcher already points at.
                mine = self._field_screens()
                if mine:
                    hint = mine[0][4]
            # The action record is read elevated - a manager may not be allowed to
            # read action definitions - but whether they may read what the screen
            # SHOWS is checked as themselves, which is the check that matters.
            action = self.env.ref(xmlid, raise_if_not_found=False)
            if not action:
                continue
            action = action.sudo()
            if action._name == 'ir.actions.act_window' \
                    and not access.check(action.res_model, 'read', False):
                continue
            value, unit = self._launcher_figure(key)
            row = {
                'key': key,
                'name': name,
                'icon': icon,
                'hint': hint,
                'action_id': action.id,
                'shortcut': str(index + 1),
                'value': value,
                'unit': unit,
                'money': key in money_keys,
                'currency_id': self.env.company.currency_id.id,
            }
            # Enough to mount the screen INSIDE the hub, so the bar of launchers
            # stays on screen and switching is one click, not a trip back.
            if action._name == 'ir.actions.client':
                row.update({'kind': 'client', 'tag': action.tag,
                            'params': action.params or {}, 'title': action.name})
            else:
                row.update({
                    'kind': 'window',
                    'res_model': action.res_model,
                    'domain': action.domain or '[]',
                    'context': action.context or '{}',
                    'views': [[v.view_id.id or False, v.view_mode]
                              for v in action.view_ids] or
                             [[False, mode] for mode in (action.view_mode or 'list').split(',')],
                    'search_view_id': action.search_view_id.id or False,
                    'title': action.name,
                })
            out.append(row)
        return out
