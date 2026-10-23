# -*- coding: utf-8 -*-
"""Where an executive gets the Lab app (2026-10-20): the page, and the APK itself.

Both need a login - the link goes round on WhatsApp, and a login is the one thing
every executive already has - and nothing more: the page is deliberately plain
HTML, readable on any phone, with the one button that matters.
"""
from markupsafe import Markup

from odoo import http
from odoo.http import request

from ..models.field_app import APP_MIMETYPE, IOS_FILE, IOS_PREFIX, platform_of


class FieldAppController(http.Controller):

    @http.route('/lab_fieldwork/app', type='http', auth='user', methods=['GET'])
    def page(self, **kw):
        App = request.env['lab.field.app']
        agent = request.httprequest.user_agent.string or ''
        # The app's WebView says who it is (MainActivity sets the user agent), so the
        # page can say "you have it" instead of offering it again.
        in_app = 'ADLField/' in agent
        in_app_version = agent.split('ADLField/', 1)[1].split()[0] if in_app else ''
        ios = platform_of(agent) == 'ios'
        return request.render('lab_fieldwork.field_app_page', {
            'doctype': Markup('<!DOCTYPE html>'),
            'info': App._info(),
            'ios_info': App._ios_info(),
            'in_app': in_app,
            'in_app_version': in_app_version,
            'android': 'Android' in agent,
            'ios': ios,
            # a computer (the office sending the link round): both phones are shown
            'desktop': not ios and 'Android' not in agent,
            'company': request.env.company,
        })

    @http.route('/lab_fieldwork/app/download', type='http', auth='user', methods=['GET'])
    def download(self, **kw):
        App = request.env['lab.field.app']
        attachment = App._attachment()
        if not attachment:
            raise request.not_found()
        version = App._version()
        filename = 'adl-pro-%s.apk' % version if version else 'adl-pro.apk'
        stream = request.env['ir.binary']._get_stream_from(
            attachment, 'raw', filename=filename, mimetype=APP_MIMETYPE)
        return stream.get_response(as_attachment=True)

    # ------------------------------------------------------------------ the iPhone
    # The iPhone's installer fetches the manifest and the build itself, WITHOUT the
    # browser's session: these two answer to the secret token in the link instead of
    # a login (a new token with every upload). The build installs only on the iPhones
    # its profile registers. (client, 2026-10-21)
    @http.route(IOS_PREFIX + '/<string:token>/manifest.plist', type='http', auth='public',
                methods=['GET'], save_session=False)
    def ios_manifest(self, token, **kw):
        App = request.env['lab.field.app'].sudo()
        if not App._ios_token_ok(token):
            raise request.not_found()
        return request.make_response(App._ios_manifest(), headers=[
            ('Content-Type', 'text/xml; charset=utf-8'), ('Cache-Control', 'no-store')])

    @http.route(IOS_PREFIX + '/<string:token>/' + IOS_FILE, type='http', auth='public',
                methods=['GET'], save_session=False)
    def ios_build(self, token, **kw):
        App = request.env['lab.field.app'].sudo()
        if not App._ios_token_ok(token):
            raise request.not_found()
        stream = request.env['ir.binary'].sudo()._get_stream_from(
            App._ios_attachment(), 'raw', filename=IOS_FILE, mimetype='application/octet-stream')
        return stream.get_response(as_attachment=True)
