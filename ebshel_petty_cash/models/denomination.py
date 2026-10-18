# Part of ebshel_petty_cash. See LICENSE file for full copyright and licensing details.
from odoo import api, fields, models


class PettyCashDenomination(models.Model):
    _name = "ebshel.petty.cash.denomination"
    _description = "Petty Cash Denomination"
    _order = "currency_id, value desc"

    currency_id = fields.Many2one("res.currency", required=True, index=True)
    value = fields.Float(required=True, digits=(16, 4))
    kind = fields.Selection([("note", "Note"), ("coin", "Coin")], required=True, default="note")
    active = fields.Boolean(default=True)

    _value_positive = models.Constraint(
        "CHECK(value > 0)", "A denomination must have a positive value."
    )
    _value_unique = models.Constraint(
        "UNIQUE(currency_id, value, kind)", "This denomination already exists for the currency."
    )

    @api.depends("currency_id", "value", "kind")
    def _compute_display_name(self):
        kinds = dict(self._fields["kind"]._description_selection(self.env))
        for denomination in self:
            amount = denomination.currency_id.format(denomination.value) if denomination.currency_id else denomination.value
            denomination.display_name = f"{amount} ({kinds.get(denomination.kind, '')})"
