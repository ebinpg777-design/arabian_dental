# -*- coding: utf-8 -*-
from odoo import models


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
        return info
