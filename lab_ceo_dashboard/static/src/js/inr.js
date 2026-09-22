/** @odoo-module **/

/**
 * Indian-style money shortening: 259,043,615.31 is not a number anyone reads,
 * 25.9 Cr is. Shared by the hub tiles and the pulse axes so the same rupees
 * never read two different ways on the same screen.
 */
export function shortINR(v) {
    const n = Math.abs(v || 0);
    if (n >= 1e7) {
        return (v / 1e7).toFixed(1) + " Cr";
    }
    if (n >= 1e5) {
        return (v / 1e5).toFixed(1) + " L";
    }
    if (n >= 1e3) {
        return (v / 1e3).toFixed(0) + " k";
    }
    return String(Math.round(v || 0));
}
