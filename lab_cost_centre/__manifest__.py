# -*- coding: utf-8 -*-
{
    'name': 'Departments & Cost Centres',
    'version': '19.0.1.1.0',
    'summary': "Every order, job, purchase, invoice and movement carries the department "
               "that owns it and the cost centre it lands on",
    'description': """
Departments & Cost Centres
==========================

The lab is run as eight or nine places that spend money and make things - Wax Up,
CAD/CAM, Metal, Ceramic, Acrylic, Orthodontic, the store, the road - and until now
nothing in the database said which of them a given row belonged to. Asked "what did
Ceramic spend last month", the only honest answer was to read the purchase orders and
guess from the product names.

This module gives the lab one master list of departments, arranged as a tree, and one
cost centre per department; then it stamps that pair onto every document that costs or
earns money, and writes the matching analytic distribution so Odoo's own analytic
reporting answers the question without a single custom report.

What carries a department and a cost centre
-------------------------------------------
* Sale orders and their lines
* Purchase orders and their lines
* Customer invoices, vendor bills and every journal item
* Transfers and stock moves (issues to a bench, receipts into the store)
* Manufacturing orders and work orders
* Material requests
* Work centres, stock locations and product categories - the masters the rest derive from

Nothing has to be typed twice. A sale line takes its department from the product's
category; a manufacturing order from the bench its first operation runs at; an invoice
line from the order line it bills; a stock move from the store it moves into or out of.
Whatever is derived can be overridden on the document, and an override is never
overwritten.

Why the analytic distribution and not only a field
--------------------------------------------------
A `department_id` on a row is good for filtering and grouping, and useless for a profit
and loss statement. Odoo reads cost centres as analytic accounts, so each department's
cost centre IS an analytic account, and every journal item gets the distribution that
matches its department. The Balance Sheet, the P&L, the Budget and the Analytic Items
list then work by department with no further work.
""",
    'author': 'Arabian Dental Lab',
    'category': 'Accounting/Accounting',
    'license': 'LGPL-3',
    'depends': [
        'hr',
        'analytic',
        'account',
        'sale_stock',
        'purchase_stock',
        'mrp',
        'mrp_account',
        'material_request',
    ],
    # No access rules: nothing here is a new concrete model. The cockpit is an
    # abstract model read through one method, exactly as the floor boards are.
    'data': [
        'views/hr_department_views.xml',
        'views/analytic_views.xml',
        'views/master_views.xml',
        'views/document_views.xml',
        'views/cost_centre_board_views.xml',
        'views/menus.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'lab_cost_centre/static/src/board/*.js',
            'lab_cost_centre/static/src/board/*.xml',
            'lab_cost_centre/static/src/board/*.scss',
        ],
    },
    'installable': True,
    'application': False,
}
