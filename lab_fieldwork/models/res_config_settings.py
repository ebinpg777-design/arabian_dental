# -*- coding: utf-8 -*-
from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    """Policy, owned by the configuration role rather than by whoever it constrains."""
    _inherit = 'res.config.settings'

    # --- proof that a visit happened
    fw_visit_radius_m = fields.Float(
        'Default Visit Radius (m)', default=300.0,
        config_parameter='lab_fieldwork.visit_radius_m')
    fw_require_odo_photo = fields.Boolean(
        'Require Odometer Photos', default=True,
        config_parameter='lab_fieldwork.require_odo_photo')
    fw_require_visit_photo = fields.Boolean(
        'Require Visit Photo', default=False,
        config_parameter='lab_fieldwork.require_visit_photo')

    # --- when a clinic counts as neglected
    fw_due_days = fields.Integer(
        'Due after (days)', default=30,
        config_parameter='lab_fieldwork.due_days')
    fw_overdue_days = fields.Integer(
        'Overdue after (days)', default=60,
        config_parameter='lab_fieldwork.overdue_days')
    fw_at_risk_days = fields.Integer(
        'At risk after (days)', default=90,
        config_parameter='lab_fieldwork.at_risk_days')

    fw_duplicate_days = fields.Integer(
        'Duplicate window (days)', default=7,
        config_parameter='lab_fieldwork.duplicate_days')

    fw_require_attendance = fields.Boolean(
        'Attendance before field work', default=True,
        config_parameter='lab_fieldwork.require_attendance')

    fw_auto_close_day = fields.Boolean(
        'Close the day automatically', default=True,
        config_parameter='lab_fieldwork.auto_close_day')
    fw_auto_close_hour = fields.Integer(
        'Close after (hour)', default=21,
        config_parameter='lab_fieldwork.auto_close_hour')

    # --- what travel is worth
    fw_rate_bike = fields.Float(
        'Two Wheeler Rate / km', default=4.0,
        config_parameter='lab_fieldwork.rate_bike')
    fw_rate_car = fields.Float(
        'Four Wheeler Rate / km', default=9.0,
        config_parameter='lab_fieldwork.rate_car')
    fw_rate_public = fields.Float(
        'Public Transport / km', default=0.0,
        config_parameter='lab_fieldwork.rate_public')

    # --- cash collected in the field
    fw_auto_float = fields.Boolean(
        'Open floats automatically', default=True,
        config_parameter='lab_fieldwork.auto_float')
    fw_cash_max_days = fields.Integer(
        'Hand over within (days)', default=2,
        config_parameter='lab_fieldwork.cash_max_days')
    fw_cash_ceiling = fields.Float(
        'Hand over above', default=10000.0,
        config_parameter='lab_fieldwork.cash_ceiling')

    # --- The trail between the doors -------------------------------------------
    fw_track_live = fields.Boolean(
        'Record the route while on duty', default=True,
        config_parameter='lab_fieldwork.track_live',
        help="While an executive's day is running, their phone reports where it is and "
             "the day sheet draws the route taken between clinics. Nothing is recorded "
             "before the day is started or after it is ended.")
    fw_track_interval = fields.Integer(
        'Ask the phone every (seconds)', default=90,
        config_parameter='lab_fieldwork.track_interval',
        help="How often the phone offers a position. Shorter draws a finer route and "
             "uses more battery; positions that show no movement are discarded either "
             "way.")
    fw_track_keep_days = fields.Integer(
        'Keep routes for (days)', default=90,
        config_parameter='lab_fieldwork.track_keep_days',
        help="Routes older than this are deleted every night. A day sheet keeps its "
             "visits and its odometer for good; only the between-the-doors trail goes.")

    # Booleans whose default is True. Odoo's own config_parameter machinery hands False
    # straight to set_param, which deletes the key — and a missing key then reads as the
    # default, so unticking any of these did nothing at all.
    _FW_TRUE_BY_DEFAULT = (
        ('fw_require_odo_photo', 'lab_fieldwork.require_odo_photo'),
        ('fw_require_attendance', 'lab_fieldwork.require_attendance'),
        ('fw_auto_close_day', 'lab_fieldwork.auto_close_day'),
        ('fw_auto_float', 'lab_fieldwork.auto_float'),
        ('fw_track_live', 'lab_fieldwork.track_live'),
    )

    def set_values(self):
        before = self._fw_coverage_bands()
        res = super().set_values()
        params = self.env['ir.config_parameter'].sudo()
        for field_name, key in self._FW_TRUE_BY_DEFAULT:
            params.set_param(key, 'True' if self[field_name] else 'False')
        if self._fw_coverage_bands() != before:
            # The coverage bands are baked into a SQL view's CASE expression, so the
            # view has to be rebuilt for a changed band to mean anything. Doing it here
            # is the difference between the setting working and merely being saved.
            self.env['lab.coverage'].sudo().init()
            # …and the cache has to go with it. Rebuilding the view changes what a row
            # MEANS without changing which rows exist, so anything already read in this
            # request keeps its old status — the setting looks saved and ignored.
            self.env['lab.coverage'].invalidate_model()
        return res

    @api.model
    def _fw_coverage_bands(self):
        get = self.env['ir.config_parameter'].sudo().get_param
        return (get('lab_fieldwork.due_days'),
                get('lab_fieldwork.overdue_days'),
                get('lab_fieldwork.at_risk_days'))
