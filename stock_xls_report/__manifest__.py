# -*- coding: utf-8 -*-
{
    'name': 'Stock Movement Summary (Excel)',
    'version': '19.0.1.0.0',
    'category': 'Inventory',
    'summary': 'Brand-wise period stock-movement summary (Opening → movements → Closing) in Excel',
    'description': """
Brand-Wise Summary of Stock Movement
====================================
Re-implemented for Odoo 19 (no report_xlsx, no legacy JS). A period stock-card,
grouped by product category ("brand") per warehouse:

Opening Balance | Purchase | Production | Transfers | Adjustments (+/-) |
Issue to Production | Sale | Closing Balance

with a quantity / value toggle. Computed from native ``stock.move`` (done moves,
classified by source/destination location usage). For plain current-stock and
valuation use the native Inventory reports + list XLSX export.
""",
    'author': 'Migrated to Odoo 19',
    'depends': ['stock', 'mrp'],
    'data': [
        'security/ir.model.access.csv',
        'wizard/stock_movement_report_views.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
