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
    ('model_preparation', 'wc_model_preparation', 'Model Preparation', 'WX-01', 'dept_wax_up', LABOUR, True),
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
    ('casting', 'wc_casting', 'Casting', 'MT-01', 'dept_metal', 0.0, False),
    ('metal_trimming', 'wc_metal_trimming', 'Trimming', 'MT-02', 'dept_metal', LABOUR, True),
    ('metal_polishing', 'wc_metal_polishing', 'Polishing', 'MT-03', 'dept_metal', LABOUR, True),
    # ---- Ceramic
    ('opaque_buildup', 'wc_opaque_buildup', 'Opaque & Buildup', 'CR-01', 'dept_ceramic', LABOUR, False),
    ('ceramic_trimming', 'wc_trimming_carving', 'Trimming', 'CR-02', 'dept_ceramic', LABOUR, True),
    ('ceramic_finishing', 'wc_ceramic_finishing', 'Finishing', 'CR-03', 'dept_ceramic', LABOUR, False),
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
    'wc_margin_scooping': ['model_preparation'],
    'wc_undercut_blocking': ['model_preparation'],
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
    'wc_model_checking': ['opaque_buildup'],
    'wc_wash_opaque': ['opaque_buildup'],
    'wc_opaque_firing': ['opaque_buildup'],
    'wc_second_opaque': ['opaque_buildup'],
    'wc_wash_firing': ['opaque_buildup'],
    'wc_first_build_up': ['opaque_buildup'],
    'wc_first_build_firing': ['opaque_buildup'],
    'wc_second_build_up': ['opaque_buildup'],
    'wc_second_build_firing': ['opaque_buildup'],
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
    'zirconia': ['model_preparation', 'scanning', 'designing', 'milling', 'sintering',
                 'opaque_buildup', 'ceramic_trimming', 'ceramic_finishing'] + FOOT,
    # the white sheet, every section of it
    'metal_ceramic': ['model_preparation', 'scanning', 'designing', 'printing', 'light_curing',
                      'spruing', 'investing', 'casting', 'metal_trimming', 'metal_polishing',
                      'opaque_buildup', 'ceramic_trimming', 'ceramic_finishing'] + FOOT,
    # the white sheet without its ceramic section
    'full_metal': ['model_preparation', 'scanning', 'designing', 'printing', 'light_curing',
                   'spruing', 'investing', 'casting', 'metal_trimming', 'metal_polishing'] + FOOT,
    # pressed, not cast: the press is the casting of it, and there is no metal to polish
    'pressed': ['model_preparation', 'scanning', 'designing', 'printing', 'light_curing',
                'spruing', 'investing', 'casting', 'metal_trimming',
                'opaque_buildup', 'ceramic_trimming', 'ceramic_finishing'] + FOOT,
    # the yellow sheet
    'orthodontic': ['ortho_model_preparation', 'wire_bending', 'acrylization',
                    'ortho_trimming'] + FOOT,
    # an Essix splint is formed and trimmed: there is no wire in it
    'splint': ['ortho_model_preparation', 'acrylization', 'ortho_trimming'] + FOOT,
    # the pink sheet
    'acrylic': ['acrylic_model_preparation', 'special_tray', 'wax_bite', 'teeth_setting', 'flasking',
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
                bench = Workcenter.create(dict(vals, costs_hour=rate))
                self._tag('mrp.workcenter', xmlid, bench)
            benches[key] = bench.sudo()
        return benches

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

