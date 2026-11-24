# -*- coding: utf-8 -*-
"""A bill of materials for every product the lab makes, by its category.

Only the fifteen costed products had a bill of materials, so only they raised a
job: everything else the lab sells was a service, and an order for a Beggs
appliance or a PMMA crown never reached the floor. This gives every product of a
production category the steps its category is made on, and makes it a good that
is manufactured to order. (client, 2026-11-24)

The lab's answers that decide a product:

* Add-ons to an appliance on the same order - "ADDITIONAL PER UNIT", "TOOTH
  ADDING", "CLASP ADDING", a set of teeth - get no job of their own: they are
  part of the appliance's job.
* Repairs and relines walk the full route of their category.
* A printed or 3D model is a CAD/CAM job (scanning to light curing), whatever
  category the product sits in.
* In Implant Prosthesis and Ceramic, crowns go by material - metal on the full
  metal route, the rest (implant crowns, e.max, veneers) on the zirconia route;
  abutments, implant screws and composite mock-ups get no job.
* Charges, material-cost lines, videos and bought kits are not made here.

Operations only: the minutes are those of the costed product of the same route,
and no components are listed - a bill of materials with components is a costed
one (the Case Cost screen shows only those).
"""
import logging
import re

from odoo import _, api, models
from odoo.exceptions import UserError

from .floor_layout import ROUTES

_logger = logging.getLogger(__name__)

# The production categories and the route each one is made on. Matched on the
# category's own name, case and spaces ignored. 'by_material' is decided per product.
CATEGORY_ROUTE = {
    'ZIRCONIA': 'zirconia',
    'METAL CERAMIC': 'metal_ceramic',
    'FULL METAL CROWNS': 'full_metal',
    'ACRYLIC': 'acrylic',
    'ORTHODONTIC': 'orthodontic',
    'IMPLANT PROSTHEIS': 'by_material',
    'IMPLANT PROSTHESIS': 'by_material',
    'CERAMIC': 'by_material',
}

# Routes that are not a whole sheet. A printed model is a CAD/CAM job: no model to
# prepare, no metal, no ceramic. A special tray and a bite registration are each
# ONE row of the acrylic sheet, sent and returned on their own before the denture.
EXTRA_ROUTES = {
    'model': ['scanning', 'designing', 'printing', 'light_curing', 'final_check', 'packing'],
    'special_tray': ['acrylic_model_preparation', 'special_tray', 'final_check', 'packing'],
    'bite_registration': ['acrylic_model_preparation', 'wax_bite', 'final_check', 'packing'],
}

# The costed product whose minutes a route borrows.
ROUTE_TIMES = {
    'zirconia': 'bom_zirconia_basic',
    'metal_ceramic': 'bom_metal_ceramic',
    'full_metal': 'bom_full_metal',
    'acrylic': 'bom_rpd',
    'orthodontic': 'bom_hawleys',
    'splint': 'bom_essix',
    'model': 'bom_metal_ceramic',
    'special_tray': 'bom_rpd',
    'bite_registration': 'bom_rpd',
}

ROUTE_CODE = {'zirconia': 'ZR', 'metal_ceramic': 'MC', 'full_metal': 'FM', 'acrylic': 'AC',
              'orthodontic': 'OR', 'splint': 'SP', 'model': 'MD', 'special_tray': 'ST',
              'bite_registration': 'BR'}

NOT_MADE = [
    (re.compile(r'COURIER|CHARGE'), 'a charge'),
    (re.compile(r'MATERIAL'), 'a material line'),
    (re.compile(r'VIDEO'), 'not made in the lab'),
    # a kit on its own is bought; an RME sold WITH a headgear kit is still made here
    (re.compile(r'^(?!.*\bR ?M ?E\b).*\bKITS?\b'), 'a bought kit'),
    (re.compile(r'ABUTMENT|ABUDMENT|IMPLANT SCREW'), 'a bought implant part'),
    (re.compile(r'MOCK ?UP|MOCKUP|MOCK TRAIL|MOCK TRIAL'), 'a mock-up'),
]
ADD_ON = re.compile(r'ADDITIONAL|ADDING|TEETH SET')
MODEL = re.compile(r'PRINTED MODEL|\b3 ?D +MODEL')
SPLINT = re.compile(r'ESSIX|SPLINT|ALIGN|ALIGHN|ORAL SCREEN|BLEACHING TRAY')
SPECIAL_TRAY = re.compile(r'SPECIAL TRAY')
BITE_REGISTRATION = re.compile(r'BITE REGISTRATION|BITE BLOCK|BITE RIM')


def _key(name):
    return ' '.join((name or '').upper().split())


class LabMastersLoader(models.AbstractModel):
    _inherit = 'lab.masters.loader'

    @api.model
    def _category_route_of(self, product):
        """(route key or False, why) for one product template."""
        name = _key(product.name)
        category = _key(product.categ_id.name)
        base = CATEGORY_ROUTE.get(category)
        if not base:
            return False, _('not a production category')
        for pattern, why in NOT_MADE:
            if pattern.search(name):
                return False, why
        if ADD_ON.search(name):
            return False, _('an add-on to an appliance on the same order')
        if MODEL.search(name):
            return 'model', _('a printed model')
        if base == 'by_material':
            return ('full_metal' if 'METAL' in name else 'zirconia'), _('by material')
        if base == 'orthodontic' and SPLINT.search(name):
            return 'splint', _('formed, no wire')
        if base == 'acrylic' and SPECIAL_TRAY.search(name):
            return 'special_tray', _('one row of the acrylic sheet')
        if base == 'acrylic' and BITE_REGISTRATION.search(name):
            return 'bite_registration', _('one row of the acrylic sheet')
        return base, _('its category')

    @api.model
    def _category_candidates(self):
        """Products of the production categories that are made, not kept in stock.

        Services (as everything sold was) and goods that are not stock-tracked.
        A stock-tracked product in these categories is a material - acrylic
        teeth, monomer - and is left alone.
        """
        Template = self.env['product.template'].sudo()
        products = Template.search([('sale_ok', '=', True)])
        return products.filtered(
            lambda p: _key(p.categ_id.name) in CATEGORY_ROUTE
            and (p.type == 'service' or (p.type == 'consu' and not p.is_storable))
            and not p.bom_ids)

    @api.model
    def _route_operations(self, route):
        """[(bench, minutes)] for a route, minutes borrowed from its costed product."""
        from .floor_layout import BENCHES
        keys = EXTRA_ROUTES.get(route) or ROUTES[route]
        xmlid_of = {key: xmlid for key, xmlid, *_rest in BENCHES}
        source = self.env.ref('lab_masters.%s' % ROUTE_TIMES[route], raise_if_not_found=False)
        minutes = {}
        if source:
            for op in source.operation_ids:
                minutes.setdefault(op.workcenter_id.id, op.time_cycle_manual)
        steps = []
        for key in keys:
            bench = self.env.ref('lab_masters.%s' % xmlid_of[key], raise_if_not_found=False)
            if not bench or not bench.active:
                raise UserError(_("The work centre for %s is missing or archived.", key))
            steps.append((bench, minutes.get(bench.id, 0.0)))
        return steps

    @api.model
    def make_category_boms(self, products=None, apply=True):
        """Give each product its category's bill of materials and make it a good.

        Returns {'made': [(product, route)], 'skipped': [(product, why)]}. With
        apply=False nothing is written: the same answer, for a review first.
        """
        products = products if products is not None else self._category_candidates()
        manufacture = self.env.ref('mrp.route_warehouse0_manufacture', raise_if_not_found=False)
        mto = self.env.ref('stock.route_warehouse0_mto', raise_if_not_found=False)
        routes = ((manufacture or self.env['stock.route']) | (mto or self.env['stock.route'])) \
            .filtered('product_selectable')
        operations = {}
        made, skipped = [], []
        for product in products.sudo():
            if product.bom_ids:
                skipped.append((product, _('has a bill of materials already')))
                continue
            route, why = self._category_route_of(product)
            if not route:
                skipped.append((product, why))
                continue
            made.append((product, route))
            if not apply:
                continue
            if route not in operations:
                operations[route] = self._route_operations(route)
            if product.type != 'consu' or product.is_storable or \
                    set(routes.ids) - set(product.route_ids.ids):
                product.write({
                    'type': 'consu',
                    'is_storable': False,
                    'route_ids': [(4, r.id) for r in routes],
                })
            self.env['mrp.bom'].sudo().create({
                'product_tmpl_id': product.id,
                'product_qty': 1,
                'type': 'normal',
                'code': '%s-%s' % (ROUTE_CODE[route], product.id),
                'operation_ids': [(0, 0, {
                    'name': bench.name, 'workcenter_id': bench.id,
                    'sequence': index * 10, 'time_mode': 'manual',
                    'time_cycle_manual': minutes,
                }) for index, (bench, minutes) in enumerate(operations[route], start=1)],
            })
        if apply:
            _logger.info("lab_masters: %s products now made on their category's route, %s left "
                         "as they are", len(made), len(skipped))
        return {'made': made, 'skipped': skipped}

    @api.model
    def action_make_category_boms(self, product_ids):
        """The product list's action: the same rule, for the products selected."""
        products = self.env['product.template'].browse(product_ids)
        result = self.make_category_boms(products)
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {
                'title': _("Bills of materials"),
                'message': _("%(made)s made on their category's route, %(left)s left as they are.",
                             made=len(result['made']), left=len(result['skipped'])),
                'type': 'success' if result['made'] else 'warning',
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }
