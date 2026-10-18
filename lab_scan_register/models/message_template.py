# -*- coding: utf-8 -*-
"""The messages the lab sends back to doctors about their scans, kept as templates.

Plain text with a few placeholders in braces. Rendering only swaps a known name for
its value: there is no expression language, so a template typed by anybody cannot
reach into the database - "{scan.__class__}" stays exactly that.
"""
import re

from odoo import api, fields, models

PLACEHOLDER = re.compile(r'\{(\w+)\}')
PLACEHOLDERS = {
    'patients': "the patients' names, e.g. 'Anu, Binu and Cini'",
    'patient_count': "how many patients",
    'scan': "the register's reference",
    'clinic': "the doctor or clinic",
    'doctor': "the doctor's name as received, else the clinic",
    'sender': "who posted it in the group",
    'delivery': "the earliest delivery date asked for",
    'orders': "the order numbers, once made",
    'channel': "the group, mailbox or platform",
    'company': "the lab's name",
}


class ScanMessageTemplate(models.Model):
    _name = 'lab.scan.message.template'
    _description = 'Scan WhatsApp Message Template'
    _order = 'sequence, id'

    name = fields.Char('Template', required=True, translate=True)
    sequence = fields.Integer(default=10)
    body = fields.Text('Message', required=True, translate=True,
                       help="Placeholders in braces are filled in from the register: "
                            + ', '.join('{%s}' % k for k in PLACEHOLDERS))
    active = fields.Boolean(default=True)

    @api.model
    def _values(self, scan):
        names = [n for n in scan.line_ids.mapped('patient_name') if n]
        if len(names) > 1:
            patients = ', '.join(names[:-1]) + ' and ' + names[-1]
        else:
            patients = names[0] if names else 'your patient'
        return {
            'patients': patients,
            'patient_count': str(len(scan.line_ids)),
            'scan': scan.name or '',
            'clinic': scan.partner_id.name or scan.clinic_name or '',
            'doctor': scan.doctor_name or scan.partner_id.name or '',
            'sender': scan.member_id.name or scan.sender_name or '',
            'delivery': scan.date_delivery.strftime('%d-%m-%Y') if scan.date_delivery else '',
            'orders': ', '.join(scan.line_ids.sale_order_id.mapped('name')),
            'channel': scan.channel_id.name or '',
            'company': scan.company_id.name or '',
        }

    def render(self, scan):
        """The template's text for one register. Unknown placeholders are left as
        they were typed, so a typo shows instead of vanishing."""
        self.ensure_one()
        values = self._values(scan)
        return PLACEHOLDER.sub(lambda m: values.get(m.group(1), m.group(0)), self.body or '').strip()
