# -*- coding: utf-8 -*-
"""What a case costs to make, beside what the lab charges for it.

The lab's own costing sheet answers this once, in a spreadsheet, for the day it
was written. This answers it from the database: the same bill of materials the
floor consumes against, the same rates the store paid, the same benches the
station board plans. When somebody corrects a rate, this moves.

One call fills the screen. A board that asks the server once per product is a
board nobody opens twice.
"""
from odoo import api, fields, models


class LabCaseCost(models.AbstractModel):
    _name = 'lab.case.cost'
    _description = "What a case costs to make, against what it sells for"

    # The lab's own costing sheet, for the products it covers: the figure they
    # print as the full cost of a unit, overheads and all. Kept here so the
    # screen can show their answer beside this one - the gap between the two is
    # the overhead their sheet absorbs and a bill of materials cannot.
    SHEET_COST = {
        'ZR-EMR': 1095.42, 'ZR-RUB': 918.99, 'ZR-DIA': 680.78,
        'ZR-PRM': 497.57, 'ZR-CLS': 515.42, 'ZR-BSC': 527.56,
        'MC-BSC': 416.82, 'OR-HAW': 159.38, 'OR-ESX': 196.88,
        'OR-RME': 1646.48, 'OR-TWB': 356.13, 'AC-RPD': 225.09,
        'AC-BPS': 820.12,
    }

    # ------------------------------------------------------------------ reading
    @api.model
    def _sold_last_year(self, templates):
        """How many of each of these the lab sold in the last twelve months.

        A margin on something sold four times a year is trivia; the same margin
        on something sold twenty-five thousand times is the business. Read in
        one grouped query, because fifteen separate counts on a table of
        eighty-five thousand lines is a screen that takes a second to open.

        Grouped by the variant, not the template: `product_template_id` on a
        sale line is computed and unstored in Odoo 19, and grouping on it raises
        rather than falling back to anything.
        """
        if not templates:
            return {}
        variants = templates.product_variant_ids
        if not variants:
            return {}
        since = fields.Date.subtract(fields.Date.context_today(self), years=1)
        groups = self.env['sale.order.line'].sudo()._read_group(
            [('product_id', 'in', variants.ids),
             ('order_id.state', 'in', ('sale', 'done')),
             ('order_id.date_order', '>=', since)],
            groupby=['product_id'],
            aggregates=['product_uom_qty:sum', 'price_subtotal:sum'],
        )
        sold = {}
        for variant, qty, revenue in groups:
            slot = sold.setdefault(variant.product_tmpl_id.id,
                                   {'qty': 0.0, 'revenue': 0.0})
            slot['qty'] += qty or 0.0
            slot['revenue'] += revenue or 0.0
        return sold

    @api.model
    def get_routes(self):
        """Every bill of materials the lab has, costed.

        Returns one row a product with its material lines, its stages grouped by
        department, and the three numbers that matter: what it costs, what it
        sells for, and what is left.
        """
        boms = self.env['mrp.bom'].sudo().search([], order='code')
        sold = self._sold_last_year(boms.product_tmpl_id)
        currency = self.env.company.currency_id
        rows = []

        # One walk up the category tree per CATEGORY, not per component line.
        # Fifteen routes share a handful of categories between a hundred and
        # fourteen lines, and every line was climbing the tree again. Costing
        # the rest of the catalogue would have multiplied that by thirty.
        department_of = {}

        def department_for(category):
            if category.id not in department_of:
                department_of[category.id] = category._lab_department()
            return department_of[category.id]

        for bom in boms:
            template = bom.product_tmpl_id
            materials = []
            material_cost = 0.0
            for line in bom.bom_line_ids:
                product = line.product_id
                cost = line.product_qty * product.standard_price
                material_cost += cost
                department = department_for(product.categ_id)
                materials.append({
                    'id': product.id,
                    'name': product.display_name,
                    'qty': line.product_qty,
                    'uom': line.product_uom_id.name or product.uom_id.name,
                    'rate': product.standard_price,
                    'cost': cost,
                    'department': department.name or '',
                })

            stages = []
            labour_cost = 0.0
            minutes = 0.0
            for operation in bom.operation_ids:
                bench = operation.workcenter_id
                cost = operation.time_cycle_manual / 60.0 * bench.costs_hour
                labour_cost += cost
                minutes += operation.time_cycle_manual
                stages.append({
                    'name': operation.name,
                    'bench': bench.display_name,
                    'department': bench.department_id.name or '',
                    'minutes': operation.time_cycle_manual,
                    'rate': bench.costs_hour,
                    'cost': cost,
                    # A furnace hour costs the lab no wages; saying so on the
                    # line stops it reading as a missing rate.
                    'unattended': not bench.costs_hour,
                })

            # By department, because that is the unit the lab manages. Material
            # and time both, so a department that consumes much and works little
            # is visible as such.
            by_department = {}
            for entry in materials:
                slot = by_department.setdefault(
                    entry['department'] or 'Unassigned',
                    {'name': entry['department'] or 'Unassigned',
                     'material': 0.0, 'labour': 0.0, 'minutes': 0.0})
                slot['material'] += entry['cost']
            for entry in stages:
                slot = by_department.setdefault(
                    entry['department'] or 'Unassigned',
                    {'name': entry['department'] or 'Unassigned',
                     'material': 0.0, 'labour': 0.0, 'minutes': 0.0})
                slot['labour'] += entry['cost']
                slot['minutes'] += entry['minutes']

            works_cost = material_cost + labour_cost
            price = template.list_price
            volume = sold.get(template.id, {})
            sheet = self.SHEET_COST.get(bom.code)

            # The margin is taken against the FULLER of the two costs. A bill of
            # materials knows what a case consumes and how long it is worked; it
            # knows nothing of rent, power, the fuel of the routes or the
            # salaries nobody bills by the hour, and those are 273 of the 417 a
            # metal ceramic crown costs by the lab's own reckoning. A margin
            # against works cost alone reads 64% where the lab's own sheet says
            # 45%, and a screen that flatters every product is worse than none.
            #
            # Where the lab has no sheet figure the margin is against works cost
            # and the row says so, rather than sitting in the same column
            # pretending to be comparable.
            full_cost = sheet if sheet and sheet > works_cost else works_cost
            rows.append({
                'bom_id': bom.id,
                'code': bom.code or '',
                'product_id': template.id,
                'name': (template.name or '').strip(),
                'category': template.categ_id.display_name,
                'material': material_cost,
                'labour': labour_cost,
                'works': works_cost,
                'price': price,
                'full_cost': full_cost,
                'margin': price - full_cost,
                'margin_pct': (price - full_cost) / price * 100.0 if price else 0.0,
                'minutes': minutes,
                'sheet_cost': sheet,
                # What the lab's sheet charges to overheads and a bill of
                # materials cannot: rent, power, logistics, packing, the fuel of
                # the routes, and the salaries nobody bills by the hour.
                'overhead': full_cost - works_cost,
                'costed_fully': bool(sheet),
                'sold_qty': volume.get('qty', 0.0),
                'revenue': volume.get('revenue', 0.0),
                'materials': materials,
                'stages': stages,
                'departments': sorted(
                    by_department.values(),
                    key=lambda slot: slot['material'] + slot['labour'], reverse=True),
            })

        return {
            'rows': rows,
            'currency': {
                'symbol': currency.symbol,
                'position': currency.position,
                'id': currency.id,
            },
            'totals': {
                'routes': len(rows),
                'benches': self.env['mrp.workcenter'].sudo().search_count([]),
                'materials': self.env['mrp.bom.line'].sudo().search_count([]),
                # The catalogue is 1,600 products long and fifteen of them are
                # costed. Saying so on the screen is the only way the other
                # fifteen hundred ever get costed.
                'uncosted': self.env['product.template'].sudo().search_count(
                    [('type', '=', 'service'), ('sale_ok', '=', True)]),
            },
        }

    # ------------------------------------------------------------ drilling down
    @api.model
    def open_bom(self, bom_id):
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'mrp.bom',
            'res_id': bom_id,
            'views': [[False, 'form']],
        }

    @api.model
    def open_product(self, product_id):
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'product.template',
            'res_id': product_id,
            'views': [[False, 'form']],
        }
