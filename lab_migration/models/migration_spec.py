# -*- coding: utf-8 -*-
"""Declarative, dependency-ordered specification of what to sync from Odoo 17.

The source is Arabian Dental Lab's production database (Odoo 17, l10n_in), whose
custom modules are NOT carried over. Their fields are mapped onto the fields the
lab suite already has; a source field with no home here is named in the README so
the decision is visible, not silent.

Each spec:
  key       : short id (also the hook suffix -> _hook_<key> on the backend)
  name      : label
  src       : Odoo 17 table name
  dst       : Odoo 19 model
  src_sql   : optional SELECT replacing "SELECT * FROM src"
  where     : optional extra SQL WHERE (string)
  match     : list of dst fields to also match an EXISTING v19 record on
              (besides x_src_id) so we adopt built-ins instead of duplicating
  scalars   : {dst_field: src_column}         copied verbatim (pass 1)
  m2o       : {dst_field: (src_column, dst_model)}   resolved via x_src_id (pass 2)
  m2m       : {dst_field: (rel_table, this_col, other_col, dst_model)} (pass 2)
  static    : {dst_field: constant}           set in pass 1
  create    : if False, only ADOPT existing v19 records (never create new)
  require   : dst relation fields that must resolve or the row is skipped

Translated fields (jsonb in Odoo 16+) are unwrapped to their en_US value by the
backend's ``_coerce`` before any of this is written.
"""

ENTITY_SPECS = [
    {
        'key': 'company', 'name': 'Company', 'src': 'res_company', 'dst': 'res.company',
        'match': ['name'], 'create': False,
        # the rest of the company is written by _write_company
        'scalars': {},
    },
    {
        # v19 rewrote uom.uom (relative_factor/relative_uom_id, no category/uom_type).
        # Built-in units are ADOPTED by name — case-insensitively, and through the
        # aliases below ("Nos" is what the lab calls Units, on 1,345 products). The
        # lab's own labels (SET, BOX, PKT, BOTTLE...) are created as units of their
        # category's reference, with the source factor, in _hook_uom.
        'key': 'uom', 'name': 'Units of Measure', 'src': 'uom_uom', 'dst': 'uom.uom',
        'match': ['name'],
        'scalars': {'name': 'name', 'rounding': 'rounding', 'active': 'active'},
    },
    {
        'key': 'product_category', 'name': 'Product Categories', 'src': 'product_category',
        'dst': 'product.category', 'match': ['complete_name'],
        'scalars': {'name': 'name', 'complete_name': 'complete_name'},
        'm2o': {'parent_id': ('parent_id', 'product.category')},
    },
    {
        # The lab's SHADE (A2, 2M2, B3...) is what the suite calls the colour scheme of
        # a work: the same thing under the ortho name. 878 distinct shades on the
        # source, some with stray commas; matched on the cleaned name so "A3," and
        # "A3" become one colour.
        'key': 'colour', 'name': 'Shades → Colours', 'src': 'res_shade', 'dst': 'product.colour',
        'match': ['name'],
        'scalars': {'name': 'name'},
    },
    {
        # Districts become partner TAGS: the suite has no district field, and a tag
        # is searchable, groupable and needs no schema. Spellings are normalised in
        # the hook (WAYAND -> WAYANAD, TRISSUR -> THRISSUR, MALAPPURA. -> MALAPPURAM).
        'key': 'district', 'name': 'Districts → Partner Tags', 'src': 'res_district',
        'dst': 'res.partner.category', 'match': ['name'],
        'scalars': {'name': 'name'},
    },
    {
        # Same l10n_in chart on both sides, so nearly every account is ADOPTED by code.
        # account_type is copied as-is (v17 and v19 share the selection).
        'key': 'account', 'name': 'Chart of Accounts', 'src': 'account_account',
        'dst': 'account.account', 'company_field': 'company_ids', 'match': ['code'],
        'scalars': {'code': 'code', 'name': 'name', 'account_type': 'account_type',
                    'reconcile': 'reconcile', 'deprecated': 'deprecated',
                    'non_trade': 'non_trade', 'note': 'note'},
    },
    {
        'key': 'tax_group', 'name': 'Tax Groups', 'src': 'account_tax_group',
        'dst': 'account.tax.group', 'match': ['name'],
        'scalars': {'name': 'name', 'sequence': 'sequence'},
    },
    {
        # The source used l10n_in's own tax names ("5% GST S", "2.5% SGST S"...), so
        # the built-in v19 taxes are adopted with their repartition and report tags
        # intact. Only the lab's own taxes (CESS variants, "0.1% G 206C(1H)") are
        # created; group taxes get their children through the filiation table.
        # By NAME alone: the lab re-typed some l10n_in child taxes ("2.5% CGST P"
        # is 'purchase' there, 'none' here), and the name carries the rate anyway.
        'key': 'tax', 'name': 'Taxes', 'src': 'account_tax', 'dst': 'account.tax',
        'company_field': 'company_id', 'match': ['name'],
        'scalars': {'name': 'name', 'amount': 'amount', 'amount_type': 'amount_type',
                    'type_tax_use': 'type_tax_use', 'price_include': 'price_include',
                    'active': 'active', 'sequence': 'sequence', 'description': 'description',
                    'invoice_label': 'invoice_label', 'tax_scope': 'tax_scope',
                    'include_base_amount': 'include_base_amount',
                    'is_base_affected': 'is_base_affected',
                    'l10n_in_reverse_charge': 'l10n_in_reverse_charge'},
        'm2o': {'tax_group_id': ('tax_group_id', 'account.tax.group')},
        'm2m': {'children_tax_ids': ('account_tax_filiation_rel', 'parent_tax', 'child_tax',
                                     'account.tax')},
    },
    {
        # No company_field: v19 ships its terms company-less (shared), and a match
        # restricted to the company would miss every one of them and duplicate the set.
        'key': 'payment_term', 'name': 'Payment Terms', 'src': 'account_payment_term',
        'dst': 'account.payment.term', 'match': ['name'],
        'scalars': {'name': 'name', 'note': 'note', 'active': 'active',
                    'sequence': 'sequence'},
        # lines for NEW terms are written by _hook_payment_term
    },
    {
        'key': 'fiscal_position', 'name': 'Fiscal Positions', 'src': 'account_fiscal_position',
        'dst': 'account.fiscal.position', 'company_field': 'company_id', 'match': ['name'],
        'scalars': {'name': 'name', 'note': 'note', 'active': 'active',
                    'sequence': 'sequence', 'auto_apply': 'auto_apply'},
    },
    {
        # Sales teams are the lab's ROUTES (MALAPPURAM, PATTAMBI, MANJERI...). The
        # partner keeps its route in sale_custom's res.partner.team_id.
        'key': 'crm_team', 'name': 'Sales Routes (Teams)', 'src': 'crm_team', 'dst': 'crm.team',
        'match': ['name'],
        'static': {'company_id': False},
        'scalars': {'name': 'name', 'active': 'active', 'sequence': 'sequence',
                    'invoiced_target': 'invoiced_target'},
        'm2o': {'user_id': ('user_id', 'res.users')},
    },
    {
        'key': 'partner', 'name': 'Partners', 'src': 'res_partner', 'dst': 'res.partner',
        # The company's own partner is the target company's partner already.
        'where': "id NOT IN (SELECT partner_id FROM res_company) "
                 "AND id NOT IN (SELECT partner_id FROM res_users WHERE share = false)",
        'static': {'company_id': False},
        'scalars': {
            'name': 'name', 'street': 'street', 'street2': 'street2', 'city': 'city',
            'zip': 'zip', 'phone': 'phone', 'email': 'email', 'website': 'website',
            'vat': 'vat', 'ref': 'ref', 'function': 'function', 'is_company': 'is_company',
            'active': 'active', 'type': 'type', 'comment': 'comment', 'lang': 'lang',
            'tz': 'tz', 'customer_rank': 'customer_rank', 'supplier_rank': 'supplier_rank',
            'l10n_in_gst_treatment': 'l10n_in_gst_treatment', 'l10n_in_pan': 'l10n_in_pan',
            'company_registry': 'company_registry', 'partner_latitude': 'partner_latitude',
            'partner_longitude': 'partner_longitude',
            # the suite's own statutory fields, from the same source columns
            'gst_number': 'vat', 'pan_number': 'l10n_in_pan',
        },
        'm2o': {'parent_id': ('parent_id', 'res.partner'),
                'team_id': ('team_id', 'crm.team'),
                'user_id': ('user_id', 'res.users'),
                'state_id': ('state_id', 'res.country.state'),
                'country_id': ('country_id', 'res.country')},
        # The source's six partner tags are Odoo's demo ones ("Computer services"),
        # so they stay behind; the district becomes the tag: _hook_partner.
        # district tag, doctor split, is_clinic/is_doctor, phone fallback: _hook_partner
    },
    {
        'key': 'product_template', 'name': 'Product Templates', 'src': 'product_template',
        'dst': 'product.template', 'company_field': 'company_id',
        'scalars': {
            'name': 'name', 'default_code': 'default_code', 'list_price': 'list_price',
            'weight': 'weight', 'volume': 'volume', 'sale_ok': 'sale_ok',
            'purchase_ok': 'purchase_ok', 'tracking': 'tracking', 'active': 'active',
            'description': 'description', 'description_sale': 'description_sale',
            'description_purchase': 'description_purchase',
            'description_picking': 'description_picking',
            'invoice_policy': 'invoice_policy', 'sale_delay': 'sale_delay',
            'sequence': 'sequence', 'service_type': 'service_type',
            'purchase_method': 'purchase_method',
            # HSN: l10n_in's own field and the suite's legacy one, from one column
            'l10n_in_hsn_code': 'l10n_in_hsn_code', 'hsn_number': 'l10n_in_hsn_code',
            'l10n_in_hsn_description': 'l10n_in_hsn_description',
        },
        'm2o': {'categ_id': ('categ_id', 'product.category'),
                'uom_id': ('uom_id', 'uom.uom'),
                'uom_po_id': ('uom_po_id', 'uom.uom')},
        'm2m': {'taxes_id': ('product_taxes_rel', 'prod_id', 'tax_id', 'account.tax'),
                'supplier_taxes_id': ('product_supplier_taxes_rel', 'prod_id', 'tax_id', 'account.tax')},
        # type/is_storable in _hook_product_template
    },
    {
        'key': 'product_product', 'name': 'Product Variants', 'src': 'product_product',
        'dst': 'product.product',
        # handled almost entirely by _sync_products (adopt the template's variant)
        'scalars': {'default_code': 'default_code', 'barcode': 'barcode', 'active': 'active'},
    },
    {
        'key': 'supplierinfo', 'name': 'Vendor Pricelists', 'src': 'product_supplierinfo',
        'dst': 'product.supplierinfo', 'company_field': 'company_id',
        'require': ['product_tmpl_id', 'partner_id'],
        'scalars': {'price': 'price', 'min_qty': 'min_qty', 'delay': 'delay',
                    'product_code': 'product_code', 'product_name': 'product_name',
                    'date_start': 'date_start', 'date_end': 'date_end',
                    'discount': 'discount', 'sequence': 'sequence'},
        'm2o': {'partner_id': ('partner_id', 'res.partner'),
                'product_tmpl_id': ('product_tmpl_id', 'product.template'),
                'product_id': ('product_id', 'product.product')},
    },
    {
        # The lab's department stores (Z- Ceramic Department, Z-Ortho Department...)
        # AND its consumption sinks ("Z-Acrylic Manufacturing Dept", "Marketing" -
        # usage 'inventory', where 252 material requests and the consumption moves
        # go). Every location comes across: Odoo's own (WH/Stock, Partners/Vendors,
        # Virtual Locations/Inventory adjustment...) are adopted by full name, the
        # rest created under their mapped parent, so transfers, adjustments and
        # on-hand keep their real location.
        'key': 'stock_location', 'name': 'Stock Locations', 'src': 'stock_location',
        'dst': 'stock.location', 'company_field': 'company_id', 'match': ['complete_name'],
        'scalars': {'name': 'name', 'complete_name': 'complete_name', 'usage': 'usage',
                    'active': 'active', 'scrap_location': 'scrap_location',
                    'return_location': 'return_location', 'comment': 'comment',
                    'barcode': 'barcode'},
        'm2o': {'location_id': ('location_id', 'stock.location')},
    },
    {
        # One warehouse on both sides; adopted by code so its picking types and
        # the reordering rules can point at it.
        'key': 'warehouse', 'name': 'Warehouses', 'src': 'stock_warehouse',
        'dst': 'stock.warehouse', 'company_field': 'company_id', 'match': ['code'],
        'create': False,
        'scalars': {'name': 'name', 'code': 'code', 'reception_steps': 'reception_steps',
                    'delivery_steps': 'delivery_steps'},
    },
    {
        # The warehouse's operation types are v19's own (created with the
        # warehouse); adopting them by their short code keeps each transfer on
        # the exact type it had, sequences included.
        'key': 'picking_type', 'name': 'Operation Types', 'src': 'stock_picking_type',
        'dst': 'stock.picking.type', 'company_field': 'company_id',
        'match': ['sequence_code', 'warehouse_id'],
        'scalars': {'name': 'name', 'sequence_code': 'sequence_code', 'code': 'code',
                    'sequence': 'sequence', 'active': 'active',
                    'reservation_method': 'reservation_method',
                    'create_backorder': 'create_backorder', 'barcode': 'barcode'},
        'm2o': {'warehouse_id': ('warehouse_id', 'stock.warehouse'),
                'default_location_src_id': ('default_location_src_id', 'stock.location'),
                'default_location_dest_id': ('default_location_dest_id', 'stock.location'),
                'return_picking_type_id': ('return_picking_type_id', 'stock.picking.type')},
    },
    {
        'key': 'orderpoint', 'name': 'Reordering Rules', 'src': 'stock_warehouse_orderpoint',
        'dst': 'stock.warehouse.orderpoint', 'company_field': 'company_id',
        'require': ['product_id', 'location_id'],
        'scalars': {'name': 'name', 'product_min_qty': 'product_min_qty',
                    'product_max_qty': 'product_max_qty', 'qty_multiple': 'qty_multiple',
                    'trigger': 'trigger', 'active': 'active',
                    'snoozed_until': 'snoozed_until'},
        'm2o': {'product_id': ('product_id', 'product.product'),
                'location_id': ('location_id', 'stock.location'),
                'warehouse_id': ('warehouse_id', 'stock.warehouse')},
    },
    {
        'key': 'hr_department', 'name': 'Departments', 'src': 'hr_department',
        'dst': 'hr.department', 'company_field': 'company_id', 'match': ['name'],
        'scalars': {'name': 'name', 'active': 'active', 'note': 'note'},
        'm2o': {'parent_id': ('parent_id', 'hr.department')},
    },
    {
        'key': 'hr_job', 'name': 'Job Positions', 'src': 'hr_job', 'dst': 'hr.job',
        'company_field': 'company_id', 'match': ['name'],
        'scalars': {'name': 'name', 'active': 'active', 'description': 'description',
                    'requirements': 'requirements'},
        'm2o': {'department_id': ('department_id', 'hr.department')},
    },
    {
        # Attendance (Start my day) needs an employee behind every field user, so the
        # employees come across with their user link. Private data stays behind:
        # only the work identity is needed here.
        'key': 'hr_employee', 'name': 'Employees', 'src': 'hr_employee', 'dst': 'hr.employee',
        'company_field': 'company_id', 'match': ['name'],
        'scalars': {'name': 'name', 'active': 'active', 'job_title': 'job_title',
                    'work_phone': 'work_phone', 'mobile_phone': 'mobile_phone',
                    'work_email': 'work_email', 'gender': 'gender', 'birthday': 'birthday',
                    'employee_type': 'employee_type', 'barcode': 'barcode', 'pin': 'pin',
                    'notes': 'notes'},
        'm2o': {'user_id': ('user_id', 'res.users'),
                'department_id': ('department_id', 'hr.department'),
                'job_id': ('job_id', 'hr.job'),
                'parent_id': ('parent_id', 'hr.employee'),
                'coach_id': ('coach_id', 'hr.employee')},
    },
    {
        # v17 mrp.workcenter carries its own name (no resource join as in v10).
        'key': 'workcenter', 'name': 'Work Centers', 'src': 'mrp_workcenter',
        'dst': 'mrp.workcenter', 'company_field': 'company_id', 'match': ['name'],
        'scalars': {'name': 'name', 'code': 'code', 'sequence': 'sequence',
                    'note': 'note', 'color': 'color', 'time_start': 'time_start',
                    'time_stop': 'time_stop', 'oee_target': 'oee_target',
                    'time_efficiency': 'time_efficiency', 'active': 'active'},
    },
    {
        'key': 'bom', 'name': 'Bills of Material', 'src': 'mrp_bom', 'dst': 'mrp.bom',
        'company_field': 'company_id', 'require': ['product_tmpl_id'],
        'scalars': {'code': 'code', 'product_qty': 'product_qty', 'type': 'type',
                    'active': 'active', 'ready_to_produce': 'ready_to_produce'},
        'm2o': {'product_tmpl_id': ('product_tmpl_id', 'product.template'),
                'product_id': ('product_id', 'product.product'),
                'product_uom_id': ('product_uom_id', 'uom.uom')},
    },
    {
        'key': 'bom_line', 'name': 'BoM Lines', 'src': 'mrp_bom_line', 'dst': 'mrp.bom.line',
        'require': ['bom_id', 'product_id'],
        'scalars': {'product_qty': 'product_qty', 'sequence': 'sequence'},
        'm2o': {'bom_id': ('bom_id', 'mrp.bom'),
                'product_id': ('product_id', 'product.product'),
                'product_uom_id': ('product_uom_id', 'uom.uom')},
    },
]

# Source district spellings that are the same place. Applied before the tag is
# matched, so every spelling lands on ONE tag.
DISTRICT_ALIASES = {
    'WAYAND': 'WAYANAD',
    'TRISSUR': 'THRISSUR',
    'MALAPPURA.': 'MALAPPURAM',
    'CALICUT': 'KOZHIKODE',
    'GUDALLUR': 'GUDALUR',
    'ALAPUZHA': 'ALAPPUZHA',
    'TIRIPUR': 'TIRUPUR',
}

# Source unit names that are a built-in v19 unit under another spelling.
UOM_ALIASES = {'NOS': 'Units', 'ML': 'ml', 'MTR': 'm', 'T': 'Ton', 'KGS': 'kg', 'GMS': 'g'}
# v17 unit category -> the v19 unit every unit of that category is relative to.
UOM_CATEGORY_REFERENCE = {
    'Nos': 'Units', 'Unit': 'Units', 'Weight': 'kg', 'Volume': 'L',
    'Length / Distance': 'm', 'Working Time': 'Hours', 'Surface': 'm²',
}

# The lab's export fiscal positions under l10n_in's current names.
FISCAL_POSITION_ALIASES = {
    'EXPORT/SEZ': 'Export',
    'LUT - EXPORT/SEZ': 'Export - LUT (WOP)',
}

# dental_sale's three-level priority -> the suite's. The suite's fourth value,
# `emergency`, is above `urgent` and is never assigned by a migration.
PRIORITY_MAP = {'high': 'urgent', 'medium': 'normal', 'low': 'low'}

# dental_sale's jaw -> the suite's upper/lower selection on a line.
JAW_MAP = {'upper': 'upper', 'lower': 'lower', 'upper_lower': 'ul'}


def teeth_from_quadrants(row):
    """FDI tooth numbers from dental_sale's four quadrant fields plus its free-text
    tooth number, in one string.

    The source keeps "4 3" in the 1st-quadrant column to mean teeth 14 and 13 (FDI:
    quadrant digit then tooth digit) and "46,47" already in FDI form in ``t_no``.
    The single string this returns ("14, 13, 46, 47") is what a technician reads
    on the job card and what the invoice prints.
    """
    import re
    teeth = []
    for quadrant in (1, 2, 3, 4):
        raw = (row.get('quad%d' % quadrant) or '').strip()
        for tooth in re.split(r'[\s,;/]+', raw):
            if tooth.isdigit():
                # a bare "4" is tooth 4 of that quadrant; "14" is already FDI
                teeth.append(tooth if len(tooth) == 2 else '%d%s' % (quadrant, tooth))
    raw = (row.get('t_no') or '').strip()
    for tooth in re.split(r'[\s,;/]+', raw):
        if tooth:
            teeth.append(tooth)
    if row.get('quarter') and not teeth:
        teeth.append(row['quarter'].upper())
    # keep order of entry, drop repeats
    return ', '.join(dict.fromkeys(teeth))
