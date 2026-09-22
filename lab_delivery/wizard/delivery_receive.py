# -*- coding: utf-8 -*-
from odoo import _, fields, models
from odoo.exceptions import UserError


class LabDeliveryReceiveWizard(models.TransientModel):
    """The lab takes an inbound bag in — the mirror of the Mark Delivered wizard.

    One honest question again: did it arrive whole? A damaged impression discovered
    at the bench two days later is a remake nobody can bill; a damaged one recorded
    at the door is a phone call the same afternoon.
    """
    _name = 'lab.delivery.receive.wizard'
    _description = 'Receive Pickup at Lab'

    delivery_id = fields.Many2one('lab.delivery', required=True, readonly=True)
    received_condition = fields.Selection(
        [('ok', 'In good condition'), ('damaged', 'Damaged')],
        required=True, default='ok')
    damage_note = fields.Char('What is damaged')
    received_by = fields.Char('Received by', default=lambda s: s.env.user.name)
    proof_image = fields.Image(max_width=1400, max_height=1400,
                               help="A photo of the parcel as it arrived.")

    def action_confirm(self):
        self.ensure_one()
        if self.received_condition == 'damaged' and not self.damage_note:
            raise UserError(_(
                "Say what is damaged - the executive has to tell the doctor "
                "something better than 'it broke'."))
        delivery = self.delivery_id
        now = fields.Datetime.now()
        delivery.write({
            'state': 'delivered',
            'delivery_outcome': 'lab',
            'received_condition': self.received_condition,
            'received_by': self.received_by,
            'proof_image': self.proof_image or delivery.proof_image,
            'delivered_datetime': now,
            'is_delayed': False,
            'late_delivered': now > delivery._deadline(),
        })
        if self.received_condition == 'damaged':
            # The person who collected it hears first - it is their doctor to call.
            delivery.activity_schedule(
                'mail.mail_activity_data_todo',
                summary=_("Damaged in transit: %s", delivery.name),
                note=self.damage_note,
                user_id=(delivery.executive_id or self.env.user).id)
        delivery.message_post(body=(
            _("Received at the lab — DAMAGED: %s", self.damage_note)
            if self.received_condition == 'damaged'
            else _("Received at the lab in good condition%s",
                   (' — ' + self.received_by) if self.received_by else '')))
        return {'type': 'ir.actions.act_window_close'}
