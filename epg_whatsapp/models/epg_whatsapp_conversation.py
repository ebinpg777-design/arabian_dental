# -*- coding: utf-8 -*-
from datetime import timedelta

from odoo import _, api, fields, models

# Meta's customer service window. Inside it you may send anything; outside it only an
# approved template will be delivered.
WINDOW_HOURS = 24


class EpgWhatsappConversation(models.Model):
    """One thread with one number — and the record of whether we may write to it.

    This exists because of the single rule that decides whether a WhatsApp integration
    works in production or only in a demo: Meta allows free-form messages **only within
    24 hours of the customer's last inbound message**. Outside that window a plain text
    send is rejected, and it is rejected at Meta rather than here, so the failure shows
    up as a mysterious error on a message nobody is watching.

    Simulation mode hides this completely — every send "works" until the day the account
    goes live. So the window is modelled explicitly rather than discovered: the thread
    knows when the doctor last wrote, and therefore what may be sent right now.
    """
    _name = 'epg.whatsapp.conversation'
    _description = 'WhatsApp Conversation'
    _order = 'last_message_at desc, id desc'
    _rec_name = 'display_name'

    account_id = fields.Many2one('epg.whatsapp.account', string='Sender',
                                 required=True, ondelete='cascade', index=True)
    number = fields.Char(required=True, index=True)
    partner_id = fields.Many2one('res.partner', index='btree_not_null')
    company_id = fields.Many2one('res.company', related='account_id.company_id',
                                 store=True)

    message_ids = fields.One2many('epg.whatsapp.message', 'conversation_id')
    message_count = fields.Integer(compute='_compute_counts')
    inbound_count = fields.Integer(compute='_compute_counts')

    last_inbound_at = fields.Datetime(
        readonly=True, help="When this number last messaged us. The 24-hour window "
                            "Meta allows free-form replies in runs from here.")
    last_outbound_at = fields.Datetime(readonly=True)
    last_message_at = fields.Datetime(compute='_compute_last_message', store=True)

    window_expires_at = fields.Datetime(
        compute='_compute_window', store=True, string='Open Until')
    # Not stored: it is a fact about *now*, and a stored copy would be wrong within the
    # hour and have to be swept by a cron to stay honest.
    window_open = fields.Boolean(compute='_compute_window_open', search='_search_window_open')
    window_state = fields.Selection(
        [('open', 'Open — anything may be sent'),
         ('closed', 'Closed — template only'),
         ('never', 'Never messaged us')],
        compute='_compute_window_open')

    opt_out = fields.Boolean(
        'Opted Out', readonly=True, copy=False,
        help="Set when this number replies STOP. Nothing further is sent to it.")
    opt_out_at = fields.Datetime(readonly=True, copy=False)

    _number_per_account = models.Constraint(
        'unique(account_id, number)',
        'There is already a conversation with this number on this sender.')

    @api.depends('number', 'partner_id')
    def _compute_display_name(self):
        for conv in self:
            conv.display_name = conv.partner_id.display_name or conv.number or _('New')

    def _compute_counts(self):
        groups = self.env['epg.whatsapp.message']._read_group(
            [('conversation_id', 'in', self.ids)],
            ['conversation_id', 'direction'], ['__count'])
        totals, inbound = {}, {}
        for conv, direction, count in groups:
            totals[conv.id] = totals.get(conv.id, 0) + count
            if direction == 'inbound':
                inbound[conv.id] = inbound.get(conv.id, 0) + count
        for conv in self:
            conv.message_count = totals.get(conv.id, 0)
            conv.inbound_count = inbound.get(conv.id, 0)

    @api.depends('last_inbound_at', 'last_outbound_at')
    def _compute_last_message(self):
        for conv in self:
            stamps = [s for s in (conv.last_inbound_at, conv.last_outbound_at) if s]
            conv.last_message_at = max(stamps) if stamps else False

    @api.depends('last_inbound_at')
    def _compute_window(self):
        for conv in self:
            conv.window_expires_at = (
                conv.last_inbound_at + timedelta(hours=WINDOW_HOURS)
                if conv.last_inbound_at else False)

    @api.depends('window_expires_at')
    def _compute_window_open(self):
        now = fields.Datetime.now()
        for conv in self:
            if not conv.last_inbound_at:
                conv.window_open, conv.window_state = False, 'never'
            elif conv.window_expires_at and conv.window_expires_at > now:
                conv.window_open, conv.window_state = True, 'open'
            else:
                conv.window_open, conv.window_state = False, 'closed'

    def _search_window_open(self, operator, value):
        if operator not in ('=', '!=') or not isinstance(value, bool):
            raise NotImplementedError
        wanted = value if operator == '=' else not value
        now = fields.Datetime.now()
        if wanted:
            return [('window_expires_at', '>', now)]
        return ['|', ('window_expires_at', '=', False),
                ('window_expires_at', '<=', now)]

    # ------------------------------------------------------------------ lookup
    @api.model
    def _get_or_create(self, account, number, partner=None):
        """The thread for this number, created if this is the first we hear of it."""
        if not (account and number):
            return self.browse()
        conv = self.search([('account_id', '=', account.id),
                            ('number', '=', number)], limit=1)
        if not conv:
            conv = self.create({
                'account_id': account.id, 'number': number,
                'partner_id': partner.id if partner else False,
            })
        elif partner and not conv.partner_id:
            conv.partner_id = partner.id
        return conv

    def _note_inbound(self, when=None):
        """A message FROM the customer — this is what opens the window."""
        self.write({'last_inbound_at': when or fields.Datetime.now()})

    def _note_outbound(self, when=None):
        self.write({'last_outbound_at': when or fields.Datetime.now()})

    def action_view_messages(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': _('Messages'),
            'res_model': 'epg.whatsapp.message', 'view_mode': 'list,form',
            'domain': [('conversation_id', '=', self.id)],
            'context': {'default_conversation_id': self.id},
        }

    def action_reopen(self):
        """Clear an opt-out, on the customer's say-so and nobody else's."""
        self.write({'opt_out': False, 'opt_out_at': False})
