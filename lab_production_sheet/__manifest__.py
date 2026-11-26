# -*- coding: utf-8 -*-
{
    'name': 'Lab Production Sheet',
    'version': '20.0.1.4.0',
    'summary': "The lab's Daily Production Report and the technician's job sheet, "
               "printed from the manufacturing order",
    'description': """
Lab Production Sheet
====================
The lab records a case's way through production on a paper form, the *Daily
Production Report*: who the case is for and what came with it at the top, then a
table for each department with a line for every stage - technician, date, time.
This prints that form from the manufacturing order, with the manufacturing
order's number as its file number.

* **Daily Production Report** - the form filled in from what the floor has
  recorded: each stage with the technician who did it and when they handed it
  on, the material each department used, who checked it and who packed it.
* **Technician Job Sheet** - the same heading with no stage or technician on
  it, and the case's barcode: the sheet that travels with the work. Scanning
  the barcode at a station board accepts the case there or hands it on.
* The three answers the form asks for that an order did not hold - a cast as
  the impression, the opposing model, the pre-cutting model - are on the order
  and on the manufacturing order.
""",
    'author': 'Ebin P G',
    'maintainer': 'Ebin P G',
    'category': 'Manufacturing',
    'license': 'OPL-1',
    'depends': [
        'lab_workcenter_scan',   # the station board: who did each step, and when
        'lab_masters',           # the benches and departments the sheet is laid out by
        'sale_mrp',              # an order's jobs, for its print buttons
    ],
    'data': [
        'data/sheet_positions.xml',
        'report/production_sheet_reports.xml',
        'report/production_sheet_templates.xml',
        'views/sale_order_views.xml',
        'views/mrp_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'lab_production_sheet/static/src/scss/production_sheet.scss',
        ],
    },
    'installable': True,
    'application': False,
}
