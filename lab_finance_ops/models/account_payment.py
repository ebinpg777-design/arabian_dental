# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError

# What an approver actually signed: the money, who receives it, and from where.
APPROVAL_FIELDS = ('amount', 'currency_id', 'partner_id', 'journal_id')


class AccountPayment(models.Model):
    """Payment entry, verification and approval workflow (gap 20).

    Odoo posts a payment the moment someone presses Confirm. The approval gate
    here sits *before* posting rather than reversing afterwards, and only bites
    above a threshold — a lab does not want a second signature on every ₹500
    cash receipt, only on the ones worth controlling.
    """
    _inherit = 'account.payment'

    approval_state = fields.Selection(
        [('not_required', 'Not Required'), ('to_approve', 'To Approve'),
         ('approved', 'Approved'), ('refused', 'Refused')],
        default='not_required', required=True, copy=False, tracking=True, index=True,
        string='Approval')
    approved_by_id = fields.Many2one(
        'res.users', string='Approved By', readonly=True, copy=False, tracking=True)
    approval_date = fields.Datetime(readonly=True, copy=False)
    approval_note = fields.Char('Approval Remark', copy=False, tracking=True)
    cheque_id = fields.Many2one(
        'lab.cheque', string='Cheque', copy=False, readonly=True, index='btree_not_null',
        help="The cheque whose clearing produced this payment.")

    # ------------------------------------------------------------------ policy
    def _approval_settings(self):
        params = self.env['ir.config_parameter'].sudo()
        enabled = params.get_param('lab_finance_ops.require_payment_approval', 'False') == 'True'
        try:
            threshold = float(params.get_param('lab_finance_ops.payment_approval_threshold', 0))
        except (TypeError, ValueError):
            threshold = 0.0
        return enabled, threshold

    def _needs_approval(self):
        """A cheque that has cleared the bank is a fact, not a discretionary
        payment — its control was the Received -> Deposited -> Cleared workflow,
        so the approval gate does not apply a second time."""
        self.ensure_one()
        if self.cheque_id or self.env.context.get('lab_cheque_clearing'):
            return False
        enabled, threshold = self._approval_settings()
        return enabled and self.amount >= threshold

    # ------------------------------------------------------------------ actions
    def action_submit_for_approval(self):
        for payment in self:
            if payment.state != 'draft':
                raise UserError(_("Only a draft payment can be sent for approval."))
            payment.write({'approval_state': 'to_approve', 'approval_note': False})
        return True

    def action_approve_payment(self):
        if not self.env.user.has_group('lab_finance_ops.group_lab_payment_approver'):
            raise UserError(_("Only a Payment Approver can approve a payment."))
        self.write({
            'approval_state': 'approved',
            'approved_by_id': self.env.user.id,
            'approval_date': fields.Datetime.now(),
        })
        return True

    def action_refuse_payment(self):
        if not self.env.user.has_group('lab_finance_ops.group_lab_payment_approver'):
            raise UserError(_("Only a Payment Approver can refuse a payment."))
        for payment in self:
            if not payment.approval_note:
                raise UserError(_("Record why payment %s is being refused.", payment.name or ''))
            payment.write({'approval_state': 'refused', 'approved_by_id': False,
                           'approval_date': False})
        return True

    def action_post(self):
        for payment in self:
            if payment._needs_approval() and payment.approval_state != 'approved':
                raise UserError(_(
                    "Payment %(name)s of %(amount)s needs approval before it can be posted.",
                    name=payment.name or _('(draft)'),
                    amount=payment.currency_id.format(payment.amount)))
        return super().action_post()

    def write(self, vals):
        """An edit to what was approved sends the payment back for approval.

        Approval stayed put through any change, so a 5,000 payment could be
        approved and then raised to 50,000 and posted on the old signature.
        """
        touched = [name for name in APPROVAL_FIELDS if name in vals]
        if not touched or not self._approval_settings()[0]:
            return super().write(vals)
        before = {p.id: tuple(p[name] for name in touched) for p in self}
        res = super().write(vals)
        for payment in self:
            if payment.state != 'draft' or \
                    tuple(payment[name] for name in touched) == before[payment.id]:
                continue
            needs = payment._needs_approval()
            if payment.approval_state == 'approved' or (
                    needs and payment.approval_state == 'not_required'):
                new_state = 'to_approve' if needs else 'not_required'
            elif payment.approval_state == 'to_approve' and not needs:
                new_state = 'not_required'
            else:
                continue    # refused stays refused until it is resubmitted
            payment.write({'approval_state': new_state, 'approved_by_id': False,
                           'approval_date': False})
        return res

    @api.model_create_multi
    def create(self, vals_list):
        payments = super().create(vals_list)
        for payment in payments:
            if payment._needs_approval() and payment.approval_state == 'not_required':
                payment.approval_state = 'to_approve'
        return payments
