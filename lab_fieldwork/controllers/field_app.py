# -*- coding: utf-8 -*-
"""Where an executive gets the Lab app (2026-10-20): the page, and the APK itself.

Both need a login - the link goes round on WhatsApp, and a login is the one thing
every executive already has - and nothing more: the page is deliberately plain
HTML, readable on any phone, with the one button that matters.
"""
from markupsafe import Markup

from odoo import http
from odoo.http import request

from ..models.field_app import APP_MIMETYPE


class FieldAppController(http.Controller):

    @http.route('/lab_fieldwork/app', type='http', auth='user', methods=['GET'])
    def page(self, **kw):
        App = request.env['lab.field.app']
        agent = request.httprequest.user_agent.string or ''
        # The app's WebView says who it is (MainActivity sets the user agent), so the
        # page can say "you have it" instead of offering it again.
        in_app = 'ADLField/' in agent
        in_app_version = agent.split('ADLField/', 1)[1].split()[0] if in_app else ''
        return request.render('lab_fieldwork.field_app_page', {
            'doctype': Markup('<!DOCTYPE html>'),
            'info': App._info(),
            'in_app': in_app,
            'in_app_version': in_app_version,
            'android': 'Android' in agent,
            'company': request.env.company,
        })

    @http.route('/lab_fieldwork/app/download', type='http', auth='user', methods=['GET'])
    def download(self, **kw):
        App = request.env['lab.field.app']
        attachment = App._attachment()
        if not attachment:
            raise request.not_found()
        version = App._version()
        filename = 'adl-field-%s.apk' % version if version else 'adl-field.apk'
        stream = request.env['ir.binary']._get_stream_from(
            attachment, 'raw', filename=filename, mimetype=APP_MIMETYPE)
        return stream.get_response(as_attachment=True)
