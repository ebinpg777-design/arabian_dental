# -*- coding: utf-8 -*-
import logging
import re
from datetime import date, datetime, timedelta

import pytz
from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import formatLang, html2plaintext, safe_eval

_logger = logging.getLogger(__name__)

# {{field}} or {{field.sub.path}} — the form a non-developer can be taught in a sentence.
# {{path}} or {{path|format}}: the field, and how it should read.
PLACEHOLDER = re.compile(r'\{\{\s*([a-zA-Z0-9_.]+)\s*(?:\|\s*([a-zA-Z_]+)\s*)?\}\}')
FORMATS = ('date', 'datetime', 'time', 'money', 'number', 'int', 'raw', 'upper', 'lower',
           'title', 'first', 'doctor')


class EpgWhatsappTemplate(models.Model):
    """A message somebody wrote, with the record's own values dropped into it.

    Placeholders are dotted field paths rather than Meta's positional {{1}} {{2}}: the
    person writing the template is a manager, not a developer, and a body that reads
    "Dear Dr. {{partner_id.name}}" can be checked by eye against the record. Positional
    variables can only be verified by counting.
    """
    _name = 'epg.whatsapp.template'
    _description = 'WhatsApp Template'
    _order = 'sequence, model_id, name'

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    account_id = fields.Many2one('epg.whatsapp.account', string='Send From')
    model_id = fields.Many2one('ir.model', string='Applies To', required=True,
                               ondelete='cascade')
    model = fields.Char(related='model_id.model', store=True, readonly=True)

    phone_field = fields.Char(
        'Phone Field', default='partner_id.phone',
        help="Where to find the number on the record, as a dotted path.")

    # WRITTEN ONCE, IN AS MANY LANGUAGES AS THE LAB WANTS.
    #
    # Odoo's own field translation: the Malayalam version of a message lives on the
    # same template, and the doctor is written to in the language their contact
    # carries. Nothing changes for a lab that only writes English. (client, 2026-09-18)
    body = fields.Text(
        'Message', required=True,
        help="Use {{field}} or {{partner_id.name}} to drop in the record's values.", translate=True)
    header_text = fields.Char('Header', translate=True)
    # --- the second and third time the same template goes to the same record: a
    # reminder that escalates, a follow-up that changes tone. (client, 2026-09-17)
    body_2 = fields.Text(
        'Second Time', translate=True,
        help="Sent instead of the message when this template goes to "
             "the same record a second time. Empty: the message again.")
    body_3 = fields.Text(
        'Third Time and After', translate=True,
        help="From the third time on. Empty: the second-time text, or the message.")
    buttons = fields.Text(
        'Link Buttons',
        help="One per line, as  Label | https://…  - the link may hold placeholders, "
             "e.g. Track order | https://lab.example/track/{{name}}. Each becomes a "
             "tappable link, and a button on the message page.")
    page_label = fields.Char(
        'The Link Says', translate=True,
        help="What the one link reads as when the buttons are on a "
                              "page: \"📄 Open invoice & pay\". Empty: \"Tap here to open\".")
    button_style = fields.Selection(
        [('links', 'Every link in the message itself'),
         ('page', 'One link, to a page with all the buttons')],
        string='Buttons As', default='links', required=True,
        help="A page keeps a message with a document, a payment and three answers "
             "down to one link - the buttons are real buttons there.")
    filter_domain = fields.Text(
        'Send Only When', default='[]',
        help="A rule on the record; the template is skipped when it does not hold. "
             "E.g. [('amount_total', '>', 0)]")
    delay_hours = fields.Float(
        'Wait Before Sending (hours)', default=0,
        help="An automatic message waits this long after its event: a feedback "
             "request the day after delivery, not the minute it is scanned.")
    quick_replies = fields.Text(
        'Quick Replies',
        help="One answer per line, e.g. '✅ Received'. Sent through WhatsApp app / "
             "Web, each becomes a link under the message: the doctor taps one and the "
             "answer lands on the record's chatter - a reply without the API.", translate=True)
    footer_text = fields.Char('Footer', translate=True)
    report_id = fields.Many2one(
        'ir.actions.report', string='Attach Report',
        domain="[('model', '=', model)]",
        help="Rendered as a PDF and attached to the message.")

    # --- the approved counterpart, used once the free-form window has closed
    meta_template_name = fields.Char(
        'Approved Template Name',
        help="The template's name as approved in Meta's WhatsApp Manager. Outside the "
             "24-hour reply window WhatsApp accepts nothing else, so a template without "
             "this can only reach someone who messaged us today.")
    meta_variables = fields.Char(
        'Template Variables',
        help="Comma-separated field paths filling the approved template's {{1}}, {{2}} "
             "… placeholders, in order. E.g. partner_id.name, name, amount_total.")
    meta_status = fields.Selection(
        [('unknown', 'Not checked'), ('approved', 'Approved'),
         ('pending', 'In review'), ('rejected', 'Rejected')],
        default='unknown', readonly=True, copy=False,
        help="Approval state last reported by Meta.")

    # The order a person meets them in - the gallery, and the list of questions
    # the lab can put to a doctor. (client, 2026-09-18)
    sequence = fields.Integer(default=10)
    company_id = fields.Many2one('res.company', default=lambda s: s.env.company)
    channel = fields.Selection(related='account_id.channel')
    has_replies = fields.Boolean(compute='_compute_has')
    has_buttons = fields.Boolean(compute='_compute_has')

    @api.depends('quick_replies', 'buttons')
    def _compute_has(self):
        for template in self:
            template.has_replies = bool((template.quick_replies or '').strip())
            template.has_buttons = bool((template.buttons or '').strip())

    # --- what the author sees while writing (client, 2026-08-28)
    # A placeholder that resolves to nothing is the failure mode of this whole
    # feature: the message still goes, with a hole in it. These say so before it is
    # saved, with the field named, rather than after a doctor has read it.
    placeholder_warnings = fields.Text(compute='_compute_placeholder_check')
    placeholder_ok = fields.Boolean(compute='_compute_placeholder_check',
                                    search='_search_placeholder_ok')
    body_length = fields.Integer(compute='_compute_body_length',
                                 help="Meta allows 1024 characters in a template body.")

    # A real record to preview against. Chips show WHICH field will land; this
    # shows WHAT lands - both are needed, at different moments.
    sample_res_id = fields.Many2oneReference(
        'Preview With', model_field='model',
        help="Any record of the model above. The message is rendered against it "
             "exactly as it would be sent, and a test can be sent to your own phone.")
    sample_body = fields.Text(compute='_compute_sample')
    sample_number = fields.Char(compute='_compute_sample')
    sample_number_ok = fields.Boolean(compute='_compute_sample')
    sample_variables = fields.Char(
        compute='_compute_sample',
        help="The values the approved template's {{1}}, {{2}}... would receive.")

    # How this template has performed, from the messages it produced.
    message_count = fields.Integer(compute='_compute_stats')
    read_count = fields.Integer(compute='_compute_stats')
    failed_count = fields.Integer(compute='_compute_stats')
    read_rate = fields.Float(compute='_compute_stats', help="Read, as a share of sent.")
    # How this message is doing, in the three numbers that say it: is it read, does
    # it get an answer, and how long the doctor takes to open it. (client, 2026-09-18)
    reply_count = fields.Integer(compute='_compute_health')
    reply_rate = fields.Float(compute='_compute_health', help="Answered, as a share of sent.")
    open_minutes = fields.Integer(compute='_compute_health',
                                  help="How long the doctor takes to open it, on average.")
    answers_tapped = fields.Char(compute='_compute_health')
    health_note = fields.Char(compute='_compute_health')

    # What Meta last said about the approved counterpart.
    meta_category = fields.Char('Meta Category', readonly=True, copy=False)
    meta_language = fields.Char('Meta Language', readonly=True, copy=False)
    meta_reason = fields.Char('Meta Reason', readonly=True, copy=False,
                              help="Why it was rejected or paused, in Meta's words.")
    meta_last_synced = fields.Datetime('Last Checked with Meta', readonly=True, copy=False)

    META_STATUS = {
        'APPROVED': 'approved', 'PENDING': 'pending', 'IN_APPEAL': 'pending',
        'FLAGGED': 'pending', 'REJECTED': 'rejected', 'DISABLED': 'rejected',
        'PAUSED': 'rejected',
    }

    # ------------------------------------------------------------------ checks
    @api.model
    def _check_path(self, model_name, path):
        """Does this dotted path exist on the model? Returns (ok, problem).

        Walks field by field through relations, the way _resolve() will at send
        time - so what passes here resolves there.
        """
        if not model_name or model_name not in self.env:
            return False, _("no model")
        Model = self.env[model_name]
        parts = [part for part in (path or '').split('.') if part]
        if not parts:
            return False, _("empty placeholder")
        for index, part in enumerate(parts):
            field = Model._fields.get(part)
            if field is None:
                if hasattr(Model, part):
                    return True, ''          # a property or method: resolvable
                return False, _("'%(part)s' is not a field of %(model)s",
                                part=part, model=Model._description or model_name)
            if index < len(parts) - 1:
                if not field.relational:
                    return False, _("'%(part)s' is a plain %(type)s field - it has "
                                    "no '%(rest)s' inside it",
                                    part=part, type=field.type,
                                    rest='.'.join(parts[index + 1:]))
                Model = self.env[field.comodel_name]
        return True, ''

    @api.depends('body', 'body_2', 'body_3', 'buttons', 'header_text', 'footer_text',
                 'phone_field', 'meta_variables', 'model')
    def _compute_placeholder_check(self):
        for template in self:
            problems = []
            texts = ((_('message'), template.body), (_('header'), template.header_text),
                     (_('footer'), template.footer_text),
                     (_('second-time text'), template.body_2),
                     (_('third-time text'), template.body_3),
                     (_('buttons'), template.buttons))
            seen = set()
            for where, text in texts:
                for path, fmt in PLACEHOLDER.findall(text or ''):
                    if fmt and fmt not in FORMATS:
                        problems.append(_("{{%(path)s|%(fmt)s}} in the %(where)s: '%(fmt)s' "
                                          "is not a format (use one of %(known)s)",
                                          path=path, fmt=fmt, where=where,
                                          known=', '.join(FORMATS)))
                    if path in seen:
                        continue
                    seen.add(path)
                    ok, why = template._check_path(template.model, path)
                    if not ok:
                        problems.append(_("{{%(path)s}} in the %(where)s: %(why)s",
                                          path=path, where=where, why=why))
            ok, why = template._check_path(template.model,
                                           template.phone_field or 'partner_id.phone')
            if not ok:
                problems.append(_("phone field '%(path)s': %(why)s",
                                  path=template.phone_field, why=why))
            for path in [p.strip() for p in (template.meta_variables or '').split(',')
                         if p.strip()]:
                ok, why = template._check_path(template.model, path)
                if not ok:
                    problems.append(_("template variable '%(path)s': %(why)s",
                                      path=path, why=why))
            template.placeholder_warnings = '\n'.join(problems)
            template.placeholder_ok = not problems

    def _search_placeholder_ok(self, operator, value):
        """A computed flag made filterable: templates are few, so checking every
        one is cheaper than storing a flag that would go stale when a field is
        renamed on the model."""
        if operator not in ('=', '!=', 'in', 'not in'):
            return [('id', 'in', [])]
        wanted = value if isinstance(value, bool) else bool(value)
        if operator in ('in', 'not in'):
            wanted = True in (value or [])
        if operator in ('!=', 'not in'):
            wanted = not wanted
        matching = self.with_context(active_test=False).search([]).filtered(
            lambda t: t.placeholder_ok == wanted)
        return [('id', 'in', matching.ids)]

    @api.depends('body')
    def _compute_body_length(self):
        for template in self:
            template.body_length = len(template.body or '')

    # ------------------------------------------------------------------ sample
    def _sample_record(self):
        self.ensure_one()
        if not (self.sample_res_id and self.model and self.model in self.env):
            return None
        return self.env[self.model].browse(self.sample_res_id).exists() or None

    @api.depends('sample_res_id', 'body', 'phone_field', 'meta_variables', 'model')
    def _compute_sample(self):
        for template in self:
            record = template._sample_record()
            if record is None:
                template.sample_body = False
                template.sample_number = False
                template.sample_number_ok = False
                template.sample_variables = False
                continue
            template.sample_body = template.render(record)
            number = template.phone_for(record)
            template.sample_number = number
            # E.164 without the plus: a country code and a national number make at
            # least ten digits, and Meta silently rejects anything shorter.
            template.sample_number_ok = bool(number) and 10 <= len(number) <= 15
            paths = [p.strip() for p in (template.meta_variables or '').split(',')
                     if p.strip()]
            template.sample_variables = ' | '.join(
                '{{%d}} = %s' % (i + 1, template._resolve(record, path) or '—')
                for i, path in enumerate(paths)) if paths else False

    @api.model
    def available_paths(self, model_name):
        """What the author may drop in: the model's own fields, and one level into
        each relation. For the picker beside the message."""
        if not model_name or model_name not in self.env:
            return []
        SKIP = {'binary', 'html', 'one2many', 'many2many', 'json', 'properties',
                'properties_definition'}
        out = []

        def add(prefix, Model, label_prefix, deep):
            for name, field in sorted(Model._fields.items()):
                if field.type in SKIP or name.startswith('_') or not field.string:
                    continue
                if name in ('write_uid', 'create_uid', 'write_date', 'create_date',
                            'id', 'message_ids', 'activity_ids'):
                    continue
                path = prefix + name
                label = (label_prefix + ' › ' if label_prefix else '') + field.string
                out.append({'path': path, 'label': label, 'type': field.type})
                if deep and field.type == 'many2one' and field.comodel_name in self.env:
                    add(path + '.', self.env[field.comodel_name], field.string, False)

        add('', self.env[model_name], '', True)
        # The record's own fields first, then one step into each relation; within
        # each, by label - the order a person scans, not the order of the schema.
        out.sort(key=lambda row: ('.' in row['path'], row['label'].lower()))
        return out

    # ------------------------------------------------------------------ stats
    def _compute_stats(self):
        Message = self.env['epg.whatsapp.message'].sudo()
        counts = {}
        for template, state, count in Message._read_group(
                [('template_id', 'in', self.ids), ('direction', '=', 'outbound')],
                ['template_id', 'state'], ['__count']):
            counts[(template.id, state)] = count
        for template in self:
            by = lambda *states: sum(counts.get((template.id, s), 0) for s in states)  # noqa: E731
            sent = by('sent', 'delivered', 'read', 'simulated')
            template.message_count = by('draft', 'ready', 'opened', 'sent', 'delivered',
                                        'read', 'simulated', 'error', 'cancel')
            template.read_count = by('read')
            template.failed_count = by('error')
            template.read_rate = round(template.read_count / sent * 100, 1) if sent else 0.0

    lang_summary = fields.Char(compute='_compute_langs')
    lang_count = fields.Integer(compute='_compute_langs')

    def _compute_langs(self):
        """The languages a version of this message has actually been written in.

        Odoo keeps a translated field's values in one jsonb column, so this is a
        read of the record, not a query per language. (client, 2026-09-18)
        """
        installed = self.env['res.lang'].get_installed()
        names = dict(installed)
        # The context carries no language in a test or a cron; the user's own then.
        current = self.env.lang or self.env.user.lang or 'en_US'
        for template in self:
            written = []
            for code, name in installed:
                text = template.with_context(lang=code).body or ''
                if code == current or (text.strip() and text != (template.body or '')):
                    written.append(name)
            # The language the record was written in first is always one of them.
            if not written:
                written = [names.get(current) or current]
            template.lang_summary = ', '.join(dict.fromkeys(written))
            template.lang_count = len(installed)

    def _compute_health(self):
        Message = self.env['epg.whatsapp.message'].sudo()
        sent_states = ('sent', 'delivered', 'read', 'simulated')
        sent = {t.id: n for t, n in Message._read_group(
            [('template_id', 'in', self.ids), ('direction', '=', 'outbound'),
             ('state', 'in', sent_states)], ['template_id'], ['__count'])}
        replies = {t.id: n for t, n in Message._read_group(
            [('reply_to_id.template_id', 'in', self.ids), ('direction', '=', 'inbound')],
            ['reply_to_id.template_id'], ['__count'])}
        # How long they take to open it: from the moment it went, to the first sign.
        opened = {}
        for template, count in Message._read_group(
                [('template_id', 'in', self.ids), ('seen_at', '!=', False),
                 ('sent_at', '!=', False)], ['template_id'], ['__count']):
            opened[template.id] = count
        minutes = {}
        if opened:
            self.env.cr.execute("""
                SELECT template_id,
                       AVG(EXTRACT(EPOCH FROM (seen_at - sent_at)) / 60.0)::int AS mins
                  FROM epg_whatsapp_message
                 WHERE template_id = ANY(%s) AND seen_at IS NOT NULL AND sent_at IS NOT NULL
                   AND seen_at >= sent_at
              GROUP BY template_id
            """, (list(opened),))
            minutes = dict(self.env.cr.fetchall())
        # Which answer they actually tap, most first.
        tapped = {}
        for template, body, count in Message._read_group(
                [('reply_to_id.template_id', 'in', self.ids), ('direction', '=', 'inbound'),
                 ('body', '!=', False)],
                ['reply_to_id.template_id', 'body'], ['__count']):
            tapped.setdefault(template.id, []).append((count, body))
        for template in self:
            out = sent.get(template.id, 0)
            answered = replies.get(template.id, 0)
            template.reply_count = answered
            template.reply_rate = round(answered / out * 100, 1) if out else 0.0
            template.open_minutes = minutes.get(template.id, 0)
            top = sorted(tapped.get(template.id, []), reverse=True)[:3]
            template.answers_tapped = ' · '.join(
                '%s (%s)' % ((body or '').strip()[:28], count) for count, body in top)
            template.health_note = template._health_note(out)

    def _health_note(self, sent):
        """The one sentence worth saying about these numbers."""
        self.ensure_one()
        if sent < 5:
            return _("Too few sent yet to tell.")
        if self.read_rate < 30:
            return _("Rarely opened - the first line is what a doctor decides on.")
        if self.quick_replies and self.reply_rate < 5:
            return _("Read, but hardly ever answered - are the answers worth tapping?")
        if self.open_minutes and self.open_minutes > 60 * 12:
            return _("Opened late - try sending it at the doctor's usual hour.")
        return _("Doing well.")

    # ------------------------------------------------------------------ actions
    def action_send_test(self):
        """The rendered message, to the person configuring it.

        Rendered against the sample record and sent to the current user's own
        phone - the one way to see exactly what a doctor will receive without a
        doctor receiving it. Respects the sender's simulation mode.
        """
        self.ensure_one()
        record = self._sample_record()
        if record is None:
            raise UserError(_("Pick a record under 'Preview With' first - the test "
                              "is rendered against it."))
        me = self.env.user.partner_id
        # Odoo 19 keeps one number on a contact; older builds had 'mobile' too.
        number = self._normalise_number(getattr(me, 'mobile', False) or me.phone, me)
        if not number:
            raise UserError(_("Your own contact has no phone number to send the test "
                              "to. Add one under your user's contact."))
        account = self.account_id or self.env['epg.whatsapp.account']._default_account()
        if not account:
            raise UserError(_("No WhatsApp sender is configured."))
        message = self.env['epg.whatsapp.message'].create({
            'account_id': account.id,
            'template_id': self.id,
            'res_model': record._name,
            'res_id': record.id,
            'partner_id': me.id,
            'number': number,
            'body': _("[TEST] ") + self.render(record),
        })
        message._attach_report(record)
        message.with_context(wa_test_send=True).action_send()
        if message.channel == 'link' and message.state == 'ready':
            # Free channel: open it in the author's own WhatsApp, to their own number.
            message.user_id = self.env.user
            return {'type': 'ir.actions.client', 'tag': 'epg_whatsapp_send',
                    'params': {'message_id': message.id}}
        # 'cancel' too: a test refused for consent (your own number opted out or
        # blacklisted) must not be announced as "Test sent".
        if message.state in ('error', 'cancel'):
            hint = ''
            if 'window' in (message.error or ''):
                hint = ' ' + _("WhatsApp only delivers free text to a number that "
                               "messaged the business in the last 24 hours: send any "
                               "message to %(sender)s from your phone first, or name "
                               "an approved template under 'Approved template'.",
                               sender=account.name)
            raise UserError(_("The test could not be sent: %s", message.error) + hint)
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {
                'type': 'success' if message.state != 'simulated' else 'info',
                'title': _("Test sent") if message.state != 'simulated'
                else _("Test simulated"),
                'message': _("To %(number)s via %(sender)s - open Messages to see it.",
                             number=number, sender=account.name),
                'sticky': False,
            },
        }

    def action_sync_meta(self):
        """Ask Meta what it thinks of the approved counterparts."""
        accounts = self.mapped('account_id') or \
            self.env['epg.whatsapp.account']._default_account()
        if not accounts:
            raise UserError(_("No WhatsApp sender is configured."))
        matched, unknown, fetched = 0, [], 0
        for account in accounts:
            result = account.sync_templates()
            matched += result['matched']
            unknown += result['unknown']
            fetched += result['fetched']
        missing = [t.meta_template_name for t in self
                   if t.meta_template_name and t.meta_status == 'unknown']
        text = _("%(fetched)s template(s) on Meta, %(matched)s matched here.",
                 fetched=fetched, matched=matched)
        if missing:
            text += ' ' + _("Not found on Meta: %s.", ', '.join(missing))
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {'type': 'warning' if missing else 'success',
                       'title': _("Checked with Meta"), 'message': text,
                       'sticky': bool(missing)},
        }

    @api.model
    def _apply_meta_status(self, name, language, event, category='', reason=''):
        """Record what Meta said about an approved template, by its name.

        Used by the sync and by the webhook alike. Language is matched when both
        sides state one; a template here that names no language takes the update.
        """
        if not name:
            return self.browse()
        status = self.META_STATUS.get((event or '').upper(), 'unknown')
        templates = self.sudo().search([('meta_template_name', '=', name)])
        if language:
            templates = templates.filtered(
                lambda t: not t.meta_language or t.meta_language == language
                or (t.account_id.template_language or '') in ('', language))
        templates.write({
            'meta_status': status,
            'meta_category': category or False,
            'meta_language': language or False,
            'meta_reason': reason or False,
            'meta_last_synced': fields.Datetime.now(),
        })
        return templates

    def action_view_messages(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': _("Messages: %s", self.name),
            'res_model': 'epg.whatsapp.message', 'view_mode': 'list,form',
            'domain': [('template_id', '=', self.id)],
            'context': {'search_default_group_state': 1},
        }

    def _meta_variables(self, message):
        """Positional values for the approved template's placeholders.

        Resolved against the record the message came from, using the same forgiving
        path walker as the free-form body: a bad path yields an empty string rather
        than taking down whatever triggered the notification.
        """
        self.ensure_one()
        paths = [p.strip() for p in (self.meta_variables or '').split(',') if p.strip()]
        if not paths:
            return []
        record = None
        if message.res_model and message.res_id and message.res_model in self.env:
            record = self.env[message.res_model].browse(message.res_id).exists()
        if not record:
            record = message.partner_id
        return [self._resolve(record, p) for p in paths]

    # ------------------------------------------------------------------ rendering
    hide_empty_lines = fields.Boolean(
        'Drop Lines With Nothing In Them', default=True,
        help="A line whose placeholders all come out empty is left out - no "
             "\"🔖 Consignment\" with nothing after it when the courier gave no "
             "number. A line with words of its own always stays.")

    def render(self, record, stage=1):
        """The finished text for one record - the second- or third-time text when
        this template has been to the record before and one is written."""
        self.ensure_one()
        body = self.body
        if stage >= 3 and self.body_3:
            body = self.body_3
        elif stage >= 2 and (self.body_2 or self.body_3):
            body = self.body_2 or self.body_3
        return self._render_text(record, body, drop_empty=self.hide_empty_lines)

    def _matches(self, record):
        """Does the Send Only When rule hold for this record? A rule that cannot be
        read lets the message through and says so in the log: a typo in a filter
        must not silence every invoice."""
        self.ensure_one()
        text = (self.filter_domain or '').strip()
        if not text or text == '[]':
            return True
        try:
            domain = safe_eval.safe_eval(text, {
                'datetime': safe_eval.datetime, 'dateutil': safe_eval.dateutil,
                'time': safe_eval.time, 'relativedelta': relativedelta,
                'context_today': lambda: fields.Date.context_today(self), 'uid': self.env.uid})
            return bool(record.sudo().filtered_domain(list(domain)))
        except Exception as exc:                                       # noqa: BLE001
            _logger.warning("WhatsApp %s: Send Only When cannot be read (%s), sending",
                            self.name, exc)
            return True

    @api.model
    def _field_value(self, record, path):
        """The value itself at a dotted path - a float as a float, a date as a date.

        `_resolve` is for the message: it hands back "₹19,000.00" and "27 Sep 2026",
        which is right in a sentence and useless to arithmetic. Anything that has to
        compute with a field (the UPI amount) reads it here. (production, 2026-09-18)
        """
        value = record
        for part in (path or '').split('.'):
            if value is None:
                return None
            try:
                value = value[part] if part in getattr(value, '_fields', {}) \
                    else getattr(value, part, None)
            except Exception:                                          # noqa: BLE001
                return None
        return value

    def _resolve(self, record, path, fmt=None):
        """Walk a dotted path, and never explode on the way.

        A template that raises takes down whatever triggered it — an invoice confirm, a
        picking validation. A placeholder that cannot be resolved leaves an empty string
        and the message still goes; the alternative is a business action failing because
        somebody mistyped a field name.

        The value comes back as it should read on a phone: a date as words, money
        with its currency, a choice by its label - or as ``fmt`` asks (``|date``,
        ``|money``, ``|doctor``…). (client, 2026-09-18)
        """
        value, field, holder = record, None, None
        for part in path.split('.'):
            if value is None:
                return ''
            try:
                fields_ = getattr(value, '_fields', {})
                if part in fields_:
                    holder, field = value, fields_[part]
                    value = value[part]
                else:
                    holder, field = None, None
                    value = getattr(value, part, None)
            except Exception:
                return ''
        try:
            return self._format_value(value, field, holder, fmt)
        except Exception:                                              # noqa: BLE001
            return '' if value in (None, False) else str(value)

    def _tz(self):
        name = (self.env.context.get('tz') or self.env.user.tz
                or self.env.company.partner_id.tz or 'Asia/Kolkata')
        try:
            return pytz.timezone(name)
        except pytz.UnknownTimeZoneError:
            return pytz.utc

    def _money(self, value, currency):
        """₹19,000.00 - the symbol first, as a message reads it, whatever position the
        currency is configured with for printed documents; a lettered symbol
        (USD, CHF) keeps a space."""
        number = formatLang(self.env, value, digits=currency.decimal_places
                            if currency else 2)
        symbol = (currency.symbol or currency.name or '') if currency else ''
        if not symbol:
            return number
        return '%s%s%s' % (symbol, ' ' if symbol[-1].isalpha() else '', number)

    def _format_value(self, value, field=None, holder=None, fmt=None):
        """One value the way a message should read it."""
        if value is None or value is False:
            return ''
        if fmt == 'raw':
            return str(value)
        if hasattr(value, '_name'):                                     # a recordset
            value = ', '.join(v.display_name or '' for v in value)
        if isinstance(value, datetime):
            local = (pytz.utc.localize(value) if value.tzinfo is None else value) \
                .astimezone(self._tz())
            day = local.strftime('%d %b %Y')
            clock = local.strftime('%I:%M %p').lstrip('0')
            return day if fmt == 'date' else clock if fmt == 'time' else '%s, %s' % (day, clock)
        if isinstance(value, date):
            return value.strftime('%d %b %Y')
        if isinstance(value, bool):
            return _('Yes')
        if isinstance(value, (int, float)):
            if fmt == 'int':
                return str(int(round(value)))
            monetary = field is not None and field.type == 'monetary'
            if fmt == 'money' or monetary:
                currency = self.env.company.currency_id
                if monetary and holder is not None and field.currency_field in holder._fields \
                        and holder[field.currency_field]:
                    currency = holder[field.currency_field]
                return self._money(value, currency)
            if isinstance(value, float) or fmt == 'number':
                return formatLang(self.env, value, digits=2)
            return str(value)
        if field is not None and field.type == 'selection' and holder is not None:
            value = field.convert_to_export(value, holder) or value
        elif field is not None and field.type == 'html':
            value = html2plaintext(str(value))
        text = str(value).strip()
        if fmt == 'upper':
            return text.upper()
        if fmt == 'lower':
            return text.lower()
        if fmt == 'title':
            return text.title()
        if fmt == 'first':
            return text.split()[0] if text.split() else ''
        if fmt == 'doctor':
            # "Dr." once: the lab's contacts often carry it in the name already, and
            # a clinic is not a doctor.
            clinic = holder is not None and holder._name == 'res.partner' and \
                getattr(holder, 'is_company', False)
            if not text or clinic or re.match(r'(?i)^dr\b', text):
                return text
            return 'Dr. %s' % text
        return text

    def phone_for(self, record):
        raw = self._resolve(record, self.phone_field or 'partner_id.phone')
        return self._normalise_number(raw, record)

    @api.model
    def _normalise_number(self, raw, record=None):
        """Digits with a country code, which is all Meta accepts.

        A ten-digit local number is silently rejected by the API rather than reported as
        wrong, so the country code is filled in from the partner, then the company.
        """
        if not raw:
            return ''
        digits = ''.join(c for c in str(raw) if c.isdigit())
        if not digits:
            return ''
        if str(raw).strip().startswith('+'):
            return digits
        if digits.startswith('00'):
            return digits[2:]
        country = self.env.company.country_id
        partner = getattr(record, 'partner_id', None) if record else None
        if partner is not None and getattr(partner, 'country_id', False):
            country = partner.country_id
        code = str(country.phone_code or '') if country else ''
        if code and digits.startswith(code):
            return digits
        return '%s%s' % (code, digits.lstrip('0'))

    # ------------------------------------------------------------------ sending
    @api.model
    def _lang_of(self, record, partner=None):
        """The language to write in: the contact's own, if they have one."""
        if partner is None and record is not None:
            partner = record if record._name == 'res.partner' else \
                getattr(record, 'partner_id', None)
        lang = partner and partner.lang
        return lang or self.env.lang or self.env.user.lang or 'en_US'

    def _for(self, record, partner=None):
        """This template as the doctor reads it - their language, where a version
        of the message exists in it. (client, 2026-09-18)"""
        return self.with_context(lang=self._lang_of(record, partner))

    def _stages(self, records):
        """How many times this template has already gone to each record, plus one:
        the send that is about to happen. Only counted when there is a second- or
        third-time text to choose."""
        self.ensure_one()
        if not (self.body_2 or self.body_3) or not records:
            return {}
        counts = {res_id: n for res_id, n in self.env['epg.whatsapp.message'].sudo()._read_group(
            [('template_id', '=', self.id), ('res_model', '=', records._name),
             ('res_id', 'in', records.ids), ('direction', '=', 'outbound'),
             ('state', '!=', 'cancel')], ['res_id'], ['__count'])}
        return {res_id: n + 1 for res_id, n in counts.items()}

    def send(self, records, account=None, defer=False, now=False):
        """Queue one message per record and send it.

        ``defer=True`` only queues: no PDF is rendered and no HTTP call is made in the
        caller's transaction. The queue cron renders the report and sends, and it is
        triggered right away, so the message still leaves within seconds — but a user
        clicking Validate / Confirm never waits on wkhtmltopdf or on Meta's API
        (2 x 20 s timeouts when the network is bad).
        """
        self.ensure_one()
        Message = self.env['epg.whatsapp.message']
        account = account or self.account_id or \
            self.env['epg.whatsapp.account']._default_account()
        sent = Message
        # ``now``: a person pressed the button - no template delay applies.
        wait = (fields.Datetime.now() + timedelta(hours=self.delay_hours)
                if self.delay_hours and self.delay_hours > 0 and not now else False)
        stages = self._stages(records)
        for record in records:
            if not self._matches(record):
                _logger.info("WhatsApp %s: %s does not meet Send Only When, not sent",
                             self.name, record.display_name)
                continue
            number = self.phone_for(record)
            if not number:
                # Nothing to send to. The number is required on a message, so the
                # create would raise and, for an event, be swallowed as a warning.
                _logger.info("WhatsApp %s: no number on %s (%s), not sent",
                             self.name, record.display_name, self.phone_field)
                continue
            message = Message.create({
                'account_id': account.id or False,
                'template_id': self.id,
                'res_model': record._name,
                'res_id': record.id,
                # A contact is its own recipient: the chatter line and the Desk
                # card then name the doctor, not "contact".
                'partner_id': record.id if record._name == 'res.partner' else
                (getattr(record, 'partner_id', False) and record.partner_id.id or False),
                'number': number,
                'body': self._for(record).render(record, stages.get(record.id, 1)),
                'scheduled_at': wait,
            })
            # A waiting message is the queue's: it goes out when its hour comes.
            if not defer and not wait:
                message._attach_report(record)
                message.action_send()
            sent |= message
        if (defer or wait) and sent:
            cron = self.env.ref('epg_whatsapp.ir_cron_whatsapp_queue', raise_if_not_found=False)
            if cron:
                cron.sudo()._trigger()
        return sent
