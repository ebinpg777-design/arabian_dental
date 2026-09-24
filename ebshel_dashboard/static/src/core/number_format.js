/** @odoo-module **/

import { formatFloat } from "@web/core/utils/numbers";

/**
 * A card's number, written the way the card asked for.
 *
 * Shared by the card itself and by the form's "how it will read" sample, so
 * the sample never drifts from what the board shows. `item` is any object
 * with the card's `aggregate`, `kind`, `digits` and `number_system`.
 */
export function formatNumber(value, item = {}) {
    const digits = item.aggregate === "count" && !["formula", "scatter"].includes(item.kind)
        ? 0 : item.digits || 0;
    const number = Number(value || 0);
    const system = item.number_system || "auto";
    if (system === "indian") {
        return formatIndian(number, digits);
    }
    if (system === "plain") {
        return formatFloat(number, { digits: [16, digits] });
    }
    // "auto" keeps the exact number up to five figures, because that is
    // where reading it stops being easier than reading "1.2K"; "short"
    // is compact all the way down.
    const humanReadable = system === "short" || Math.abs(number) >= 100000;
    return formatFloat(number, { digits: [16, digits], humanReadable, decimals: 1 });
}

/**
 * The lakh-and-crore grouping: a hundred thousand is 1 L, ten million
 * 1 Cr. Below a lakh the number is written out, because that is how it
 * is read.
 */
function formatIndian(number, digits) {
    const size = Math.abs(number);
    if (size >= 1e7) {
        return `${formatFloat(number / 1e7, { digits: [16, 2] })} Cr`;
    }
    if (size >= 1e5) {
        return `${formatFloat(number / 1e5, { digits: [16, 2] })} L`;
    }
    // Indian grouping below a lakh: 12,345 reads the same as elsewhere.
    return formatFloat(number, { digits: [16, digits] });
}

/** The number with its prefix and unit around it. */
export function formatWithUnit(value, item = {}) {
    const prefix = item.prefix ? `${item.prefix} ` : "";
    const unit = item.symbol ? ` ${item.symbol}` : "";
    return `${prefix}${formatNumber(value, item)}${unit}`;
}
