/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";

/**
 * Single source of truth for card colours. The keys match the `color` selection
 * of `dashboard.item` / `dashboard.board` (see models/dashboard_item.py); the
 * hex values live here only, so restyling never needs a server upgrade.
 *
 * Mid-tone colours on purpose: they stay readable as an accent on a white
 * background and as a tint on a dark one.
 */
export const CARD_COLORS = {
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

export const DEFAULT_CARD_COLOR = "indigo";

/** The CSS colour of a card payload. */
export function resolveCardColor(item) {
    return CARD_COLORS[item && item.color] || CARD_COLORS[DEFAULT_CARD_COLOR];
}

/**
 * Categorical ramps used inside one chart, when the *slices* need to be told
 * apart rather than the card. Each is ordered so neighbours stay
 * distinguishable in both themes and for the common colour-vision
 * deficiencies: hue and lightness both move between one entry and the next.
 * The keys match the board's `palette` selection.
 */
export const PALETTES = {
    default: [
        "#2a78d6", "#eb6834", "#0d9488", "#7c3aed", "#d97706",
        "#0891b2", "#e11d48", "#65a30d", "#4f46e5", "#64748b",
    ],
    cool: [
        "#2563eb", "#0891b2", "#7c3aed", "#0d9488", "#4f46e5",
        "#0284c7", "#6366f1", "#14b8a6", "#3b82f6", "#64748b",
    ],
    warm: [
        "#ea580c", "#d97706", "#e11d48", "#b45309", "#f59e0b",
        "#be123c", "#c2410c", "#eab308", "#9a3412", "#78716c",
    ],
    neon: [
        "#22d3ee", "#a3e635", "#f472b6", "#facc15", "#34d399",
        "#818cf8", "#fb7185", "#38bdf8", "#c084fc", "#fb923c",
    ],
    sunset: [
        "#7c3aed", "#db2777", "#ea580c", "#f59e0b", "#e11d48",
        "#9333ea", "#f97316", "#be185d", "#c026d3", "#facc15",
    ],
};

/** One chart's colours: the palette ramp, or shades of the card's colour. */
export function seriesColors(count, palette = "default", base = null) {
    if (palette === "mono" && base) {
        // Shades of one colour: the first slice full, then lighter and lighter.
        const colors = [];
        for (let index = 0; index < count; index++) {
            const alpha = Math.max(0.25, 1 - index * (0.75 / Math.max(1, count - 1)));
            colors.push(withAlpha(base, alpha));
        }
        return colors;
    }
    const ramp = PALETTES[palette] || PALETTES.default;
    const colors = [];
    for (let index = 0; index < count; index++) {
        colors.push(ramp[index % ramp.length]);
    }
    return colors;
}

/** A hex colour with an alpha channel, for dimming what is not the focus. */
export function withAlpha(hex, alpha) {
    const clean = String(hex || "").replace("#", "");
    if (clean.length !== 6) {
        return hex;
    }
    const value = parseInt(clean, 16);
    const r = (value >> 16) & 255;
    const g = (value >> 8) & 255;
    const b = value & 255;
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

/**
 * Chart ink that follows the backend theme. Read at draw time rather than
 * stored: the user can flip the theme while a board is open.
 */
export function chartInk() {
    const dark =
        document.documentElement.dataset.colorScheme === "dark" ||
        (window.matchMedia &&
            window.matchMedia("(prefers-color-scheme: dark)").matches &&
            !document.documentElement.dataset.colorScheme);
    return dark
        ? { ink: "#e7e6e1", muted: "#898781", grid: "#2c2c2a" }
        : { ink: "#0b0b0b", muted: "#6b6a66", grid: "#e1e0d9" };
}

/** Ordered, translated palette, for a colour picker. */
export function getCardPalette() {
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
    ].map((entry) => ({ ...entry, hex: CARD_COLORS[entry.key] }));
}
