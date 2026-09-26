# -*- coding: utf-8 -*-
"""Give the records the lab already has the names this module needs.

Sixteen hundred products, twenty locations and two dozen categories came across
from Odoo 17 without external identifiers, so nothing in an XML data file can
point at them. Creating second copies to point at instead would split the lab's
stock and its price list down the middle.

So this finds them - by name, the way a person would - and writes the
``ir.model.data`` row that a later data file's ``ref()`` resolves. Every row is
written ``noupdate``, which is what the lab asked for: a name it corrects later
must survive an upgrade.

The rest of the module then reads like any other: plain ``<record>`` elements
with ``ref('lab_masters.product_emerald_zirconia')`` in them.
"""
import logging

from odoo import api, models

_logger = logging.getLogger(__name__)


# The categories and locations the lab already keeps, and the name this module
# will know each of them by. Matched on the whole name, case and stray spaces
# ignored, because the lab's own data has both.
EXISTING_CATEGORIES = {
    'categ_raw_material': 'RAW MATERIAL',
    'categ_packing_material': 'PACKING MATERIAL',
    'categ_zirconia': 'ZIRCONIA',
    'categ_ceramic': 'CERAMIC',
    'categ_metal_ceramic': 'METAL CERAMIC',
    'categ_full_metal': 'FULL METAL CROWNS',
    'categ_acrylic': 'ACRYLIC',
    'categ_orthodontic': 'ORTHODONTIC',
    'categ_cadcam': 'CAD/CAM',
    'categ_wax_up': 'WAX UP',
    'categ_metal': 'METAL',
    'categ_consumable': 'CONSUMABLE',
    'categ_tools': 'TOOLS & MACHINERIES',
}

# The three departments the lab already keeps, and the place each one takes in
# the new tree. They are ADOPTED, not duplicated: nine employees are filed under
# them, and a second "Administration" beside the first would put half the staff
# on one cost centre and half on another. Tagging them before
# `hr_department_data.xml` loads turns its <record> elements into writes on
# these, which is how they get a code, a kind and a parent.
#
# `sales`, in lower case, is where the field executives sit; it becomes Field
# Sales under Commercial. The lab may want them somewhere else, and moving them
# is one drag in the tree.
ADOPTED_DEPARTMENTS = {
    'dept_admin': 'Administration',
    'dept_accounts': 'Accounts',
    'dept_field_sales': 'sales',
}

EXISTING_LOCATIONS = {
    'loc_wax_up': 'Z-Wax Up Department',
    'loc_cadcam': 'Z- CAD/CAM Department',
    'loc_metal': 'Z- Metal Department',
    'loc_ceramic': 'Z- Ceramic Department',
    'loc_acrylic': 'Z- Acrylic Department',
    'loc_ortho': 'Z-Ortho Department',
}

# The fifteen products *PRODUCTION DETAILS.xlsx* costs, against the name each
# one carries in the lab's own price list. The sheet's names and the price
# list's names are not the same words - "ZIRCONIA PREMIUM Emrald" against
# "ARABIAN EMERALD ZIRCONIA SINGLE UNIT" - so the mapping is written out rather
# than guessed at runtime.
ANCHOR_PRODUCTS = {
    'product_zirconia_emerald': 'ARABIAN EMERALD  ZIRCONIA  SINGLE UNIT',
    'product_zirconia_ruby': 'ARABIAN RUBY ZIRCONIA SINGLE UNIT',
    'product_zirconia_diamond': 'ARABIAN DIAMOND ZIRCONIA SINGLE UNIT',
    'product_zirconia_premium': 'ARABIAN PREMIUM ZIRCONIA',
    'product_zirconia_classic': 'ARABIAN CLASSIC ZIRCONIA',
    'product_zirconia_basic': 'ARABIAN BASIC ZIRCONIA',
    'product_metal_ceramic': 'BASIC Metal Ceramic FULL COVERING',
    'product_full_metal': 'FULL METAL CROWN',
    'product_veneer': 'VENEER',
    'product_hawleys': 'HAWLEYS PER JAW',
    'product_essix': 'ESSIX HARD SPLINT(PER JAW) 1MM',
    'product_banded_rme': 'BANDED RME WITH ABP',
    'product_twin_block': 'TWIN BLOCK',
    'product_rpd': 'Normal  Acrylic RPD SINGLE UNIT',
    'product_bps': 'BPS Denture Ivo base SINGLE UNIT',
}


class LabMastersLoader(models.AbstractModel):
    _name = 'lab.masters.loader'
    _description = "Names the lab's existing records so data files can reach them"

    # ------------------------------------------------------------------ tagging
    @api.model
    def _tag(self, model, xmlid, record):
        """Write the ``ir.model.data`` row, or leave an existing one alone.

        Leaving it alone matters: on a second upgrade the record may have been
        renamed or replaced by hand, and re-pointing the identifier at whatever
        now happens to match the old name would move every bill of materials
        with it.
        """
        data = self.env['ir.model.data'].sudo()
        existing = data.search([('module', '=', 'lab_masters'), ('name', '=', xmlid)])
        if existing:
            return existing.res_id
        data.create({
            'module': 'lab_masters',
            'name': xmlid,
            'model': model,
            'res_id': record.id,
            'noupdate': True,
        })
        return record.id

    @api.model
    def _find_one(self, model, name, domain=None):
        """The one record with this name, or nothing, with a word either way.

        Matched on the trimmed, case-folded name because the lab's price list
        has ``' FULL METAL CROWN '`` with a space at each end and
        ``'Normal  Acrylic RPD SINGLE UNIT'`` with two in the middle. Where two
        records share a name - and a handful do, the price list having been
        keyed twice - the oldest wins, because that is the one the twenty-six
        thousand orders point at.
        """
        records = self.env[model].sudo().with_context(active_test=False).search(
            (domain or []) + [('name', 'ilike', name.strip())], order='id')
        wanted = name.strip().casefold()
        exact = records.filtered(lambda r: (r.name or '').strip().casefold() == wanted)
        if not exact:
            _logger.warning("lab_masters: no %s named %r; nothing tagged", model, name)
            return self.env[model]
        if len(exact) > 1:
            _logger.info("lab_masters: %s records named %r; taking the oldest (id %s)",
                         len(exact), name, exact[0].id)
        return exact[0]

    @api.model
    def tag_existing(self):
        """Called from the data file, before anything that refers to these."""
        for xmlid, name in EXISTING_CATEGORIES.items():
            record = self._find_one('product.category', name)
            if record:
                self._tag('product.category', xmlid, record)
        for xmlid, name in ADOPTED_DEPARTMENTS.items():
            record = self._find_one('hr.department', name)
            if record:
                self._tag('hr.department', xmlid, record)
        for xmlid, name in EXISTING_LOCATIONS.items():
            record = self._find_one('stock.location', name, [('usage', '=', 'internal')])
            if record:
                self._tag('stock.location', xmlid, record)
        for xmlid, name in ANCHOR_PRODUCTS.items():
            record = self._find_one('product.template', name)
            if record:
                self._tag('product.template', xmlid, record)
        return True

    # -------------------------------------------------------- making them goods
    @api.model
    def make_anchors_manufacturable(self):
        """Turn the costed products from services into goods.

        Everything the lab sells is a ``service`` today, which is why a database
        with twenty-six thousand orders holds no manufacturing order at all: a
        bill of materials cannot be written against a service, and Odoo will not
        raise a job for one. ``sale_custom.action_confirm`` already plans the
        manufacturing orders an order generates, and ``lab_workcenter_scan`` is
        a station board for their work orders - the suite was built expecting
        these, and only the product type stood in the way.

        ``is_storable`` stays off, so a crown gets a job and a route but no
        quants and no valuation layer of its own: the lab does not hold finished
        crowns in stock, it makes one and sends it out the same week.
        """
        manufacture = self.env.ref('mrp.route_warehouse0_manufacture', raise_if_not_found=False)
        mto = self.env.ref('stock.route_warehouse0_mto', raise_if_not_found=False)
        routes = manufacture or self.env['stock.route']
        if mto:
            # Replenish on Order: the job is raised for the case in hand, not
            # for a forecast. A dental crown is made for one patient's mouth.
            routes |= mto
            if not mto.active:
                mto.sudo().active = True
        changed = self.env['product.template']
        for xmlid in ANCHOR_PRODUCTS:
            product = self.env.ref('lab_masters.%s' % xmlid, raise_if_not_found=False)
            if not product or product.type != 'service':
                continue
            product.sudo().write({
                'type': 'consu',
                'is_storable': False,
                'route_ids': [(6, 0, routes.ids)],
            })
            changed |= product
        if changed:
            _logger.info("lab_masters: %s costed products are now manufactured goods",
                         len(changed))
        return True

    # ------------------------------------------------- departments and stores
    @api.model
    def link_departments(self):
        """Point each department at its sub-store, and the store at the lab's.

        The Inventory SOP has the main store issuing material to a department
        against a request, and the lab already keeps a location per department
        for exactly that. Naming them on the department is what lets a
        consumption move find its cost centre without anyone choosing one.
        """
        pairs = [
            ('dept_wax_up', 'loc_wax_up'),
            ('dept_cadcam', 'loc_cadcam'),
            ('dept_metal', 'loc_metal'),
            ('dept_ceramic', 'loc_ceramic'),
            ('dept_acrylic', 'loc_acrylic'),
            ('dept_ortho', 'loc_ortho'),
        ]
        for dept_xmlid, loc_xmlid in pairs:
            department = self.env.ref('lab_masters.%s' % dept_xmlid, raise_if_not_found=False)
            location = self.env.ref('lab_masters.%s' % loc_xmlid, raise_if_not_found=False)
            if not department or not location:
                continue
            department.sudo().write({
                'store_location_id': location.id,
                'consumption_location_id': location.id,
            })
            if not location.department_id:
                location.sudo().department_id = department.id

        stock = self.env.ref('stock.stock_location_stock', raise_if_not_found=False)
        store = self.env.ref('lab_masters.dept_store', raise_if_not_found=False)
        if stock and store:
            store.sudo().store_location_id = stock.id
            if not stock.department_id:
                stock.sudo().department_id = store.id
        return True

    @api.model
    def stamp_workcenter_analytic(self):
        """Point every bench's analytic distribution at its own cost centre.

        The bench carries a distribution of its own, which core uses to post the
        cost of the time worked at it. `lab_cost_centre` fills it from an
        onchange, and an onchange never fires for a record a data file creates -
        so fifty-seven benches would arrive with a department and no way to post
        against it.

        Only a blank one is filled. A bench somebody has split between two
        centres by hand stays split.
        """
        benches = self.env['mrp.workcenter'].sudo().search([
            ('cost_centre_id', '!=', False),
            ('analytic_distribution', '=', False),
        ])
        for bench in benches:
            bench.analytic_distribution = {str(bench.cost_centre_id.id): 100.0}
        _logger.info("lab_masters: %s benches now post to their own cost centre",
                     len(benches))
        return True

    @api.model
    def link_categories(self):
        """Point the lab's own product categories at the department that owns them.

        Every purchase line, every consumption move and every valuation entry
        for a product in one of these lands on that department's cost centre
        from here on, because ``lab_cost_centre`` reads the category when
        nothing more specific says otherwise.
        """
        pairs = [
            ('categ_zirconia', 'dept_cadcam'),
            ('categ_cadcam', 'dept_cadcam'),
            ('categ_ceramic', 'dept_ceramic'),
            ('categ_metal_ceramic', 'dept_ceramic'),
            ('categ_full_metal', 'dept_metal'),
            ('categ_metal', 'dept_metal'),
            ('categ_wax_up', 'dept_wax_up'),
            ('categ_acrylic', 'dept_acrylic'),
            ('categ_orthodontic', 'dept_ortho'),
            ('categ_packing_material', 'dept_packing'),
            ('categ_raw_material', 'dept_store'),
            ('categ_consumable', 'dept_store'),
            ('categ_tools', 'dept_it'),
        ]
        for categ_xmlid, dept_xmlid in pairs:
            category = self.env.ref('lab_masters.%s' % categ_xmlid, raise_if_not_found=False)
            department = self.env.ref('lab_masters.%s' % dept_xmlid, raise_if_not_found=False)
            # Never argue with a person: a category somebody has already filed
            # under a department stays where they put it.
            if category and department and not category.department_id:
                category.sudo().department_id = department.id
        return True


def uninstall_hook(env):
    """Let go of the lab's own records before Odoo deletes this module's.

    Uninstalling a module deletes every record it has an ``ir.model.data`` row
    for - and this module wrote rows for records it did not create: the RAW
    MATERIAL category, six department stores, three departments with nine
    employees filed under them, and the fifteen products that carry a third of
    everything the lab has ever sold.

    Without this, uninstalling would take all of them with it. This runs first
    (``loading.py`` calls the hook before ``module_uninstall``) and drops the
    rows, so Odoo finds nothing of the lab's to delete.

    The fifteen products go back to being services, which is what they were, but
    only where no job has been raised against them yet. Once the floor has
    worked a case, the product stays a good: a manufacturing order against a
    service is a record nobody can open.
    """
    data = env['ir.model.data'].sudo()
    borrowed = list(EXISTING_CATEGORIES) + list(ADOPTED_DEPARTMENTS) \
        + list(EXISTING_LOCATIONS) + list(ANCHOR_PRODUCTS)

    anchors = env['product.template']
    for xmlid in ANCHOR_PRODUCTS:
        product = env.ref('lab_masters.%s' % xmlid, raise_if_not_found=False)
        if product:
            anchors |= product
    if anchors:
        worked = env['mrp.production'].sudo().search([
            ('product_id', 'in', anchors.product_variant_ids.ids)]).product_id.product_tmpl_id
        untouched = anchors - worked
        if untouched:
            untouched.sudo().write({'type': 'service', 'route_ids': [(5, 0, 0)]})
            _logger.info("lab_masters: %s products returned to services on uninstall",
                         len(untouched))
        if worked:
            _logger.info("lab_masters: %s products stay goods; jobs exist against them",
                         len(worked))

    rows = data.search([('module', '=', 'lab_masters'), ('name', 'in', borrowed)])
    _logger.info("lab_masters: releasing %s of the lab's own records", len(rows))
    rows.unlink()
