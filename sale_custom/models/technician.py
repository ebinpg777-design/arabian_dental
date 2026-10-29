# -*- coding: utf-8 -*-
"""Technicians: the contacts an order's Technicians field picks from (client,
2026-10-29: "Add a boolean in partner that is_technician. filter the partner with
is_technician ... Provide a new widget button of creating new technician from SO").

The order form's `technician_tags` widget (static/src/js/technician_tags_field.js)
calls the methods here: what a typed name may already be, and a technician made
without a second spelling of somebody who exists.
"""
import re
from odoo import _, api, fields, models
from odoo.exceptions import UserError

def technician_key(name):
    """A name the way two spellings of it agree: 'Rana.' and 'RANA' are one."""
    return re.sub(r'[^0-9A-Z]', '', (name or '').upper())


def technician_name(name):
    """Written as the lab writes technicians: upper case, single spaces."""
    return ' '.join((name or '').split()).upper()


class ResPartner(models.Model):
    _inherit = 'res.partner'

    is_technician = fields.Boolean(
        string='Technician', index=True,
        help="Offered in the Technicians of a sale order.")

    @api.model
    def lab_technician_lookup(self, name, limit=6):
        """Who a name being typed may already be: technicians whose name holds it (or
        is held by it), then any other contact spelled exactly so. Each with the
        number of orders naming it, so the busy one is recognised at a glance."""
        key = technician_key(name)
        if len(key) < 2:
            return []
        self.env.cr.execute("""
            SELECT p.id, p.is_technician,
                   regexp_replace(upper(p.name), '[^0-9A-Z]', '', 'g') AS k
              FROM res_partner p
             WHERE p.active AND p.name IS NOT NULL
               AND (   (p.is_technician AND (regexp_replace(upper(p.name), '[^0-9A-Z]', '', 'g') LIKE %(like)s
                                             OR %(key)s LIKE '%%' || regexp_replace(upper(p.name), '[^0-9A-Z]', '', 'g') || '%%'))
                    OR regexp_replace(upper(p.name), '[^0-9A-Z]', '', 'g') = %(key)s)
        """, {'like': '%' + key + '%', 'key': key})
        rows = [r for r in self.env.cr.fetchall() if r[2]]
        if not rows:
            return []
        ids = [r[0] for r in rows]
        self.env.cr.execute("""
            SELECT partner_id, count(*) FROM sale_order_technician_rel
             WHERE partner_id IN %s GROUP BY partner_id
        """, (tuple(ids),))
        orders = dict(self.env.cr.fetchall())
        partners = self.browse(ids).exists()
        names = {p.id: p.display_name for p in partners}
        out = [{
            'id': pid, 'name': names[pid], 'technician': bool(tech),
            'exact': k == key, 'orders': orders.get(pid, 0),
        } for pid, tech, k in rows if pid in names]
        out.sort(key=lambda r: (not r['exact'], not r['technician'], -r['orders'], r['name']))
        return out[:limit]

    @api.model
    def lab_create_technician(self, name, phone=False):
        """A new technician - unless one is already spelled this way, which is
        returned instead: the order then names the person it always named."""
        clean = technician_name(name)
        key = technician_key(clean)
        if not key:
            raise UserError(_("Type the technician's name."))
        for same in self.search([('is_technician', '=', True), ('name', '=ilike', clean[:1] + '%')]):
            if technician_key(same.name) == key:
                return {'id': same.id, 'name': same.display_name, 'created': False}
        partner = self.create({
            'name': clean, 'is_technician': True,
            'phone': (phone or '').strip() or False,
        })
        return {'id': partner.id, 'name': partner.display_name, 'created': True}

    def lab_mark_technician(self):
        """An existing contact, from now on offered as a technician."""
        self.filtered(lambda p: not p.is_technician).write({'is_technician': True})
        return True

