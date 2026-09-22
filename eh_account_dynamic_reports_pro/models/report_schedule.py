# -*- encoding: utf-8 -*-
##############################################################################
#
# ERP Heritage
# Copyright (C) 2026 (https://www.erpheritage.com.au/)
#
##############################################################################
"""
eh.report.schedule: cron driven email delivery of dynamic reports.

A schedule binds a report record + an options dict + a recipient list +
a recurrence (daily, weekly, monthly). The cron _cron_run_due runs hourly
and dispatches every schedule whose next_run is past. Each delivery
generates an attachment (XLSX, PDF, or both) and emails it via mail.mail.

Failure handling:

* Delivery exceptions are caught per schedule. The error is logged on the
  schedule (last_error, last_run_status='error') and the next_run is still
  advanced so a single bad schedule does not freeze the queue.
* If a recipient list is empty at delivery time, the schedule errors with
  a clear message rather than silently sending to nobody.

Attachment lifecycle:

* Email attachments ride on the mail.mail record.
* Webhook attachments are creator-only ir.attachment rows (no res_model /
  res_id, create_uid = the schedule's creator), so the download link works
  for the schedule owner and administrators only. They are tagged in
  description and garbage collected by the cron after
  WEBHOOK_ATTACHMENT_RETENTION_DAYS.
* The execution audit row from the orchestrator captures the exact options
  used, so a recipient can verify the render is reproducible.
"""

import base64
import json
import logging
import urllib.error
import urllib.request

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from odoo.addons.eh_account_base.tools.net_guard import (
    UnsafeUrlError, assert_safe_url,
)

from .date_tokens import resolve_relative_dates

_logger = logging.getLogger(__name__)

# Webhook download attachments are tagged "<tag>:<schedule id>" in
# ir.attachment.description so the cron can find and expire them.
WEBHOOK_ATTACHMENT_TAG = 'eh.report.schedule:webhook'
WEBHOOK_ATTACHMENT_RETENTION_DAYS = 30


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuse every HTTP redirect on the server-issued webhook POST.

    assert_safe_url() vets the configured URL only. urllib follows 30x
    responses by default, so a public endpoint answering
    "302 Location: http://169.254.169.254/..." would bounce the POST onto
    the metadata service or an internal host after the check passed.
    Webhook gateways (Slack, Teams) answer the POST directly, so a redirect
    is treated as a failed delivery instead of being followed.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(
            req.full_url, code,
            "redirect to %s refused" % newurl,
            headers, fp,
        )


class EhReportSchedule(models.Model):
    _name = 'eh.report.schedule'
    _description = "Scheduled report email delivery"
    _order = 'next_run asc, id asc'
    _inherit = ['mail.thread']

    name = fields.Char(required=True, tracking=True)
    active = fields.Boolean(default=True, tracking=True)

    user_id = fields.Many2one(
        'res.users',
        required=True,
        default=lambda self: self.env.user,
        index=True,
    )
    company_id = fields.Many2one(
        'res.company',
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    report_id = fields.Many2one(
        'eh.account.dynamic.report',
        required=True,
        ondelete='cascade',
        index=True,
    )

    options_json = fields.Text(
        required=True,
        default='{}',
        help="JSON serialised options dict applied at delivery time.",
    )

    interval = fields.Integer(default=1, required=True)
    interval_unit = fields.Selection(
        [
            ('day', "Day(s)"),
            ('week', "Week(s)"),
            ('month', "Month(s)"),
        ],
        default='month',
        required=True,
    )
    next_run = fields.Datetime(
        required=True,
        default=fields.Datetime.now,
        index=True,
    )
    last_run = fields.Datetime(readonly=True)
    last_run_status = fields.Selection(
        [
            ('success', "Success"),
            ('error', "Error"),
        ],
        readonly=True,
    )
    last_error = fields.Text(readonly=True)
    last_attachment_count = fields.Integer(default=0, readonly=True)

    delivery_format = fields.Selection(
        [
            ('xlsx', "XLSX"),
            ('pdf', "PDF"),
            ('both', "XLSX + PDF"),
        ],
        default='xlsx',
        required=True,
    )

    recipient_user_ids = fields.Many2many(
        'res.users',
        'eh_report_schedule_user_rel',
        'schedule_id',
        'user_id',
        string="User Recipients",
    )
    recipient_partner_ids = fields.Many2many(
        'res.partner',
        'eh_report_schedule_partner_rel',
        'schedule_id',
        'partner_id',
        string="Partner Recipients",
    )
    recipient_emails = fields.Char(
        help="Additional comma separated email addresses.",
    )

    subject = fields.Char(
        required=True,
        default="Scheduled Report",
        translate=True,
    )
    body = fields.Html(translate=True)

    # ---- delivery channel ----
    #
    # The original delivery path posts the rendered attachments via
    # mail.mail. Modern shops also want the report to land directly in
    # a Slack/Teams channel. The webhook channel POSTs a JSON payload
    # to a configurable URL with a download URL for each attachment;
    # Slack and Teams both consume the same shape (text + optional
    # attachment array). Generic HTTP webhooks accept the same payload.
    delivery_channel = fields.Selection(
        [
            ('email',   "Email"),
            ('webhook', "Webhook (Slack / Teams / HTTP)"),
            ('both',    "Email + Webhook"),
        ],
        default='email', required=True,
        help=(
            "Email sends the report as an attachment via mail.mail. "
            "Webhook POSTs a JSON payload to webhook_url with the "
            "report subject, body, and a download link per attachment. "
            "Both fires email and webhook in sequence; an email failure "
            "does not block the webhook and vice versa."
        ),
    )
    webhook_url = fields.Char(
        help=(
            "HTTPS endpoint that accepts a JSON POST. Slack incoming "
            "webhooks (https://hooks.slack.com/...) and MS Teams "
            "incoming connectors both work without further config; "
            "generic webhooks receive the canonical payload shape."
        ),
    )
    webhook_format = fields.Selection(
        [
            ('slack',   "Slack"),
            ('teams',   "Microsoft Teams"),
            ('generic', "Generic JSON"),
        ],
        default='slack',
        help=(
            "Selects the JSON shape the payload is wrapped in. Slack "
            "uses {text, attachments[]}; Teams uses MessageCard; "
            "Generic uses the raw {subject, body, attachments} dict."
        ),
    )
    webhook_timeout = fields.Integer(
        default=15,
        help="Network timeout in seconds for the webhook POST.",
    )

    _positive_interval = models.Constraint(
        'check(interval > 0)',
        'Schedule interval must be a positive integer.',
    )

    # ---- owner integrity ----

    def _eh_guard_owner(self, vals):
        """Refuse to hand a schedule's owner slot to another (or a
        higher-privilege) user.

        The render binds to the immutable create_uid (see _build_attachments),
        but user_id must stay honest too: it is the displayed owner and the
        email_from fallback, and leaving it freely writable invites a future
        regression that re-wires rendering onto it. A non-manager may only own
        their own schedules, and nobody may assign a schedule to a system
        administrator they are not themselves. Runs on create and write; sudo
        (module/demo data, cron bookkeeping writes) is exempt.
        """
        if self.env.su or 'user_id' not in vals or not vals.get('user_id'):
            return
        caller = self.env.user
        target = self.env['res.users'].browse(vals['user_id']).exists()
        if not target:
            return
        if target != caller and not caller.has_group(
                'eh_account_base.group_eh_manager'):
            raise UserError(_(
                "You may only own your own scheduled reports. Ask an "
                "accounting manager to assign a schedule to another user."
            ))
        if (target.has_group('base.group_system')
                and not caller.has_group('base.group_system')):
            raise UserError(_(
                "You cannot assign a scheduled report to a system "
                "administrator."
            ))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._eh_guard_owner(vals)
        return super().create(vals_list)

    def write(self, vals):
        self._eh_guard_owner(vals)
        return super().write(vals)

    # ---- public actions ----

    def action_run_now(self):
        """Run the schedule immediately. Useful for testing the cadence
        without waiting for the cron tick.

        Requires write access (the owner rule): the run stamps the schedule's
        bookkeeping fields and renders under its creator."""
        self.check_access('write')
        for schedule in self:
            try:
                schedule._send_now()
                schedule._advance_next_run()
            except Exception as exc:
                schedule._record_failure(exc)
                raise
        return True

    def action_pause(self):
        for schedule in self:
            schedule.active = False
        return True

    def action_resume(self):
        for schedule in self:
            schedule.active = True
        return True

    @api.model
    def _cron_run_due(self):
        """Runner entry point for ir.cron. Dispatches every active
        schedule whose next_run is in the past.

        Per schedule failures are isolated; one bad schedule does not
        break the rest of the run. Each delivery runs inside its own
        savepoint: a failure (including a database error that would
        otherwise abort the whole transaction) rolls back only that
        schedule's partial writes, then the error is recorded on it.
        """
        self._gc_webhook_attachments()
        now = fields.Datetime.now()
        due = self.search([
            ('active', '=', True),
            ('next_run', '<=', now),
        ])
        for schedule in due:
            try:
                with self.env.cr.savepoint():
                    schedule._send_now()
                    schedule._advance_next_run()
            except Exception as exc:
                _logger.warning(
                    "eh.report.schedule %s failed: %s",
                    schedule.id, exc,
                )
                # Still advance so the next cron pass does not retry the
                # same broken schedule until manually fixed.
                try:
                    with self.env.cr.savepoint():
                        schedule._record_failure(exc)
                        schedule._advance_next_run()
                except Exception:
                    _logger.exception(
                        "eh.report.schedule %s: could not record failure",
                        schedule.id,
                    )
        return True

    @api.model
    def _gc_webhook_attachments(self):
        """Delete webhook download attachments past their retention."""
        cutoff = fields.Datetime.now() - relativedelta(
            days=WEBHOOK_ATTACHMENT_RETENTION_DAYS)
        try:
            with self.env.cr.savepoint():
                self.env['ir.attachment'].sudo().search([
                    ('res_model', '=', False),
                    ('description', '=like', WEBHOOK_ATTACHMENT_TAG + ':%'),
                    ('create_date', '<', cutoff),
                ]).unlink()
        except Exception:  # noqa: BLE001 - housekeeping never blocks delivery
            _logger.exception("eh.report.schedule: webhook attachment GC failed")

    # ---- internals ----

    def _send_now(self):
        """Dispatch a single delivery via the configured channel(s).

        Delivery channels run independently: an email failure does not
        block the webhook and vice versa. The schedule is recorded as
        successful when at least one channel succeeds; a total failure
        bubbles up to the cron handler which sets last_run_status=error.
        """
        self.ensure_one()
        options = self._parse_options()
        attachments = self._build_attachments(options)
        if not attachments:
            raise UserError(_("Could not build any attachment for delivery."))

        body_html = self.body or self._default_body_html(options)
        successes = 0
        errors = []

        if self.delivery_channel in ('email', 'both'):
            try:
                self._dispatch_email(attachments, body_html)
                successes += 1
            except Exception as exc:  # noqa: BLE001
                errors.append("email: %s" % exc)

        if self.delivery_channel in ('webhook', 'both'):
            try:
                self._dispatch_webhook(attachments, body_html)
                successes += 1
            except Exception as exc:  # noqa: BLE001
                errors.append("webhook: %s" % exc)

        if not successes:
            raise UserError(_(
                "All delivery channels failed for schedule '%(name)s': "
                "%(errors)s",
                name=self.name,
                errors='; '.join(errors),
            ))

        self.write({
            'last_run': fields.Datetime.now(),
            'last_run_status': 'success',
            'last_error': '; '.join(errors)[:8000] if errors else False,
            'last_attachment_count': len(attachments),
        })
        return True

    def _dispatch_email(self, attachments, body_html):
        """Original email path. Refused when no recipients are set."""
        self.ensure_one()
        emails = self._resolve_recipient_emails()
        if not emails:
            raise UserError(_(
                "Schedule '%s' has no recipient emails configured.",
            ) % self.name)
        mail_vals = {
            'subject': self.subject,
            'body_html': body_html,
            'email_to': ','.join(emails),
            'email_from': (
                self.env.company.email
                or self.user_id.email
                or self.env.user.email
                or False
            ),
            'auto_delete': False,
            'attachment_ids': [(0, 0, att) for att in attachments],
        }
        mail = self.env['mail.mail'].sudo().create(mail_vals)
        # send() swallows SMTP / connection errors: it flags the mail
        # 'exception' and returns True. Read the outcome back so a bounced
        # delivery is reported as a failed channel, not a success.
        mail.send(raise_exception=False)
        if mail.exists() and mail.state in ('exception', 'cancel'):
            raise UserError(_(
                "Email delivery failed for schedule '%(name)s': %(reason)s",
                name=self.name,
                reason=mail.failure_reason or mail.state,
            ))

    def _dispatch_webhook(self, attachments, body_html):
        """POST a JSON payload to the configured webhook URL.

        Attachments are linked-by-URL rather than body-encoded so the
        webhook payload stays under the platform-specific size limits
        (Slack rejects payloads above 30 kB; Teams adaptive cards
        cap at 28 kB). Each attachment is stored as a creator-only
        ir.attachment, so the standard /web/content/<id>?download=true
        link opens for the schedule's creator (and administrators) once
        signed in; other channel members see the notification only. The
        attachments expire after WEBHOOK_ATTACHMENT_RETENTION_DAYS.
        """
        self.ensure_one()
        if not self.webhook_url:
            raise UserError(_(
                "Schedule '%s' has webhook delivery enabled but no "
                "webhook_url configured.",
            ) % self.name)

        # SSRF guard: webhook_url is user-editable (any group_eh_user) but the
        # POST is issued by the hourly cron under system privileges. Without
        # this a user could point it at http://169.254.169.254/ (cloud
        # metadata), http://127.0.0.1:8069/ or an RFC 1918 host and have the
        # server fetch internal-only endpoints - the classic credential-theft
        # SSRF. Reject non-public targets before any network call. A real
        # Slack/Teams/HTTP webhook is a public https host, so legitimate use
        # is unaffected.
        try:
            assert_safe_url(self.webhook_url)
        except UnsafeUrlError as exc:
            raise UserError(_(
                "Webhook URL for schedule '%(name)s' is not allowed: "
                "%(reason)s",
                name=self.name, reason=str(exc),
            )) from exc

        # Creator-only attachments (no res_model / res_id), consistent with
        # the report exports: ir.attachment access then falls back to
        # create_uid, so the link opens for the schedule's creator (the user
        # the report was rendered as) and administrators, never for every
        # internal user. Created as that user so create_uid is right even
        # though the cron runs as root. Expired by _gc_webhook_attachments.
        owner = self.create_uid or self.env.user
        tag = '%s:%d' % (WEBHOOK_ATTACHMENT_TAG, self.id)
        attachment_ids = self.env['ir.attachment'].with_user(owner).sudo().create([
            dict(att, res_model=False, res_id=False, description=tag)
            for att in attachments
        ])
        base_url = self.env['ir.config_parameter'].sudo().get_param(
            'web.base.url', default='',
        ).rstrip('/')
        attachment_links = [
            {
                'name': att.name,
                'url': "%s/web/content/%d?download=true" % (base_url, att.id),
                'mimetype': att.mimetype,
                'size_bytes': att.file_size or 0,
            }
            for att in attachment_ids
        ]

        payload = self._build_webhook_payload(
            subject=self.subject,
            body_text=self._strip_html(body_html),
            attachment_links=attachment_links,
        )
        encoded = json.dumps(payload).encode('utf-8')
        request = urllib.request.Request(
            self.webhook_url,
            data=encoded,
            method='POST',
            headers={
                'Content-Type': 'application/json',
                'User-Agent': 'eh-account-dynamic-reports/1.0',
            },
        )
        timeout = max(int(self.webhook_timeout or 15), 1)
        # Redirects are refused (NoRedirectHandler): following one would
        # re-target the POST at a host assert_safe_url() never checked.
        opener = urllib.request.build_opener(NoRedirectHandler)
        try:
            with opener.open(request, timeout=timeout) as resp:
                # Slack/Teams typically return 200 + small body. Non-2xx
                # is a failure; we read the body so the chatter line
                # can show what the gateway rejected.
                status = getattr(resp, 'status', 200) or 200
                if status >= 300:
                    body = resp.read(2048).decode('utf-8', errors='replace')
                    raise UserError(_(
                        "Webhook returned HTTP %(status)s: %(body)s",
                        status=status, body=body,
                    ))
        except urllib.error.HTTPError as exc:
            body = ''
            try:
                body = exc.read(2048).decode('utf-8', errors='replace')
            except Exception:  # noqa: BLE001
                pass
            raise UserError(_(
                "Webhook POST failed with HTTP %(status)s: %(body)s",
                status=exc.code, body=body or exc.reason,
            )) from exc
        except urllib.error.URLError as exc:
            raise UserError(_(
                "Webhook POST failed: %s",
            ) % exc.reason) from exc

    def _build_webhook_payload(self, subject, body_text, attachment_links):
        """Return the JSON dict for the chosen webhook_format.

        Slack expects {text, attachments[]} where each attachment has
        title/title_link. Teams expects an Adaptive-Card-style
        MessageCard with sections. Generic just emits the canonical
        triple {subject, body, attachments}.
        """
        self.ensure_one()
        if self.webhook_format == 'slack':
            return {
                'text': subject,
                'attachments': [
                    {
                        'color': '#1A2C3D',
                        'title': link['name'],
                        'title_link': link['url'],
                        'text': body_text or '',
                        'footer': 'ERP Heritage Accounting Suite',
                    }
                    for link in attachment_links
                ] or [{
                    'color': '#1A2C3D',
                    'text': body_text or '',
                    'footer': 'ERP Heritage Accounting Suite',
                }],
            }
        if self.webhook_format == 'teams':
            return {
                '@type': 'MessageCard',
                '@context': 'https://schema.org/extensions',
                'summary': subject,
                'themeColor': '1A2C3D',
                'title': subject,
                'sections': [
                    {
                        'text': body_text or '',
                    },
                    {
                        'title': 'Attachments',
                        'facts': [
                            {'name': link['name'], 'value': link['url']}
                            for link in attachment_links
                        ],
                    },
                ] if attachment_links else [
                    {'text': body_text or ''},
                ],
            }
        # Generic: caller controls the consumer; emit the canonical
        # {subject, body, attachments} triple.
        return {
            'subject': subject,
            'body': body_text or '',
            'attachments': attachment_links,
            'company': self.company_id.display_name,
            'schedule': self.name,
        }

    @staticmethod
    def _strip_html(html):
        """Crude HTML-to-text stripper for webhook payloads.

        Webhook gateways render text, not HTML. We strip tags and
        collapse whitespace; preserving every nuance of the email
        body is not the goal — webhook delivery is the high-level
        notification, the attachment is the source of truth.
        """
        if not html:
            return ''
        import re
        text = re.sub(r'<[^>]+>', ' ', html)
        text = re.sub(r'\s+', ' ', text).strip()
        return text[:1500]

    def _record_failure(self, exc):
        self.ensure_one()
        self.write({
            'last_run': fields.Datetime.now(),
            'last_run_status': 'error',
            'last_error': str(exc)[:8000],
        })

    def _advance_next_run(self):
        """Move next_run to the first slot of the cadence after now.

        Slots are anchored on the previous next_run, not on the time the run
        finished, so a delivery that takes minutes (or a cron that fires late)
        does not push every later run back by that much. A schedule that
        missed several slots catches up to the next future one instead of
        firing once per missed slot. Slots are computed as anchor + n
        intervals (never chained) so a month-end anchor does not decay
        (Jan 31 -> Feb 28 -> Mar 31, not Mar 28). A next_run already in the
        future (a manual Run Now ahead of schedule) is left unchanged.
        """
        self.ensure_one()
        now = fields.Datetime.now()
        anchor = self.next_run or now
        candidate = anchor
        periods = 0
        while candidate <= now:
            periods += 1
            candidate = anchor + self._compute_delta(periods)
        if candidate != self.next_run:
            self.next_run = candidate

    def _compute_delta(self, periods=1):
        count = self.interval * periods
        if self.interval_unit == 'day':
            return relativedelta(days=count)
        if self.interval_unit == 'week':
            return relativedelta(weeks=count)
        return relativedelta(months=count)

    def _parse_options(self):
        """Stored options with relative date tokens resolved.

        options_json may carry tokens such as "auto_prev_month_start" or
        "today" (see date_tokens); the report handlers only accept ISO dates.
        Tokens resolve against the local day of the schedule's creator (the
        user the report renders as) and the schedule company's fiscal year.
        """
        try:
            options = json.loads(self.options_json or '{}')
        except ValueError:
            return {}
        if not isinstance(options, dict):
            return {}
        owner = self.create_uid or self.env.user
        today = fields.Date.context_today(self.with_user(owner))
        return resolve_relative_dates(options, today, self.company_id)

    def _resolve_recipient_emails(self):
        """Combine user, partner, and free text email lists. Returns a
        deduplicated, comma free list of email addresses."""
        emails = set()
        for user in self.recipient_user_ids:
            if user.email:
                emails.add(user.email.strip())
        for partner in self.recipient_partner_ids:
            if partner.email:
                emails.add(partner.email.strip())
        if self.recipient_emails:
            for raw in self.recipient_emails.split(','):
                stripped = raw.strip()
                if stripped:
                    emails.add(stripped)
        return sorted(e for e in emails if e and '@' in e)

    def _build_attachments(self, options):
        attachments = []
        # SECURITY: the cron runs as root, which bypasses record rules. Render
        # every attachment as the schedule's IMMUTABLE creator (create_uid),
        # never the freely-writable user_id, so the report engine's
        # company-scope clamp (_eh_clamp_company_ids) applies to the person who
        # actually owns this schedule. Binding with_user() to a mutable field
        # would let any writer re-point the owner at a higher-privilege user
        # (base.group_system) or a better-scoped colleague and have the root
        # cron render another company/user's financials under those rights and
        # email them out; create_uid is stamped once by the ORM and cannot be
        # reassigned over RPC or the form.
        owner = self.create_uid or self.env.user
        report = self.report_id.with_user(owner).with_company(self.company_id)
        today_str = fields.Date.context_today(self).isoformat()

        if self.delivery_format in ('xlsx', 'both'):
            xlsx_bytes = report.render_xlsx(options)
            attachments.append({
                'name': "%s_%s.xlsx" % (report.code, today_str),
                'datas': base64.b64encode(xlsx_bytes),
                'mimetype': (
                    'application/vnd.openxmlformats-officedocument'
                    '.spreadsheetml.sheet'
                ),
            })
        if self.delivery_format in ('pdf', 'both'):
            try:
                pdf_bytes = report.render_pdf(options)
                attachments.append({
                    'name': "%s_%s.pdf" % (report.code, today_str),
                    'datas': base64.b64encode(pdf_bytes),
                    'mimetype': 'application/pdf',
                })
            except Exception as exc:
                _logger.warning(
                    "PDF rendering failed for schedule %s: %s",
                    self.id, exc,
                )
                if self.delivery_format == 'pdf':
                    raise
        return attachments

    def _default_body_html(self, options):
        report = self.report_id
        date_block = options.get('date') or {}
        period = ''
        if date_block.get('date_from') and date_block.get('date_to'):
            period = " for the period %s to %s" % (
                date_block['date_from'], date_block['date_to'],
            )
        return (
            "<p>Hello,</p>"
            "<p>Please find attached the latest <strong>%(name)s</strong>%(period)s.</p>"
            "<p>This is an automated delivery from your scheduled report.</p>"
            "<p>Generated by ERP Heritage Accounting.</p>"
        ) % {
            'name': report.name or '',
            'period': period,
        }
