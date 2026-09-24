# -*- coding: utf-8 -*-
"""What the home screen has to say, worked out once when the page loads.

Carried on the session rather than fetched by the browser: it is a single
`limit=1` read that every user would otherwise pay for in an extra round trip,
and a warning nobody has to ask for is the whole point of putting it there.
"""
import logging

from odoo import models

_logger = logging.getLogger(__name__)


class IrHttp(models.AbstractModel):
    _inherit = 'ir.http'

    # The wallpapers the module ships, and which of them need light captions.
    # Named here and not read off the directory: a name that does not exist has
    # to fall back to something rather than leave the home screen blank.
    WALLPAPERS = ('studio', 'daylight', 'midnight', 'ceramic')
    DARK_WALLPAPERS = ('midnight', 'ceramic')
    DEFAULT_WALLPAPER = 'studio'

    def session_info(self):
        info = super().session_info()
        info['home_alerts'] = self._home_alerts()
        info['home_wallpaper'] = self._home_wallpaper()
        return info

    def _home_wallpaper(self):
        """Which wallpaper this database shows, from `lab_home.wallpaper`.

        A setting and not a preference: the home screen is the lab's front door
        and it should look the same to everybody who walks through it.
        """
        chosen = (self.env['ir.config_parameter'].sudo()
                  .get_param('lab_home.wallpaper') or '').strip().lower()
        if chosen not in self.WALLPAPERS:
            chosen = self.DEFAULT_WALLPAPER
        return {'name': chosen, 'dark': chosen in self.DARK_WALLPAPERS}

    def _home_alerts(self):
        """The warning strip on the app grid, worst first.

        Only for users who could do something about it. A warning shown to
        somebody who cannot act on it is noise, and this one names the state of
        the database's backups, which is not everybody's business.
        """
        if not self.env.user.has_group('base.group_system'):
            return []
        alerts = []
        for builder in (self._home_alert_backup,):
            try:
                alert = builder()
            except Exception:
                # A broken warning must not take the home screen down with it:
                # this runs inside session_info, which is every page load.
                _logger.exception("home screen alert failed")
                continue
            if alert:
                alerts.append(alert)
        return alerts

    def _home_alert_backup(self):
        """The nightly backup did not report success on its last run.

        What counts as a failure, and what the warning says, is the backup
        module's own business - it writes the log and knows its wording. This
        only asks. The module is not a dependency: a home screen that will not
        load without a backup add-on installed is the wrong trade.
        """
        if 'auto.database.backup.status' not in self.env:
            return None
        return self.env['auto.database.backup.status'].sudo()._home_alert()
