# -*- coding: utf-8 -*-
import logging

from odoo import api, models

_logger = logging.getLogger(__name__)

# IANA "backward" aliases: still valid names that Python accepts and Odoo therefore
# offers in its timezone dropdown, but which recent PostgreSQL builds no longer list in
# pg_timezone_names. Storing one is silent until something runs `AT TIME ZONE`, and then
# EVERY view with a date group-by dies for that user — the work centre kanban, every
# dashboard, every pivot.
#
# An alias is replaced even where THIS server's PostgreSQL still lists it. The same
# database moves between servers that disagree — staging runs PostgreSQL 14, which
# still accepts Asia/Calcutta; node 27 runs 18, which does not — so a value that only
# works on the server it was typed on breaks the day the dump is restored elsewhere.
# The current name works on all of them, and it is the name the rest of the lab's code
# falls back to ('Asia/Kolkata'). (2026-09-15)
LEGACY_ALIASES = {
    'Asia/Calcutta': 'Asia/Kolkata',
    'Asia/Dacca': 'Asia/Dhaka',
    'Asia/Katmandu': 'Asia/Kathmandu',
    'Asia/Rangoon': 'Asia/Yangon',
    'Asia/Saigon': 'Asia/Ho_Chi_Minh',
    'Europe/Kiev': 'Europe/Kyiv',
    'America/Godthab': 'America/Nuuk',
}


class TimezoneNormaliserMixin(models.AbstractModel):
    """Refuse to store a timezone PostgreSQL cannot use — here or on the next server.

    Deliberately a substitution and not an error: the two names mean the SAME zone, so
    rejecting the save would block a user from a setting that is, as far as they are
    concerned, correct. Anything unrecognised that is not a known alias is left alone
    and logged — guessing at an unknown zone would silently move somebody's working day.
    """
    _name = 'lab.tz.normaliser'
    _description = 'Timezone normaliser'

    @api.model
    def _pg_timezones(self):
        self.env.cr.execute("SELECT name FROM pg_timezone_names")
        return {row[0] for row in self.env.cr.fetchall()}

    @api.model
    def _normalise_tz(self, vals):
        tz = vals.get('tz')
        if not tz:
            return vals
        known = self._pg_timezones()
        replacement = LEGACY_ALIASES.get(tz)
        if replacement and replacement in known:
            _logger.info(
                "Timezone %s is a legacy alias; storing %s, which is the same zone "
                "under its current name.", tz, replacement)
            vals = dict(vals, tz=replacement)
        elif tz not in known:
            _logger.warning(
                "Timezone %s is not known to PostgreSQL. Views that group by date will "
                "fail for this record until it is changed.", tz)
        return vals


class ResUsers(models.Model):
    _inherit = 'res.users'

    @api.model_create_multi
    def create(self, vals_list):
        norm = self.env['lab.tz.normaliser']
        return super().create([norm._normalise_tz(v) for v in vals_list])

    def write(self, vals):
        return super().write(self.env['lab.tz.normaliser']._normalise_tz(vals))


class ResPartner(models.Model):
    _inherit = 'res.partner'

    @api.model_create_multi
    def create(self, vals_list):
        norm = self.env['lab.tz.normaliser']
        return super().create([norm._normalise_tz(v) for v in vals_list])

    def write(self, vals):
        return super().write(self.env['lab.tz.normaliser']._normalise_tz(vals))


class HrEmployee(models.Model):
    """The other half of hr's timezone sync.

    hr copies a timezone from the user to the employee and back, and stops only once
    the two match (``vals['tz'] != employee.user_id.tz``). Normalising one side alone
    guarantees they never match: hr keeps handing on 'Asia/Calcutta' while the user
    keeps storing 'Asia/Kolkata', each write triggers the other, and a login dies with
    RecursionError after ~230 round trips. Normalising here too makes both sides agree
    on the first hop, whichever direction the write came from.
    """
    _inherit = 'hr.employee'

    @api.model_create_multi
    def create(self, vals_list):
        norm = self.env['lab.tz.normaliser']
        return super().create([norm._normalise_tz(v) for v in vals_list])

    def write(self, vals):
        return super().write(self.env['lab.tz.normaliser']._normalise_tz(vals))


class ResourceResource(models.Model):
    """Where an employee's timezone is actually stored (hr.employee _inherits it)."""
    _inherit = 'resource.resource'

    @api.model_create_multi
    def create(self, vals_list):
        norm = self.env['lab.tz.normaliser']
        return super().create([norm._normalise_tz(v) for v in vals_list])

    def write(self, vals):
        return super().write(self.env['lab.tz.normaliser']._normalise_tz(vals))
