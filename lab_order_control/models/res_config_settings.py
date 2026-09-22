# -*- coding: utf-8 -*-
from odoo import api, fields, models

PARAM = 'lab_order_control.verification_maker_groups'
# Integer settings where 0 is an answer, not "unset". Core saves an integer 0 as
# False, which DELETES the parameter, and the getters then read their defaults of 3 and
# 5 - so "0 disables the automatic hold" could never actually be saved.
ZERO_KEPT = ('lab_doctor_call_attempts_before_hold', 'lab_hold_warning_days')


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    lab_require_order_verification = fields.Boolean(
        'Require Order Verification',
        config_parameter='lab_order_control.require_order_verification',
        help="A registered case must be verified by a second person before it can be "
             "confirmed into production.")
    lab_verification_maker_group_ids = fields.Many2many(
        'res.groups', string='Roles Needing Verification',
        help="Only orders entered by someone in these roles are held for checking. "
             "Typically the field/sales executives, who register cases at the clinic. "
             "Leave empty to check every order, whoever entered it.")
    lab_allow_self_verification = fields.Boolean(
        'Allow Self-verification',
        config_parameter='lab_order_control.allow_self_verification',
        help="Let the person who registered a case also verify it. Off by default — "
             "turn it on only if the lab genuinely runs a single-person counter.")
    lab_doctor_call_attempts_before_hold = fields.Integer(
        'Call attempts before hold', default=3,
        config_parameter='lab_order_control.doctor_call_attempts_before_hold',
        help="After this many unanswered attempts the order moves itself to Pending "
             "Information and a manager is told. 0 disables the automatic hold.")
    lab_hold_warning_days = fields.Integer(
        'Warn after days on hold', default=5,
        config_parameter='lab_order_control.hold_warning_days',
        help="How long an order may sit in Pending Information before managers are "
             "notified — once, not daily.")

    lab_credit_limit_policy = fields.Selection(
        [('none', 'No Check'), ('warn', 'Warn Only'), ('block', 'Block Order')],
        string='Default Over-limit Action', default='warn',
        config_parameter='lab_order_control.credit_limit_policy')

    # `config_parameter=` cannot carry a Many2many, so the ids are stored as CSV and
    # read back by hand.
    @api.model
    def get_values(self):
        res = super().get_values()
        raw = self.env['ir.config_parameter'].sudo().get_param(PARAM, '')
        ids = [int(x) for x in raw.split(',') if x.strip().isdigit()]
        res['lab_verification_maker_group_ids'] = [
            (6, 0, self.env['res.groups'].browse(ids).exists().ids)]
        return res

    def set_values(self):
        # Read BEFORE core saves: saving a 0 deletes the parameter, and the cache
        # invalidation that follows loses any value not yet written to the row.
        zeros = self._zero_param_names()
        res = super().set_values()
        self._keep_zero_params(zeros)
        self.env['ir.config_parameter'].sudo().set_param(
            PARAM, ','.join(str(i) for i in self.lab_verification_maker_group_ids.ids))
        # The flag is stored on every order, so changing who is checked has to restate
        # it — otherwise the rule changes and the existing queue still reflects the old
        # one.
        orders = self.env['sale.order'].sudo().search(
            [('state', 'in', ('draft', 'sent'))])
        orders._compute_verification_needed()
        return res

    def _zero_param_names(self):
        return [name for name in ZERO_KEPT if not self[name]]

    def _keep_zero_params(self, names=None):
        """Write back the explicit 0 core has just removed."""
        params = self.env['ir.config_parameter'].sudo()
        for name in (self._zero_param_names() if names is None else names):
            params.set_param(self._fields[name].config_parameter, '0')
