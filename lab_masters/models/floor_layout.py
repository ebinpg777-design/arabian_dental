# -*- coding: utf-8 -*-
"""The floor as the lab's own Daily Production Report lays it out.

The first layout (2026-09-25) followed the SOP sheets: fifty-seven benches, one
for every stage a document named, and a metal ceramic crown crossing thirty-two
of them. Nobody on the floor fills in thirty-two lines. The paper the technicians
actually sign has four versions - metal and ceramic, zirconia, orthodontic,
acrylic - and between them the rows that are the benches now: each sheet's model
preparation, the rows of each department's table, and the final check and the
packing the acrylic sheet signs for at its foot. Thirty-one in all.
(client, 2026-11-24)

What a merged bench carries
---------------------------
"Opaque & Buildup" is nine of the old stages. Its minutes are the HANDS-ON
minutes of those nine - the build-ups and the model check, not the firings in
between - because the bench is costed at the labour rate and the furnace was
never costed at all (`mrp_workcenter_data.xml` says why). Adding the firing
minutes in would have charged wages for a furnace cooling, and moved every
figure on the Case Cost screen by an amount nobody asked for. A bench that is a
machine from end to end - Sintering, Casting, Curing - still carries every
minute, at no rate, exactly as before.

So the labour cost of each of the fifteen products is the same before and after,
to the paisa, and `TestFloorLayout` holds it there.

Opaque and Buildup, apart (client, 2026-11-26)
----------------------------------------------
The ceramic table's first row was one bench, "Opaque & Buildup". The lab works it
as two - one group lays the opaque, another builds up - so it is two benches and
two steps now. The minutes go where the old stages say they belong: the model
check and the opaque coats to Opaque, the two build-ups to Buildup. Same rate on
both, so again no product's labour cost moves.
"""
import logging
from collections import OrderedDict

from odoo import api, models

_logger = logging.getLogger(__name__)

LABOUR = 105.77

# key, xmlid, the row's name on the sheet, code, department xmlid, hourly rate for a
# bench that is new, and whether the row's name is one several departments share.
#
# A bench that already exists keeps the rate the lab has on it.
#
# "Trimming" is a row of the metal table, the ceramic table, the orthodontic sheet
# and the acrylic sheet, and they are four different benches with four different
# people at them - as are the model preparation of each sheet, the two polishings
# and the two packings. Those are named for their department as well, the way the
# lab asked: "Metal/Trimming", "Acrylic/Model Preparation". A row only one
# department has keeps its plain name. (client, 2026-11-24)
BENCHES = [
    # Wax Up's model preparation is three benches since 2026-11-27 ("Split Wax Up/Model
    # Preparation to Cutting, Scooping, Blocking"): the record that was all of it is
    # Cutting, so the key and xmlid still say model preparation
    ('model_preparation', 'wc_model_preparation', 'Cutting', 'WX-01', 'dept_wax_up', LABOUR, False),
    ('scooping', 'wc_wax_scooping', 'Scooping', 'WX-04', 'dept_wax_up', LABOUR, False),
    ('blocking', 'wc_wax_blocking', 'Blocking', 'WX-05', 'dept_wax_up', LABOUR, False),
    # ---- CAD / CAM: the white sheet's "3D" column and the blue sheet
    ('scanning', 'wc_scanning', 'Scanning', 'CC-01', 'dept_cadcam', 0.0, False),
    ('designing', 'wc_designing', 'Designing', 'CC-02', 'dept_cadcam', 0.0, False),
    ('printing', 'wc_resin_printing', 'Printing', 'CC-03', 'dept_cadcam', 0.0, False),
    ('light_curing', 'wc_light_curing', 'Light Curing', 'CC-04', 'dept_cadcam', 0.0, False),
    ('milling', 'wc_milling', 'Milling', 'CC-05', 'dept_cadcam', 0.0, False),
    ('sintering', 'wc_sintering', 'Sintering', 'CC-06', 'dept_cadcam', 0.0, False),
    # ---- Wax up
    ('spruing', 'wc_spruing', 'Spruing', 'WX-02', 'dept_wax_up', LABOUR, False),
    ('investing', 'wc_investing', 'Investing', 'WX-03', 'dept_wax_up', LABOUR, False),
    # ---- Metal
    # Sintering before the casting and Printing after it (client, 2026-11-27: "Add two
    # workcentres before and after Metal/Casting: Metal/Sintering, Metal/Casting,
    # Metal/printing"). Named for the department: CAD/CAM has a Sintering and a
    # Printing of its own.
    ('metal_sintering', 'wc_metal_sintering', 'Sintering', 'MT-04', 'dept_metal', 0.0, True),
    # "Metal/Casting" on the floor, as the lab named it, and beside Metal/Sintering and
    # Metal/Printing: never put back to a plain "Casting"
    ('casting', 'wc_casting', 'Casting', 'MT-01', 'dept_metal', 0.0, True),
    ('metal_printing', 'wc_metal_printing', 'Printing', 'MT-05', 'dept_metal', 0.0, True),
    ('metal_trimming', 'wc_metal_trimming', 'Trimming', 'MT-02', 'dept_metal', LABOUR, True),
    ('metal_polishing', 'wc_metal_polishing', 'Polishing', 'MT-03', 'dept_metal', LABOUR, True),
    # ---- Ceramic
    # one bench until 2026-11-26 ("Opaque & Buildup"): the record that was both is
    # the Opaque bench now, which is why its xmlid still says both
    ('opaque', 'wc_opaque_buildup', 'Opaque', 'CR-01', 'dept_ceramic', LABOUR, False),
    ('buildup', 'wc_ceramic_buildup', 'Buildup', 'CR-02', 'dept_ceramic', LABOUR, False),
    ('ceramic_trimming', 'wc_trimming_carving', 'Trimming', 'CR-03', 'dept_ceramic', LABOUR, True),
    ('ceramic_finishing', 'wc_ceramic_finishing', 'Finishing', 'CR-04', 'dept_ceramic', LABOUR, False),
    # ---- Orthodontic
    ('ortho_model_preparation', 'wc_ortho_model_prep', 'Model Preparation', 'OR-01', 'dept_ortho', LABOUR, True),
    ('wire_bending', 'wc_wire_bending', 'Wire Bending', 'OR-02', 'dept_ortho', LABOUR, False),
    ('acrylization', 'wc_acrylisation', 'Acrylization', 'OR-03', 'dept_ortho', LABOUR, False),
    ('ortho_trimming', 'wc_ortho_trimming', 'Trimming', 'OR-04', 'dept_ortho', LABOUR, True),
    # ---- Acrylic
    ('acrylic_model_preparation', 'wc_acr_model_prep', 'Model Preparation', 'AC-01', 'dept_acrylic', LABOUR, True),
    ('special_tray', 'wc_special_tray', 'Special Tray', 'AC-02', 'dept_acrylic', LABOUR, False),
    ('wax_bite', 'wc_wax_bite', 'Wax Bite', 'AC-03', 'dept_acrylic', LABOUR, False),
    ('teeth_setting', 'wc_teeth_setting', 'Teeth Setting', 'AC-04', 'dept_acrylic', LABOUR, False),
    # after the try-in: the trial denture carved, then waxed up for flasking. The lab's
    # own names and spelling - it made these two benches itself. (client, 2026-11-25)
    ('trail_carving', 'wc_trail_carving', 'Trail Carving', 'AC-11', 'dept_acrylic', LABOUR, False),
    ('acrylic_wax_up', 'wc_acrylic_wax_up', 'Wax Up', 'AC-12', 'dept_acrylic', LABOUR, False),
    ('flasking', 'wc_flasking', 'Flasking', 'AC-05', 'dept_acrylic', LABOUR, False),
    ('de_waxing', 'wc_acr_dewaxing', 'De Waxing', 'AC-06', 'dept_acrylic', LABOUR, False),
    ('acrylic_packing', 'wc_packing_material', 'Packing', 'AC-07', 'dept_acrylic', LABOUR, True),
    ('curing', 'wc_curing', 'Curing', 'AC-08', 'dept_acrylic', 0.0, False),
    ('acrylic_trimming', 'wc_acr_trimming', 'Trimming', 'AC-09', 'dept_acrylic', LABOUR, True),
    ('acrylic_polishing', 'wc_acr_polishing', 'Polishing', 'AC-10', 'dept_acrylic', LABOUR, True),
    # ---- The foot of the sheet: "Final Checked by", "Packed by". The packing
    # department's own Packing is not prefixed with itself.
    ('final_check', 'wc_quality_control', 'Final Check', 'QC-01', 'dept_qc', LABOUR, False),
    ('packing', 'wc_packing', 'Packing', 'PK-01', 'dept_packing', LABOUR, False),
]


def bench_name(row, department, shared):
    """A bench's name: its row on the sheet, with its department in front when
    other departments have a row of the same name."""
    if shared and department and department.name:
        return '%s/%s' % (department.name.strip(), row)
    return row


# Where each bench of the old layout went: the new stages that took its work,
# the likeliest first. More than one, because the same old bench served several
# departments - sand blasting was a metal stage that the denture route borrowed,
# and a denture has no metal trimming to fold it into.
STAGE_OF = {
    'wc_model_preparation': ['model_preparation'],
    # the old benches that came back as stages of their own on 2026-11-27; a route
    # without them still folds them into the model preparation
    'wc_margin_scooping': ['scooping', 'model_preparation'],
    'wc_undercut_blocking': ['blocking', 'model_preparation'],
    # the twin block is articulated at the orthodontic model bench
    'wc_articulation': ['model_preparation', 'ortho_model_preparation'],
    'wc_acr_model_prep': ['acrylic_model_preparation'],
    'wc_ortho_model_prep': ['ortho_model_preparation'],
    'wc_spruing': ['spruing'],
    'wc_investing': ['investing'],
    'wc_scanning': ['scanning'],
    'wc_designing': ['designing'],
    'wc_resin_printing': ['printing'],
    'wc_light_curing': ['light_curing'],
    'wc_milling': ['milling'],
    'wc_sintering': ['sintering'],
    'wc_dewaxing': ['casting'],
    'wc_burn_out': ['casting'],
    'wc_casting': ['casting'],
    'wc_pressing': ['casting'],
    'wc_quenching': ['metal_trimming'],
    'wc_sand_blasting': ['metal_trimming', 'acrylic_trimming', 'ceramic_trimming'],
    'wc_metal_model_prep': ['metal_trimming'],
    'wc_metal_polishing': ['metal_polishing'],
    'wc_model_checking': ['opaque'],
    'wc_wash_opaque': ['opaque'],
    'wc_opaque_firing': ['opaque'],
    'wc_second_opaque': ['opaque'],
    'wc_wash_firing': ['opaque'],
    'wc_first_build_up': ['buildup'],
    'wc_first_build_firing': ['buildup'],
    'wc_second_build_up': ['buildup'],
    'wc_second_build_firing': ['buildup'],
    'wc_trimming_carving': ['ceramic_trimming'],
    'wc_correction_firing': ['ceramic_trimming'],
    'wc_staining_glazing': ['ceramic_finishing'],
    'wc_final_firing': ['ceramic_finishing'],
    'wc_micro_blasting': ['ceramic_finishing'],
    'wc_teeth_setting': ['teeth_setting'],
    'wc_acr_wax_up': ['wax_bite'],
    'wc_flasking': ['flasking'],
    'wc_acr_dewaxing': ['de_waxing'],
    'wc_packing_material': ['acrylic_packing'],
    'wc_curing': ['curing'],
    'wc_acr_trimming': ['acrylic_trimming'],
    'wc_acr_polishing': ['acrylic_polishing'],
    'wc_wire_bending': ['wire_bending'],
    'wc_soldering': ['wire_bending'],
    'wc_sealing': ['acrylization'],
    'wc_acrylisation': ['acrylization'],
    'wc_pressing_ortho': ['acrylization'],
    'wc_ortho_trimming': ['ortho_trimming'],
    'wc_ortho_polishing': ['ortho_trimming'],
    'wc_quality_control': ['final_check'],
    'wc_packing': ['packing'],
}

FOOT = ['final_check', 'packing']

# The rows of each sheet, in the order the sheet prints them.
ROUTES = {
    # the blue sheet
    'zirconia': ['model_preparation', 'scooping', 'blocking', 'scanning', 'designing', 'milling', 'sintering',
                 'opaque', 'buildup', 'ceramic_trimming', 'ceramic_finishing'] + FOOT,
    # the white sheet, every section of it
    'metal_ceramic': ['model_preparation', 'scooping', 'blocking', 'scanning', 'designing', 'printing', 'light_curing',
                      'spruing', 'investing', 'metal_sintering', 'casting', 'metal_printing', 'metal_trimming', 'metal_polishing',
                      'opaque', 'buildup', 'ceramic_trimming', 'ceramic_finishing'] + FOOT,
    # the white sheet without its ceramic section
    'full_metal': ['model_preparation', 'scooping', 'blocking', 'scanning', 'designing', 'printing', 'light_curing',
                   'spruing', 'investing', 'metal_sintering', 'casting', 'metal_printing', 'metal_trimming', 'metal_polishing'] + FOOT,
    # pressed, not cast: the press is the casting of it, and there is no metal to polish
    'pressed': ['model_preparation', 'scooping', 'blocking', 'scanning', 'designing', 'printing', 'light_curing',
                'spruing', 'investing', 'metal_sintering', 'casting', 'metal_printing', 'metal_trimming',
                'opaque', 'buildup', 'ceramic_trimming', 'ceramic_finishing'] + FOOT,
    # the yellow sheet
    'orthodontic': ['ortho_model_preparation', 'wire_bending', 'acrylization',
                    'ortho_trimming'] + FOOT,
    # an Essix splint is formed and trimmed: there is no wire in it
    'splint': ['ortho_model_preparation', 'acrylization', 'ortho_trimming'] + FOOT,
    # the pink sheet
    'acrylic': ['acrylic_model_preparation', 'special_tray', 'wax_bite', 'teeth_setting',
                'trail_carving', 'acrylic_wax_up', 'flasking',
                'de_waxing', 'acrylic_packing', 'curing', 'acrylic_trimming',
                'acrylic_polishing'] + FOOT,
}

BOM_ROUTE = {
    'bom_zirconia_emerald': 'zirconia',
    'bom_zirconia_ruby': 'zirconia',
    'bom_zirconia_diamond': 'zirconia',
    'bom_zirconia_premium': 'zirconia',
    'bom_zirconia_classic': 'zirconia',
    'bom_zirconia_basic': 'zirconia',
    'bom_metal_ceramic': 'metal_ceramic',
    'bom_full_metal': 'full_metal',
    'bom_veneer': 'pressed',
    'bom_hawleys': 'orthodontic',
    'bom_twin_block': 'orthodontic',
    'bom_banded_rme': 'orthodontic',
    'bom_essix': 'splint',
    'bom_rpd': 'acrylic',
    'bom_bps': 'acrylic',
}

OPEN_MO_STATES = ('confirmed', 'progress', 'to_close')


class LabMastersLoader(models.AbstractModel):
    _inherit = 'lab.masters.loader'

    # ------------------------------------------------------------------ benches
    @api.model
    def _floor_benches(self):
        """The benches of the layout, by key: made, renamed or re-coded as needed.

        A bench the lab already has keeps its rate, its technicians, its heads and
        its label - only what the sheet calls it, its code and its place in the
        list change. A new one is made at the rate in the table.
        """
        Workcenter = self.env['mrp.workcenter'].sudo().with_context(active_test=False)
        benches = {}
        for sequence, (key, xmlid, row, code, department, rate, shared) in enumerate(BENCHES, start=1):
            dept = self.env.ref('lab_masters.%s' % department, raise_if_not_found=False)
            bench = self.env.ref('lab_masters.%s' % xmlid, raise_if_not_found=False)
            vals = {'name': bench_name(row, dept, shared), 'code': code,
                    'sequence': sequence * 10, 'active': True}
            if dept:
                vals['department_id'] = dept.id
            if bench:
                changed = {f: v for f, v in vals.items()
                           if (bench[f].id if f == 'department_id' else bench[f]) != v}
                if changed:
                    bench.sudo().write(changed)
            else:
                create = dict(vals, costs_hour=rate)
                # its department's cost centre, as the form's onchange would put it:
                # a bench made here was otherwise left posting its time nowhere
                centre = dept and dept.cost_centre_id
                if centre:
                    create['analytic_distribution'] = {str(centre.id): 100.0}
                bench = Workcenter.create(create)
                self._tag('mrp.workcenter', xmlid, bench)
            benches[key] = bench.sudo()
        return benches

    @api.model
    def _floor_add_after_teeth_setting(self):
        """Trail Carving and Wax Up, right after Teeth Setting, in every bill of
        materials that has Teeth Setting (client, 2026-11-25: "add the operations
        Trail Carving and Wax Up after Teeth setting in all BoMs contains Teeth
        setting").

        The two benches take Teeth Setting's people - its technicians and its leads -
        where nobody is posted yet. A step is added at no minutes, so no product's
        labour cost moves; the steps after it move down. A bill of materials that
        already has either step is left as it is, so this runs again harmlessly (it
        was first done on the live server by hand, the same way).
        Returns how many bills of materials were changed.
        """
        benches = self._floor_benches()
        teeth = benches['teeth_setting']
        new = [benches['trail_carving'], benches['acrylic_wax_up']]
        for bench in new:
            vals = {}
            # both fields come from modules loaded after this one (sale_custom's
            # technicians, the station's leads): not there yet in a fresh install
            for field in ('users', 'head_user_ids'):
                if field in bench._fields and not bench[field]:
                    vals[field] = [(6, 0, teeth[field].ids)]
            if vals:
                bench.write(vals)
        Operation = self.env['mrp.routing.workcenter'].sudo()
        changed = 0
        for bom in self.env['mrp.bom'].sudo().search([('operation_ids.workcenter_id', '=', teeth.id)]):
            steps = bom.operation_ids.sorted(lambda o: (o.sequence, o.id))
            if steps.filtered(lambda o: o.workcenter_id in new[0] | new[1]):
                continue
            setting = steps.filtered(lambda o: o.workcenter_id == teeth)[:1]
            for step in steps.filtered(lambda o: (o.sequence, o.id) > (setting.sequence, setting.id)):
                step.sequence += 10 * len(new)
            for offset, bench in enumerate(new, start=1):
                Operation.create({
                    'bom_id': bom.id, 'name': bench.name, 'workcenter_id': bench.id,
                    'sequence': setting.sequence + 10 * offset, 'time_mode': 'manual',
                    'time_cycle_manual': 0.0,
                })
            changed += 1
        _logger.info("lab_masters: Trail Carving and Wax Up added after Teeth Setting "
                     "in %s bills of materials", changed)
        return changed

    @api.model
    def _floor_slot_after(self, steps, step):
        """The number for a new step right after `step` among `steps` - operations
        of a bill of materials, or work orders of a job: halfway to the one that
        follows. Steps stand ten apart, so that is five on, and nothing else is
        touched (every write to an operation marks the open jobs of its bill of
        materials outdated - renumbering the later steps was most of a minute of
        upgrade). Where two stand shoulder to shoulder, the later ones move down
        ten first."""
        later = steps.filtered(lambda s: (s.sequence, s.id) > (step.sequence, step.id))
        if not later:
            return step.sequence + 10
        gap = min(later.mapped('sequence')) - step.sequence
        if gap < 2:
            for other in later:
                other.sequence += 10
            gap += 10
        return step.sequence + gap // 2

    @api.model
    def _floor_split_opaque_buildup(self):
        """One step "Opaque & Buildup" becomes two, Opaque then Buildup, in every bill
        of materials that has it (client, 2026-11-26: "Split opaque and buildup to two
        work centres and split operations also").

        The bench that was both is the Opaque bench (`_floor_benches` renames it); the
        Buildup bench is new, at the same rate, calendar and cost centre, with the same
        technicians and leads until the lab says who is where. The step's minutes are
        divided the way the old stages had them - the fifteen costed products still
        carry those stages, archived; a product made on a category route has the
        minutes of its route's costed product, and takes that product's division. So
        no product's labour cost moves.

        A bill of materials that already has a Buildup step is left alone: this runs
        again harmlessly. Returns how many were changed.
        """
        benches = self._floor_benches()
        opaque, buildup = benches['opaque'], benches['buildup']
        Operation = self.env['mrp.routing.workcenter'].sudo()
        if not Operation.with_context(active_test=False).search_count(
                [('workcenter_id', '=', buildup.id)], limit=1):
            # a bench nothing stands at yet: the twin of the one it was split from
            vals = {'costs_hour': opaque.costs_hour}
            for field in ('resource_calendar_id', 'analytic_distribution', 'users',
                          'head_user_ids', 'is_finisher', 'scan_confirm'):
                if field not in buildup._fields:
                    continue        # other modules' fields: not there in a fresh install
                if buildup._fields[field].type == 'many2many':
                    if not buildup[field]:
                        vals[field] = [(6, 0, opaque[field].ids)]
                elif buildup._fields[field].type == 'many2one':
                    vals[field] = opaque[field].id
                else:
                    vals[field] = opaque[field]
            buildup.write(vals)
        old_stages = self._floor_old_stages()
        todo, known = [], {}
        for bom in self.env['mrp.bom'].sudo().search(
                [('operation_ids.workcenter_id', '=', opaque.id)]):
            steps = bom.operation_ids
            if steps.filtered(lambda o: o.workcenter_id == buildup):
                continue
            step = steps.filtered(lambda o: o.workcenter_id == opaque).sorted(
                lambda o: (o.sequence, o.id))[:1]
            # what the old stages say, where the product still carries them
            old = Operation.with_context(active_test=False).search([
                ('bom_id', '=', bom.id), ('active', '=', False),
                ('workcenter_id', 'in', list(old_stages))])
            minutes = self._floor_minutes(old, ['opaque', 'buildup'], benches, old_stages)[0] \
                if old else {}
            share = None
            if minutes and sum(minutes.values()):
                share = minutes['opaque'] / sum(minutes.values())
                if abs(sum(minutes.values()) - step.time_cycle_manual) < 0.01:
                    known[round(step.time_cycle_manual, 2)] = share
            todo.append((bom, step, share))
        for bom, step, share in todo:
            total = step.time_cycle_manual
            if share is None:
                share = known.get(round(total, 2))
            if share is None:
                # re-timed by hand since: nothing says how it divides, so the bench
                # that does the long work keeps it
                share = 0.0
                _logger.info("lab_masters: %s - Opaque & Buildup of %s min has no known "
                             "division; all of it left on Buildup", bom.display_name, total)
            first = round(total * share, 2)
            before = self._floor_labour(bom)
            step.write({'name': opaque.name, 'time_cycle_manual': first})
            Operation.create({
                'bom_id': bom.id, 'name': buildup.name, 'workcenter_id': buildup.id,
                'sequence': self._floor_slot_after(bom.operation_ids, step),
                'time_mode': 'manual', 'time_cycle_manual': total - first,
            })
            bom.invalidate_recordset(['operation_ids'])
            after = self._floor_labour(bom)
            if abs(before - after) > 0.005:
                _logger.warning("lab_masters: %s labour moved from %.2f to %.2f",
                                bom.display_name, before, after)
        _logger.info("lab_masters: Opaque and Buildup made two steps in %s bills of "
                     "materials", len(todo))
        return len(todo)

    @api.model
    def _floor_model_bench_of(self, product, benches):
        """The model preparation a product's printed model goes to: its own
        department's, where that department has one."""
        department = product.categ_id._lab_department() \
            if hasattr(product.categ_id, '_lab_department') else False
        for key in ('ortho_model_preparation', 'acrylic_model_preparation'):
            if department and benches[key].department_id == department:
                return benches[key]
        return benches['model_preparation']

    @api.model
    def _floor_printed_model_boms(self, benches):
        """The bills of materials of a printed model: the CAD/CAM steps and the foot
        of the sheet, nothing between - whatever product they were made for."""
        printing, curing = benches['printing'], benches['light_curing']
        allowed = benches['scanning'] | benches['designing'] | printing | curing \
            | benches['final_check'] | benches['packing']
        boms = self.env['mrp.bom'].sudo().search(
            [('operation_ids.workcenter_id', '=', printing.id)])
        return boms.filtered(lambda b: curing in b.operation_ids.workcenter_id
                             and not (b.operation_ids.workcenter_id - allowed))

    @api.model
    def _floor_add_model_preparation(self):
        """Model Preparation after Light Curing on every printed model (client,
        2026-11-26: "Model preparation workorder should be added to the MOs with SO
        is 3d printing").

        A printed model was a CAD/CAM job from end to end - "no model to prepare".
        There is: the print comes off the plate to be cleaned, trimmed and die-cut
        like a poured one. The step goes to the model bench of the product's own
        department, at no minutes (nobody has timed it), so no cost moves. A bill
        of materials that has the step already is not one `_floor_printed_model_boms`
        returns. Returns how many were changed.
        """
        benches = self._floor_benches()
        Operation = self.env['mrp.routing.workcenter'].sudo()
        boms = self._floor_printed_model_boms(benches)
        for bom in boms:
            curing = bom.operation_ids.filtered(
                lambda o: o.workcenter_id == benches['light_curing']).sorted(
                    lambda o: (o.sequence, o.id))[-1:]
            bench = self._floor_model_bench_of(bom.product_tmpl_id, benches)
            Operation.create({
                'bom_id': bom.id, 'name': bench.name, 'workcenter_id': bench.id,
                'sequence': self._floor_slot_after(bom.operation_ids, curing),
                'time_mode': 'manual', 'time_cycle_manual': 0.0,
            })
        _logger.info("lab_masters: Model Preparation added after Light Curing in %s "
                     "bills of materials", len(boms))
        return len(boms)

    @api.model
    def _floor_twin(self, bench, model):
        """A new bench made the twin of the one its work came from: the same rate,
        calendar, cost centre, technicians and leads, until the lab says who is
        where. Only while nothing stands at it yet - afterwards it is the lab's."""
        if self.env['mrp.routing.workcenter'].sudo().with_context(active_test=False).search_count(
                [('workcenter_id', '=', bench.id)], limit=1):
            return
        vals = {'costs_hour': model.costs_hour}
        for field in ('resource_calendar_id', 'analytic_distribution', 'users',
                      'head_user_ids', 'is_finisher', 'scan_confirm'):
            if field not in bench._fields:
                continue        # other modules' fields: not there in a fresh install
            if bench._fields[field].type == 'many2many':
                if not bench[field]:
                    vals[field] = [(6, 0, model[field].ids)]
            elif bench._fields[field].type == 'many2one':
                vals[field] = model[field].id
            else:
                vals[field] = model[field]
        bench.write(vals)

    @api.model
    def _floor_slot_before(self, steps, step):
        """The number for a new step right before `step` among `steps`: halfway from
        the one before it. Where there is no room, `step` and everything after it
        move down ten first."""
        earlier = steps.filtered(lambda s: (s.sequence, s.id) < (step.sequence, step.id))
        floor = max(earlier.mapped('sequence')) if earlier else -10
        if step.sequence - floor < 2:
            for other in steps.filtered(lambda s: (s.sequence, s.id) >= (step.sequence, step.id)):
                other.sequence += 10
        return floor + (step.sequence - floor) // 2

    @api.model
    def _floor_split_model_preparation(self):
        """Wax Up's Model Preparation becomes Cutting, Scooping and Blocking, in every
        bill of materials that has it (client, 2026-11-27).

        The bench that was all three is Cutting (`_floor_benches` renames it);
        Scooping and Blocking are new, twins of it. The step's minutes are divided
        the way the old stages had them - Model Preparation and Articulation to
        Cutting, Margin Scooping to Scooping, Undercut Blocking to Blocking (metal
        ceramic: 8 = 6 + 1 + 1) - the costed products still carry those stages,
        archived, and a category route takes its costed product's division. A step
        re-timed since, with nothing to say how it divides, stays on Cutting. So no
        product's labour cost moves. A bill of materials that already has Scooping is
        left alone. Returns how many were changed.
        """
        benches = self._floor_benches()
        cutting, scooping, blocking = benches['model_preparation'], benches['scooping'], benches['blocking']
        self._floor_twin(scooping, cutting)
        self._floor_twin(blocking, cutting)
        Operation = self.env['mrp.routing.workcenter'].sudo()
        old_stages = self._floor_old_stages()
        keys = ['model_preparation', 'scooping', 'blocking']
        todo, known = [], {}
        # a route split before divides the same total the same way (a costed product
        # is split with the first upgrade; a route made later has no old stages)
        for done in self.env['mrp.bom'].sudo().search([('operation_ids.workcenter_id', '=', scooping.id)]):
            trio = [sum(done.operation_ids.filtered(lambda o, b=b: o.workcenter_id == b)
                        .mapped('time_cycle_manual')) for b in (cutting, scooping, blocking)]
            if sum(trio):
                known.setdefault(round(sum(trio), 2), [part / sum(trio) for part in trio])
        for bom in self.env['mrp.bom'].sudo().search([('operation_ids.workcenter_id', '=', cutting.id)]):
            steps = bom.operation_ids
            if steps.filtered(lambda o: o.workcenter_id == scooping):
                continue
            step = steps.filtered(lambda o: o.workcenter_id == cutting).sorted(
                lambda o: (o.sequence, o.id))[:1]
            old = Operation.with_context(active_test=False).search([
                ('bom_id', '=', bom.id), ('active', '=', False),
                ('workcenter_id', 'in', list(old_stages))])
            minutes = self._floor_minutes(old, keys, benches, old_stages)[0] if old else {}
            shares = None
            if minutes and sum(minutes.values()):
                total_old = sum(minutes.values())
                shares = [minutes[k] / total_old for k in keys]
                if abs(total_old - step.time_cycle_manual) < 0.01:
                    known[round(step.time_cycle_manual, 2)] = shares
            todo.append((bom, step, shares))
        for bom, step, shares in todo:
            total = step.time_cycle_manual
            if shares is None:
                shares = known.get(round(total, 2)) or [1.0, 0.0, 0.0]
            parts = [round(total * share, 2) for share in shares]
            parts[0] = round(total - parts[1] - parts[2], 2)
            before = self._floor_labour(bom)
            step.write({'name': cutting.name, 'time_cycle_manual': parts[0]})
            second = Operation.create({
                'bom_id': bom.id, 'name': scooping.name, 'workcenter_id': scooping.id,
                'sequence': self._floor_slot_after(bom.operation_ids, step),
                'time_mode': 'manual', 'time_cycle_manual': parts[1],
            })
            bom.invalidate_recordset(['operation_ids'])
            Operation.create({
                'bom_id': bom.id, 'name': blocking.name, 'workcenter_id': blocking.id,
                'sequence': self._floor_slot_after(bom.operation_ids, second),
                'time_mode': 'manual', 'time_cycle_manual': parts[2],
            })
            bom.invalidate_recordset(['operation_ids'])
            after = self._floor_labour(bom)
            if abs(before - after) > 0.005:
                _logger.warning("lab_masters: %s labour moved from %.2f to %.2f",
                                bom.display_name, before, after)
        _logger.info("lab_masters: Model Preparation made Cutting, Scooping and Blocking in "
                     "%s bills of materials", len(todo))
        return len(todo)

    @api.model
    def _floor_add_metal_steps(self):
        """Metal/Sintering before Metal/Casting and Metal/Printing after it, in every
        bill of materials that casts (client, 2026-11-27). New benches, twins of the
        casting bench; no minutes (nobody has timed them), so no cost moves. A bill
        of materials that has Metal/Sintering already is left alone. Returns how many
        were changed."""
        benches = self._floor_benches()
        casting, sintering, printing = benches['casting'], benches['metal_sintering'], benches['metal_printing']
        self._floor_twin(sintering, casting)
        self._floor_twin(printing, casting)
        Operation = self.env['mrp.routing.workcenter'].sudo()
        boms = self.env['mrp.bom'].sudo().search([('operation_ids.workcenter_id', '=', casting.id)])
        # a step is named for its bench: the lab renamed the bench "Metal/Casting" and
        # its steps still said "Casting"
        Operation.search([('workcenter_id', '=', casting.id), ('name', '!=', casting.name)]).write(
            {'name': casting.name})
        boms = boms.filtered(lambda b: sintering not in b.operation_ids.workcenter_id)
        for bom in boms:
            cast = bom.operation_ids.filtered(lambda o: o.workcenter_id == casting).sorted(
                lambda o: (o.sequence, o.id))[:1]
            Operation.create({
                'bom_id': bom.id, 'name': sintering.name, 'workcenter_id': sintering.id,
                'sequence': self._floor_slot_before(bom.operation_ids, cast),
                'time_mode': 'manual', 'time_cycle_manual': 0.0,
            })
            bom.invalidate_recordset(['operation_ids'])
            Operation.create({
                'bom_id': bom.id, 'name': printing.name, 'workcenter_id': printing.id,
                'sequence': self._floor_slot_after(bom.operation_ids, cast),
                'time_mode': 'manual', 'time_cycle_manual': 0.0,
            })
            bom.invalidate_recordset(['operation_ids'])
        _logger.info("lab_masters: Metal/Sintering and Metal/Printing added around Metal/Casting "
                     "in %s bills of materials", len(boms))
        return len(boms)

    # ------------------------------------------- the jobs already on the floor
    @api.model
    def _floor_insert_step(self, after, bench, operation, minutes=0.0):
        """A step for `bench` right after the work order `after`, in its own chain:
        the same arch, between it and whatever followed."""
        # Its number puts it in its place, and that is all it needs: on a job that
        # is confirmed, core links every step to the one before it by number again
        # whenever a step is added (mrp.workorder.create), which is the chain the
        # board reads "where does it go next" from. Never the same number as
        # another step - core renumbers the whole job from its bill of materials
        # when two steps share one.
        return self.env['mrp.workorder'].sudo().create({
            'name': bench.name,
            'production_id': after.production_id.id,
            'workcenter_id': bench.id,
            'operation_id': operation.id or False,
            'duration_expected': minutes,
            'sequence': self._floor_slot_after(after.production_id.workorder_ids, after),
            'state': 'ready' if after.state in ('done', 'cancel') else 'blocked',
            **({'arch': after.arch} if 'arch' in after._fields and after.arch else {}),
        })

    @api.model
    def _floor_job_is_current(self, job):
        """Changing a bill of materials marks its open jobs outdated (core offers
        "Update BoM" on them). A job this module has just given the missing step
        has every step of its bill of materials again, so the mark comes off."""
        if 'is_outdated_bom' in job._fields and job.is_outdated_bom \
                and not (job.bom_id.operation_ids - job.workorder_ids.operation_id):
            job.is_outdated_bom = False

    @api.model
    def split_open_opaque_steps(self, limit=None, productions=None):
        """The jobs waiting at "Opaque & Buildup": the step becomes Opaque, and a
        Buildup step follows it. Only a step nobody has finished - one already done
        was done as one, and stays one. Returns how many are left to do.

        Run with a limit and commit between calls, after the upgrade: it is a few
        hundred small writes, and there is no reason to hold the site down for it.
        `productions` narrows it to those jobs - a test gives it its own.
        """
        benches = self._floor_benches()
        opaque, buildup = benches['opaque'], benches['buildup']
        Workorder = self.env['mrp.workorder'].sudo()
        domain = [('workcenter_id', '=', opaque.id), ('state', 'not in', ('done', 'cancel')),
                  ('production_id.state', 'not in', ('done', 'cancel')),
                  ('name', '!=', opaque.name)]
        if productions is not None:
            domain.append(('production_id', 'in', productions.ids))
        for step in Workorder.search(domain, order='id desc', limit=limit):
            has_arch = 'arch' in step._fields
            twin = step.production_id.workorder_ids.filtered(
                lambda w: w.workcenter_id == buildup
                and (not has_arch or (w.arch or '') == (step.arch or '')))
            if not twin:
                ops = step.production_id.bom_id.operation_ids
                first = ops.filtered(lambda o: o.workcenter_id == opaque)[:1]
                second = ops.filtered(lambda o: o.workcenter_id == buildup)[:1]
                total = step.duration_expected or 0.0
                both = (first.time_cycle_manual or 0.0) + (second.time_cycle_manual or 0.0)
                part = round(total * first.time_cycle_manual / both, 2) if both else 0.0
                self._floor_insert_step(step, buildup, second, total - part)
                step.duration_expected = part
            step.name = opaque.name
            self._floor_job_is_current(step.production_id)
        return Workorder.search_count(domain)

    @api.model
    def add_open_model_preparation(self, limit=None, productions=None):
        """The printed models already on the floor: Model Preparation after Light
        Curing, where the job has not passed its final check. Returns how many
        steps were added. `productions` narrows it to those jobs."""
        benches = self._floor_benches()
        curing, check = benches['light_curing'], benches['final_check']
        model_benches = benches['model_preparation'] | benches['ortho_model_preparation'] \
            | benches['acrylic_model_preparation']
        boms = self.env['mrp.bom'].sudo().search(
            [('operation_ids.workcenter_id', '=', curing.id),
             ('operation_ids.workcenter_id', 'in', model_benches.ids)])
        allowed = benches['scanning'] | benches['designing'] | benches['printing'] | curing \
            | check | benches['packing'] | model_benches
        boms = boms.filtered(lambda b: not (b.operation_ids.workcenter_id - allowed))
        domain = [('bom_id', 'in', boms.ids), ('state', 'not in', ('done', 'cancel'))]
        if productions is not None:
            domain.append(('id', 'in', productions.ids))
        jobs = self.env['mrp.production'].sudo().search(domain, order='id desc')
        done = 0
        for job in jobs:
            steps = job.workorder_ids
            if steps.filtered(lambda w: w.workcenter_id in model_benches):
                continue
            operation = job.bom_id.operation_ids.filtered(
                lambda o: o.workcenter_id in model_benches)[:1]
            if not operation:
                continue
            for cured in steps.filtered(lambda w: w.workcenter_id == curing
                                        and w.state != 'cancel'):
                has_arch = 'arch' in cured._fields
                checked = steps.filtered(
                    lambda w: w.workcenter_id == check and w.state == 'done'
                    and (not has_arch or (w.arch or '') == (cured.arch or '')))
                if not checked:
                    self._floor_insert_step(cured, operation.workcenter_id, operation)
                    done += 1
            self._floor_job_is_current(job)
            if limit and done >= limit:
                break
        return done

    @api.model
    def _floor_insert_step_before(self, before, bench, operation, minutes=0.0):
        """A step for `bench` right before the work order `before`, in its own chain:
        `before` then waits for it (core links the chain again by number when a
        step is added, and the state follows the links)."""
        job = before.production_id
        has_arch = 'arch' in before._fields
        chain = job.workorder_ids.filtered(
            lambda w: w.state != 'cancel' and (not has_arch or (w.arch or '') == (before.arch or '')))
        waiting = chain.filtered(lambda w: (w.sequence, w.id) < (before.sequence, before.id)
                                 and w.state not in ('done', 'cancel'))
        return self.env['mrp.workorder'].sudo().create({
            'name': bench.name,
            'production_id': job.id,
            'workcenter_id': bench.id,
            'operation_id': operation.id or False,
            'duration_expected': minutes,
            'sequence': self._floor_slot_before(job.workorder_ids, before),
            'state': 'blocked' if waiting else 'ready',
            **({'arch': before.arch} if has_arch and before.arch else {}),
        })

    @api.model
    def split_open_model_preparation(self, limit=None, productions=None):
        """The jobs not yet past Wax Up's Model Preparation: the step becomes Cutting,
        and Scooping and Blocking follow it. Only a step nobody has started - one
        started or done was done as one, and stays one. Returns how many are left.

        Online after the upgrade, with a limit and a commit between calls.
        `productions` narrows it to those jobs - a test gives it its own.
        """
        benches = self._floor_benches()
        cutting, scooping, blocking = benches['model_preparation'], benches['scooping'], benches['blocking']
        Workorder = self.env['mrp.workorder'].sudo()
        domain = [('workcenter_id', '=', cutting.id),
                  ('state', 'not in', ('done', 'cancel', 'progress')),
                  ('production_id.state', 'not in', ('done', 'cancel')),
                  ('name', '!=', cutting.name)]
        if productions is not None:
            domain.append(('production_id', 'in', productions.ids))
        for step in Workorder.search(domain, order='id desc', limit=limit):
            has_arch = 'arch' in step._fields
            twin = step.production_id.workorder_ids.filtered(
                lambda w: w.workcenter_id == scooping
                and (not has_arch or (w.arch or '') == (step.arch or '')))
            if not twin:
                ops = step.production_id.bom_id.operation_ids
                first = ops.filtered(lambda o: o.workcenter_id == cutting)[:1]
                second = ops.filtered(lambda o: o.workcenter_id == scooping)[:1]
                third = ops.filtered(lambda o: o.workcenter_id == blocking)[:1]
                total = step.duration_expected or 0.0
                whole = sum((o.time_cycle_manual or 0.0) for o in (first, second, third))
                scoop = round(total * second.time_cycle_manual / whole, 2) if whole else 0.0
                block = round(total * third.time_cycle_manual / whole, 2) if whole else 0.0
                made = self._floor_insert_step(step, scooping, second, scoop)
                self._floor_insert_step(made, blocking, third, block)
                step.duration_expected = round(total - scoop - block, 2)
            step.name = cutting.name
            self._floor_job_is_current(step.production_id)
        return Workorder.search_count(domain)

    @api.model
    def add_open_metal_steps(self, limit=None, productions=None):
        """The jobs not yet past Metal/Casting: Metal/Sintering before the casting
        step and Metal/Printing after it. Only a casting nobody has started.
        Returns how many casting steps are left to do."""
        benches = self._floor_benches()
        casting, sintering, printing = benches['casting'], benches['metal_sintering'], benches['metal_printing']
        domain = [('workcenter_id', '=', casting.id),
                  ('state', 'not in', ('done', 'cancel', 'progress')),
                  ('production_id.state', 'not in', ('done', 'cancel'))]
        if productions is not None:
            domain.append(('production_id', 'in', productions.ids))

        def untouched(step):
            has_arch = 'arch' in step._fields
            return not step.production_id.workorder_ids.filtered(
                lambda w: w.workcenter_id == sintering
                and (not has_arch or (w.arch or '') == (step.arch or '')))

        todo = self.env['mrp.workorder'].sudo().search(domain, order='id desc').filtered(untouched)
        for step in todo[:limit] if limit else todo:
            ops = step.production_id.bom_id.operation_ids
            self._floor_insert_step_before(
                step, sintering, ops.filtered(lambda o: o.workcenter_id == sintering)[:1])
            self._floor_insert_step(
                step, printing, ops.filtered(lambda o: o.workcenter_id == printing)[:1])
            step.name = casting.name
            self._floor_job_is_current(step.production_id)
        return max(0, len(todo) - (limit or len(todo)))

    # ------------------------------------------------------------------- routes
    @api.model
    def _floor_old_stages(self):
        """{bench id: [stage keys]} for every bench of the old layout that exists."""
        stages = {}
        for xmlid, keys in STAGE_OF.items():
            bench = self.env.ref('lab_masters.%s' % xmlid, raise_if_not_found=False)
            if bench:
                stages[bench.id] = keys
        return stages

    @api.model
    def _floor_route_of(self, bom, old_stages):
        """The stages this product passes through, in the sheet's order.

        One of the fifteen: the sheet it is made on says. Anything else - a bill
        of materials somebody added since - walks the stages its own operations
        map to, in the order it already had them, so it is never left pointing
        at a bench that has gone.
        """
        data = self.env['ir.model.data'].sudo().search([
            ('module', '=', 'lab_masters'), ('model', '=', 'mrp.bom'), ('res_id', '=', bom.id)],
            limit=1)
        if data.name in BOM_ROUTE:
            return list(ROUTES[BOM_ROUTE[data.name]])
        route = []
        for operation in bom.operation_ids.sorted(lambda o: (o.sequence, o.id)):
            keys = old_stages.get(operation.workcenter_id.id)
            if keys and keys[0] not in route:
                route.append(keys[0])
        return route

    @api.model
    def _floor_minutes(self, operations, route, benches, old_stages):
        """Minutes per stage, and the old operations that found no stage.

        Hands-on minutes on a bench that is paid by the hour, every minute on one
        that is not - see the module docstring.
        """
        minutes = OrderedDict((key, 0.0) for key in route)
        dropped = []
        for operation in operations:
            stage = next((key for key in old_stages.get(operation.workcenter_id.id, [])
                          if key in minutes), None)
            if not stage:
                dropped.append(operation)
                continue
            paid_bench = benches[stage].costs_hour > 0
            if operation.workcenter_id.costs_hour > 0 or not paid_bench:
                minutes[stage] += operation.time_cycle_manual
        return minutes, dropped

    @api.model
    def _floor_labour(self, bom):
        return sum(op.time_cycle_manual / 60.0 * op.workcenter_id.costs_hour
                   for op in bom.operation_ids)

    @api.model
    def _floor_rebuild_bom(self, bom, benches, old_stages):
        """Replace a bill of materials' operations with its sheet's rows.

        Returns False when there was nothing to do: the operations already are
        the layout's, which is what makes a second upgrade a no-op and leaves
        alone whatever the lab has re-timed since.
        """
        # Only the steps that stand at a bench of the old layout are this module's
        # to replace. A step somebody added at a bench of their own stays where
        # it is, in the place its sequence gives it.
        operations = bom.operation_ids.filtered(
            lambda op: op.workcenter_id.id in old_stages)
        if not operations or all(self._floor_is_new_operation(op, benches)
                                 for op in operations):
            return False
        route = self._floor_route_of(bom, old_stages)
        if not route:
            return False
        before = self._floor_labour(bom)
        minutes, dropped = self._floor_minutes(operations, route, benches, old_stages)
        # Archived, not deleted: a step somebody finished keeps pointing at the
        # operation it was, and core marks the open orders "outdated" from here.
        operations.action_archive()
        self.env['mrp.routing.workcenter'].sudo().create([{
            'bom_id': bom.id,
            'name': benches[key].name,
            'workcenter_id': benches[key].id,
            'sequence': index * 10,
            'time_mode': 'manual',
            'time_cycle_manual': minutes[key],
        } for index, key in enumerate(route, start=1)])
        bom.invalidate_recordset(['operation_ids'])
        after = self._floor_labour(bom)
        if dropped:
            _logger.info("lab_masters: %s - no row on its sheet for %s", bom.display_name,
                         ', '.join(sorted({op.name for op in dropped})))
        if abs(before - after) > 0.005:
            _logger.warning("lab_masters: %s labour moved from %.2f to %.2f",
                            bom.display_name, before, after)
        return True

    @api.model
    def _floor_is_new_operation(self, operation, benches):
        """Is this operation already one of the layout's?

        The benches that kept their record (Scanning, Casting, Packing...) are in
        both layouts, so being on one proves nothing. An operation is the new
        layout's when it is NAMED for its bench as the layout names it - the old
        ones were named for the old stage ("Trimming and Carving").
        """
        layout = {bench.id: bench.name for bench in benches.values()}
        return layout.get(operation.workcenter_id.id) == operation.name

    @api.model
    def apply_floor_layout(self):
        """Benches, routes, and the old benches put away. Safe to run twice."""
        benches = self._floor_benches()
        self.stamp_workcenter_analytic()
        old_stages = self._floor_old_stages()
        boms = self.env['mrp.bom'].sudo().search([])
        rebuilt = boms.filtered(lambda b: self._floor_rebuild_bom(b, benches, old_stages))
        archived = self._floor_archive_unused(benches)
        _logger.info("lab_masters: floor layout - %s benches, %s routes rebuilt, %s old "
                     "benches archived", len(benches), len(rebuilt), len(archived))
        return {'benches': len(benches), 'routes': len(rebuilt), 'archived': len(archived)}

    @api.model
    def _floor_archive_unused(self, benches):
        """Archive every bench that is not on the layout and that no route uses.

        "No route uses" is what keeps this honest on a database that is not the
        lab's: a bench somebody made for a product of their own, with a bill of
        materials that still walks it, is theirs and stays.
        """
        keep = {bench.id for bench in benches.values()}
        in_use = set(self.env['mrp.routing.workcenter'].sudo().search(
            [('bom_id.active', '=', True)]).workcenter_id.ids)
        unused = self.env['mrp.workcenter'].sudo().search(
            [('id', 'not in', list(keep | in_use))])
        if unused:
            unused.write({'active': False})
        return unused

    # --------------------------------------------------------------- open orders
    @api.model
    def _floor_stale_order_ids(self, limit=None):
        """Open orders still waiting at a step whose operation has been retired."""
        self.env['mrp.workorder'].flush_model()
        self.env['mrp.routing.workcenter'].flush_model()
        self.env.cr.execute("""
            SELECT DISTINCT wo.production_id
              FROM mrp_workorder wo
              JOIN mrp_production mo ON mo.id = wo.production_id
              JOIN mrp_routing_workcenter op ON op.id = wo.operation_id
             WHERE mo.state IN %s
               AND wo.state IN ('blocked', 'ready')
               AND NOT op.active
             ORDER BY wo.production_id DESC
        """ + (" LIMIT %s" if limit else ""),
            (OPEN_MO_STATES,) + ((int(limit),) if limit else ()))
        return [row[0] for row in self.env.cr.fetchall()]

    @api.model
    def reroute_open_orders(self, limit=None):
        """Put open orders on their product's new steps. Returns how many are left.

        Core's own "Update BoM", which is what the button on an outdated order
        does: the steps nobody has started are replaced by the bill of
        materials' operations, and a step already started or finished is kept.
        Newest first, so the cases actually on the floor are right before the
        backlog is. Call it with a limit and commit between calls - a backlog of
        thousands is minutes of work, and there is no reason to hold the site
        down for it.
        """
        ids = self._floor_stale_order_ids(limit)
        orders = self.env['mrp.production'].sudo().browse(ids)
        for order in orders:
            if order.bom_id.active and order.bom_id.operation_ids:
                order.action_update_bom()
        return len(self._floor_stale_order_ids()) if ids else 0

