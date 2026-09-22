# -*- coding: utf-8 -*-
"""Order List: the register of cases as a printable document.

The counter and the managers read the same list on screen every day — orders for a
period, narrowed by clinic, route or product, and grouped the way the question is asked
("what did EKM2 book this month", "how many Hawley's for this clinic"). This wizard puts
that list on paper with the same columns.
"""
from datetime import datetime, time, timedelta

import pytz
from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools.misc import formatLang

# A production slip is real paperwork travelling to a real bench: a filter wide
# enough to catch 10,000 orders (the list view's own default range) must not
# turn into a 1,200-page print job nobody meant to start. Past this many
# orders the button asks for a narrower filter instead of silently grinding.
PRODUCTION_PRINT_LIMIT = 400


def local_midnight_utc(record, day):
    """Midnight at the start of `day` in the reader's timezone, as the naive UTC
    datetime a datetime column is stored in.

    Context, then user, then the company, then the lab's own zone: most users carry
    no tz, and a UTC fallback started their day at 05:30.
    """
    env = record.env
    tz = pytz.timezone(env.context.get('tz') or env.user.tz
                       or env.company.partner_id.tz or 'Asia/Kolkata')
    start = tz.localize(datetime.combine(day, time.min))
    return start.astimezone(pytz.utc).replace(tzinfo=None)


class SaleOrderListWizard(models.TransientModel):
    _name = 'sale.order.list.report'
    _description = 'Order List'

    def _default_date_from(self):
        return fields.Date.context_today(self).replace(day=1)

    date_from = fields.Date(string='From', required=True, default=_default_date_from)
    date_to = fields.Date(string='To', required=True, default=fields.Date.context_today)
    partner_ids = fields.Many2many(
        'res.partner', 'sale_order_list_partner_rel', string='Customers',
        domain="[('customer_rank', '>', 0)]",
        help="Leave empty for every clinic.")
    team_ids = fields.Many2many(
        'crm.team', 'sale_order_list_team_rel', string='Sales Routes',
        help="Leave empty for every route.")
    product_ids = fields.Many2many(
        'product.product', 'sale_order_list_product_rel', string='Products',
        help="Only orders that contain one of these works.")
    invoice_filter = fields.Selection(
        [('all', 'All'), ('invoiced', 'Invoiced'), ('not_invoiced', 'Not invoiced')],
        string='Invoicing', default='all', required=True)
    state_filter = fields.Selection(
        [('open', 'Everything except cancelled'), ('confirmed', 'Confirmed orders only'),
         ('all', 'Including cancelled')],
        string='Orders', default='open', required=True)
    # The kind of appliance, ticked: "the removable cases this month", "what
    # still has no type". Nothing ticked means every kind. Tick-boxes rather
    # than a single choice because the question is often two kinds at once.
    # (client, 2026-09-18)
    type_fixed = fields.Boolean('Fixed')
    type_removable = fields.Boolean('Removable')
    type_clear_retainer = fields.Boolean('Clear Retainer')
    type_other = fields.Boolean('Other')
    type_unset = fields.Boolean('Not set', help="Orders whose appliance type was never filled in.")
    # How urgent, ticked the same way: "the urgent cases this month" is the
    # question the counter asks when a doctor rings about a promise.
    # Nothing ticked means every priority. (client, 2026-09-19)
    prio_emergency = fields.Boolean('Emergency')
    prio_urgent = fields.Boolean('Urgent')
    prio_normal = fields.Boolean('Normal')
    prio_low = fields.Boolean('Low')
    group_by = fields.Selection(
        [('none', 'No grouping'), ('team', 'Sales Route'),
         ('partner', 'Customer'), ('product', 'Product'),
         ('appliance', 'Appliance Type')],
        string='Group by', default='team', required=True)
    company_id = fields.Many2one(
        'res.company', required=True, default=lambda self: self.env.company)
    # Orders ticked in the list and sent here by Action ▸ Print Order List: the
    # list is exactly those, and the period and the other filters step aside.
    # (client, 2026-09-18)
    order_ids = fields.Many2many(
        'sale.order', 'sale_order_list_order_rel', string='Selected Orders')
    order_count = fields.Integer(compute='_compute_order_count')

    @api.depends('order_ids')
    def _compute_order_count(self):
        for wizard in self:
            wizard.order_count = len(wizard.order_ids)

    @api.onchange('date_from')
    def _onchange_date_from(self):
        if self.date_from and self.date_to and self.date_to < self.date_from:
            self.date_to = self.date_from

    # The wizard's tick-boxes against the order's own selection keys.
    APPLIANCE_TICKS = (('type_fixed', 'fixed'), ('type_removable', 'removable'),
                       ('type_clear_retainer', 'clear_retainer'), ('type_other', 'other'))

    def _appliance_keys(self):
        """The ticked appliance types, as sale.order.appliance_type keys."""
        self.ensure_one()
        return [key for field, key in self.APPLIANCE_TICKS if self[field]]

    def _appliance_labels(self):
        labels = dict(self.env['sale.order']._fields['appliance_type'].selection)
        chosen = [labels[key] for key in self._appliance_keys()]
        if self.type_unset:
            chosen.append(_('Not set'))
        return chosen

    # The wizard's priority ticks against the order's own selection keys. No
    # "not set" here: priority is required on an order and defaults to normal.
    # Emergency first: it is the top of the scale, and a list of ticks that runs
    # upwards reads backwards. `emergency` is added to the order's selection by
    # lab_order_control; with that module absent nothing carries the value and
    # the tick simply selects nothing.
    PRIORITY_TICKS = (('prio_emergency', 'emergency'), ('prio_urgent', 'urgent'),
                      ('prio_normal', 'normal'), ('prio_low', 'low'))

    def _priority_keys(self):
        """The ticked priorities, as sale.order.priority keys."""
        self.ensure_one()
        return [key for field, key in self.PRIORITY_TICKS if self[field]]

    def _priority_labels(self):
        labels = dict(self.env['sale.order']._fields['priority'].selection)
        # A key the installed modules do not define is named, not crashed on.
        return [labels.get(key, key.title()) for key in self._priority_keys()]

    def _domain(self):
        self.ensure_one()
        # date_order is a datetime: take the whole of both boundary days, in the user's
        # timezone, so "1st to 31st" means what the person picking the dates means.
        # Local midnight converted to UTC - the old bounds were the local wall-clock
        # numbers used as if they were UTC, i.e. a UTC day shifted by nothing.
        start = local_midnight_utc(self, self.date_from)
        end = local_midnight_utc(self, self.date_to + timedelta(days=1))
        domain = [
            ('date_order', '>=', start), ('date_order', '<', end),
            ('company_id', '=', self.company_id.id),
        ]
        if self.state_filter == 'open':
            domain.append(('state', '!=', 'cancel'))
        elif self.state_filter == 'confirmed':
            domain.append(('state', '=', 'sale'))
        if self.partner_ids:
            domain.append(('partner_id', 'in', self.partner_ids.ids))
        if self.team_ids:
            domain.append(('team_id', 'in', self.team_ids.ids))
        if self.product_ids:
            domain.append(('order_line.product_id', 'in', self.product_ids.ids))
        if self.invoice_filter == 'invoiced':
            domain.append(('invoice_status', '=', 'invoiced'))
        elif self.invoice_filter == 'not_invoiced':
            domain.append(('invoice_status', '!=', 'invoiced'))
        keys = self._appliance_keys()
        if keys and self.type_unset:
            domain += ['|', ('appliance_type', 'in', keys), ('appliance_type', '=', False)]
        elif keys:
            domain.append(('appliance_type', 'in', keys))
        elif self.type_unset:
            domain.append(('appliance_type', '=', False))
        priorities = self._priority_keys()
        if priorities:
            domain.append(('priority', 'in', priorities))
        return domain

    def _orders(self):
        self.ensure_one()
        if self.order_ids:
            return self.order_ids.sorted(
                key=lambda o: (o.date_order or fields.Datetime.now(), o.name or ''))
        return self.env['sale.order'].search(self._domain(), order='date_order, name')

    def action_print(self):
        self.ensure_one()
        return self.env.ref('sale_custom.action_report_order_list').report_action(self)

    def action_print_production_orders(self):
        """The production slip of every order this filter selects.

        Same criteria the list on screen uses — period, clinic, route, product,
        invoicing — so "print the production orders for what I'm looking at"
        needs no second wizard. Slips are paperwork for the bench, so only
        CONFIRMED orders qualify: a quotation has nothing to make yet, and
        printing one would send an empty or misleading slip to the floor.
        """
        self.ensure_one()
        orders = self._orders().filtered(lambda o: o.state == 'sale')
        if not orders:
            raise UserError(_(
                "No confirmed orders match this filter. Production slips print "
                "only confirmed sales orders — quotations have nothing to make "
                "yet."))
        limit = int(self.env['ir.config_parameter'].sudo().get_param(
            'sale_custom.production_slip_print_limit', PRODUCTION_PRINT_LIMIT))
        if len(orders) > limit:
            raise UserError(_(
                "%(count)s orders match this filter — more than the %(limit)s "
                "this button prints in one go. Narrow the period, clinic or "
                "route and try again.", count=len(orders), limit=limit))
        # This module's own action, not lab_reports': lab_reports depends
        # on sale_custom, never the reverse, and both actions render the same
        # sale_custom.report_production_slips template underneath.
        return self.env.ref(
            'sale_custom.action_report_production_slips_sale').report_action(orders)


class SaleOrderListSelectionReport(models.AbstractModel):
    """Print ▸ Order List on the orders list: the ticked orders through the same
    renderer, as a wizard made on the spot with the selection and its span."""
    _name = 'report.sale_custom.report_order_list_selection'
    _description = 'Order List renderer (selected orders)'

    def _get_report_values(self, docids, data=None):
        orders = self.env['sale.order'].browse(docids).exists()
        days = [fields.Datetime.context_timestamp(self, order.date_order).date()
                for order in orders if order.date_order]
        today = fields.Date.context_today(self)
        wizard = self.env['sale.order.list.report'].create({
            'order_ids': [(6, 0, orders.ids)],
            'date_from': min(days) if days else today,
            'date_to': max(days) if days else today,
            'group_by': 'none',
            'state_filter': 'all',
        })
        return self.env['report.sale_custom.report_order_list']._get_report_values(wizard.ids)


class SaleOrderListReport(models.AbstractModel):
    _name = 'report.sale_custom.report_order_list'
    _description = 'Order List renderer'

    def _money(self, company, amount):
        currency = company.currency_id
        return u'%s %s' % (
            currency.symbol or '',
            formatLang(self.env, amount or 0.0, digits=currency.decimal_places))

    @api.model
    def _products(self, order, only=None):
        """Distinct works on the order — or just the one being grouped on."""
        names = []
        for line in order.order_line.filtered(lambda l: not l.display_type and l.product_id):
            if only and line.product_id != only:
                continue
            name = line.product_id.name or ''
            if name and name not in names:
                names.append(name)
        if len(names) > 3:
            return '%s  +%d more' % (', '.join(names[:3]), len(names) - 3)
        return ', '.join(names)

    @api.model
    def _rows(self, wizard, orders):
        """(group label, rows) in the requested grouping.

        Grouping by product lists an order once per product it contains, and the amount
        of each row is that product's own subtotal — otherwise an order with four works
        would be counted four times in the totals.
        """
        groups = {}
        order_of = []
        appliance_selection = self.env['sale.order']._fields['appliance_type'].selection
        appliance_labels = dict(appliance_selection)

        def bucket(key, label):
            if key not in groups:
                groups[key] = {'label': label or 'None', 'rows': [], 'total': 0.0}
                order_of.append(key)
            return groups[key]

        for order in orders:
            if wizard.group_by == 'product':
                wanted = wizard.product_ids
                lines = order.order_line.filtered(lambda l: not l.display_type and l.product_id)
                if wanted:
                    lines = lines.filtered(lambda l: l.product_id in wanted)
                for product in lines.mapped('product_id'):
                    amount = sum(lines.filtered(lambda l: l.product_id == product)
                                 .mapped('price_subtotal'))
                    group = bucket(product.id, product.display_name)
                    group['rows'].append((order, self._products(order, only=product), amount))
                    group['total'] += amount
                continue
            if wizard.group_by == 'team':
                key, label = order.team_id.id, order.team_id.name
            elif wizard.group_by == 'partner':
                key, label = order.partner_id.id, order.partner_id.name
            elif wizard.group_by == 'appliance':
                key = order.appliance_type or 'none'
                label = appliance_labels.get(order.appliance_type) or _('Not set')
            else:
                key, label = 0, ''
            group = bucket(key, label)
            group['rows'].append((order, self._products(order), order.amount_total))
            group['total'] += order.amount_total

        result = [groups[k] for k in order_of]
        if wizard.group_by == 'appliance':
            # The lab's own order of the four kinds, the untyped last - not the
            # alphabet's.
            rank = {label: index for index, (_key, label) in enumerate(appliance_selection)}
            result.sort(key=lambda g: rank.get(g['label'], len(rank)))
        elif wizard.group_by != 'none':
            result.sort(key=lambda g: (g['label'] == 'None', g['label'] or ''))
        return result

    def _get_report_values(self, docids, data=None):
        wizard = self.env['sale.order.list.report'].browse(docids)[:1]
        orders = wizard._orders()
        groups = self._rows(wizard, orders)
        company = wizard.company_id
        criteria = []
        if wizard.order_ids:
            criteria.append(('Selection', _("%s orders picked in the list", len(wizard.order_ids))))
        if wizard.partner_ids and not wizard.order_ids:
            criteria.append(('Customers', ', '.join(wizard.partner_ids.mapped('name'))))
        if wizard.team_ids and not wizard.order_ids:
            criteria.append(('Routes', ', '.join(wizard.team_ids.mapped('name'))))
        if wizard.product_ids and not wizard.order_ids:
            criteria.append(('Products', ', '.join(wizard.product_ids.mapped('display_name'))))
        if (wizard._appliance_keys() or wizard.type_unset) and not wizard.order_ids:
            criteria.append(('Appliance', ', '.join(wizard._appliance_labels())))
        if wizard._priority_keys() and not wizard.order_ids:
            criteria.append(('Priority', ', '.join(wizard._priority_labels())))
        if wizard.invoice_filter != 'all' and not wizard.order_ids:
            criteria.append(('Invoicing', dict(
                wizard._fields['invoice_filter'].selection)[wizard.invoice_filter]))
        if wizard.state_filter != 'open' and not wizard.order_ids:
            criteria.append(('Orders', dict(
                wizard._fields['state_filter'].selection)[wizard.state_filter]))
        return {
            'doc_ids': wizard.ids,
            'doc_model': 'sale.order.list.report',
            'docs': wizard,
            'wizard': wizard,
            'company': company,
            'groups': groups,
            'grouped': wizard.group_by != 'none',
            'group_label': dict(wizard._fields['group_by'].selection)[wizard.group_by],
            'order_count': len(orders),
            # Only print the colour legend when the sheet actually carries a
            # tinted row - a key to colours that are not there is noise.
            'has_rush': any(o.priority in ('urgent', 'emergency') for o in orders),
            'grand_total': sum(g['total'] for g in groups),
            'criteria': criteria,
            'money': lambda amount: self._money(company, amount),
            'state_labels': dict(self.env['sale.order']._fields['state'].selection),
            'invoice_labels': dict(
                self.env['sale.order']._fields['invoice_status'].selection),
            'printed_on': fields.Datetime.context_timestamp(
                wizard, fields.Datetime.now()).strftime('%d/%m/%Y %H:%M'),
        }
