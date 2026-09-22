# -*- coding: utf-8 -*-
"""Audiences: who a message goes to, written once and kept.

"Every doctor in Kochi", "the engaged ones", "doctors with no case in 45 days" -
picked by hand for every campaign, or written as a rule once and reused by every
campaign and automation after it. (client, 2026-09-17)

A rule is a contact filter built in the usual editor, plus two things the editor
does not say: a recency rule ("nothing newer than 45 days", counted from the day
the audience is used, so it never goes stale) and the WhatsApp exclusions - no
number, asked to stop, quiet.
"""
from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import safe_eval

STARTERS = [
    ('everyone', 'Everyone with a WhatsApp number'),
    ('engaged', 'Engaged: read or answered in the last 90 days'),
    ('quiet', 'Quiet: messaged, nothing back in 90 days'),
    ('never', 'Never messaged on WhatsApp'),
]
STARTER_DOMAINS = {
    'everyone': "[]",
    'engaged': "[('whatsapp_engagement', '=', 'engaged')]",
    'quiet': "[('whatsapp_engagement', '=', 'quiet')]",
    'never': "[('whatsapp_engagement', '=', 'never')]",
}
RECENCY = [('older', 'nothing newer than'), ('newer', 'something within')]
SAMPLE_ROWS = 5


class EpgWhatsappAudience(models.Model):
    _name = 'epg.whatsapp.audience'
    _description = 'WhatsApp Audience'
    _order = 'sequence, name'

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    description = fields.Char(help="What this audience is for, in a sentence.")
    company_id = fields.Many2one('res.company', default=lambda s: s.env.company)
    # For the rules editor, which asks its model from a field.
    model_name = fields.Char(default='res.partner', readonly=True)
    starter = fields.Selection(
        STARTERS, string='Start From',
        help="Fills the rules below with a common starting point; edit from there.")
    domain = fields.Text(
        'Rules', default='[]', required=True,
        help="Which contacts, in the usual filter editor. Dates may be relative: "
             "context_today() - relativedelta(days=30).")
    recency_field = fields.Char(
        'Activity', help="A date on the contact, or on its records through a dot - "
                         "sale_order_ids.date_order for their orders. Empty: no rule.")
    recency_kind = fields.Selection(RECENCY, default='older', required=True)
    recency_days = fields.Integer('Days', default=45)
    recency_domain = fields.Text(
        'Only Counting', default='[]',
        help="Narrows the records looked at: [('state', '=', 'sale')] counts confirmed "
             "orders only.")
    require_number = fields.Boolean('Only Contacts With a WhatsApp Number', default=True)
    exclude_stopped = fields.Boolean('Leave Out Who Asked to Stop', default=True)
    exclude_quiet = fields.Boolean(
        'Leave Out the Quiet', default=False,
        help="Contacts messaged in the last 90 days who read and answered nothing.")

    count = fields.Integer('Contacts', compute='_compute_count')
    sample = fields.Char(compute='_compute_count')
    problem = fields.Char(compute='_compute_count')
    campaign_count = fields.Integer(compute='_compute_usage')
    automation_count = fields.Integer(compute='_compute_usage')

    @api.onchange('starter')
    def _onchange_starter(self):
        if self.starter:
            self.domain = STARTER_DOMAINS[self.starter]
            self.exclude_quiet = False
            self.starter = False

    # ------------------------------------------------------------------ rules
    def _eval_context(self):
        return {
            'datetime': safe_eval.datetime,
            'dateutil': safe_eval.dateutil,
            'time': safe_eval.time,
            'relativedelta': relativedelta,
            'context_today': lambda: fields.Date.context_today(self),
            'uid': self.env.uid,
        }

    @api.model
    def _field_at(self, model_name, path):
        """The field a dotted path ends on, or a plain-words error."""
        Model = self.env[model_name]
        parts = [p for p in (path or '').split('.') if p]
        if not parts:
            raise UserError(_("The activity field is empty."))
        field = None
        for index, part in enumerate(parts):
            field = Model._fields.get(part)
            if field is None:
                raise UserError(_("'%(part)s' is not a field of %(model)s.",
                                  part=part, model=Model._description or model_name))
            if index < len(parts) - 1:
                if not field.relational:
                    raise UserError(_("'%s' has nothing inside it.", part))
                Model = self.env[field.comodel_name]
        return field

    def _recency_leaf(self, narrow):
        """The recency rule as domain leaves, counted from today."""
        self.ensure_one()
        path = (self.recency_field or '').strip()
        if not path:
            return []
        last = self._field_at('res.partner', path)
        if last.type not in ('date', 'datetime'):
            raise UserError(_("'%s' is not a date.", path))
        days = max(self.recency_days, 0)
        since = (fields.Date.context_today(self) - relativedelta(days=days)
                 if last.type == 'date' else fields.Datetime.now() - relativedelta(days=days))
        head, _sep, tail = path.partition('.')
        field = self.env['res.partner']._fields[head]
        if field.type in ('one2many', 'many2many'):
            inner = [(tail, '>=', since)] + narrow
            return [(head, 'any' if self.recency_kind == 'newer' else 'not any', inner)]
        if self.recency_kind == 'newer':
            return [(path, '>=', since)]
        return ['|', (path, '=', False), (path, '<', since)]

    def _domain(self):
        """The whole rule, ready to search with."""
        self.ensure_one()
        context = self._eval_context()
        try:
            domain = list(safe_eval.safe_eval(self.domain or '[]', context))
            narrow = list(safe_eval.safe_eval(self.recency_domain or '[]', context))
        except Exception as exc:                                       # noqa: BLE001
            raise UserError(_("The rules of %(name)s cannot be read: %(error)s",
                              name=self.name, error=exc)) from exc
        domain += self._recency_leaf(narrow)
        Partner = self.env['res.partner']
        if self.require_number:
            domain.append(('whatsapp_number' if 'whatsapp_number' in Partner._fields
                           else 'phone', '!=', False))
        excluded = [key for key, on in (('stopped', self.exclude_stopped),
                                        ('quiet', self.exclude_quiet)) if on]
        if excluded:
            domain.append(('whatsapp_engagement', 'not in', excluded))
        # A contact who switched marketing off is never in a marketing audience.
        if 'whatsapp_marketing_optin' in Partner._fields:
            domain.append(('whatsapp_marketing_optin', '=', True))
        return domain

    @api.model
    def _json_domain(self, domain):
        """The same domain with dates as strings, for an action the browser opens."""
        out = []
        for leaf in domain:
            if isinstance(leaf, (list, tuple)) and len(leaf) == 3:
                field, operator, value = leaf
                if isinstance(value, list) and value and isinstance(value[0], (list, tuple)):
                    value = self._json_domain(value)
                elif hasattr(value, 'strftime'):
                    value = (fields.Datetime.to_string(value) if hasattr(value, 'hour')
                             else fields.Date.to_string(value))
                out.append((field, operator, value))
            else:
                out.append(leaf)
        return out

    def _partners(self, limit=None):
        self.ensure_one()
        return self.env['res.partner'].search(self._domain(), limit=limit)

    # ------------------------------------------------------------------ counts
    @api.depends('domain', 'recency_field', 'recency_kind', 'recency_days', 'recency_domain',
                 'require_number', 'exclude_stopped', 'exclude_quiet')
    def _compute_count(self):
        Partner = self.env['res.partner']
        for audience in self:
            try:
                domain = audience._domain()
                audience.count = Partner.search_count(domain)
                audience.sample = ', '.join(
                    Partner.search(domain, limit=SAMPLE_ROWS).mapped('display_name'))
                audience.problem = False
            except Exception as exc:                                   # noqa: BLE001
                audience.count = 0
                audience.sample = False
                audience.problem = str(exc)

    def _compute_usage(self):
        campaigns = {a.id: n for a, n in self.env['epg.whatsapp.campaign']._read_group(
            [('audience_id', 'in', self.ids)], ['audience_id'], ['__count'])}
        automations = {a.id: n for a, n in self.env['epg.whatsapp.automation']._read_group(
            [('audience_id', 'in', self.ids)], ['audience_id'], ['__count'])}
        for audience in self:
            audience.campaign_count = campaigns.get(audience.id, 0)
            audience.automation_count = automations.get(audience.id, 0)

    # ------------------------------------------------------------------ actions
    def action_preview(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': self.name,
            'res_model': 'res.partner', 'view_mode': 'list,kanban,form',
            'domain': self._json_domain(self._domain()),
            'context': {'create': False},
        }

    def action_new_campaign(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': _('New Campaign'),
            'res_model': 'epg.whatsapp.campaign', 'view_mode': 'form',
            'context': {'default_audience_id': self.id, 'default_name': self.name},
        }

    def action_view_campaigns(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': _('Campaigns'),
            'res_model': 'epg.whatsapp.campaign', 'view_mode': 'list,form',
            'domain': [('audience_id', '=', self.id)],
        }
