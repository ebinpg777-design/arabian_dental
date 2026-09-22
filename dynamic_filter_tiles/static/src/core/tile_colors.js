/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";

/**
 * Single source of truth for tile colours. The keys match the `color` selection
 * of `filter.tile` (see models/filter_tile.py); the hex values live here only,
 * so restyling never needs a server upgrade.
 *
 * Mid-tone colours on purpose: they stay readable as an accent on a white
 * background and as a tint on a dark one.
 */
export const TILE_COLORS = {
    indigo: "#4f46e5",
    sky: "#0284c7",
    cyan: "#0891b2",
    teal: "#0d9488",
    emerald: "#059669",
    lime: "#65a30d",
    amber: "#d97706",
    orange: "#ea580c",
    rose: "#e11d48",
    violet: "#7c3aed",
    slate: "#64748b",
};

export const DEFAULT_TILE_COLOR = "indigo";

/**
 * Order used when a tile set is generated automatically.
 * Keep in sync with AUTO_COLOR_CYCLE in models/filter_tile.py - the server
 * assigns the colours, this copy only previews them.
 */
export const AUTO_COLOR_CYCLE = [
    "sky", "amber", "emerald", "violet", "rose", "cyan", "orange", "teal", "indigo", "lime",
];

/** Ordered, translated palette for the colour picker widget. */
export function getTilePalette() {
    return [
        { key: "indigo", label: _t("Indigo") },
        { key: "sky", label: _t("Sky") },
        { key: "cyan", label: _t("Cyan") },
        { key: "teal", label: _t("Teal") },
        { key: "emerald", label: _t("Emerald") },
        { key: "lime", label: _t("Lime") },
        { key: "amber", label: _t("Amber") },
        { key: "orange", label: _t("Orange") },
        { key: "rose", label: _t("Rose") },
        { key: "violet", label: _t("Violet") },
        { key: "slate", label: _t("Slate") },
    ].map((entry) => ({ ...entry, hex: TILE_COLORS[entry.key] }));
}

/**
 * Resolve the CSS colour of a tile definition: a free-form custom colour wins,
 * then the palette key, then the default.
 */
export function resolveTileColor(tile) {
    const custom = (tile && tile.custom_color ? String(tile.custom_color) : "").trim();
    if (custom) {
        return custom;
    }
    return TILE_COLORS[tile && tile.color] || TILE_COLORS[DEFAULT_TILE_COLOR];
}
