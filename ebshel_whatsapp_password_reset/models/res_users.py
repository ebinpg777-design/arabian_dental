# Part of ebshel_whatsapp_password_reset. See LICENSE file for full copyright and licensing details.
import re
from urllib.parse import quote

from odoo import _, models


def international_digits(raw, country_code=0):
    """A phone number as WhatsApp's links want it - country code and number, digits
    only - or '' when it is not one. '+91 98097 81772', '0091 9809781772' and, with
    country code 91, '98097 81772' or '098097 81772' all give '919809781772'."""
    raw = (raw or '').strip()
    if not raw:
        return ''
    digits = re.sub(r'\D', '', raw)
    if raw.startswith('+'):
        number = digits
    elif digits.startswith('00'):
        number = digits[2:]
    else:
        national = digits.lstrip('0')
        code = str(country_code or '')
        # a number already written with its country code, without the +
        if code and not (national.startswith(code) and len(national) > 10):
            number = code + national
        else:
            number = national
    return number if 8 <= len(number) <= 15 else ''


class ResUsers(models.Model):
    _inherit = 'res.users'

    def action_whatsapp_reset_password(self):
        """Open WhatsApp with a message to this user holding a fresh link to set a new
        password - the link Odoo's "Send Password Reset" emails, for the people who
        read WhatsApp and not email. Administrators only, never for yourself or an
        archived user (get_reset_password_link says so). Without a number, WhatsApp
        asks whom to send it to. The chatter records that it went, never the link."""
        self.ensure_one()
        link = self.get_reset_password_link()
        number = self._whatsapp_reset_number()
        url = 'https://wa.me/%s?text=%s' % (number, quote(self._whatsapp_reset_message(link)))
        self.message_post(body=_(
            "Password reset link sent on WhatsApp to %s.",
            '+' + number if number else _("a chat chosen in WhatsApp")))
        return {'type': 'ir.actions.act_url', 'url': url, 'target': 'new'}

    def _whatsapp_reset_number(self):
        """The number to send to, in international digits, or '': the contact's WhatsApp
        number where a module adds one, else the employee's work mobile and phone,
        else the contact's phone."""
        self.ensure_one()
        user = self.sudo()
        partner = user.partner_id
        candidates = []
        if 'whatsapp_number' in partner._fields:
            candidates.append(partner.whatsapp_number)
        employee = user.employee_id if 'employee_id' in user._fields else False
        if employee:
            candidates += [employee.mobile_phone, employee.work_phone]
        candidates.append(partner.phone)
        country = partner.country_id or user.company_id.country_id
        for raw in candidates:
            number = international_digits(raw, country.phone_code if country else 0)
            if number:
                return number
        return ''

    def _whatsapp_reset_message(self, link):
        self.ensure_one()
        hours = self.env['ir.config_parameter'].sudo().get_int(
            'auth_signup.reset_password.validity.hours') or 4
        return _(
            "Hello %(name)s,\n\nSet a new password for your %(company)s login (%(login)s) "
            "with this link:\n%(link)s\n\nThe link works once, within %(hours)s hours.",
            name=self.name, company=self.company_id.name, login=self.login, link=link, hours=hours)
