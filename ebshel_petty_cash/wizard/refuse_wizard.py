# Part of ebshel_petty_cash. See LICENSE file for full copyright and licensing details.
from odoo import fields, models
from odoo.exceptions import UserError

REFUSABLE_MODELS = (
    "ebshel.petty.cash.expense",
    "ebshel.petty.cash.advance",
    "ebshel.petty.cash.replenishment",
)


class PettyCashRefuseWizard(models.TransientModel):
    _name = "ebshel.petty.cash.refuse.wizard"
    _description = "Refuse a Petty Cash Document"

    reason = fields.Text(required=True)

    def action_refuse(self):
        self.ensure_one()
        model = self.env.context.get("active_model")
        if model not in REFUSABLE_MODELS:
            raise UserError(self.env._("Nothing to refuse."))
        records = self.env[model].browse(self.env.context.get("active_ids", [])).exists()
        records._petty_cash_refuse(self.reason)
        return {"type": "ir.actions.act_window_close"}
