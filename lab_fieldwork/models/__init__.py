# -*- coding: utf-8 -*-
# The mixins come first: Odoo resolves `_inherit` as each class is added to the
# registry, so a model importing before the abstract it inherits fails outright with
# "inherits from non-existing model".
from . import own_records_mixin

from . import res_partner
from . import lab_beat
from . import lab_case
from . import lab_visit
from . import sale_order
from . import lab_trip
from . import lab_target
from . import fieldwork_settings
from . import res_config_settings
from . import lab_coverage
# Before lab_performance: that model is a SQL VIEW selecting from this
# table, and a view cannot be created over a table that does not exist
# yet. Registry order is import order. (client, 2026-09-12)
from . import cash_collection
from . import lab_performance
from . import cash_movement
from . import cash_handover
from . import clinic_favourite
from . import my_day
from . import desk
from . import attendance
from . import day_close
from . import visit_tags
from . import crm_team
from . import new_clinics
from . import ir_ui_menu
from . import desk_cover
from . import daily_update
from . import day_track
from . import location_halt
from . import location_event
from . import location_ping
from . import location_live
from . import location_road
# After location_ping and location_live: it imports the ping module (whose
# lab.my.day extension must follow my_day) and extends the users model they built.
from . import field_app
from . import field_live
from . import weekly_report
from . import ir_http
