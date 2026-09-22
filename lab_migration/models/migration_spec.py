# -*- coding: utf-8 -*-
"""Declarative, dependency-ordered specification of what to sync from Odoo 10.

Each spec:
  key       : short id (also the hook suffix -> _hook_<key> on the backend)
  name      : label
  src       : Odoo 10 table name
  dst       : Odoo 19 model
  where     : optional extra SQL WHERE (string)
  match     : list of dst fields to also match an EXISTING v19 record on
              (besides x_odoo10_id) so we adopt built-ins instead of duplicating
  scalars   : {dst_field: src_column}         copied verbatim (pass 1)
  m2o       : {dst_field: (src_column, dst_model)}   resolved via x_odoo10_id (pass 2)
  m2m       : {dst_field: (rel_table, this_col, other_col, dst_model)} (pass 2)
  static    : {dst_field: constant}           set in pass 1
  create    : if False, only ADOPT existing v19 records (never create new)
"""

ENTITY_SPECS = [
    {
        'key': 'company', 'name': 'Company', 'src': 'res_company', 'dst': 'res.company',
        'match': ['name'], 'create': False,
        'scalars': {
            'gst_number': 'gst_number', 'pan_number': 'pan_number',
            'emergency_service_perc': 'emergency_service_perc',
            'company_warning': 'company_warning',
            'logo1': 'logo1', 'logo2': 'logo2',
            'invoice_signature': 'invoice_signature', 'excel_logo': 'excel_logo',
        },
    },
    {
        # v18/19 rewrote uom.uom (relative_factor/relative_uom_id, no category/uom_type).
        # We ADOPT the built-in v19 UoMs by name and tag them with x_odoo10_id; we do
        # NOT recreate them. Products whose v10 UoM has no name match keep the v19 default.
        'key': 'uom', 'name': 'Units of Measure', 'src': 'product_uom', 'dst': 'uom.uom',
        'match': ['name'], 'create': False,
        'scalars': {'name': 'name'},
    },
    {
        'key': 'product_category', 'name': 'Product Categories', 'src': 'product_category',
        'dst': 'product.category',
        'scalars': {'name': 'name'},
        'm2o': {'parent_id': ('parent_id', 'product.category')},
    },
    {
        'key': 'appliance_style', 'name': 'Appliance Styles', 'src': 'product_appliance_style',
        'dst': 'product.appliance.style', 'match': ['name'],
        'scalars': {'name': 'name', 'description': 'description', 'active': 'active'},
    },
    {
        'key': 'colour', 'name': 'Colours', 'src': 'product_colour', 'dst': 'product.colour',
        'match': ['name'],
        'scalars': {'name': 'name', 'description': 'description', 'active': 'active'},
    },
    {
        'key': 'send_through', 'name': 'Send Through', 'src': 'send_through', 'dst': 'send.through',
        'match': ['name'],
        'scalars': {'name': 'name', 'description': 'description', 'active': 'active'},
    },
    {
        'key': 'account', 'name': 'Chart of Accounts', 'src': 'account_account',
        'dst': 'account.account', 'company_field': 'company_ids', 'match': ['code'],
        'scalars': {'code': 'code', 'name': 'name'},  # reconcile handled in _hook_account
        # account_type computed in _hook_account (pass 1)
    },
    {
        'key': 'tax_group', 'name': 'Tax Groups', 'src': 'account_tax_group',
        'dst': 'account.tax.group', 'match': ['name'],
        'scalars': {'name': 'name'},
        # country_id set in _hook_tax_group
    },
    {
        'key': 'tax', 'name': 'Taxes', 'src': 'account_tax', 'dst': 'account.tax', 'company_field': 'company_id',
        'match': ['name', 'type_tax_use'],
        # price_include is read here but translated in _hook_tax: v19 computes it
        # from price_include_override, so writing it directly is a no-op.
        'scalars': {'name': 'name', 'amount': 'amount', 'amount_type': 'amount_type',
                    'type_tax_use': 'type_tax_use', 'price_include': 'price_include',
                    'active': 'active'},
        'm2o': {'tax_group_id': ('tax_group_id', 'account.tax.group')},
        # country_id set in _hook_tax
    },
    {
        'key': 'payment_term', 'name': 'Payment Terms', 'src': 'account_payment_term',
        'dst': 'account.payment.term', 'company_field': 'company_id', 'match': ['name'],
        'scalars': {'name': 'name', 'note': 'note', 'active': 'active'},
    },
    {
        # Sales teams are COMMON to both companies. crm.team.company_id has no
        # default, so an unset company means "shared across all companies" —
        # which is also how 24 of the 46 v10 teams are already stored (NULL).
        # Deliberately NO 'company_field': _company_val falls back to
        # env.company for a NULL source company, and env.company follows
        # allowed_company_ids order (Aligners first), which silently stamped
        # every shared team onto the wrong company. 'static' forces the value on
        # create AND on re-sync, so an already-migrated DB self-heals.
        'key': 'crm_team', 'name': 'Sales Teams', 'src': 'crm_team', 'dst': 'crm.team',
        'match': ['name'],
        'static': {'company_id': False},
        'scalars': {'name': 'name', 'phone': 'phone', 'address': 'address',
                    'use_quotations': 'use_quotations', 'active': 'active'},
        # Team Leader: drives the salesperson of every order/invoice on the route.
        'm2o': {'user_id': ('user_id', 'res.users')},
    },
    {
        # Partners are COMMON to both companies (client, 2026-08-20). A partner with
        # company_id set is invisible to the other company (core rule: company_id is
        # False OR in the allowed companies), which hid 6,972 doctors from Aligners and
        # left 167 ledger lines pointing at a partner that company could not open.
        # Deliberately NO 'company_field': _company_val falls back to env.company for a
        # NULL source company, so a re-sync would stamp one back on. 'static' forces the
        # value on create AND on re-sync, so an already-migrated DB self-heals — the
        # same treatment as crm_team above. Company-DEPENDENT fields (receivable /
        # payable accounts, pricelist) are unaffected: they stay per company.
        'key': 'partner', 'name': 'Partners', 'src': 'res_partner', 'dst': 'res.partner',
        'static': {'company_id': False},
        'scalars': {
            'name': 'name', 'street': 'street', 'street2': 'street2', 'city': 'city',
            'zip': 'zip', 'phone': 'phone', 'mobile': 'mobile', 'email': 'email',
            'website': 'website', 'vat': 'vat', 'ref': 'ref', 'function': 'function',
            'is_company': 'is_company', 'active': 'active', 'type': 'type', 'comment': 'comment',
            # dental / lab custom fields
            'gst_number': 'gst_number', 'pan_number': 'pan_number', 'dci_number': 'dci_number',
            'is_clinic': 'is_clinic', 'is_doctor': 'is_doctor', 'cr_number': 'cr_number',
            'contact_person': 'contact_person', 'hospital_reg_no': 'hospital_reg_no',
            'vendor_code': 'vendor_code', 'evaluation_period': 'evaluation_period',
            'total_supplies': 'total_supplies', 'qty_rejected': 'qty_rejected',
            'no_qty_rejected': 'no_qty_rejected', 'delivery_performance': 'delivery_performance',
            'invoicing': 'invoicing', 'rating': 'rating', 'quality_certificate': 'quality_certificate',
            'major_clients': 'major_clients', 'approved_by': 'approved_by',
            'activity_type': 'activity_type', 'iso_status': 'iso_status',
            'vendor_status': 'vendor_status', 'credit_period': 'credit_period',
            'no_employees': 'no_employees',
        },
        'm2o': {'parent_id': ('parent_id', 'res.partner'),
                # Sales Route: v19 dropped res.partner.team_id; sale_custom re-adds it.
                'team_id': ('team_id', 'crm.team'),
                # Salesperson (387 partners in v10) — drives sale.order.user_id.
                'user_id': ('user_id', 'res.users')},
        # customer/supplier -> rank in _hook_partner
    },
    {
        'key': 'product_template', 'name': 'Product Templates', 'src': 'product_template',
        'dst': 'product.template', 'company_field': 'company_id',
        'scalars': {
            'name': 'name', 'default_code': 'default_code', 'list_price': 'list_price',
            'weight': 'weight', 'volume': 'volume', 'sale_ok': 'sale_ok',
            'purchase_ok': 'purchase_ok', 'tracking': 'tracking', 'active': 'active',
            'description': 'description', 'description_sale': 'description_sale',
            'invoice_policy': 'invoice_policy', 'sale_delay': 'sale_delay',
            'hsn_number': 'hsn_number', 'taxable_percentage': 'taxable_percentage',
        },
        'm2o': {'categ_id': ('categ_id', 'product.category'),
                'uom_id': ('uom_id', 'uom.uom'),
                'uom_po_id': ('uom_po_id', 'uom.uom'),
                'appliance_style': ('appliance_style', 'product.appliance.style')},
        'm2m': {'taxes_id': ('product_taxes_rel', 'prod_id', 'tax_id', 'account.tax'),
                'supplier_taxes_id': ('product_supplier_taxes_rel', 'prod_id', 'tax_id', 'account.tax')},
        # type/is_storable in _hook_product_template
    },
    {
        'key': 'product_product', 'name': 'Product Variants', 'src': 'product_product',
        'dst': 'product.product',
        # handled almost entirely by _hook_product_product (adopt the template's variant)
        'scalars': {'default_code': 'default_code', 'barcode': 'barcode', 'active': 'active'},
    },
    {
        'key': 'supplierinfo', 'name': 'Vendor Pricelists', 'src': 'product_supplierinfo',
        'dst': 'product.supplierinfo', 'company_field': 'company_id',
        'require': ['product_tmpl_id'],
        'scalars': {'price': 'price', 'min_qty': 'min_qty', 'delay': 'delay',
                    'product_code': 'product_code', 'product_name': 'product_name',
                    'date_start': 'date_start', 'date_end': 'date_end'},
        'm2o': {'partner_id': ('name', 'res.partner'),  # v10 vendor column is `name`
                'product_tmpl_id': ('product_tmpl_id', 'product.template'),
                'product_id': ('product_id', 'product.product')},
    },
    {
        # v10 mrp.workcenter _inherits resource.resource, so name/code/company/
        # time_efficiency/active live on resource_resource. v19 has them on the
        # model itself -> read from a JOIN via src_sql.
        # working_state is computed+stored in v19: never write it.
        'key': 'workcenter', 'name': 'Work Centers', 'src': 'mrp_workcenter',
        'src_sql': """SELECT w.id, w.sequence, w.note, w.color, w.time_start, w.time_stop,
                             w.oee_target,
                             r.name AS wc_name, r.code AS wc_code,
                             r.time_efficiency AS wc_time_efficiency,
                             r.active AS wc_active, r.company_id AS company_id
                      FROM mrp_workcenter w
                      JOIN resource_resource r ON r.id = w.resource_id""",
        'dst': 'mrp.workcenter', 'company_field': 'company_id', 'match': ['name'],
        'scalars': {'name': 'wc_name', 'code': 'wc_code', 'sequence': 'sequence',
                    'note': 'note', 'color': 'color', 'time_start': 'time_start',
                    'time_stop': 'time_stop', 'oee_target': 'oee_target',
                    'time_efficiency': 'wc_time_efficiency', 'active': 'wc_active'},
    },
    {
        'key': 'bom', 'name': 'Bills of Material', 'src': 'mrp_bom', 'dst': 'mrp.bom', 'company_field': 'company_id',
        'require': ['product_tmpl_id'],
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
