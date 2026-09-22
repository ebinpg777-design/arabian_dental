# -*- coding: utf-8 -*-
import base64
import logging
import time
from datetime import timedelta

from odoo import _, api, fields, models
from .epg_whatsapp_account import TRANSPORT_ERROR

_logger = logging.getLogger(__name__)

# Backoff between attempts, in minutes. Deliberately short at first (a blip clears in
# seconds) and long afterwards (an unverified number will not fix itself).
BACKOFF = [2, 10, 60, 360]
# One cron run: how many messages at most, in chunks, and for how long. The queue shares
# its process with the web client, so a backlog is spread over runs (2026-08-19).
BATCH_SIZE = 200
# Small chunks because the expensive part is rendering a PDF per message (~2 s each):
# the budget can only be honoured as tightly as one chunk.
CHUNK_SIZE = 5
QUEUE_BUDGET_SECONDS = 60
MAX_TRIES = len(BACKOFF)


class EpgWhatsappMessage(models.Model):
    """Every message, whether it left or not, and what became of it.

    The log is the feature. A notification channel without one produces the worst
    support conversation there is — "we sent it" against "I never got it", with nothing
    to check. Each row keeps the number, the exact text, the record it came from and,
    when it failed, what Meta said about it.

    "Sent" is not the end of the story, which is why the states run past it: Meta
    reports back delivery and read receipts, and the difference between *sent* and
    *read* is the difference between the lab having done its part and the doctor
    actually knowing.
    """
    _name = 'epg.whatsapp.message'
    _description = 'WhatsApp Message'
    _order = 'create_date desc, id desc'
    _rec_name = 'number'

    account_id = fields.Many2one('epg.whatsapp.account', string='Sender')
    template_id = fields.Many2one('epg.whatsapp.template', string='Template')
    partner_id = fields.Many2one('res.partner', index='btree_not_null')
    conversation_id = fields.Many2one('epg.whatsapp.conversation', index=True,
                                      ondelete='set null')
    # Indexed: the Desk, the warnings, the best-time reading and the opt-out all ask
    # for one number's messages, and the log outgrows a sequential scan in a year.
    number = fields.Char('To', required=True, index=True)
    body = fields.Text(required=True)
    attachment_id = fields.Many2one('ir.attachment', string='Attachment')

    direction = fields.Selection(
        [('outbound', 'Sent by us'), ('inbound', 'From the customer')],
        default='outbound', required=True, index=True)

    res_model = fields.Char('Document Model', index=True)
    res_id = fields.Many2oneReference('Document', model_field='res_model', index=True)

    # 'ready' and 'opened' are the WhatsApp app / Web channel: written by Odoo,
    # waiting for a person to send it, then opened in WhatsApp but not yet confirmed.
    state = fields.Selection(
        [('draft', 'Queued'), ('ready', 'To Send'), ('opened', 'Opened in WhatsApp'),
         ('sent', 'Sent'), ('delivered', 'Delivered'),
         ('read', 'Read'), ('simulated', 'Simulated'), ('received', 'Received'),
         ('error', 'Failed'), ('cancel', 'Cancelled')],
        default='draft', required=True, index=True)
    channel = fields.Selection(related='account_id.channel', store=True, index=True)
    error = fields.Char(readonly=True)
    error_code = fields.Char(readonly=True)
    external_id = fields.Char('Meta Message ID', readonly=True, index='btree_not_null')
    sent_at = fields.Datetime(readonly=True, index='btree_not_null')
    delivered_at = fields.Datetime(readonly=True)
    read_at = fields.Datetime(readonly=True)

    # Sent as an approved template because the free-form window had closed.
    sent_as_template = fields.Boolean(readonly=True, copy=False)

    try_count = fields.Integer(default=0, readonly=True, copy=False)
    next_try_at = fields.Datetime(readonly=True, copy=False, index='btree_not_null')

    company_id = fields.Many2one('res.company', default=lambda s: s.env.company)

    def _attach_report(self, record):
        """Render the template's report and attach it."""
        self.ensure_one()
        report = self.template_id.report_id
        if not report or not record:
            return
        pdf, _ext = report.sudo()._render_qweb_pdf(report.report_name, [record.id])
        # Named as the doctor should see it in the chat: "Tax Invoice - OC245822.pdf",
        # not a bare number.
        self.attachment_id = self.env['ir.attachment'].sudo().create({
            'name': '%s - %s.pdf' % (report.name, record.display_name or 'document'),
            'type': 'binary',
            'datas': base64.b64encode(pdf),
            'res_model': self._name,
            'res_id': self.id,
            'mimetype': 'application/pdf',
        }).id

    # ------------------------------------------------------------------ sending
    def _conversation(self):
        self.ensure_one()
        if self.conversation_id:
            return self.conversation_id
        conv = self.env['epg.whatsapp.conversation']._get_or_create(
            self.account_id, self.number, self.partner_id)
        self.conversation_id = conv.id
        return conv

    def _send_blocked_reason(self):
        """Why this message must not be sent at all, or False.

        A consent decision, not a failure: the message is cancelled with the reason
        and never retried. Business modules extend this with their own consent rules
        (a contact's opt-in, the phone blacklist) so that every path that sends — an
        event, the composer, a retry, the queue — checks the same thing.
        """
        self.ensure_one()
        return False

    def _cancel_blocked(self):
        """Cancel the messages that must not go, with the reason; return the rest."""
        blocked = self.browse()
        for message in self:
            # 'cancel' included: action_send on a message cancelled for consent must
            # ask again, not treat the earlier refusal as a licence to send.
            if message.direction != 'outbound' or \
                    message.state not in ('draft', 'error', 'cancel'):
                continue
            reason = message._send_blocked_reason()
            if reason:
                message.write({'state': 'cancel', 'error': reason[:500],
                               'next_try_at': False})
                blocked |= message
        return self - blocked

    def action_send(self):
        for message in self:
            if message.state in ('sent', 'simulated', 'delivered', 'read', 'opened'):
                continue
            if message.direction == 'inbound':
                continue
            if not message.number:
                message._fail(_('No phone number on the record.'))
                continue
            account = message.account_id
            if not account:
                message._fail(_('No WhatsApp sender is configured.'))
                continue
            if not message._cancel_blocked():
                # Not a failure to retry — a decision to respect.
                continue

            conv = message._conversation()
            if conv.opt_out:
                # Not a failure to retry — a decision to respect.
                message.write({'state': 'cancel',
                               'error': _('This number has opted out of messages.')})
                continue

            if account.channel == 'link':
                # Nothing leaves the server: the message waits on the Desk for a
                # person to send it from the lab's own WhatsApp.
                if message.state != 'ready':
                    message._prepare_link()
                continue

            # Outside the 24-hour window Meta will not deliver free-form text. Send the
            # approved template instead of a message that would be rejected.
            use_template = not conv.window_open
            # A test the author sends to their own phone through a SIMULATED sender
            # reaches nobody, so the window rule has nothing to protect; through a
            # live sender it applies exactly as it would for a doctor.
            if account.simulation_mode and self.env.context.get('wa_test_send'):
                use_template = False
            template_name = message.template_id.meta_template_name if use_template else None
            if use_template and not template_name:
                message._fail(_(
                    "The 24-hour reply window for this number has closed, and this "
                    "message has no approved Meta template to fall back on. WhatsApp "
                    "will only accept a template here."))
                continue

            ok, detail = account._send(
                message.number, message.body,
                attachment=message.attachment_id or None,
                template=template_name,
                variables=message.template_id._meta_variables(message) if use_template else None)
            if ok:
                message.write({
                    'state': 'simulated' if account.simulation_mode else 'sent',
                    'error': False, 'error_code': False,
                    'external_id': detail if not account.simulation_mode else False,
                    'sent_at': fields.Datetime.now(),
                    'sent_as_template': bool(template_name),
                    'next_try_at': False,
                })
                conv._note_outbound()
                message._chatter_sent()
            else:
                message._fail(detail)
                if (detail or '').startswith(TRANSPORT_ERROR):
                    # The endpoint is unreachable or the sender is not configured: every
                    # remaining message would wait out the same timeout, so stop here and
                    # leave them queued for the next run. Without this one outage turned a
                    # 10-minute cron into an 8-minute one, on a worker the web client
                    # needed (2026-08-19).
                    _logger.warning(
                        "WhatsApp: transport is down (%s) — %s message(s) left queued",
                        (detail or '')[:120], len(self) - list(self).index(message) - 1)
                    return False
        return True

    def _fail(self, detail):
        """Record a failure and decide whether it is worth trying again."""
        self.ensure_one()
        tries = self.try_count + 1
        vals = {'state': 'error', 'error': (detail or '')[:500], 'try_count': tries}
        if tries <= MAX_TRIES:
            vals['next_try_at'] = fields.Datetime.now() + timedelta(
                minutes=BACKOFF[tries - 1])
        else:
            # Stop. A queue that retries for ever turns one bad number into a permanent
            # background load and hides the failures that could still be fixed.
            vals['next_try_at'] = False
        self.write(vals)

    def action_retry(self):
        self.write({'state': 'draft', 'error': False, 'error_code': False,
                    'try_count': 0, 'next_try_at': False})
        return self.action_send()

    def _attach_pending_reports(self):
        """Render the report of every message in `self` that still needs one."""
        for message in self:
            if not (message.template_id.report_id and not message.attachment_id
                    and message.res_model and message.res_id):
                continue
            record = message._record()
            if record is None:
                continue
            try:
                message._attach_report(record)
            except Exception as exc:                            # noqa: BLE001
                _logger.warning("WhatsApp: report for %s failed: %s", message.id, exc)

    @api.model
    def _cron_send_queue(self):
        """Send what is queued and retry what failed, once its backoff has elapsed.

        The run is bounded: a batch of `BATCH_SIZE` and a wall-clock budget, because this
        cron shares its process with the web client. A backlog is worked through over
        several runs instead of one run holding a worker for minutes.
        """
        started = time.monotonic()
        budget = int(self.env['ir.config_parameter'].sudo().get_param(
            'epg_whatsapp.queue_seconds', QUEUE_BUDGET_SECONDS))
        now = fields.Datetime.now()
        # Unsent WhatsApp app / Web messages past their sender's limit are dropped
        # first, so the Desk never fills with news nobody wants any more.
        self._expire_ready()
        self._cron_nudge_unopened()
        due = self.search([
            ('direction', '=', 'outbound'),
            # A delayed event (feedback the day after delivery) waits its turn.
            '|', ('scheduled_at', '=', False), ('scheduled_at', '<=', now),
            '|',
            ('state', '=', 'draft'),
            # A retry is an API matter: a person sends link messages, and a failure
            # left over from the paid channel must not flood their Desk.
            '&', '&', ('state', '=', 'error'), ('next_try_at', '<=', now),
            ('channel', '!=', 'link'),
        ], limit=BATCH_SIZE)
        if not due:
            return 0
        retries = due.filtered(lambda m: m.state == 'error')
        retries.write({'state': 'draft', 'error': False})
        sent = 0
        for chunk in [due[i:i + CHUNK_SIZE] for i in range(0, len(due), CHUNK_SIZE)]:
            if time.monotonic() - started > budget:
                _logger.info("WhatsApp: %ss budget reached, %s message(s) left for the "
                             "next run", budget, len(due) - sent)
                break
            # Deferred sends (template.send(defer=True)) arrive without their PDF: it is
            # rendered here, off the user's click, right before sending — a chunk at a
            # time, because a wkhtmltopdf run is seconds and rendering a whole batch up
            # front is what turned this cron into 8-minute runs (2026-08-19).
            # Consent first: a blocked message must not cost a PDF render.
            chunk._cancel_blocked()._attach_pending_reports()
            if not chunk.action_send():
                break                      # transport down: the rest stay queued
            sent += len(chunk)
            self.env.cr.commit()           # keep what has gone out if the run is cut short
        _logger.info("WhatsApp: processed %s queued/retry message(s) in %.1fs",
                     sent, time.monotonic() - started)
        return sent

    # ------------------------------------------------------------------ receipts
    @api.model
    def _apply_status(self, external_id, status, timestamp=None, error=None,
                      error_code=None):
        """Apply one delivery receipt from Meta's webhook.

        Statuses can arrive out of order — a `delivered` after a `read` is normal — so a
        receipt never moves a message backwards. Without that rule a late callback
        silently downgrades a message somebody has already read.
        """
        message = self.sudo().search([('external_id', '=', external_id)], limit=1)
        if not message:
            return False
        rank = {'sent': 1, 'delivered': 2, 'read': 3}
        when = timestamp or fields.Datetime.now()
        if status == 'failed':
            message.write({'state': 'error', 'error': (error or _('Failed at WhatsApp'))[:500],
                           'error_code': error_code or False})
            return True
        if status not in rank:
            return False
        if rank.get(status, 0) <= rank.get(message.state, 0):
            return False
        vals = {'state': status}
        if status == 'delivered':
            vals['delivered_at'] = when
        elif status == 'read':
            vals['read_at'] = when
            if not message.delivered_at:
                # A read receipt implies delivery even if that callback never arrived.
                vals['delivered_at'] = when
        message.write(vals)
        return True

    def action_open_document(self):
        self.ensure_one()
        if not (self.res_model and self.res_id):
            return False
        return {
            'type': 'ir.actions.act_window', 'res_model': self.res_model,
            'res_id': self.res_id, 'view_mode': 'form',
        }
