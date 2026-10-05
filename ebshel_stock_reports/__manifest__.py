# Part of Advanced Stock Reports. See LICENSE file for full copyright and licensing details.
{
    'name': 'Advanced Stock Reports',
    'version': '19.0.1.0.0',
    'summary': 'Stock ledger, aging, WIP, planned vs actual consumption, OTIF, purchase price '
               'variance, ABC/XYZ, MO cost analysis, GST registers - every figure reconciled to '
               'the Odoo valuation, exported to Excel and PDF.',
    'description': """
Advanced Stock Reports
======================
Nineteen inventory and manufacturing reports built on one engine: a daily
movement table and a daily valuation table that mirror, line for line, how Odoo
19 values stock (standard, average and FIFO, at any date, per company). Every
report foots to the Inventory > Reporting > Stock figure, and says so when it
cannot.

Reports
-------
* Stock ledger and stock card, with a valuation adjustment column that makes
  every warehouse and location foot to the company value.
* Planned vs actual consumption, WIP at a date (with month-end snapshots),
  MO cost and production analysis, monthly production account.
* Inventory aging (valuation stack or physical in-date), slow and non-moving
  stock, ABC / XYZ classification, turnover and days of cover.
* Customer OTIF, vendor OTIF (next to Odoo's own vendor delay rate),
  purchase price variance.
* MO component shortage, open order shortage.
* Valued count variance, scrap and returns by reason, inward / outward /
  internal / dropship registers.
* Inventory vs general ledger with the four accrual listings (received not
  billed, billed not received, delivered not invoiced, invoiced not delivered).
* India GST stock register (Rule 56 grouping) and monthly production account.

Engine
------
* Daily movement summary per company, product, location and movement type.
* Daily closing quantity and value per product, computed exactly as Odoo's
  ``total_value`` at that instant (standard price history, AVCO replay,
  FIFO stack), anchored on the current quants like Odoo.
* A dirty queue fed by every write path that changes a done move's value
  (adjust valuation, bills, landed costs, purchase price edits, backdating,
  move line edits, standard price changes), recomputed by a five-minute cron.
* Health check and one-click rebuild.

Security
--------
* User, Manager and See Values groups; value columns disappear from screens,
  Excel files and PDFs for users who may not see them.
* Multi-company record rules on every stored table.

Compatibility
-------------
* Odoo 19.0 Community; designed to install on Enterprise.
* Depends on ``stock_account``, ``mrp_account``, ``purchase_stock`` and
  ``sale_stock``; ``xlsxwriter`` ships with Odoo.
    """,
    'category': 'Warehouse',
    'author': 'Ebshel Technologies',
    'maintainer': 'Ebshel Technologies',
    'website': 'https://ebshel.com',
    'support': 'ebinpg777@gmail.com',
    'license': 'OPL-1',
    'price': 199.00,
    'currency': 'USD',
    'depends': [
        'stock',
        'stock_account',
        'mrp',
        'mrp_account',
        'purchase_stock',
        'sale_stock',
    ],
    'external_dependencies': {'python': ['xlsxwriter']},
    'data': [
        'security/groups.xml',
        'security/ir.model.access.csv',
        'data/paperformat_data.xml',
        'data/reason_tag_data.xml',
        'data/cron_data.xml',
        'report/report_templates.xml',
        'report/report_actions.xml',
        'views/res_config_settings_views.xml',
        'views/stock_move_daily_views.xml',
        'views/stock_value_daily_views.xml',
        'views/stock_dirty_views.xml',
        'views/product_class_views.xml',
        'views/wip_snapshot_views.xml',
        'views/stock_move_views.xml',
        'views/stock_scrap_views.xml',
        'views/product_category_views.xml',
        'wizard/stock_inventory_adjustment_name_views.xml',
        'wizard/stock_return_picking_views.xml',
        'wizard/report_stock_ledger_views.xml',
        'wizard/report_consumption_views.xml',
        'wizard/report_wip_views.xml',
        'wizard/report_aging_views.xml',
        'wizard/report_slow_moving_views.xml',
        'wizard/report_abc_xyz_views.xml',
        'wizard/report_turnover_views.xml',
        'wizard/report_customer_otif_views.xml',
        'wizard/report_vendor_otif_views.xml',
        'wizard/report_price_variance_views.xml',
        'wizard/report_mo_shortage_views.xml',
        'wizard/report_order_shortage_views.xml',
        'wizard/report_count_variance_views.xml',
        'wizard/report_scrap_return_views.xml',
        'wizard/report_register_views.xml',
        'wizard/report_accrual_views.xml',
        'wizard/report_mo_cost_views.xml',
        'wizard/report_gst_register_views.xml',
        'wizard/report_production_account_views.xml',
        'views/menus.xml',
    ],
    'post_init_hook': 'post_init_hook',
    'images': [
        'static/description/banner.png',
    ],
    'application': True,
}
