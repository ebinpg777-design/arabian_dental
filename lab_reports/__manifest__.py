# -*- coding: utf-8 -*-
{
    'name': 'Lab Reports',
    'version': '20.0.1.3.0',
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
* Purchase Order, Request for Quotation and the Purchase Request's RFQ on the
  tax invoice's stationery (PDF)
* Partner lab fields, work-order delay/remarks, lot extras

Incoming & Final inspection are handled by the native **Quality** app (see MIGRATION_NOTES).
""",
    'author': 'Migrated to Odoo 19',
    'depends': [
        'sale_custom',
        'purchase',
        # its RFQ print-out is drawn on the lab's stationery here
        'ebshel_purchase_request',
        'mrp',
        'stock',
    ],
    'data': [
        'data/ir_sequence_data.xml',
        'report/production_order_report.xml',
        'report/purchase_reports.xml',
        'wizard/wizard_views.xml',
        'views/res_partner_views.xml',
        'views/mrp_workorder_views.xml',
        'views/stock_lot_views.xml',
        'security/ir.access.csv',
    ],
    'assets': {
        'web.report_assets_common': [
            'lab_reports/static/src/scss/purchase_report.scss',
        ],
    },
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
