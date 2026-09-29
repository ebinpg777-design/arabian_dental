/** @odoo-module **/

/** Money the way the company shows it, from the {symbol, position, decimals} the server sends. */
export function money(value, cur, decimals) {
    if (value === null || value === undefined || value === "") {
        return "";
    }
    const n = Number(value);
    const d = decimals !== undefined ? decimals : (cur && cur.decimals !== undefined ? cur.decimals : 2);
    const s = Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
    const sym = (cur && cur.symbol) || "";
    const body = cur && cur.position === "before" ? `${sym} ${s}` : `${s} ${sym}`;
    return n < 0 ? `-${body}` : body;
}

/** 1.2M / 340k style, for chart labels. */
export function compact(value, cur) {
    const n = Number(value || 0);
    const a = Math.abs(n);
    let s;
    if (a >= 1e6) {
        s = (a / 1e6).toFixed(1) + "M";
    } else if (a >= 1e4) {
        s = (a / 1e3).toFixed(0) + "k";
    } else {
        s = a.toLocaleString(undefined, { maximumFractionDigits: 0 });
    }
    const sym = (cur && cur.symbol) || "";
    return (n < 0 ? "-" : "") + (cur && cur.position === "before" ? `${sym}${s}` : `${s}${sym}`);
}

export function pct(value, digits = 1) {
    return `${Number(value || 0).toFixed(digits)}%`;
}

export function today() {
    return new Date().toISOString().slice(0, 10);
}

export function addDays(iso, days) {
    const d = new Date(iso + "T00:00:00");
    d.setDate(d.getDate() + days);
    return d.toISOString().slice(0, 10);
}
