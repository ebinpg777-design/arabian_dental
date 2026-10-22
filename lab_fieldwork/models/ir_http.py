# -*- coding: utf-8 -*-
from odoo import api, models
from odoo.http import request

# Whoever holds one of these does office work too: in a browser, Odoo's own start
# screen is theirs; only inside ADL FieldX do they open on My Day.
OFFICE_GROUPS = ('lab_fieldwork.group_fieldwork_manager', 'sales_team.group_sale_manager',
                 'account.group_account_manager', 'base.group_system')


class IrHttp(models.AbstractModel):
    _inherit = 'ir.http'

    def session_info(self):
        """The two phone exceptions, in the session.

        Every field-work screen that would ask the phone for a position or a
        camera (My Day, the visit buttons, the live trail, the scan station)
        reads them from here, with no extra round trip and no way to be out of
        step with the server rules. (client, 2026-09-28)
        """
        info = super().session_info()
        flags = self.env.user._fw_phone_exceptions()
        info['fw_location_exception'] = flags['location']
        info['fw_camera_exception'] = flags['camera']
        # Straight to My Day, not Odoo's app screen and a tap on Field Work: every
        # opening of ADL FieldX paid for a screen the executive only walked through.
        # A home action set on the user still wins. (client, 2026-10-22: "check the
        # delay in connecting ADL FieldX app")
        if not info.get('home_action_id'):
            home = self._fw_home_action_id()
            if home:
                info['home_action_id'] = home
        return info

    @api.model
    def _fw_home_action_id(self):
        """My Day for field staff: inside ADL FieldX for anyone who has My Day, and in
        a browser for executives whose only work is field work."""
        user = self.env.user
        if not user.has_group('lab_fieldwork.group_fieldwork_executive'):
            return False
        try:
            agent = request.httprequest.user_agent.string if request else ''
        except RuntimeError:  # outside a request
            agent = ''
        in_app = 'ADLField/' in (agent or '')
        if not in_app and any(user.has_group(group) for group in OFFICE_GROUPS):
            return False
        action = self.env.ref('lab_fieldwork.action_my_day', raise_if_not_found=False)
        return action.id if action else False
