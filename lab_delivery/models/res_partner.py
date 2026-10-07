# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.fields import Domain


class ResPartner(models.Model):
    _inherit = 'res.partner'

    # Read by the partner restriction that lets a carrier open the clinics they
    # carry work to - deliveries cross routes, the route alone does not.
    delivery_ids = fields.One2many('lab.delivery', 'partner_id', string='Deliveries')

    @api.model
    def _fw_reachable_domain(self):
        """A carrier's clinics. Deliveries are handed to whoever is going that way,
        so an executive regularly carries work to a clinic on someone else's route -
        and the route-only partner rule then refused the very clinic on the delivery
        and on the visit it was handed over at ("doesn't have 'read' access to
        Contact"). Only the clinics on deliveries the executive may already see
        (rule_delivery_own: carried by them or raised by them), with their doctors.
        (client, 2026-09-15)

        Odoo 20: in 19 this was its own Executive-group record rule, OR-ed with
        lab_fieldwork's route and visited rules. In 20 a group row is a permission
        and cannot widen lab_fieldwork's partner restriction, so the clause joins
        the restriction's own domain here instead.
        """
        user = self.env.user
        return Domain.OR([
            super()._fw_reachable_domain(),
            [('delivery_ids.executive_id', '=', user.id)],
            [('delivery_ids.create_uid', '=', user.id)],
            [('parent_id.delivery_ids.executive_id', '=', user.id)],
            [('parent_id.delivery_ids.create_uid', '=', user.id)],
        ])
