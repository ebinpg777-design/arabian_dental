# -*- coding: utf-8 -*-
from odoo import _, fields, models
from odoo.tools import SQL

# how the report tells one person from another: the name without case, spaces and
# punctuation (see sale.technician.work), so "ABHAY." and "ABHAY." are one technician
PERSON_KEY = "COALESCE(NULLIF(UPPER(REGEXP_REPLACE(%s, '[^[:alnum:]]', '', 'g')), ''), %s::text)"


class ResPartner(models.Model):
    _inherit = 'res.partner'

    technician_work_count = fields.Integer(
        string='Works', compute='_compute_technician_work_count',
        help="Order lines of the cases this technician is named on.")

    def _technician_person_ids(self):
        """{contact id: the contact the Technician Work report counts this person
        under} - the oldest technician contact with the same name."""
        if not self.ids:
            return {}
        self.env.cr.execute(SQL("""
            SELECT p.id, CASE WHEN p.is_technician THEN (
                       SELECT MIN(o.id) FROM res_partner o WHERE o.is_technician AND %(other)s = %(mine)s
                   ) END
              FROM res_partner p WHERE p.id IN %(ids)s
        """, other=SQL(PERSON_KEY, SQL('o.name'), SQL('o.id')), mine=SQL(PERSON_KEY, SQL('p.name'), SQL('p.id')),
            ids=tuple(self.ids)))
        return {partner_id: person_id or partner_id for partner_id, person_id in self.env.cr.fetchall()}

    def _compute_technician_work_count(self):
        technicians = self.filtered('is_technician')
        persons = technicians._technician_person_ids() if technicians else {}
        counts = {}
        if persons:
            counts = {person.id: n for person, n in self.env['sale.technician.work']._read_group(
                [('technician_id', 'in', list(set(persons.values())))], ['technician_id'], ['__count'])}
        for partner in self:
            partner.technician_work_count = counts.get(persons.get(partner._origin.id), 0)

    def action_view_technician_work(self):
        """The Technician Work screen, on this technician."""
        self.ensure_one()
        person = self._technician_person_ids().get(self.id, self.id)
        return {
            'type': 'ir.actions.client', 'tag': 'lab_technician_work_board',
            'name': _('Work of %s', self.display_name),
            'context': {'technician_ids': [person]},
        }
