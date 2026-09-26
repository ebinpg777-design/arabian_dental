# -*- coding: utf-8 -*-
{
    'name': 'Lab Work Centre Scanning',
    'version': '19.0.3.14.1',
    'summary': 'Move a work order to another work centre by scanning the station QR code',
    'description': """
Work Centre Scanning
====================
A technician on the shop floor moves a job to another station by pointing the phone at
the station's QR label. No searching a dropdown of work centres with gloves on, and no
typing on a shared terminal.

* Every work centre carries a **unique code and a printable QR label**.
* **Move by Scan** on a work order opens the camera and reassigns the job to whatever
  station is scanned.
* Every move is written to the work order's chatter with who moved it and from where,
  because a job that changes station without a trace is a job nobody can account for.
""",
    'author': 'Ebin P G',
    'maintainer': 'Ebin P G',
    'category': 'Manufacturing',
    'license': 'OPL-1',
    # sale_custom owns `mrp.workcenter.users` (Technicians), and the access rules
    # below are written against it. A record rule cannot reference a field from a
    # module you do not depend on — it is evaluated at install, when that module
    # may not have loaded yet, and fails with "Invalid field".
    'depends': ['mrp', 'web', 'sale_custom'],
    'data': [
        'security/workcenter_security.xml',
        'security/ir.model.access.csv',
        'data/ir_sequence_data.xml',
        'data/close_delivered_cron.xml',
        'data/redo_reason_data.xml',
        'data/mrp_routing_group_data.xml',
        'report/workcenter_label_report.xml',
        'report/job_label_report.xml',
        'report/production_report.xml',
        'report/floor_reports.xml',
        'views/mrp_workcenter_views.xml',
        'views/work_target_views.xml',
        'views/mrp_workorder_views.xml',
        'views/mrp_production_list_views.xml',
        'views/station_views.xml',
        'views/flow_views.xml',
        'views/tracking_views.xml',
        'views/production_performance_views.xml',
        'views/mrp_redo_views.xml',
        'wizard/production_report_views.xml',
        'wizard/floor_print_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'lab_workcenter_scan/static/src/scss/wc_scan.scss',
            'lab_workcenter_scan/static/src/scss/station.scss',
            'lab_workcenter_scan/static/src/js/doing_it_dialog.js',
            'lab_workcenter_scan/static/src/js/where_dialog.js',
            'lab_workcenter_scan/static/src/js/cancel_dialog.js',
            'lab_workcenter_scan/static/src/js/find_dialog.js',
            'lab_workcenter_scan/static/src/js/change_dialog.js',
            'lab_workcenter_scan/static/src/js/restart_dialog.js',
            'lab_workcenter_scan/static/src/js/targets_dialog.js',
            'lab_workcenter_scan/static/src/js/station_pick_dialog.js',
            'lab_workcenter_scan/static/src/js/station.js',
            'lab_workcenter_scan/static/src/scss/flow.scss',
            'lab_workcenter_scan/static/src/scss/mrp_report.scss',
            'lab_workcenter_scan/static/src/js/flow.js',
            'lab_workcenter_scan/static/src/js/mrp_report.js',
            'lab_workcenter_scan/static/src/xml/flow.xml',
            'lab_workcenter_scan/static/src/xml/mrp_report.xml',
            'lab_workcenter_scan/static/src/xml/station.xml',
            'lab_workcenter_scan/static/src/xml/doing_it_dialog.xml',
            'lab_workcenter_scan/static/src/xml/where_dialog.xml',
            'lab_workcenter_scan/static/src/xml/cancel_dialog.xml',
            'lab_workcenter_scan/static/src/xml/find_dialog.xml',
            'lab_workcenter_scan/static/src/xml/change_dialog.xml',
            'lab_workcenter_scan/static/src/xml/restart_dialog.xml',
            'lab_workcenter_scan/static/src/xml/targets_dialog.xml',
            'lab_workcenter_scan/static/src/xml/station_pick_dialog.xml',
            'lab_workcenter_scan/static/src/js/stale_page.js',
            'lab_workcenter_scan/static/src/js/wc_scan.js',
            'lab_workcenter_scan/static/src/xml/wc_scan.xml',
        ],
        'web.assets_tests': [
            'lab_workcenter_scan/static/tests/tours/finisher_popup.js',
        ],
    },
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'application': False,
}
