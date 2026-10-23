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

**The iPhone** (client, 2026-10-21: "create the iOS app also, with a download button
on the same link"). The same shell exists for iOS (projects/adl_field_app/ios). Apple
lets an iPhone install an app only signed by the lab's Apple Developer account, so the
page offers one of two things the administrator sets in Field Work settings: a signed
Ad Hoc build (.ipa) - installed straight from this site on the iPhones its profile
registers, through an `itms-services` manifest - or a TestFlight / App Store link.
Until there is either, an iPhone is shown how to put the site on its home screen.
"""
import hmac
import io
import plistlib
import re
import secrets
import zipfile
from urllib.parse import quote

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.http import request

from .location_ping import HEARTBEAT_SECONDS, MIN_MOVE_METRES

APP_ATTACHMENT = 'lab_fieldwork.field_app.apk'
APP_MIMETYPE = 'application/vnd.android.package-archive'
APP_PAGE = '/lab_fieldwork/app'
APP_DOWNLOAD = '/lab_fieldwork/app/download'
IOS_ATTACHMENT = 'lab_fieldwork.field_app.ipa'
IOS_PREFIX = '/lab_fieldwork/app/ios'
IOS_FILE = 'ADLFieldX.ipa'
IOS_AGENTS = ('iPhone', 'iPad', 'iPod')
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


def platform_of(agent):
    """'ios' for an iPhone / iPad (the app's WebView included), else 'android'."""
    return 'ios' if any(word in (agent or '') for word in IOS_AGENTS) else 'android'


def request_platform():
    try:
        return platform_of(request.httprequest.user_agent.string if request else '')
    except RuntimeError:  # outside a request: tests, crons
        return 'android'


def read_ipa(content, env=None):
    """What an uploaded iPhone build is: its bundle, version and who may install it.

    An .ipa is a zip holding Payload/<App>.app/Info.plist (binary or XML plist) and,
    for a build signed for distribution outside the App Store, embedded.mobileprovision
    - a signed envelope round an XML plist listing the iPhones it installs on
    (ProvisionedDevices) or, for an enterprise build, all of them. A build for the
    App Store / TestFlight lists none: it cannot be installed from a link, and is
    refused here with the way out (its TestFlight link).
    """
    # env._ when there is one: a bare _() outside a model finds no language to use
    t = env._ if env is not None else _
    content = bytes(content or b'')
    if not content.startswith(b'PK\x03\x04'):
        raise UserError(t("That file is not an iPhone app (.ipa)."))
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = archive.namelist()
            info_name = next((n for n in names if re.fullmatch(r'Payload/[^/]+\.app/Info\.plist', n)), None)
            if not info_name:
                raise UserError(t("That file is not an iPhone app (.ipa): it holds no app."))
            info = plistlib.loads(archive.read(info_name))
            profile_name = info_name[:-len('Info.plist')] + 'embedded.mobileprovision'
            profile = archive.read(profile_name) if profile_name in names else b''
    except (zipfile.BadZipFile, plistlib.InvalidFileException, ValueError) as error:
        raise UserError(t("That file is not a readable iPhone app (.ipa): %s", error)) from error
    bundle_id = str(info.get('CFBundleIdentifier') or '')
    if not bundle_id:
        raise UserError(t("That iPhone app names no bundle identifier."))
    devices, all_devices, expires = 0, False, None
    start, end = profile.find(b'<?xml'), profile.find(b'</plist>')
    if start >= 0 and end > start:
        try:
            provision = plistlib.loads(profile[start:end + len(b'</plist>')])
        except (plistlib.InvalidFileException, ValueError):
            provision = {}
        devices = len(provision.get('ProvisionedDevices') or [])
        all_devices = bool(provision.get('ProvisionsAllDevices'))
        expires = provision.get('ExpirationDate')
    if not (devices or all_devices):
        raise UserError(t(
            "This build is signed for the App Store / TestFlight, which an iPhone cannot "
            "install from a link. Upload an Ad Hoc build, or enter its TestFlight link instead."))
    return {
        'bundle_id': bundle_id,
        'version': clean_version(info.get('CFBundleShortVersionString') or info.get('CFBundleVersion')),
        'build': clean_version(info.get('CFBundleVersion')),
        'name': str(info.get('CFBundleDisplayName') or info.get('CFBundleName') or 'ADL FieldX'),
        'devices': devices,
        'all_devices': all_devices,
        'expires': expires.date().isoformat() if hasattr(expires, 'date') else '',
    }


def track_interval(env):
    """How often the phone sends, in seconds: the same setting the browser reads. It
    only sends when it has something new - standing still that is its five-minute
    heartbeat - so this is the delay while moving."""
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
            # the door takes gzipped batches (controllers/gps.py): six times less data
            'gzip': True,
            'min_move_m': MIN_MOVE_METRES,
            'heartbeat_s': HEARTBEAT_SECONDS,
            'idle_check_s': IDLE_CHECK_SECONDS,
            # the newest build for THIS phone: an iPhone is not told to install the APK
            'latest_version': self.env['lab.field.app']._latest_version(request_platform()),
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

    # ------------------------------------------------------------------ the iPhone
    @api.model
    def _param(self, key, default=''):
        return self.env['ir.config_parameter'].sudo().get_str('lab_fieldwork.%s' % key, default) or default

    @api.model
    def _set_param(self, key, value):
        self.env['ir.config_parameter'].sudo().set_str('lab_fieldwork.%s' % key, value or '')

    @api.model
    def _ios_attachment(self):
        return self.env['ir.attachment'].sudo().search(
            [('name', '=', IOS_ATTACHMENT), ('res_model', '=', False)], limit=1)

    @api.model
    def _ios_store(self, content):
        """Keep the one iPhone build the executives install - the latest - and what
        it says about itself. A fresh link token each time: a link sent with an old
        build stops working when a new one is up."""
        meta = read_ipa(content, self.env)
        for key in ('bundle_id', 'version', 'build', 'name', 'expires'):
            self._set_param('ios_%s' % key, meta[key])
        self._set_param('ios_devices', 'all' if meta['all_devices'] else str(meta['devices']))
        self._set_param('ios_token', secrets.token_urlsafe(24))
        vals = {'raw': bytes(content), 'mimetype': 'application/octet-stream',
                'description': 'Lab app for iPhone %s' % meta['version']}
        attachment = self._ios_attachment()
        if attachment:
            attachment.write(vals)
        else:
            self.env['ir.attachment'].sudo().create(
                dict(vals, name=IOS_ATTACHMENT, res_model=False, res_id=0, public=False))
        return meta

    @api.model
    def _ios_set_link(self, link):
        """A TestFlight or App Store link, or nothing. Only https: it is handed to
        every executive's phone."""
        link = (link or '').strip()
        if link and not link.startswith('https://'):
            raise UserError(_("The iPhone link must start with https:// (a TestFlight or App Store link)."))
        self._set_param('ios_link', link)
        return link

    @api.model
    def _ios_remove(self):
        self._ios_attachment().unlink()
        for key in ('bundle_id', 'version', 'build', 'name', 'expires', 'devices', 'token'):
            self._set_param('ios_%s' % key, '')

    @api.model
    def _ios_token_ok(self, token):
        expected = self._param('ios_token')
        return bool(expected and token) and hmac.compare_digest(str(token), expected) \
            and bool(self._ios_attachment())

    @api.model
    def _ios_manifest(self):
        """The itms-services manifest an iPhone reads before it installs the build."""
        base = self.env['ir.config_parameter'].sudo().get_str('web.base.url', '').rstrip('/')
        token = self._param('ios_token')
        return plistlib.dumps({'items': [{
            'assets': [{'kind': 'software-package', 'url': '%s%s/%s/%s' % (base, IOS_PREFIX, token, IOS_FILE)}],
            'metadata': {
                'bundle-identifier': self._param('ios_bundle_id'),
                'bundle-version': self._param('ios_version') or '1.0',
                'kind': 'software',
                'title': self._param('ios_name') or 'ADL FieldX',
            },
        }]})

    @api.model
    def _ios_info(self):
        """What the page and My Day say about the iPhone app, or None without one."""
        attachment = self._ios_attachment()
        if attachment and self._param('ios_token'):
            base = self.env['ir.config_parameter'].sudo().get_str('web.base.url', '').rstrip('/')
            manifest = '%s%s/%s/manifest.plist' % (base, IOS_PREFIX, self._param('ios_token'))
            devices = self._param('ios_devices')
            return {
                'kind': 'ipa',
                'url': APP_PAGE,
                'install_url': 'itms-services://?action=download-manifest&url=' + quote(manifest, safe=''),
                'version': self._param('ios_version'),
                'size_mb': round((attachment.file_size or 0) / 1048576.0, 1),
                'devices': devices,
                'expires': self._param('ios_expires'),
                'uploaded': fields.Datetime.to_string(attachment.write_date),
            }
        link = self._param('ios_link')
        if link:
            return {'kind': 'link', 'url': APP_PAGE, 'install_url': link,
                    'version': self._param('ios_version'), 'size_mb': 0, 'devices': '',
                    'expires': '', 'uploaded': ''}
        return None

    @api.model
    def _latest_version(self, platform='android'):
        return self._param('ios_version') if platform == 'ios' else self._version()

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
