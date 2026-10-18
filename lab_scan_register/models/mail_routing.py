# -*- coding: utf-8 -*-
"""Which fetched e-mails become scan registers.

An incoming mail server set to create Scan Registers made one of every mail in
the mailbox - newsletters and account notices included. With "Only scans from
known senders or subjects" on the server (the default), a new mail becomes a
register only when an e-mail source recognises its sender or its subject; the
rest are read and left alone. A reply to an existing register is never held
back. (client, 2026-10-13)
"""
import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class FetchmailServer(models.Model):
    _inherit = 'fetchmail.server'

    object_model = fields.Char(related='object_id.model', string='Creates')
    scan_matching_only = fields.Boolean(
        'Only scans from known senders or subjects', default=True,
        help="When this server creates Scan Registers: register only the e-mails "
             "whose sender or subject an E-mail source recognises (Scan Register > "
             "Configuration > Sources). Other e-mails are read and skipped. Untick "
             "to register every e-mail.")


class MailThread(models.AbstractModel):
    _inherit = 'mail.thread'

    @api.model
    def message_route(self, message, message_dict, model=None, thread_id=None, custom_values=None):
        routes = super().message_route(message, message_dict, model=model, thread_id=thread_id,
                                       custom_values=custom_values)
        server_id = self.env.context.get('default_fetchmail_server_id')
        if not server_id or not routes:
            return routes
        server = self.env['fetchmail.server'].sudo().browse(server_id).exists()
        if not server.scan_matching_only:
            return routes
        kept = []
        for route in routes:
            new_scan = route[0] == 'lab.scan' and not route[1]
            if new_scan and not self.env['lab.scan'].sudo()._mail_source(message_dict):
                _logger.info("Scan register: skipped mail from %s, subject %r - no e-mail "
                             "source recognises its sender or subject",
                             message_dict.get('email_from'), message_dict.get('subject'))
                continue
            kept.append(route)
        return kept
