# -*- coding: utf-8 -*-
"""An amendment waiting on a case.

A case in the lab gets changed after it was ordered: the doctor rings to add a unit,
a try-in comes back with a correction, the floor finds the prescription and the order
disagree. The person who learns of it is usually on the floor, and the order was
invoiced as it stood because nothing on it said "not yet". So the floor can say it:
the order is marked as having an amendment, with what it is; it wears a badge and a
banner until somebody does the amendment and says so; and it cannot be invoiced in
between. (client, 2026-11-26: "a feature for manufacturing user to mark a SO has an
amendment; only able to invoice after doing the amendment; show a badge")
"""
from odoo import _, fields, models
from odoo.exceptions import AccessError, UserError

# Who may say an amendment is needed, and who may say it is done: the floor and the
# office both. Marking is not editing the order - a manufacturing user has no right to
# write a sales order and needs none for this - so both go through methods that check
# the role and write elevated, the way the case slip raises its order.
AMENDMENT_GROUPS = ('mrp.group_mrp_user', 'sales_team.group_sale_salesman')


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    has_amendment = fields.Boolean(
        'Has Amendment', tracking=True, copy=False, readonly=True,
        help="An amendment is waiting on this case. It cannot be invoiced until the "
             "amendment is done and marked so.")
    amendment_note = fields.Text('Amendment', copy=False, readonly=True)
    amendment_user_id = fields.Many2one(
        'res.users', 'Amendment Marked By', copy=False, readonly=True)
    amendment_date = fields.Datetime('Amendment Marked On', copy=False, readonly=True)

    def _check_amendment_role(self, what):
        if self.env.su or any(self.env.user.has_group(g) for g in AMENDMENT_GROUPS):
            return
        raise AccessError(_(
            "Only manufacturing and sales users can %s.", what))

    def action_amendment_needed(self):
        """Ask what the amendment is: a flag with no words is an instruction to go
        and find out."""
        self.ensure_one()
        self._check_amendment_role(_("mark an amendment"))
        return {
            'type': 'ir.actions.act_window', 'name': _('Mark an Amendment'),
            'res_model': 'lab.amendment.wizard', 'view_mode': 'form', 'target': 'new',
            'context': {'default_order_id': self.id},
        }

    def _mark_amendment(self, note):
        self._check_amendment_role(_("mark an amendment"))
        note = (note or '').strip()
        if not note:
            raise UserError(_("Say what has to be amended."))
        # elevated from here: the role was checked above, and a manufacturing user
        # can neither read nor write a sales order
        for order in self.sudo():
            if order.state == 'cancel':
                raise UserError(_("%s is cancelled.", order.name))
            # the order's own note grows: a second amendment before the first is done
            # is added to it, not written over it
            text = '\n'.join(filter(None, [order.amendment_note, note])) \
                if order.has_amendment else note
            order.write({
                'has_amendment': True, 'amendment_note': text,
                'amendment_user_id': self.env.uid,
                'amendment_date': fields.Datetime.now(),
            })
            order.message_post(body=_(
                "Amendment needed, marked by %(user)s: %(note)s. The order cannot be "
                "invoiced until it is done.", user=self.env.user.name, note=note))
        return True

    def action_amendment_done(self):
        """The amendment is done: the order may be invoiced again."""
        self._check_amendment_role(_("mark an amendment done"))
        for order in self.sudo().filtered('has_amendment'):
            was = order.amendment_note or ''
            order.write({
                'has_amendment': False, 'amendment_note': False,
                'amendment_user_id': False, 'amendment_date': False,
            })
            order.message_post(body=_(
                "Amendment done, marked by %(user)s. It was: %(note)s",
                user=self.env.user.name, note=was or _('(not written)')))
        return True

    def _create_invoices(self, *args, **kwargs):
        waiting = self.sudo().filtered('has_amendment')
        if waiting:
            raise UserError(_(
                "%(orders)s cannot be invoiced yet: an amendment is waiting on it.\n\n"
                "%(notes)s\n\nDo the amendment, press \"Amendment Done\" on the order, "
                "then invoice it.",
                orders=', '.join(waiting.mapped('name')),
                notes='\n'.join('%s: %s' % (o.name, o.amendment_note or '')
                                for o in waiting)))
        return super()._create_invoices(*args, **kwargs)


class MrpProduction(models.Model):
    """The floor sees the flag on the job, and can raise or clear it from there: a
    manufacturing user lives in the manufacturing order, not in the sales order."""
    _inherit = 'mrp.production'

    has_amendment = fields.Boolean(related='sale_id.has_amendment', string='Has Amendment')
    amendment_note = fields.Text(related='sale_id.amendment_note', string='Amendment')

    def action_amendment_needed(self):
        self.ensure_one()
        if not self.sale_id:
            raise UserError(_("%s was not made for a sales order.", self.name))
        return self.sale_id.action_amendment_needed()

    def action_amendment_done(self):
        return self.sale_id.action_amendment_done()
