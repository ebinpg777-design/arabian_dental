# -*- coding: utf-8 -*-
"""The jobs behind a batch of sales orders, eight to an A4 sheet.

Two things about this database shape the code:

* `sale.order.mrp_production_ids` is a non-stored compute that core derives from the
  stock moves a procurement created. These orders were imported and carry no such
  chain, so that field returns nothing on 24,659 orders while the stored
  `mrp.production.sale_id` finds the work perfectly well. So we search, never trust it.

* Roughly one confirmed order in five has no manufacturing order at all (4,396 of
  5,415 in April, 962 of 1,215 in August). A sheet driven purely off manufacturing
  orders would come out BLANK for those, which at the counter is indistinguishable
  from a broken printer. So an order with no MO still gets a slip per order line, with
  the sales order's own barcode on it - the number the bench and the delivery scanner
  both use anyway. (client, 2026-08-28)
"""
from odoo import api, fields, models
from odoo.tools.misc import format_date

# Eight to a sheet, two across. Chunked in Python rather than in the template:
# wkhtmltopdf collapses an inline-block's `height` in mm to its content, so the
# slips flowed five rows to a page instead of four. A fixed-height page box with
# quarter-height rows inside it is the only layout that holds. (client, 2026-08-28)
PER_PAGE = 8


def _pages(slips):
    rows = list(slips)
    return [rows[i:i + PER_PAGE] for i in range(0, len(rows), PER_PAGE)]


def _sel_label(record, fname):
    """The word on the screen, not the key underneath it.

    A slip is a dict, so `t-field`'s formatting is not available in the template and
    the label has to be resolved here - otherwise the bench reads "u" where the form
    says "Upper".
    """
    value = record[fname]
    if not value:
        return ''
    selection = record._fields[fname]._description_selection(record.env)
    return dict(selection).get(value, value)


def _rework(order):
    """The job this one is remaking: the original's number and the day it was placed.

    A bench holding a remake needs to know which case it is redoing - the original
    number is what the office, the doctor and the old paperwork all quote, and its
    date says whether this is a fortnight-old job or a year-old one. (client, 2026-09-10)

    Read defensively: reworks are lab_rework's idea and this module cannot depend
    on it - the dependency runs the other way - so the slip works with or without
    that module installed. The link is optional even where it is: 2,686 of the
    lab's 2,786 reworks came from the old system with no original recorded, and
    those say so rather than printing a blank.
    """
    blank = {'rework': False, 'rework_of': '', 'rework_date': ''}
    if not order or 'is_rework' not in order._fields or not order.is_rework:
        return blank
    origin = order.rework_origin_id if 'rework_origin_id' in order._fields \
        else order.browse()
    if not origin:
        return dict(blank, rework=True)
    placed = origin.date_order
    if placed:
        # date_order is a UTC datetime; the day it was placed is the day HERE.
        placed = fields.Datetime.context_timestamp(origin, placed).date()
    return {
        'rework': True,
        'rework_of': origin.name or '',
        # Formatted here, in the reader's own language: the template is handed a
        # dict, so t-field's formatting is not available to it.
        'rework_date': format_date(order.env, placed) if placed else '',
    }


def _colour(value):
    """`color_scheme` is a char on mrp.production and a many2one on the order line."""
    if not value:
        return ''
    return value.display_name if hasattr(value, 'display_name') else value


def _slips_from_mo(mo):
    """The slips one manufacturing order needs: one per piece being made.

    A case sold as Upper & Lower is two pieces that travel the benches
    separately, so it needs two job cards - each naming its own arch - or the
    second piece goes round the floor with no paper of its own. The barcode
    stays the manufacturing reference on both: the bench scans the card and the
    board works out which arch it is holding from where the scan happened.
    (client, 2026-09-12)
    """
    arches = sorted({wo.arch for wo in mo.workorder_ids if wo.arch}) \
        if 'arch' in mo.workorder_ids._fields else []
    if len(arches) < 2:
        return [_slip_from_mo(mo)]
    labels = {'upper': 'U', 'lower': 'L'}
    return [dict(_slip_from_mo(mo), ul=labels.get(arch, arch), arch=arch)
            for arch in arches]


def _slip_from_mo(mo):
    """One slip for one manufacturing order. The barcode is the MO's own number."""
    order = mo.sale_id
    line = mo.sale_line_id if 'sale_line_id' in mo._fields else mo.browse()
    # date_deadline is empty on every order in this database, so the planned finish
    # is the only date that means anything.
    return {
        'mo': mo.name,
        'so': order.name or '',
        'code': mo.name,
        'partner': order.partner_id.display_name if order else '',
        'patient': order.patient if order else '',
        'product': mo.product_id.display_name or '',
        'date': mo.date_deadline or mo.date_finished or False,
        'ul': (_sel_label(line, 'ul') if line else '') or _sel_label(mo, 'ul'),
        'colour': (_colour(line.color_scheme) if line else '') or _colour(mo.color_scheme),
        'route': order.team_id.name if order else '',
        'qty': mo.product_qty,
        'uom': mo.product_uom_id.name or '',
        # 'urgent' / 'emergency' / '' - the bench must see this before it sees
        # anything else on the slip; lab_order_control's priority field is
        # what the dispatch board and the delay grace already read.
        'priority': order.priority if order and order.priority in ('urgent', 'emergency') else '',
        # Set per arch by _slips_from_mo on a two-piece case; empty otherwise.
        'arch': '',
        **_rework(order),
    }


def _slip_from_line(line):
    """One slip for an order line whose work was never raised as an MO."""
    order = line.order_id
    return {
        'mo': '',
        'so': order.name or '',
        'code': order.name or '',
        'partner': order.partner_id.display_name,
        'patient': order.patient,
        'product': line.product_id.display_name or '',
        'date': order.commitment_date or order.expected_date or False,
        'ul': _sel_label(line, 'ul'),
        'colour': _colour(line.color_scheme),
        'route': order.team_id.name or '',
        'qty': line.product_uom_qty,
        'uom': line.product_uom_id.name or '',
        'priority': order.priority if order.priority in ('urgent', 'emergency') else '',
        **_rework(order),
    }


def slips_for_orders(orders):
    """A slip for every piece of work under these orders, in the office's own order.

    Manufacturing orders where they exist; the order's own lines where they do not, so
    the sheet is never empty for an order that has not been exploded into MOs yet.
    """
    productions = orders.env['mrp.production'].search(
        [('sale_id', 'in', orders.ids), ('state', '!=', 'cancel')],
        order='sale_id, name')
    slips = []
    for order in orders:
        made = productions.filtered(lambda m, o=order: m.sale_id.id == o.id)
        if made:
            for mo in made:
                slips.extend(_slips_from_mo(mo))
        else:
            slips.extend(_slip_from_line(line) for line in order.order_line
                         if line.product_id and not line.display_type)
    return slips


class ReportProductionSlipsSale(models.AbstractModel):
    _name = 'report.sale_custom.report_production_slips_sale'
    _description = 'Production Slips from Sales Orders'

    @api.model
    def _get_report_values(self, docids, data=None):
        orders = self.env['sale.order'].browse(docids)
        slips = slips_for_orders(orders)
        return {
            'doc_ids': docids,
            'doc_model': 'sale.order',
            'docs': orders,
            'slips': slips,
            'pages': _pages(slips),
        }


class ReportProductionSlipsMo(models.AbstractModel):
    _name = 'report.sale_custom.report_production_slips_mo'
    _description = 'Production Slips'

    @api.model
    def _get_report_values(self, docids, data=None):
        productions = self.env['mrp.production'].browse(docids)
        slips = [slip for mo in productions for slip in _slips_from_mo(mo)]
        return {
            'doc_ids': docids,
            'doc_model': 'mrp.production',
            'docs': productions,
            'slips': slips,
            'pages': _pages(slips),
        }
