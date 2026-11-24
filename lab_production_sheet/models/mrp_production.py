# -*- coding: utf-8 -*-
"""The manufacturing order's side of the Daily Production Report.

Everything the two printed sheets say is assembled here, by `_lab_sheet`, as one
plain dictionary. The templates only lay it out, and the tests read the
dictionary rather than a PDF - what a sheet says can be checked without
printing one.
"""
import re

from odoo import _, api, fields, models

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

# FDI: the first digit is the quadrant (5-8 for baby teeth), the second the tooth.
TOOTH = re.compile(r'(?<!\d)([1-8])([1-8])(?!\d)')
# The four cells of the cross a dentist draws, read facing the patient: the
# patient's right is on the left of the page.
QUADRANTS = {'1': 'upper_right', '5': 'upper_right', '2': 'upper_left', '6': 'upper_left',
             '4': 'lower_right', '8': 'lower_right', '3': 'lower_left', '7': 'lower_left'}


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
        return self.env.ref(
            'lab_production_sheet.action_report_job_sheet').report_action(self)

    # ------------------------------------------------------------- what it says
    @api.model
    def _lab_sheet_teeth(self, text):
        """The teeth of a line, in the four cells of the cross.

        Upper right and lower right read towards the midline (18 ... 11), the left
        side away from it (21 ... 28), as on any chart.
        """
        cells = {'upper_right': [], 'upper_left': [], 'lower_right': [], 'lower_left': []}
        for quadrant, tooth in TOOTH.findall(text or ''):
            number = quadrant + tooth
            cell = cells[QUADRANTS[quadrant]]
            if number not in cell:
                cell.append(number)
        for name, numbers in cells.items():
            numbers.sort(reverse=name.endswith('right'))
        return {name: ' '.join(numbers) for name, numbers in cells.items()}

    @api.model
    def _lab_sheet_label(self, name, department):
        """A step's name as the sheet prints it: "Metal Trimming" is the Trimming
        row of the Metal table, and the table already says Metal."""
        words = (name or '').split()
        dept = (department.name or '').strip().lower()
        if len(words) > 1 and dept:
            first = words[0].lower()
            if first == dept or (len(first) >= 4 and dept.startswith(first)):
                return ' '.join(words[1:])
        return name or ''

    def _lab_sheet_step(self, step):
        """One step: who did it, and when they handed it on."""
        Station = self.env['lab.station']
        people = step.bench_user_id | step.finisher_user_id
        if not people and step.state == 'done':
            people = step.handed_over_by_id or step.accepted_by_id
        when = step.handed_over_at or (step.state == 'done' and step.date_finished) or False
        local = Station._lab_time(when) if when else False
        return {
            'label': self._lab_sheet_label(step.name, step.workcenter_id.department_id),
            'technician': ' / '.join(people.mapped('name')),
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
            'teeth_text': line.teeth or '',
            'teeth': self._lab_sheet_teeth(line.teeth),
            'shade': self.color_scheme or '',
            'due': due and fields.Date.to_date(due).strftime('%d-%m-%Y') or '',
            'urgent': bool(line and line.is_urgent),
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
