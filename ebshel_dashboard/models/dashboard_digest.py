# -*- coding: utf-8 -*-
"""Dynamic Dashboards - the board in an inbox.

A dashboard nobody opens is a dashboard nobody reads. A board can post
itself: its headline numbers in the body of an e-mail, the whole page
attached as a PDF, every morning, every Monday, or on the first of the
month.

Each recipient who is an Odoo user gets the board computed **as themselves**,
so two people on the same digest can legitimately receive different numbers -
the same rule the screen follows. Addresses that are not users are computed
as the person the digest belongs to, and that is said in the form.

The mail goes out through `ir.mail_server`, which lives in `base`: no
dependency on the mail application for a module that otherwise needs none.
"""
import logging
from datetime import datetime, timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError

from .dashboard_item import PERIODS

_logger = logging.getLogger(__name__)

INTERVALS = [
    ('daily', 'Every day'),
    ('weekly', 'Every week'),
    ('monthly', 'Every month'),
]
WEEKDAYS = [
    ('0', 'Monday'), ('1', 'Tuesday'), ('2', 'Wednesday'), ('3', 'Thursday'),
    ('4', 'Friday'), ('5', 'Saturday'), ('6', 'Sunday'),
]
# The number cards that make a readable e-mail body. More than this and the
# mail becomes the dashboard, which is what the attachment is for.
BODY_CARDS = 8


class DashboardBoardDigest(models.Model):
    _inherit = 'dashboard.board'

    digest_active = fields.Boolean(
        string='Send by E-mail',
        help="Posts this dashboard to its recipients on a schedule.")
    digest_interval = fields.Selection(
        INTERVALS, string='How Often', default='weekly', required=True)
    digest_hour = fields.Integer(
        string='At (UTC)', default=7,
        help="Hour of the day, in server time (UTC), from which the digest may go out.")
    digest_weekday = fields.Selection(WEEKDAYS, string='On', default='0')
    digest_monthday = fields.Integer(
        string='On Day', default=1,
        help="Day of the month, 1 to 28, so every month has one.")
    digest_period = fields.Selection(
        PERIODS, string='Over', default='last_7',
        help="The period the digest is computed over, whatever the dashboard "
             "itself opens with.")
    digest_user_ids = fields.Many2many(
        'res.users', 'dashboard_board_digest_user_rel', 'board_id', 'user_id',
        string='To Users',
        help="Each of them receives the dashboard computed with their own rights.")
    digest_emails = fields.Char(
        string='And To',
        help="Other addresses, comma separated. They get the dashboard as its "
             "owner sees it, since there is no user to compute it for.")
    digest_attach_pdf = fields.Boolean(string='Attach the PDF', default=True)
    digest_last_sent = fields.Datetime(string='Last Sent', readonly=True, copy=False)
    digest_next_send = fields.Char(string='Next', compute='_compute_digest_next_send')

    @api.constrains('digest_hour', 'digest_monthday')
    def _check_digest_schedule(self):
        for board in self:
            if not 0 <= board.digest_hour <= 23:
                raise UserError(self.env._('The digest hour is a number between 0 and 23.'))
            if not 1 <= board.digest_monthday <= 28:
                raise UserError(self.env._(
                    'The digest day is between 1 and 28, so that every month has one.'))

    @api.depends('digest_active', 'digest_interval', 'digest_hour', 'digest_weekday',
                 'digest_monthday', 'digest_last_sent')
    def _compute_digest_next_send(self):
        """Said in words on the form, so nobody has to guess the schedule."""
        for board in self:
            if not board.digest_active:
                board.digest_next_send = ''
                continue
            when = self.env._('%(hour)02d:00 UTC', hour=board.digest_hour)
            if board.digest_interval == 'daily':
                board.digest_next_send = self.env._('Every day at %s', when)
            elif board.digest_interval == 'weekly':
                day = dict(WEEKDAYS).get(board.digest_weekday, 'Monday')
                board.digest_next_send = self.env._(
                    'Every %(day)s at %(when)s', day=self.env._(day), when=when)
            else:
                board.digest_next_send = self.env._(
                    'Day %(day)s of every month at %(when)s',
                    day=board.digest_monthday, when=when)

    # ------------------------------------------------------------------
    # When a digest is due
    # ------------------------------------------------------------------
    def _digest_due(self, now=None):
        """Is this board's digest due right now?

        One send per window: a digest that already went out today does not go
        out again this afternoon, whatever the cron does.
        """
        self.ensure_one()
        if not self.digest_active:
            return False
        now = now or fields.Datetime.now()
        if now.hour < self.digest_hour:
            return False
        if self.digest_interval == 'weekly' and str(now.weekday()) != (self.digest_weekday or '0'):
            return False
        if self.digest_interval == 'monthly' and now.day != (self.digest_monthday or 1):
            return False
        last = self.digest_last_sent
        if not last:
            return True
        if self.digest_interval == 'daily':
            return last.date() < now.date()
        if self.digest_interval == 'weekly':
            return last < now - timedelta(days=6)
        return last < now - timedelta(days=27)

    @api.model
    def _cron_send_digests(self):
        """Hourly: post every board whose turn it is."""
        boards = self.sudo().search([('digest_active', '=', True)])
        now = fields.Datetime.now()
        for board in boards:
            if not board._digest_due(now):
                continue
            # A savepoint per board: one that cannot be built leaves the
            # others alone, and no half-written state behind it.
            try:
                with self.env.cr.savepoint():
                    board._send_digest()
                    board.digest_last_sent = now
            except Exception:  # noqa: BLE001 - one board must not stop the rest
                _logger.exception('Dashboard digest failed for board %s', board.id)

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------
    def action_send_digest(self):
        """The "Send now" button: the same mail the schedule would post."""
        self.ensure_one()
        sent = self._send_digest()
        self.digest_last_sent = fields.Datetime.now()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'success',
                'message': self.env._('Digest sent to %s recipients.', sent),
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }

    def _send_digest(self):
        """Post this board to everyone on its list. Returns how many went out."""
        self.ensure_one()
        users = self.digest_user_ids
        extras = [address.strip() for address in (self.digest_emails or '').split(',')
                  if address.strip()]
        if not users and not extras:
            raise UserError(self.env._(
                'Add at least one user or one address before sending this digest.'))
        sent = 0
        for user in users:
            board = self.with_user(user).with_context(lang=user.lang or self.env.lang)
            try:
                board.check_access('read')
                if not board._is_visible(board._user_groups()):
                    raise UserError(self.env._('Not shared with this user.'))
            except Exception:  # noqa: BLE001 - a reader who lost access is skipped
                _logger.info('Dashboard digest: %s may no longer read board %s',
                             user.login, self.id)
                continue
            if user.email and board._post_digest([user.email]):
                sent += 1
        # Addresses that are not users see the board as its owner does.
        if extras:
            owner = self.owner_id or self.create_uid
            board = self.with_user(owner) if owner and owner != self.env.user else self
            if board.sudo(False)._post_digest(extras):
                sent += len(extras)
        return sent

    def _post_digest(self, addresses):
        """One mail, to one or more addresses, from this recordset's own rights."""
        self.ensure_one()
        subject = self.env._('%(board)s · %(period)s', board=self.name,
                             period=dict(PERIODS).get(self.digest_period, ''))
        body = self._digest_body()
        attachments = []
        if self.digest_attach_pdf:
            try:
                attachments = [self._build_pdf(period=self.digest_period)]
            except UserError as err:
                _logger.info('Dashboard digest: no PDF for board %s (%s)', self.id, err)
        Server = self.env['ir.mail_server'].sudo()
        attachments = [(name, content, 'application/pdf') for name, content in attachments]
        # 19 renamed the builder; the signature is the same on both.
        build = getattr(Server, '_build_email__', None) or Server.build_email
        message = build(
            email_from=Server._get_default_from_address() or self.env.user.email_formatted,
            email_to=addresses,
            subject=subject,
            body=body,
            subtype='html',
            attachments=attachments,
        )
        Server.send_email(message)
        return True

    def _digest_body(self):
        """The headline numbers as a small table, then a link to the board.

        Inline styles only: an e-mail client throws away a stylesheet.
        """
        self.ensure_one()
        data = self.read_board(self.id, period=self.digest_period)
        rows = []
        for item in data['items']:
            if item.get('hidden') or item.get('error') or item['kind'] not in (
                    'kpi', 'gauge', 'status', 'bullet', 'formula'):
                continue
            value = ('%s%%' % item['ratio']) if item.get('as_ratio') and item.get('ratio') is not None \
                else '%s%s%s' % (item.get('prefix') or '',
                                 self._digest_number(item),
                                 item.get('symbol') or '')
            delta = ''
            if item.get('delta_percent') is not None:
                arrow = '&#9650;' if item['delta_percent'] >= 0 else '&#9660;'
                colour = '#059669' if item['delta_percent'] >= 0 else '#dc2626'
                delta = ('<span style="color:%s;font-size:12px;">%s %s%%</span>'
                         % (colour, arrow, abs(item['delta_percent'])))
            rows.append(
                '<tr>'
                '<td style="padding:6px 12px 6px 0;color:#374151;font-size:13px;">%s</td>'
                '<td style="padding:6px 0;text-align:right;font-size:16px;'
                'font-weight:700;color:#111827;">%s</td>'
                '<td style="padding:6px 0 6px 10px;text-align:right;">%s</td>'
                '</tr>' % (item['name'], value, delta))
            if len(rows) >= BODY_CARDS:
                break
        table = ('<table style="border-collapse:collapse;margin:12px 0;">%s</table>'
                 % ''.join(rows)) if rows else ''
        link = '%s/odoo/action-ebshel_dashboard.action_dashboard_open' % (
            self.env['ir.config_parameter'].sudo().get_param('web.base.url') or '')
        counts = self.env._('%(cards)s cards · %(period)s',
                            cards=len(data['items']),
                            period=dict(PERIODS).get(self.digest_period, ''))
        return (
            '<div style="font-family:Arial,Helvetica,sans-serif;color:#111827;">'
            '<h2 style="margin:0 0 2px;font-size:18px;">%(name)s</h2>'
            '<div style="color:#6b7280;font-size:12px;">%(counts)s</div>'
            '%(table)s'
            '<div style="font-size:13px;color:#374151;">%(attached)s</div>'
            '<p style="margin:14px 0 0;"><a href="%(link)s" '
            'style="background:#4f46e5;color:#ffffff;text-decoration:none;'
            'padding:8px 14px;border-radius:4px;font-size:13px;">%(open)s</a></p>'
            '</div>'
        ) % {
            'name': self.name,
            'counts': counts,
            'table': table,
            'attached': self.env._('The whole dashboard is attached as a PDF.')
                        if self.digest_attach_pdf else '',
            'link': link,
            'open': self.env._('Open the dashboard'),
        }

    def _digest_number(self, item):
        digits = 0 if item.get('aggregate') == 'count' and item.get('kind') != 'formula' \
            else int(item.get('digits') or 0)
        try:
            return '{:,.{d}f}'.format(float(item.get('value') or 0), d=digits)
        except (TypeError, ValueError):
            return str(item.get('value'))
