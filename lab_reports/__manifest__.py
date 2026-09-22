# -*- coding: utf-8 -*-
{
    'name': 'Lab Reports',
    'version': '19.0.1.2.0',
    'category': 'Reporting',
    'summary': 'Dental lab QC/registration Excel reports, job cards, production-order labels',
    'description': """
Arabian Dental Lab reporting
===================================
Migrated to Odoo 19. Provides:

* Doctor/Hospital register + Client registration form (XLSX)
* Approved vendor list, vendor registration & vendor evaluation forms (XLSX)
* Bill of Material report (from Sale Order and from BoM) (XLSX)
* Daily Production report per work centre (XLSX)
* Job Card (department-wise routing) (XLSX)
* Production Order barcode label (PDF)
* Partner lab fields, work-order delay/remarks, lot extras

Incoming & Final inspection are handled by the native **Quality** app (see MIGRATION_NOTES).
""",
    'author': 'Migrated to Odoo 19',
    'depends': [
        'sale_custom',
        'purchase',
        'mrp',
        'stock',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_sequence_data.xml',
        'report/production_order_report.xml',
        'wizard/wizard_views.xml',
        'views/res_partner_views.xml',
        'views/mrp_workorder_views.xml',
        'views/stock_lot_views.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
