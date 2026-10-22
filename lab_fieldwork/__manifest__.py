# -*- coding: utf-8 -*-
{
    'name': 'Lab Field Work',
    'version': '20.0.7.0.1',
    'summary': 'Field work for an orthodontic lab: beats, visits, travel and targets',
    'description': """
Lab Field Work
================
Everything a marketing executive does in a day, on one screen.

**The design constraint is that ordinary people use this.** So the module is built
around the single loop an executive actually performs — go to a clinic, do something
there, leave — and everything else is derived from it or generated for them.

* **Beats** — a named round of clinics with the weekday it is worked. Plan the week
  once and the visits create themselves; an executive never fills in a visit form to
  say they intend to go somewhere.
* **My Day** — the executive's whole application: today's clinics as cards, one button
  on each. Check in, record what happened, check out.
* **Trips** — the day's travel. Opening and closing odometer, distance and claim
  computed.
* **Targets** — a monthly number per person, with achievement measured from real
  visits and orders rather than typed in.

* **Field Live** — the one screen the managers and the CEO open: where every
  executive is right now (pushed from the phones, not polled), each one's day as
  facts and as a timeline, the team's numbers across the top, the alerts worth a
  word (no signal, a long stop, a phone dying, a day not started, a claim far from
  the trail, cash past the policy), which phones run the ADL FieldX app, and a feed
  of what is happening. Beside the map, three reports on the same day: everyone's
  day on one clock, a scoreboard by person and by route, and the trends — the
  fortnight's visits, cases, money and km, day by day and person by person, and the
  month so far. The three role desks it replaced (Ops Desk, Marketing Desk, Command
  Center) were removed on 2026-10-20.

* **Day Sheets** — each executive's day assembles itself at day close from the visits,
  travel, cases and attendance it produced, and crosses two desks in order: the
  **Operational Manager** vouches that the day happened as recorded, then the
  **Marketing Manager** judges the activities, each from the Approvals menu. The week
  of both is filed and mailed to the Field Work Administrator every Monday.

Four roles, and each sees a *different application* rather than the same one with
pieces greyed out. The roles are parallel, not stacked: a manager never sees the
executive's own-work menus, because a manager does not work a beat, and neither
manager desk sees the other's approval queue. Records lock themselves once closed —
in the server, not merely in the form.
""",
    'author': 'Ebin P G',
    'maintainer': 'Ebin P G',
    'category': 'Services/Field Service',
    'license': 'OPL-1',
    'depends': [
        'lab_access_control',   # state-lock mixin, proven and tested
        'lab_order_control',    # the verification state a new case lands in
        'sale_custom',            # patient / U-L / colour — the lab's own order fields
        'base_geolocalize',       # clinic coordinates
        'sale_management',        # orders taken during a visit
        'hr',
        'hr_attendance',        # the day an executive starts, on Odoo's own model
    ],
    'data': [
        'data/visit_tag_data.xml',
        'security/lab_fieldwork_security.xml',
        'data/ir_sequence_data.xml',
        'data/ir_cron_data.xml',
        'views/my_day_views.xml',
        'views/res_users_views.xml',
        'views/lab_beat_views.xml',
        'views/lab_case_views.xml',
        'views/lab_visit_views.xml',
        # must follow lab_visit_views.xml: it inherits view_visit_search
        'views/quick_entry_views.xml',
        'views/lab_trip_views.xml',
        'views/lab_target_views.xml',
        'views/lab_coverage_views.xml',
        'views/lab_performance_views.xml',
        'views/res_partner_views.xml',
        'views/cash_movement_views.xml',
        'views/collect_cash_views.xml',
        'views/fieldwork_settings_views.xml',
        'views/res_config_settings_views.xml',
        'views/field_app_templates.xml',
        # The day-sheet flow (2026-08-29): actions must exist before the menus
        # that point at them; the migration function runs after the groups it
        # rehomes people into.
        'views/daily_update_views.xml',
        'views/weekly_report_views.xml',
        'report/weekly_report_templates.xml',
        'report/cash_handover_receipt.xml',
        'data/daily_flow_data.xml',
        'views/lab_fieldwork_menus.xml',
        'views/live_map_views.xml',
        'views/field_live_views.xml',
        # After the menus: its menuitem hangs off menu_fw_approvals.
        'views/desk_cover_views.xml',
        # After the menus too: it hangs off menu_fw_ops and the Petty Cash app.
        'views/cash_handover_views.xml',
        'security/ir.access.csv',
    ],
    'demo': ['data/lab_fieldwork_demo.xml'],
    'assets': {
        'web.assets_backend': [
            'lab_fieldwork/static/src/scss/mobile_home.scss',
            'lab_fieldwork/static/src/js/mobile_home.js',
            'lab_fieldwork/static/src/xml/mobile_home.xml',
            'lab_fieldwork/static/src/js/mobile_infinite_list.js',
            'lab_fieldwork/static/src/xml/mobile_infinite_list.xml',
            'lab_fieldwork/static/src/scss/fieldwork.scss',
            'lab_fieldwork/static/src/scss/new_clinics.scss',
            'lab_fieldwork/static/src/scss/mobile_savebar.scss',
            'lab_fieldwork/static/src/xml/mobile_savebar.xml',
            'lab_fieldwork/static/src/lib/leaflet/leaflet.css',
            'lab_fieldwork/static/src/lib/leaflet/leaflet.js',
            'lab_fieldwork/static/src/scss/day_map.scss',
            'lab_fieldwork/static/src/js/day_map.js',
            'lab_fieldwork/static/src/xml/day_map.xml',
            # The watcher is a service, so it must load whatever screen the web
            # client opens on — not only My Day.
            'lab_fieldwork/static/src/js/position.js',
            'lab_fieldwork/static/src/js/live_track.js',
            'lab_fieldwork/static/src/js/geo_button.js',
            'lab_fieldwork/static/src/xml/geo_button.xml',
            'lab_fieldwork/static/src/js/count_field.js',
            'lab_fieldwork/static/src/xml/count_field.xml',
            'lab_fieldwork/static/src/js/mobile_widgets.js',
            'lab_fieldwork/static/src/xml/mobile_widgets.xml',
            'lab_fieldwork/static/src/scss/case_entry.scss',
            'lab_fieldwork/static/src/js/case_items.js',
            'lab_fieldwork/static/src/xml/case_items.xml',
            'lab_fieldwork/static/src/js/my_day.js',
            'lab_fieldwork/static/src/js/new_clinics.js',
            'lab_fieldwork/static/src/xml/my_day.xml',
            'lab_fieldwork/static/src/xml/new_clinics.xml',
            # The field, live (2026-10-19): the manager's live map and the
            # executive's own trail card on My Day. Leaflet (above) is already loaded.
            'lab_fieldwork/static/src/scss/live_map.scss',
            'lab_fieldwork/static/src/js/live_map.js',
            'lab_fieldwork/static/src/xml/live_map.xml',
            'lab_fieldwork/static/src/scss/field_live.scss',
            'lab_fieldwork/static/src/js/field_live.js',
            'lab_fieldwork/static/src/xml/field_live.xml',
            'lab_fieldwork/static/src/scss/my_trail.scss',
            'lab_fieldwork/static/src/js/my_trail.js',
            'lab_fieldwork/static/src/xml/my_trail.xml',
        ],
    },
    'installable': True,
    'application': True,
}
