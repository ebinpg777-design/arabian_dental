# -*- coding: utf-8 -*-
from odoo import _, fields, models
from odoo.exceptions import UserError


class LabDeliveryDoneWizard(models.TransientModel):
    """The handover moment (R5). One question that must be answered honestly: did it
    reach the clinic, or was it left somewhere nearby — and if nearby, where."""
    _name = 'lab.delivery.done.wizard'
    _description = 'Mark Delivery Done'

    delivery_id = fields.Many2one('lab.delivery', required=True, readonly=True)
    delivery_outcome = fields.Selection(
        [('clinic', 'Delivered to Clinic / Doctor'),
         ('near_place', 'Delivered to Near Place')],
        required=True, default='clinic')
    near_place_name = fields.Char(
        'Left at', help="e.g. Sunrise Pharmacy next door — the doctor will be told.")
    received_by = fields.Char('Received by')
    proof_image = fields.Image(max_width=1400, max_height=1400)

    def action_confirm(self):
        self.ensure_one()
        if self.delivery_outcome == 'near_place' and not self.near_place_name:
            raise UserError(_(
                "Say where it was left. 'Near place' with no place is a package "
                "nobody can find."))
        delivery = self.delivery_id
        now = fields.Datetime.now()
        delivery.write({
            'state': 'delivered',
            'delivery_outcome': self.delivery_outcome,
            'near_place_name': self.near_place_name if
                self.delivery_outcome == 'near_place' else False,
            'received_by': self.received_by,
            'proof_image': self.proof_image,
            'delivered_datetime': now,
            'is_delayed': False,
            'late_delivered': now > delivery._deadline(),
        })
        # The visit is knowable now and only now: the executive is standing in the
        # clinic they checked into. A nightly matching job would be guessing.
        delivery._attach_visit()
        delivery.message_post(body=(
            _("Delivered to near place: %s", self.near_place_name)
            if self.delivery_outcome == 'near_place'
            else _("Delivered to the clinic%s",
                   (' — ' + self.received_by) if self.received_by else '')))
        return {'type': 'ir.actions.act_window_close'}
