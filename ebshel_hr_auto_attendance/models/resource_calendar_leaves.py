# Part of ebshel_hr_auto_attendance. See LICENSE file for full copyright and licensing details.
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from odoo import api, models


class ResourceCalendarLeaves(models.Model):
    _inherit = 'resource.calendar.leaves'

    @api.model_create_multi
    def create(self, vals_list):
        leaves = super().create(vals_list)
        leaves._whole_day_public_holidays()
        return leaves

    def _whole_day_public_holidays(self):
        """A public holiday with no length becomes its whole day.

        Odoo 20 loads its public holidays (Time Off > Public Holidays > Load, and the
        yearly job that adds next year's) with date_from = date_to = the date at
        midnight UTC: no length at all, so no working hour is taken out - neither
        here nor in Odoo's own time-off counts. A company-wide one is meant as the
        day, so it becomes 00:00-23:59:59 in the company's time zone, as a holiday
        typed in by hand is.
        """
        for leave in self.filtered(lambda l: not l.resource_id and l.date_from and l.date_to
                                   and l.date_from == l.date_to and l.date_from.time() == time.min):
            tz = ZoneInfo(leave.company_id.tz or leave.calendar_id.company_id.tz or self.env.user.tz or 'UTC')
            day = leave.date_from.date()
            leave.write({
                'date_from': datetime.combine(day, time.min, tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None),
                'date_to': datetime.combine(day, time(23, 59, 59), tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None),
            })
        return True
