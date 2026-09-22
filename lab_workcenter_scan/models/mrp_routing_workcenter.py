# -*- coding: utf-8 -*-
from odoo import api, fields, models


class MrpRoutingWorkcenter(models.Model):
    """An operation on a bill of materials.

    In this lab an operation IS its bench: the routing of an appliance reads
    Wire Bending, Acrylisation, Trimming, Polishing, and the operation was
    being typed out by hand to match the work centre that was just chosen.
    Two names for one thing drift - "Wire Bending / Adams Clasp" against
    "WireBending Adams", and the station board then shows an operation nobody
    recognises. (client, 2026-09-11)
    """
    _inherit = 'mrp.routing.workcenter'

    @api.onchange('workcenter_id')
    def _onchange_workcenter_name(self):
        """Name the operation after the bench, unless somebody has named it.

        Fills a blank, and renames an operation whose name is itself a work
        centre's name - that name was taken from a bench, so it follows the
        bench. A step deliberately called "Polish - second pass" is nobody's
        bench name and survives the work centre changing under it.

        Judged by the name, not by the bench the record was saved with: a new
        operation has no saved bench, so picking One and then Two left it
        called One.
        """
        Workcenter = self.env['mrp.workcenter'].with_context(active_test=False)
        for operation in self:
            if not operation.workcenter_id:
                continue
            if not operation.name or Workcenter.search_count(
                    [('name', '=', operation.name)], limit=1):
                operation.name = operation.workcenter_id.name
