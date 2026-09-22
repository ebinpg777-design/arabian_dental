# -*- coding: utf-8 -*-
from odoo import _, api, fields, models

# The single definition of every Field Work parameter: field name, the
# ir.config_parameter key it lives under, its default, and how to read it back.
# Both this model and the General Settings section point at these same keys, so the
# two pages can never disagree about what the value is.
PARAMS = (
    ('fw_visit_radius_m',      'visit_radius_m',      300.0, 'float'),
    ('fw_require_odo_photo',   'require_odo_photo',   True,  'bool'),
    ('fw_require_visit_photo', 'require_visit_photo', False, 'bool'),
    ('fw_due_days',            'due_days',            30,    'int'),
    ('fw_overdue_days',        'overdue_days',        60,    'int'),
    ('fw_at_risk_days',        'at_risk_days',        90,    'int'),
    ('fw_duplicate_days',      'duplicate_days',      7,     'int'),
    # How far back My Day may be opened, and therefore how late a round can still
    # be written up. 0 means today only. (client, 2026-08-25)
    ('fw_backdate_days',       'backdate_days',       3,     'int'),
    ('fw_require_attendance',  'require_attendance',  True,  'bool'),
    ('fw_auto_close_day',      'auto_close_day',      True,  'bool'),
    ('fw_auto_close_hour',     'auto_close_hour',     21,    'int'),
    ('fw_rate_bike',           'rate_bike',           4.0,   'float'),
    ('fw_rate_car',            'rate_car',            9.0,   'float'),
    ('fw_rate_public',         'rate_public',         0.0,   'float'),
    # The day-sheet flow (2026-08-29): assembled at day close and put straight
    # on the operational desk; unsigned sheets are raised with the
    # administrator once the SLA passes.
    ('fw_auto_submit_day_sheet', 'auto_submit_day_sheet', True, 'bool'),
    ('fw_approval_sla_days',     'approval_sla_days',     2,    'int'),
    # Cash in the field (2026-09-18): a float opens itself the first time
    # somebody takes cash; held cash past these is chased.
    ('fw_auto_float',            'auto_float',            True,    'bool'),
    ('fw_cash_max_days',         'cash_max_days',         2,       'int'),
    ('fw_cash_ceiling',          'cash_ceiling',          10000.0, 'float'),
)
BANDS = ('fw_due_days', 'fw_overdue_days', 'fw_at_risk_days')


class LabFieldworkSettings(models.TransientModel):
    """Field Work's own settings page, owned by the Field Work Administrator.

    Odoo's `res.config.settings` is gated on `base.group_system`, so a Field Work
    Administrator cannot open it — the module's Configuration menu simply vanished for
    the one role that is supposed to own the configuration.

    Granting that role access to `res.config.settings` would have fixed the symptom and
    created a much worse problem: that model saves every app's settings, so the field
    work administrator could change anything on it that is not separately group-gated,
    including things they cannot even see. A parameter page the module owns outright is
    the honest fix — this role can write these parameters and nothing else.

    The equivalent section still exists under General Settings for a system
    administrator. Both write the same ir.config_parameter keys, so they cannot drift.
    """
    _name = 'lab.fieldwork.settings'
    _description = 'Field Work Settings'

    fw_visit_radius_m = fields.Float(
        'Visit Radius (m)',
        help="How close an executive must be for a check-in to count as being at the "
             "clinic. A check-in outside it is recorded and must be explained, never "
             "blocked — a blocked visit is simply an unrecorded one.")
    fw_require_odo_photo = fields.Boolean('Require Odometer Photos')
    fw_require_visit_photo = fields.Boolean('Require Visit Photo')

    fw_due_days = fields.Integer('Due after (days)')
    fw_overdue_days = fields.Integer('Overdue after (days)')
    fw_at_risk_days = fields.Integer('At risk after (days)')

    fw_duplicate_days = fields.Integer(
        'Duplicate window (days)',
        help="How far back to look for the same patient at the same clinic when an "
             "executive registers a case.")

    fw_backdate_days = fields.Integer(
        string='Days an executive may go back', default=3,
        help="How many days before today My Day can be opened, and a visit or case "
             "still written up. 0 keeps everyone on today only. A round finished "
             "after the phone died is the case this exists for; a fortnight of "
             "back-filled visits is not.")
    fw_require_attendance = fields.Boolean(
        'Attendance before field work',
        help="An executive must mark attendance before checking in at a clinic or "
             "starting their travel.")

    fw_auto_close_day = fields.Boolean(
        'Close the day automatically',
        help="Sign out anyone still on duty at the end of the day, and close visits "
             "and trips that only lack the final tap.")
    fw_auto_close_hour = fields.Integer(
        'Close after (hour, 0-23)',
        help="In each executive's own timezone.")

    fw_rate_bike = fields.Float('Two Wheeler Rate / km')
    fw_rate_car = fields.Float('Four Wheeler Rate / km')
    fw_rate_public = fields.Float('Public Transport / km')

    fw_auto_submit_day_sheet = fields.Boolean(
        'Auto-submit Day Sheets',
        help="At day close, each executive's day sheet is assembled from the "
             "day's own records and put straight on the Operational Manager's "
             "desk. Off, the executive submits it themselves.")
    fw_approval_sla_days = fields.Integer(
        'Approval SLA (days)',
        help="A day sheet waiting longer than this on either desk is raised "
             "with the Field Work Administrators - once.")

    fw_auto_float = fields.Boolean(
        'Open floats automatically',
        help="The first time an executive takes cash at a clinic, a petty cash "
             "float opens in their name to hold it - no advance, no journal. Off, "
             "accounts opens every float by hand and cash waits in the pocket "
             "until they do.")
    fw_cash_max_days = fields.Integer(
        'Hand over within (days)',
        help="Collected cash older than this is chased: a red chip on My Day, a "
             "to-do for the executive, and the person's row on the Ops Desk. "
             "0 never chases by age.")
    fw_cash_ceiling = fields.Float(
        'Hand over above',
        help="Collected cash at or above this amount is chased whatever its age. "
             "0 never chases by amount.")

    # ------------------------------------------------------------------ read
    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        # sudo: reading a parameter is not the same privilege as being a system
        # administrator, and this page exposes exactly nine of them.
        get = self.env['ir.config_parameter'].sudo().get_param
        for name, key, default, kind in PARAMS:
            raw = get('lab_fieldwork.%s' % key)
            res[name] = default if raw in (None, False, '') else _cast(raw, kind, default)
        return res

    # ------------------------------------------------------------------ write
    def action_save(self):
        self.ensure_one()
        params = self.env['ir.config_parameter'].sudo()
        before = self._bands()
        for name, key, _default, kind in PARAMS:
            value = self[name]
            # `set_param` DELETES the parameter when handed False, so a boolean whose
            # default is True silently turns itself back on the moment it is unticked.
            # Written as an explicit string, "off" is a value rather than an absence.
            if kind == 'bool':
                value = 'True' if value else 'False'
            params.set_param('lab_fieldwork.%s' % key, value)
        if self._bands() != before:
            # The coverage bands are compiled into a SQL view's CASE expression, so a
            # saved band that does not rebuild the view is a setting that silently does
            # nothing. Invalidating matters too: the rebuild changes what a row MEANS
            # without changing which rows exist, so anything already read this request
            # would keep its old status.
            self.env['lab.coverage'].sudo().init()
            self.env['lab.coverage'].invalidate_model()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'message': _('Field Work settings saved.'),
                'type': 'success',
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }

    def _bands(self):
        get = self.env['ir.config_parameter'].sudo().get_param
        return tuple(get('lab_fieldwork.%s' % key)
                     for name, key, _d, _k in PARAMS if name in BANDS)


def _cast(raw, kind, default):
    try:
        if kind == 'bool':
            return str(raw).lower() not in ('false', '0', '')
        return int(float(raw)) if kind == 'int' else float(raw)
    except (TypeError, ValueError):
        return default
