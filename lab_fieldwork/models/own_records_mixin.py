# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError

MANAGER = 'lab_fieldwork.group_fieldwork_manager'


class LabOwnRecordMixin(models.AbstractModel):
    """An executive's records are their own — including who they are assigned to.

    The record rules already stop an executive READING somebody else's work. They do not
    stop them *handing their own work over*: a write that changes `user_id` to a
    colleague passes the rule check, the record vanishes from the writer's own list, and
    it lands in someone else's without that person or their manager being asked. It is
    the one way an executive can affect another's numbers, and it looked like an access
    rule was covering it.

    This closes it in `create()` and `write()` rather than in `@api.constrains`, for the
    error message: a constraint fires after the rules do, so the user would see Odoo's
    generic "you have stumbled upon some top-secret records" instead of being told what
    they actually did wrong.

    `allowed_user_ids` exists so the FORM can be honest too. Offering an executive a
    dropdown of every user in the database and then refusing the save is a worse
    experience than not offering the choice, and it is the same rule stated twice — once
    where it is enforced and once where it is seen.
    """
    _name = 'lab.own.record.mixin'
    _description = 'Own-records-only mixin'

    allowed_user_ids = fields.Many2many(
        'res.users', string='Assignable To', compute='_compute_allowed_users',
        help="Who this record may be assigned to. An executive may only assign work to "
             "themselves; a manager may assign it to anyone in the field.")

    # The value depends on WHO IS ASKING, which is context and not data. Without this
    # Odoo caches the first answer against the record and hands it to the next user in
    # the same worker — an executive opens a visit, a manager opens the same visit, and
    # the manager gets the executive's one-name list.
    @api.depends_context('uid')
    def _compute_allowed_users(self):
        allowed = self._fw_assignable_users()
        for record in self:
            record.allowed_user_ids = allowed

    @api.model
    def _fw_assignable_users(self):
        if self.env.su or self.env.user.has_group(MANAGER):
            group = self.env.ref('lab_fieldwork.group_fieldwork_executive',
                                 raise_if_not_found=False)
            # A manager assigns work to the field force, not to the whole company.
            people = group.sudo().all_user_ids.filtered('active') if group \
                else self.env['res.users']
            return people | self.env.user
        return self.env.user

    @api.model
    def _fw_check_assignment(self, user_ids):
        """Refuse work being put in, or moved to, somebody else's name."""
        if self.env.su or self.env.user.has_group(MANAGER):
            return
        me = self.env.user.id
        wrong = {uid for uid in user_ids if uid and uid != me}
        if wrong:
            names = self.env['res.users'].sudo().browse(sorted(wrong)).mapped('name')
            raise UserError(_(
                "You can only record your own field work. This would put it in "
                "%(who)s's name — ask your manager to assign it.",
                who=', '.join(names)))

    @api.model_create_multi
    def create(self, vals_list):
        self._fw_check_assignment([vals.get('user_id') for vals in vals_list])
        return super().create(vals_list)

    def write(self, vals):
        if 'user_id' in vals:
            self._fw_check_assignment([vals['user_id']])
        return super().write(vals)
