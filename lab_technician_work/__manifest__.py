# -*- coding: utf-8 -*-
{
    'name': 'Technician Work',
    'version': '20.0.1.0.0',
    'category': 'Sales/Reporting',
    'summary': "How much work each technician did: cases, order lines, units and teeth, "
               "from the Technicians named on the orders",
    'description': """
Technician Work
===============
A technician's work is read from the orders that name them (the order's Technicians):
every line of such an order counts for each technician named on it.

* Technician Work analysis: pivot, graph and list by technician, month, department,
  product, clinic - cases, works (order lines), quantity, teeth and value.
* Technician Work Summary: a printed sheet for a period, one row per technician,
  with the products they worked on if asked.
* A Works button on each technician's contact.
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
        'wizard/technician_work_summary_views.xml',
        'report/technician_work_report.xml',
    ],
    'installable': True,
    'application': False,
}
