# -*- coding: utf-8 -*-
{
    'name': 'Technician Work',
    'version': '20.0.2.0.0',
    'category': 'Sales/Reporting',
    'summary': "How much work each technician did: cases, order lines, units and teeth, "
               "from the Technicians named on the orders",
    'description': """
Technician Work
===============
A technician's work is read from the orders that name them (the order's Technicians):
every line of such an order counts for each technician named on it.

* One screen (Sales > Reporting > Technician Work): the period's figures against the
  period before, insights, a leaderboard whose rows open into their products (or
  departments, clinics...), a heatmap of who worked when; filters for the period, the
  technicians, departments, categories, routes, clinics, reworks and shared cases;
  columns to add or take away; rows by technician or any other dimension.
* Print it (PDF on the lab letterhead, as filtered) or export it (Excel: the summary
  and every order line behind it).
* A Works button on each technician's contact opens the screen on them.
""",
    'author': 'Ebin P G',
    'maintainer': 'Ebin P G',
    'license': 'LGPL-3',
    # sale_custom: the order's Technicians and the line's Teeth
    # lab_cost_centre: the line's Department
    'depends': ['sale_custom', 'lab_cost_centre'],
    'data': [
        'security/ir.access.csv',
        'views/technician_work_views.xml',
        'views/res_partner_views.xml',
        'report/technician_work_report.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'lab_technician_work/static/src/technician_board.js',
            'lab_technician_work/static/src/technician_board.xml',
            'lab_technician_work/static/src/technician_board.scss',
        ],
    },
    'installable': True,
    'application': False,
}
