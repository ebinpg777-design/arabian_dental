# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

# Every account type a direct payment is allowed to land on. Deliberately every
# expense/income flavour the chart of accounts offers - depreciation and cost of
# revenue included - not just the two most common ones, because whoever is
# recording a direct payment already knows which of these it is; second-guessing
# them here would only mean a domain nobody understands why it rejected an account.
DIRECT_ACCOUNT_TYPES = (
    'expense', 'expense_other', 'expense_depreciation', 'expense_direct_cost',
    'income', 'income_other',
)


class AccountPayment(models.Model):
    _inherit = 'account.payment'

    payment_kind = fields.Selection(
        [('partner', 'Customer/Vendor'),
         ('direct', 'Direct (Expense/Income)'),
         ('contra', 'Contra (Bank/Cash Transfer)')],
        default='partner', required=True, tracking=True,
        help="Customer/Vendor: the usual flow, posted against that partner's "
             "receivable or payable.\n"
             "Direct: posted straight against an Expense or Income account - "
             "petty cash, bank charges, interest received...\n"
             "Contra: a transfer between two of the company's own Bank/Cash "
             "accounts. Posting this leg raises its mirror on the other "
             "journal automatically.")
    destination_journal_id = fields.Many2one(
        comodel_name='account.journal',
        string='To Journal',
        copy=False,
        check_company=True,
        domain="[('type', 'in', ('bank', 'cash')), ('id', '!=', journal_id), "
               "('company_id', '=', company_id)]",
        help="The other Bank/Cash journal this contra payment transfers to. "
             "Posting creates the mirror payment there automatically.")

    # ------------------------------------------------------------------ low-level
    @api.model_create_multi
    def create(self, vals_list):
        # The onchange only runs behind a form - _create_contra_mirror and any
        # other programmatic create (a script, another module) bypass it, so
        # partner_type would default to 'customer' and trip whatever other
        # module keys a requirement off that, same as the UI path below.
        for vals in vals_list:
            if vals.get('payment_kind') in ('direct', 'contra') and 'partner_type' not in vals:
                vals['partner_type'] = 'supplier'
        return super().create(vals_list)

    def write(self, vals):
        if vals.get('payment_kind') in ('direct', 'contra') and 'partner_type' not in vals:
            vals = {**vals, 'partner_type': 'supplier'}
        return super().write(vals)

    # ------------------------------------------------------------------ onchange
    @api.onchange('payment_kind')
    def _onchange_payment_kind(self):
        for pay in self:
            if pay.payment_kind != 'partner':
                pay.partner_id = False
                # partner_type only ever means customer/vendor, and other modules
                # key required/visible fields off it (e.g. a "Sales Route" that
                # only makes sense for an inbound customer payment). 'supplier' is
                # simply the value that trips none of those - it stays otherwise
                # unused for a direct/contra payment.
                pay.partner_type = 'supplier'
            if pay.payment_kind != 'contra':
                pay.destination_journal_id = False
            if pay.payment_kind != 'direct':
                pay.destination_account_id = False

    # ------------------------------------------------------------------ compute
    @api.depends('partner_type', 'partner_id', 'journal_id', 'payment_kind',
                 'destination_journal_id')
    def _compute_destination_account_id(self):
        special = self.filtered(lambda p: p.payment_kind in ('direct', 'contra'))
        super(AccountPayment, self - special)._compute_destination_account_id()
        for pay in special:
            if pay.payment_kind == 'contra':
                pay.destination_account_id = pay.company_id.transfer_account_id
            else:
                # 'direct': nothing to derive it from - it is exactly what the
                # user picks, so leave whatever is already on the record (a
                # plain self-assignment, since a stored compute must still
                # assign every record it runs for).
                pay.destination_account_id = pay.destination_account_id

    # ------------------------------------------------------------------ helpers
    @api.model
    def _get_valid_payment_account_types(self):
        # _seek_for_lines() uses this list to recognise the counterpart line of
        # the journal entry - without it, a direct payment's expense/income line
        # would be read back as a write-off instead of the counterpart, and any
        # edit to the payment afterwards would silently drop it.
        return super()._get_valid_payment_account_types() + list(DIRECT_ACCOUNT_TYPES)

    # ------------------------------------------------------------------ constraints
    @api.constrains('payment_kind', 'destination_account_id')
    def _check_direct_destination_account(self):
        for pay in self:
            if pay.payment_kind == 'direct' and pay.destination_account_id \
                    and pay.destination_account_id.account_type not in DIRECT_ACCOUNT_TYPES:
                raise ValidationError(_(
                    "%(account)s is a %(type)s account. A direct payment "
                    "posts against an Expense or Income account, not this one.",
                    account=pay.destination_account_id.display_name,
                    type=pay.destination_account_id.account_type,
                ))

    # ------------------------------------------------------------------ posting
    def action_post(self):
        to_pair = self.filtered(
            lambda p: p.payment_kind == 'contra' and not p.paired_internal_transfer_payment_id)
        for pay in to_pair:
            if not pay.destination_journal_id:
                raise UserError(_(
                    "%s is a contra payment with no journal to transfer to. "
                    "Pick the journal it moves the money into.", pay.display_name))
            if pay.destination_journal_id == pay.journal_id:
                raise UserError(_(
                    "A contra payment needs two different journals - pick "
                    "somewhere other than %s to transfer to.",
                    pay.journal_id.display_name))
            if not pay.company_id.transfer_account_id:
                raise UserError(_(
                    "%s has no Internal Transfer account configured "
                    "(Accounting Settings) - a contra payment needs one to "
                    "post both legs against.", pay.company_id.display_name))

        super().action_post()

        for pay in to_pair:
            pay._create_contra_mirror()

    def _create_contra_mirror(self):
        """Raise the other leg of a contra payment and reconcile the two.

        Called once self is already posted. The mirror is the same amount and
        date, moving the other way, on the journal this one names as its
        destination - both booked through the company's Internal Transfer
        account, which is what this reconciles closed so the transfer nets to
        zero rather than sitting there as two unrelated open lines.
        """
        self.ensure_one()
        mirror = self.create({
            'payment_kind': 'contra',
            'payment_type': 'inbound' if self.payment_type == 'outbound' else 'outbound',
            'journal_id': self.destination_journal_id.id,
            'destination_journal_id': self.journal_id.id,
            'amount': self.amount,
            'date': self.date,
            'currency_id': self.currency_id.id,
            'partner_id': False,
            'memo': self.memo,
            'paired_internal_transfer_payment_id': self.id,
        })
        self.paired_internal_transfer_payment_id = mirror.id
        mirror.action_post()

        transfer_account = self.company_id.transfer_account_id
        lines = (self.move_id.line_ids + mirror.move_id.line_ids).filtered(
            lambda l: l.account_id == transfer_account and not l.reconciled)
        if len(lines) == 2:
            lines.reconcile()

    def action_cancel(self):
        super().action_cancel()
        paired = self.filtered(
            lambda p: p.payment_kind == 'contra' and p.paired_internal_transfer_payment_id)
        for pay in paired:
            other = pay.paired_internal_transfer_payment_id
            if other.state != 'canceled':
                other.action_cancel()
