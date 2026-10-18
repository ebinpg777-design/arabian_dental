# -*- coding: utf-8 -*-
"""What one line of a sale counts as, when an executive's incentive is worked out.

Client rule (2026-08-09): "incentive calculated based on the sale amount of the
products, but some products has cost is high so incentive sale price configuration
needed in the products for incentive calculation."

Client rule (Ortho, 2026-10-08): "Some products are not manufacturing, for those items
needs to calculate on its margin."

Both say the same thing from two directions: the sale price is the right base only
when the lab made the thing. For an item the lab buys in and resells, the sale price
is mostly somebody else's money, and paying a percentage of it rewards an executive
for turnover the lab never earned. So a product can declare its own base.

The margin is typed in, not derived. Odoo could compute sale minus cost, but bought-in
items commonly carry a cost of zero, so a derived margin would silently equal the full
sale price -- the exact over-payment this is meant to stop.
"""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

INCENTIVE_BASES = [
    ('sale', 'Sale value — what the doctor was charged'),
    ('price', 'Incentive price — a fixed figure per unit'),
    ('margin_pct', 'Margin % of the sale value'),
    ('margin_amount', 'Margin amount per unit'),
]


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    incentive_basis = fields.Selection(
        INCENTIVE_BASES, string='Incentive Counted On', default='sale', required=True,
        help="What an executive earns incentive on for this product. Use a margin "
             "for items the lab does not manufacture: the sale price of a bought-in "
             "item is mostly the supplier's money.")
    incentive_price = fields.Monetary(
        'Incentive Price', currency_field='currency_id',
        help="What one unit of this product counts as, for incentive calculation "
             "only — never for invoicing. Used when the basis is 'Incentive price'.")
    incentive_margin_pct = fields.Float(
        'Incentive Margin %',
        help="The share of the sale value the lab actually keeps on this item. "
             "Used when the basis is 'Margin %'. Leave at zero to fall back to the "
             "Default Margin % on the incentive rule.")
    incentive_margin_amount = fields.Monetary(
        'Incentive Margin / Unit', currency_field='currency_id',
        help="What the lab keeps on one unit of this item. Used when the basis is "
             "'Margin amount per unit'.")

    @api.constrains('incentive_basis', 'incentive_margin_pct', 'incentive_margin_amount')
    def _check_incentive_margin(self):
        for product in self:
            if product.incentive_basis == 'margin_pct' and not 0 <= product.incentive_margin_pct <= 100:
                raise ValidationError(_(
                    "%s: an incentive margin is a percentage of the sale value, so it "
                    "must be between 0 and 100.", product.display_name))
            # A per-unit margin has no default to fall back on, so leaving it at zero
            # does not mean "unset" -- it means this product earns nobody anything,
            # quietly, on every sheet from now on. Say so at the point of typing it.
            if product.incentive_basis == 'margin_amount' and product.incentive_margin_amount <= 0:
                raise ValidationError(_(
                    "%s is set to earn incentive on a margin per unit, so it needs a "
                    "margin above zero — as it stands it would earn nothing at all.",
                    product.display_name))

    def _incentive_base(self, sale_amount, quantity, default_margin_pct=0.0):
        """What this product contributes to an executive's incentive base.

        `sale_amount` is the line's billed amount and `quantity` the units on it,
        both signed (negative on a credit note), and `default_margin_pct` the period
        rule's fallback. Returns the sale amount unchanged for anything not
        configured, so a product nobody has looked at behaves exactly as it did
        before this field existed.
        """
        self.ensure_one()
        basis = self.incentive_basis or 'sale'
        if basis == 'price' and self.incentive_price:
            return self.incentive_price * quantity
        if basis == 'margin_amount':
            return self.incentive_margin_amount * quantity
        if basis == 'margin_pct':
            pct = self.incentive_margin_pct or default_margin_pct
            return sale_amount * pct / 100.0
        return sale_amount

    def _uses_own_incentive_base(self):
        self.ensure_one()
        return (self.incentive_basis or 'sale') != 'sale'


class ProductProduct(models.Model):
    _inherit = 'product.product'

    def _incentive_base(self, sale_amount, quantity, default_margin_pct=0.0):
        self.ensure_one()
        return self.product_tmpl_id._incentive_base(
            sale_amount, quantity, default_margin_pct)

    def _uses_own_incentive_base(self):
        self.ensure_one()
        return self.product_tmpl_id._uses_own_incentive_base()
