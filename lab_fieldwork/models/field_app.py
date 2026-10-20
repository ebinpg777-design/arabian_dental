# -*- coding: utf-8 -*-
"""The Lab app (client, 2026-10-20: "avoid the third-party Traccar Client app and
implement live GPS tracking with Odoo").

The app is a thin Android shell around this very web client - the same My Day, the
same visit forms, the same camera - with the one thing a web page cannot do added
natively: it keeps recording the phone's position with the screen off, as a
foreground service that posts to `/lab_fieldwork/gps` (controllers/gps.py), the door
the Traccar app already used. Nothing about the trail changes. Positions are kept
only inside the attendance window, thinned to movement, and feed the live map, the
arrival reminders and the halts exactly as before.

Three things live here.

* **Provisioning.** The app identifies its phone the way Traccar did, by the user's
  device identifier - but the page hands it out itself the first time the app asks
  (`res.users._fw_native_config`), as the executive, for the executive. Nobody types
  a 16-character code into anything and no administrator switches anything on. The
  administrator's levers are the ones they already had: the phone exception (never
  watched) and the lab-wide tracking switch.
* **Standing down.** Inside the app, the page's own GPS watch is told not to run -
  two watches on one phone is double the battery for one trail - and so is every
  other browser of that user while the app is evidently posting.
* **The APK itself.** Uploaded once per build by the Field Work administrator (Field
  Work settings) and served to executives from `/lab_fieldwork/app`: no Play Store,
  no third-party store, a link the office sends on WhatsApp.

The app's side of the contract is projects/adl_field_app (its README has the bridge).
"""
import re

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .location_ping import HEARTBEAT_SECONDS, MIN_MOVE_METRES

APP_ATTACHMENT = 'lab_fieldwork.field_app.apk'
APP_MIMETYPE = 'application/vnd.android.package-archive'
APP_PAGE = '/lab_fieldwork/app'
APP_DOWNLOAD = '/lab_fieldwork/app/download'
GPS_PATH = '/lab_fieldwork/gps'
# A phone whose app posted within this long is live, and every browser of that user
# stands down. The app's heartbeat is 300 s and its uploads 90 s apart, so a parked
# phone is heard from well inside this.
APP_LIVE_SECONDS = 900
# Idle, the service still reports in this often: the answer says whether the day is
# still running (controllers/gps.py), so a day ended on the office computer stops
# the phone too.
IDLE_CHECK_SECONDS = 600
VERSION_MAX = 32
VERSION_CHARS = re.compile(r'[^0-9A-Za-z._-]')


def clean_version(version):
    return VERSION_CHARS.sub('', str(version or ''))[:VERSION_MAX]


def track_interval(env):
    """How often the phone sends, in seconds: the same setting the browser reads."""
    try:
        return max(30, int(env['ir.config_parameter'].sudo().get_str(
            'lab_fieldwork.track_interval', '90')))
    except (TypeError, ValueError):
        return 90


class ResUsersFieldApp(models.Model):
    _inherit = 'res.users'

    fw_native_app = fields.Boolean(
        'Uses ADL FieldX', readonly=True, copy=False,
        groups='lab_fieldwork.group_fieldwork_manager',
        help="This person's phone runs ADL FieldX, the lab's app, which records the "
             "route with the screen off. The app sets this itself the first time it is "
             "opened; browsers then stop asking for a position while it is posting.")
    fw_native_app_version = fields.Char(
        'ADL FieldX Version', readonly=True, copy=False, size=VERSION_MAX,
        groups='lab_fieldwork.group_fieldwork_manager')

    def _fw_native_config(self, version=None):
        """The settings the app's tracking service runs on, provisioning the phone on
        the way: a device identifier (the Traccar one, reused), the flag, the version
        the app reports. Only ever called for env.user (tracking_state), so an
        executive provisions their own phone and nobody else's."""
        self.ensure_one()
        me = self.sudo()
        vals = {}
        if not me.fw_gps_device_id:
            vals['fw_gps_device_id'] = self._fw_new_device_id()
        if not me.fw_native_app:
            vals['fw_native_app'] = True
        version = clean_version(version)
        if version and version != me.fw_native_app_version:
            vals['fw_native_app_version'] = version
        if vals:
            me.write(vals)
        return {
            'path': GPS_PATH,
            'device_id': me.fw_gps_device_id,
            'interval_s': track_interval(self.env),
            'min_move_m': MIN_MOVE_METRES,
            'heartbeat_s': HEARTBEAT_SECONDS,
            'idle_check_s': IDLE_CHECK_SECONDS,
            'latest_version': self.env['lab.field.app']._version(),
            'notice': _("Your route is recorded while your day is running."),
        }

    def _fw_native_last_fix_age(self):
        """Seconds since the app on this phone last posted a kept position, or None."""
        self.ensure_one()
        last = self.env['lab.location.ping'].sudo().search(
            [('user_id', '=', self.id), ('source', '=', 'app')], order='ts desc', limit=1)
        return int((fields.Datetime.now() - last.ts).total_seconds()) if last else None


class LabFieldApp(models.AbstractModel):
    _name = 'lab.field.app'
    _description = 'Lab Field App'

    @api.model
    def _attachment(self):
        # Not attached to any record: the one APK of the one lab, read through sudo
        # by the download controller and nothing else.
        return self.env['ir.attachment'].sudo().search(
            [('name', '=', APP_ATTACHMENT), ('res_model', '=', False)], limit=1)

    @api.model
    def _version(self):
        return self.env['ir.config_parameter'].sudo().get_str('lab_fieldwork.app_version', '')

    @api.model
    def _set_version(self, version):
        version = clean_version(version)
        self.env['ir.config_parameter'].sudo().set_str('lab_fieldwork.app_version', version)
        return version

    @api.model
    def _store(self, content, version, filename=None):
        """Keep the one APK the executives get - the latest - and its version."""
        content = bytes(content or b'')
        # An APK is a zip; anything else is a wrong file picked in the dialog.
        if not content.startswith(b'PK\x03\x04'):
            raise UserError(_("That file is not an Android package (.apk)."))
        version = self._set_version(version)
        vals = {
            'raw': content,
            'mimetype': APP_MIMETYPE,
            'description': 'Lab app %s' % version if version else 'Lab app',
        }
        attachment = self._attachment()
        if attachment:
            attachment.write(vals)
        else:
            attachment = self.env['ir.attachment'].sudo().create(
                dict(vals, name=APP_ATTACHMENT, res_model=False, res_id=0, public=False))
        return attachment

    @api.model
    def _info(self):
        """What the download page and My Day say about the app, or None without one."""
        attachment = self._attachment()
        if not attachment:
            return None
        return {
            'url': APP_PAGE,
            'download_url': APP_DOWNLOAD,
            'version': self._version(),
            'size_mb': round((attachment.file_size or 0) / 1048576.0, 1),
            'uploaded': fields.Datetime.to_string(attachment.write_date),
        }
