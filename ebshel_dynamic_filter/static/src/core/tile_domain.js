/** @odoo-module **/

import { Domain } from "@web/core/domain";
import { user } from "@web/core/user";

/**
 * The full domain of one tile definition, "and it is mine" included.
 *
 * A tile with `mine_field` counts each viewer's own records; on the browser
 * side "the viewer" is simply the session's user id, so the leaf is a literal
 * id - nothing to evaluate later, and the facet it becomes reads the same as
 * one built by hand. Server-side counting applies the same leaf itself from
 * `env.uid`; this helper is for the places that hand a domain to the client
 * framework: facets, breakdown splits, the tile wall. (client, 2026-08-28)
 */
export function tileDomain(def) {
    const base = def.domain || "[]";
    if (!def.mine_field) {
        return base;
    }
    // The server resolves what "mine" means for the model - a dental lab's work
    // order belongs to the technician who did it AND to the one who finished
    // it, so its leaf is an OR of two fields. Rebuilding a single leaf here
    // would filter to less than the tile counted. (client, 2026-09-10)
    const mine =
        def.mine_domain && def.mine_domain.length
            ? new Domain(def.mine_domain)
            : new Domain([[def.mine_field, "=", user.userId]]);
    return Domain.and([new Domain(base), mine]).toString();
}

const DATE_TYPES = ["date", "datetime"];

/**
 * The `group_by` context entry a tile asks for: `"field"`, or `"field:month"`
 * for a date field - the same spelling a search view's own group-by filter
 * uses, so an action context can carry it as-is. Empty for a plain tile.
 */
export function tileGroupBySpec(def) {
    if (!def.group_by) {
        return "";
    }
    if (DATE_TYPES.includes(def.group_by_type)) {
        return `${def.group_by}:${def.group_by_interval || "month"}`;
    }
    return def.group_by;
}
