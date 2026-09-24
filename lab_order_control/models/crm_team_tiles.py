# -*- coding: utf-8 -*-
"""Sales Routes are a SEARCH PANEL, not filter tiles (client, 2026-08-18).

There is one route per sales area and the list grew past twenty, so a tile per route
filled the ribbon and pushed the queue tiles ("To Verify", "Doctor Call"…) off screen.
The routes now live in the search panel down the left of every order / invoice / contact
list, where they are a single scannable column with counts and can be combined with the
tiles above. What is left here is the removal of the tiles this module used to generate,
and the invoice-tile pinning that is unrelated to routes.
"""
from odoo import api, models

# Customer invoices are reached through several menus; the invoice tiles are pinned to
# the Accounting one and "Also On" the others (see lab_invoice_tile_actions()).
INVOICE_ACTIONS = ['account.action_move_out_invoice',          # Accounting > Customers > Invoices
                   'sale.action_invoice_salesteams',           # Sales > To Invoice > Invoices
                   'account.action_move_out_invoice_type']     # dashboards / direct links


class CrmTeam(models.Model):
    _inherit = 'crm.team'

    # xml-id prefixes of the per-route tiles this module used to create
    ROUTE_TILE_PREFIXES = ('tile_route_', 'tile_route_inv_', 'tile_route_partner_')

    @api.model
    def _lab_remove_route_tiles(self):
        """Delete the per-route tiles and the now empty "Sales Routes" ribbon rows."""
        if 'filter.tile' not in self.env:
            return 0
        Data = self.env['ir.model.data'].sudo()
        data = Data.search([
            ('module', '=', 'lab_order_control'), ('model', '=', 'filter.tile'),
            '|', '|',
            ('name', 'like', 'tile_route\\_%'),
            ('name', 'like', 'tile_route\\_inv\\_%'),
            ('name', 'like', 'tile_route\\_partner\\_%'),
        ])
        tiles = self.env['filter.tile'].sudo().with_context(active_test=False) \
            .browse(data.mapped('res_id')).exists()
        # a manager may have added a route tile by hand: those carry the same domain
        tiles |= self.env['filter.tile'].sudo().with_context(active_test=False).search(
            [('domain', 'like', "('team_id', '='")])
        count = len(tiles)
        tiles.unlink()
        data.unlink()
        self.env['filter.tile.row'].sudo().search([('name', 'ilike', 'Sales Route')]).unlink()
        return count

    @api.model
    def _lab_pin_invoice_tiles(self):
        """Every account.move tile of ours: pinned to Accounting's Invoices.

        ebshel_dynamic_filter used to carry an "Also On" (extra_action_ids) list;
        that concept is gone from the tile model, and writing the dead field
        broke every FRESH install of this module at post_init while the live
        database sailed on with data loaded under the old schema. Pinning to
        the one main action is all that is left to do. (2026-08-31)
        """
        Tile = self.env['filter.tile'].sudo()
        actions = [self.env.ref(x, raise_if_not_found=False) for x in INVOICE_ACTIONS]
        actions = [a for a in actions if a]
        if not actions:
            return
        main = actions[0]
        tiles = Tile.with_context(active_test=False).search([
            ('model_name', '=', 'account.move'), ('action_id', 'in', [a.id for a in actions])])
        tiles.write({'action_id': main.id})
