# -*- coding: utf-8 -*-
# Copyright (C) 2025-2026 Ebshel Technologies (https://ebshel.com)
# License OPL-1. See LICENSE file for full copyright and licensing details.
from odoo import fields, models, _
from odoo.exceptions import UserError

class PrRejectWizard(models.TransientModel):
    _name = 'ebshel.pr.reject.wizard'
    _description = 'Purchase Request Rejection Wizard'

    request_id = fields.Many2one(
        'ebshel.purchase.request', string='Purchase Request',
        required=True, ondelete='cascade',
    )
    reason = fields.Text('Rejection Reason', required=True)

    def action_confirm_reject(self):
        self.ensure_one()
        request = self.request_id
        if request.state != 'submitted':
            raise UserError(_('Only submitted requests can be rejected.'))
        request._check_can_approve()
        request.write({'state': 'rejected', 'rejection_reason': self.reason,
                       'pending_approver_ids': [(5, 0, 0)]})
        request._create_approval_log('rejected', comments=self.reason)
        request.message_post(
            body=_('Request rejected. Reason: %s', self.reason),
            subtype_xmlid='mail.mt_note',
        )
        template = self.env.ref(
            'ebshel_purchase_request.mail_template_pr_rejected',
            raise_if_not_found=False,
        )
        if template:
            template.send_mail(request.id, 
                               email_values={
                                    'email_to': request.requester_id.email,
                                },
                               force_send=True)
        return {'type': 'ir.actions.act_window_close'}
        
