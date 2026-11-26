# -*- coding: utf-8 -*-
"""The manufacturing order's side of the Daily Production Report.

Everything the two printed sheets say is assembled here, by `_lab_sheet`, as one
plain dictionary. The templates only lay it out, and the tests read the
dictionary rather than a PDF - what a sheet says can be checked without
printing one.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.addons.sale_custom.models.account_move import dental_palmer

YES_NO = [('Yes', 'Yes'), ('No', 'No')]

# The answers the sheet's heading asks for. They are the ORDER's - the case came
# with a wax bite or it did not, whichever line of the order is being made - so
# the manufacturing order shows the order's and writes back to it.
ORDER_ANSWERS = {
    'lab_impression_type': 'impression_type',
    'lab_wax_bite': 'wax_bite',
    'lab_opposing_model': 'opposing_model',
    'lab_pre_cutting_model': 'pre_cutting_model',
    'lab_impression_tray': 'impression_tray',
}

# The order's priorities that are rushed work, as sale_custom's RUSH: emergency (the
# patient in the chair, from lab_order_control) above urgent.
RUSH_LABELS = {'emergency': 'EMERGENCY', 'urgent': 'URGENT'}


class MrpProduction(models.Model):
    _inherit = 'mrp.production'

    lab_impression_type = fields.Selection(
        selection=lambda self: self.env['sale.order']._fields['impression_type']
        ._description_selection(self.env),
        string='Impression Type', compute='_compute_lab_order_answers',
        inverse=lambda self: self._lab_write_order_answer('lab_impression_type'))
    lab_wax_bite = fields.Selection(
        YES_NO, string='Wax Bite', compute='_compute_lab_order_answers',
        inverse=lambda self: self._lab_write_order_answer('lab_wax_bite'))
    lab_opposing_model = fields.Selection(
        YES_NO, string='Opposing Model', compute='_compute_lab_order_answers',
        inverse=lambda self: self._lab_write_order_answer('lab_opposing_model'))
    lab_pre_cutting_model = fields.Selection(
        YES_NO, string='Pre Cutting Model', compute='_compute_lab_order_answers',
        inverse=lambda self: self._lab_write_order_answer('lab_pre_cutting_model'))
    lab_impression_tray = fields.Selection(
        selection=lambda self: self.env['sale.order']._fields['impression_tray']
        ._description_selection(self.env),
        string='Impression Tray', compute='_compute_lab_order_answers',
        inverse=lambda self: self._lab_write_order_answer('lab_impression_tray'))
    lab_teeth = fields.Char(related='sale_line_id.teeth', string='Teeth (FDI)')

    @api.depends('sale_id.impression_type', 'sale_id.wax_bite', 'sale_id.opposing_model',
                 'sale_id.pre_cutting_model', 'sale_id.impression_tray')
    def _compute_lab_order_answers(self):
        for production in self:
            order = production.sale_id.sudo()
            for name, source in ORDER_ANSWERS.items():
                production[name] = order[source] if order else False

    def _lab_write_order_answer(self, name):
        """Written to the order, where the fact lives.

        One answer at a time: a single inverse for all five read the other four
        back from a cache that did not hold them, and blanked them on the order.

        sudo: the bench that notices the opposing model is missing has no right to
        edit a sale order, and should not need one to say so. Five answers about
        what came in the bag are all this can reach.
        """
        source = ORDER_ANSWERS[name]
        for production in self:
            order = production.sale_id.sudo()
            if order and order[source] != production[name]:
                order[source] = production[name]

    # ------------------------------------------------------------------ printing
    def action_print_production_report(self):
        return self.env.ref(
            'lab_production_sheet.action_report_production_report').report_action(self)

    def action_print_job_sheet(self):
        """The job sheets of these jobs - from the form, or the jobs ticked in the
        list. A cancelled job is left out: its sheet would send a dead case to a
        bench."""
        jobs = self.filtered(lambda p: p.state != 'cancel')
        if not jobs:
            raise UserError(_("Nothing to print: the selected jobs are cancelled."))
        return self.env.ref(
            'lab_production_sheet.action_report_job_sheet').report_action(jobs)

    # ------------------------------------------------------------- what it says
    @api.model
    def _lab_sheet_teeth(self, text):
        """The teeth of a line, in the four cells of the cross.

        Palmer, as the lab's invoices draw it (sale_custom's `dental_palmer`): the
        cell is the quadrant, so a tooth is written by its own number, 7 and not 17,
        and a milk tooth by its letter A-E. Upper right and lower right read towards
        the midline (8 ... 1), the left side away from it (1 ... 8). What is not a
        tooth is kept in `rest`, printed under the cross. (client, 2026-11-24: "not
        needed first digit")
        """
        cross = dental_palmer(text) or {}
        return {
            'upper_right': cross.get('ur', ''), 'upper_left': cross.get('ul', ''),
            'lower_right': cross.get('lr', ''), 'lower_left': cross.get('ll', ''),
            'rest': cross.get('rest', '') if cross else (text or '').strip(),
        }

    @api.model
    def _lab_sheet_label(self, name, department):
        """A step's name as the sheet prints it: "Metal/Trimming" is the Trimming
        row of the Metal table, and the table already says Metal.

        The lab names a bench that several departments have as Department/Row.
        The older spelling, "Metal Trimming", is read the same way.
        """
        name = (name or '').strip()
        dept = (department.name or '').strip().lower()
        if not dept:
            return name
        if name.lower().startswith(dept + '/'):
            return name[len(dept) + 1:].strip()
        words = name.split()
        if len(words) > 1:
            first = words[0].lower()
            if first == dept or (len(first) >= 4 and dept.startswith(first)):
                return ' '.join(words[1:])
        return name

    def _lab_sheet_step(self, step):
        """One step: who did it, and when they handed it on."""
        Station = self.env['lab.station']
        people = step.bench_user_id | step.bench_helper_ids | step.finisher_user_id
        if not people and step.state == 'done':
            people = step.handed_over_by_id or step.accepted_by_id
        when = step.handed_over_at or (step.state == 'done' and step.date_finished) or False
        local = Station._lab_time(when) if when else False
        technician = ' / '.join(people.mapped('name'))
        # Reassigned: the sheet names who worked on it first as well - the paper
        # version had both signatures in the box. (client, 2026-11-26)
        if 'reassigned' in step._fields and step.reassigned and step.initial_user_ids:
            technician = _("%(now)s (first: %(first)s)", now=technician,
                           first=' / '.join(step.initial_user_ids.sorted('name').mapped('name')))
        return {
            'label': self._lab_sheet_label(step.name, step.workcenter_id.department_id),
            'technician': technician,
            'date': local.strftime('%d-%m-%Y') if local else '',
            'time': local.strftime('%H:%M') if local else '',
            'remarks': (step['remarks'] or '') if 'remarks' in step._fields else '',
            'done': step.state == 'done',
        }

    def _lab_sheet_materials(self):
        """{department id: "Glaze Powder 0.25 g; ..."} from the order's components.

        A component belongs to the department its operation is done in when the
        bill of materials says which operation consumes it, and otherwise to the
        department its category is kept by - ceramic powder is Ceramic's whoever
        opened the jar. What was actually used once it is recorded, what the bill
        of materials expects until then.
        """
        self.ensure_one()
        totals = {}
        for move in self.move_raw_ids.filtered(lambda m: m.state != 'cancel'):
            department = move.operation_id.workcenter_id.department_id \
                or move.product_id.categ_id._lab_department()
            quantity = move.quantity if move.state == 'done' else move.product_uom_qty
            key = (department.id, move.product_id, move.uom_id)
            totals[key] = totals.get(key, 0.0) + quantity
        lines = {}
        for (department_id, product, uom), quantity in totals.items():
            lines.setdefault(department_id, []).append('%s %s %s' % (
                product.name, ('%.2f' % quantity).rstrip('0').rstrip('.'), uom.name))
        return {department_id: '; '.join(parts) for department_id, parts in lines.items()}

    def _lab_sheet_sections(self, steps):
        """The department tables, in the order the case walks them."""
        ref = lambda xmlid: self.env.ref(xmlid, raise_if_not_found=False)  # noqa: E731
        ceramic, metal = ref('lab_masters.dept_ceramic'), ref('lab_masters.dept_metal')
        cadcam = ref('lab_masters.dept_cadcam')
        printing, milling = ref('lab_masters.wc_resin_printing'), ref('lab_masters.wc_milling')
        benches = steps.workcenter_id
        has_metal = bool(metal and metal in benches.department_id)
        materials = self._lab_sheet_materials()

        sections = []
        for step in steps.filtered(lambda s: s.workcenter_id.sheet_position == 'section'):
            department = step.workcenter_id.department_id
            if not sections or sections[-1]['department_id'] != department.id:
                subtitle = ''
                if ceramic and department == ceramic:
                    subtitle = _('Metal Ceramic') if has_metal else _('Metal free Ceramic')
                elif cadcam and department == cadcam:
                    subtitle = ' / '.join(filter(None, [
                        printing and printing in benches and _('3D'),
                        milling and milling in benches and _('Zirconia')]))
                sections.append({
                    'department_id': department.id,
                    'title': (department.name or _('Production')).upper(),
                    'subtitle': subtitle,
                    'rows': [],
                    # asked for once per department, however many tables it has
                    'materials': materials.pop(department.id, ''),
                })
            sections[-1]['rows'].append(self._lab_sheet_step(step))
        for section in sections:
            section['has_remarks'] = any(row['remarks'] for row in section['rows'])
        # Material a department used without having a table on this sheet - plaster
        # at model preparation, a box at packing - is not dropped.
        other = '; '.join(text for text in materials.values() if text)
        return sections, other

    def _lab_sheet_officer(self):
        """The marketing officer: who took the case, else whose route the clinic is on."""
        order = self.sale_id.sudo()
        if not order:
            return ''
        visit = order.visit_id if 'visit_id' in order._fields else False
        person = (visit and visit.user_id) or order.team_id.user_id or order.user_id
        return person.name or ''

    def _lab_sheet_prepared_by(self):
        """Who prepared the sheet: whoever is responsible for the order if somebody
        was named, else the person who registered the case.

        Not the order's creator on its own: the manufacturing order is raised by the
        system when a case is confirmed, and "Prepared By OdooBot" names nobody.
        """
        order = self.sale_id.sudo()
        people = self.user_id
        if order:
            if 'entered_by_id' in order._fields:
                people |= order.entered_by_id
            people |= order.create_uid
        people |= self.create_uid
        person = people.filtered(lambda u: not u._is_superuser() and u.active and not u.share)[:1]
        return person.name or ''

    def _lab_sheet(self):
        """Everything either sheet prints about this order."""
        self.ensure_one()
        Station = self.env['lab.station']
        order = self.sale_id.sudo()
        line = self.sale_line_id.sudo()
        steps = self.workorder_ids.filtered(lambda s: s.state != 'cancel').sorted(
            lambda s: (s.sequence, s.id))
        sections, other_materials = self._lab_sheet_sections(steps)

        def named(position):
            found = steps.filtered(lambda s: s.workcenter_id.sheet_position == position)
            return self._lab_sheet_step(found[:1]) if found else False

        created = Station._lab_time(self.create_date) if self.create_date else False
        choices = lambda field: [tuple(choice) for choice in  # noqa: E731
                                 self._fields[field]._description_selection(self.env)]
        due = order and ('date_due' in order._fields and order.date_due
                         or order.commitment_date) or False
        # How rushed the work is, in print big enough to be seen across the bench:
        # the order's Priority, or a line ticked Urgent on a normal order.
        priority = order.priority if order and 'priority' in order._fields else False
        priority_label = priority and dict(
            order._fields['priority']._description_selection(self.env)).get(priority, '') or ''
        rush = RUSH_LABELS.get(priority) or (line and line.is_urgent and RUSH_LABELS['urgent']) or ''
        bag = []
        if order:
            for name in ('is_screw', 'is_bite', 'is_bands', 'is_wires', 'is_teeth', 'is_facebow',
                         'is_3d_model_print', 'is_others'):
                if name in order._fields and order[name]:
                    bag.append(order._fields[name].string)
        return {
            'file_no': self.name,
            'order_no': order.name or self.origin or '',
            'clinic': order.partner_id.display_name or '',
            'patient': order.patient or '',
            'age': order.age or '',
            'gender': order and order.gender and dict(
                order._fields['gender']._description_selection(self.env)).get(order.gender) or '',
            'impression_type': self.lab_impression_type or '',
            'impression_types': choices('lab_impression_type'),
            'wax_bite': self.lab_wax_bite or '',
            'opposing_model': self.lab_opposing_model or '',
            'pre_cutting_model': self.lab_pre_cutting_model or '',
            'impression_tray': self.lab_impression_tray or '',
            'impression_trays': choices('lab_impression_tray'),
            'prepared_by': self._lab_sheet_prepared_by(),
            'date': created.strftime('%d-%m-%Y') if created else '',
            'time': created.strftime('%H:%M') if created else '',
            'work': self.product_id.display_name or '',
            'quantity': ('%.2f' % self.product_qty).rstrip('0').rstrip('.'),
            'uom': self.uom_id.name or '',
            'arch': dict(self._fields['ul']._description_selection(self.env)).get(self.ul, ''),
            'teeth': self._lab_sheet_teeth(line.teeth),
            'shade': self.color_scheme or '',
            # the day the work is owed: the order's Due Date (sale_custom sets it on
            # confirming - the same day for rushed work, two days later otherwise)
            'due': due and fields.Date.to_date(due).strftime('%d-%m-%Y') or '',
            'priority': priority_label,
            'rush': rush,
            'bag': ', '.join(bag),
            'modification': order.modification or '',
            'instruction': order.instruction or '',
            'model': named('model'),
            'sections': sections,
            'other_materials': other_materials,
            'checked': named('check'),
            'packed': named('packed'),
            'officer': self._lab_sheet_officer(),
        }
