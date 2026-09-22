# -*- coding: utf-8 -*-
"""Visit purposes and outcomes as records, not hard-coded selections (R2, R3).

A single visit routinely does several things at once — deliver finished work AND
collect a new impression AND take payment. A single-value Selection cannot say that,
and "Delivery" did not exist as a value at all.

The legacy Selection fields stay: the mobile My-Day flow, the day-close cron and the
control tower all read them, and breaking a working phone UI to normalise a field is a
bad trade. They become the *primary* purpose/outcome; the tags carry everything else.
Writing the legacy field automatically adds the matching tag, so old habits still
produce complete data.
"""
from odoo import api, fields, models


class LabVisitPurpose(models.Model):
    _name = 'lab.visit.purpose'
    _description = 'Visit Purpose'
    _order = 'sequence, id'

    name = fields.Char(required=True, translate=True)
    code = fields.Char(index=True, help="Stable key used by code; never shown.")
    sequence = fields.Integer(default=10)
    color = fields.Integer(default=0)
    active = fields.Boolean(default=True)
    is_delivery = fields.Boolean(
        help="Visits carrying this purpose are expected to hand over finished work — "
             "the delivery cross-checks hang off this flag.")


class LabVisitOutcome(models.Model):
    _name = 'lab.visit.outcome'
    _description = 'Visit Outcome'
    _order = 'sequence, id'

    name = fields.Char(required=True, translate=True)
    code = fields.Char(index=True)
    sequence = fields.Integer(default=10)
    color = fields.Integer(default=0)
    active = fields.Boolean(default=True)
    is_delivery = fields.Boolean()


class LabVisit(models.Model):
    _inherit = 'lab.visit'

    purpose_ids = fields.Many2many(
        'lab.visit.purpose', 'lab_visit_purpose_rel', 'visit_id', 'purpose_id',
        string='Purposes')
    outcome_ids = fields.Many2many(
        'lab.visit.outcome', 'lab_visit_outcome_rel', 'visit_id', 'outcome_id',
        string='Outcomes')
    # "Is the Rework(s) Collected chip lit?" - what the form shows the rework
    # count on. A flag rather than an expression on outcome_ids because a view
    # expression sees tag ids, not codes, and the id differs per database.
    has_rework_outcome = fields.Boolean(compute='_compute_has_rework_outcome')

    @api.depends('outcome_ids.code')
    def _compute_has_rework_outcome(self):
        for visit in self:
            visit.has_rework_outcome = 'rework' in visit.outcome_ids.mapped('code')

    def _drop_orphan_rework_count(self):
        """A rework count only means something under the rework outcome.

        The form hides the count the moment the chip goes out, but a number
        typed before that would otherwise survive unseen and be summed on the
        day sheet. Cleared here, once, on the write that removed the chip.
        """
        stale = self.filtered(
            lambda v: v.reworks_collected and not v.has_rework_outcome)
        if stale:
            stale.with_context(_fw_tag_sync=True).write({'reworks_collected': 0})

    @api.model
    def _tag_for(self, model, code):
        return self.env[model].sudo().search([('code', '=', code)], limit=1)

    def _sync_legacy_tags(self):
        """Old code writing the single-value field (a stray `.outcome = 'order'`
        somewhere not yet converted) still feeds the tag set, so nothing silently
        stops working the day this module is upgraded."""
        for visit in self:
            if visit.purpose:
                tag = self._tag_for('lab.visit.purpose', visit.purpose)
                if tag and tag not in visit.purpose_ids:
                    visit.purpose_ids = [(4, tag.id)]
            if visit.outcome:
                tag = self._tag_for('lab.visit.outcome', visit.outcome)
                if tag and tag not in visit.outcome_ids:
                    visit.outcome_ids = [(4, tag.id)]

    def _sync_primary_from_tags(self):
        """The tags are the only thing a person touches now — this is what keeps the
        single-value field honest for the code that still reads it: the checkout gate
        (`if not self.outcome`), the day-close cron, and the auto-stamp a taken case
        puts on its visit. Every seeded master code has a matching legacy Selection
        value (see PURPOSES/OUTCOMES above), so this never has nowhere to put a tag.

        The "primary" tag is whichever has the lowest `sequence` — the same order the
        chips are drawn in, so the derived single value is the leftmost chip that is
        lit, which is the reading a person glancing at the row would make too.
        """
        for visit in self:
            vals = {}
            if visit.purpose_ids:
                primary = visit.purpose_ids.sorted('sequence')[0]
                if primary.code and visit.purpose != primary.code:
                    vals['purpose'] = primary.code
            if visit.outcome_ids:
                primary = visit.outcome_ids.sorted('sequence')[0]
                if primary.code and visit.outcome != primary.code:
                    vals['outcome'] = primary.code
            if vals:
                visit.write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        visits = super().create(vals_list)
        # Both directions, because a first save can arrive from either side: the quick
        # entry wizard still creates with the scalar `purpose` set, while the ordinary
        # form now creates with `outcome_ids`/`purpose_ids` set from the chips a person
        # tapped before ever saving.
        synced = visits.with_context(_fw_tag_sync=True)
        synced._sync_legacy_tags()
        synced._sync_primary_from_tags()
        synced._drop_orphan_rework_count()
        return visits

    def write(self, vals):
        res = super().write(vals)
        # The context flag is what stops this ping-ponging: each sync's own write()
        # call carries it, so the write it triggers runs the ORM logic but not another
        # round of syncing.
        if self.env.context.get('_fw_tag_sync'):
            return res
        synced = self.with_context(_fw_tag_sync=True)
        if 'purpose' in vals or 'outcome' in vals:
            synced._sync_legacy_tags()
        if 'purpose_ids' in vals or 'outcome_ids' in vals:
            synced._sync_primary_from_tags()
        if 'outcome_ids' in vals or 'outcome' in vals or 'reworks_collected' in vals:
            synced._drop_orphan_rework_count()
        return res
