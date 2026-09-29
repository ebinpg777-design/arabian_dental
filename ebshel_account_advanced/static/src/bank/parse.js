/** @odoo-module **/

/**
 * Bank statements pasted from Excel or a PDF, or an exported CSV: find the
 * separator, the date column, and either a signed amount or a debit/credit
 * (withdrawal/deposit) pair. Returns the guessed mapping so the screen can show
 * it and let the user correct it.
 */
const MONTHS = { jan: 1, feb: 2, mar: 3, apr: 4, may: 5, jun: 6, jul: 7, aug: 8, sep: 9, oct: 10, nov: 11, dec: 12 };

export function parseDate(text) {
    const t = String(text || "").trim();
    let m = t.match(/^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})/);
    if (m) {
        return iso(+m[1], +m[2], +m[3]);
    }
    m = t.match(/^(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})/);
    if (m) {
        const y = m[3].length === 2 ? 2000 + +m[3] : +m[3];
        return iso(y, +m[2], +m[1]);           // day first: the way banks in India and Europe print it
    }
    m = t.match(/^(\d{1,2})[-\s/]([A-Za-z]{3})[A-Za-z]*[-\s/,]+(\d{2,4})/);
    if (m && MONTHS[m[2].toLowerCase()]) {
        const y = m[3].length === 2 ? 2000 + +m[3] : +m[3];
        return iso(y, MONTHS[m[2].toLowerCase()], +m[1]);
    }
    return null;
}

function iso(y, mo, d) {
    if (!y || mo < 1 || mo > 12 || d < 1 || d > 31) {
        return null;
    }
    return `${y}-${String(mo).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
}

export function parseAmount(text) {
    let t = String(text || "").trim();
    if (!t) {
        return null;
    }
    let sign = 1;
    if (/^\(.*\)$/.test(t) || /(dr|debit)$/i.test(t) || /^-/.test(t) || /-$/.test(t)) {
        sign = -1;
    }
    if (/cr$/i.test(t)) {
        sign = 1;
    }
    t = t.replace(/[^0-9.,]/g, "");
    if (!t) {
        return null;
    }
    // 1.234,56 (comma decimals) against 1,234.56
    if (/,\d{1,2}$/.test(t) && t.includes(".")) {
        t = t.replace(/\./g, "").replace(",", ".");
    } else {
        t = t.replace(/,/g, "");
    }
    const n = parseFloat(t);
    return isNaN(n) ? null : sign * n;
}

export function splitRows(text) {
    const lines = String(text || "").split(/\r?\n/).filter((l) => l.trim());
    if (!lines.length) {
        return { sep: "\t", rows: [] };
    }
    const counts = ["\t", ";", ",", "|"].map((s) => [s, lines.slice(0, 10).reduce((n, l) => n + (l.split(s).length - 1), 0)]);
    counts.sort((a, b) => b[1] - a[1]);
    const sep = counts[0][1] ? counts[0][0] : "\t";
    const rows = lines.map((l) => (sep === "," ? csvSplit(l) : l.split(sep)).map((c) => c.trim().replace(/^"|"$/g, "")));
    return { sep, rows };
}

function csvSplit(line) {
    const out = [];
    let cur = "", q = false;
    for (const ch of line) {
        if (ch === '"') {
            q = !q;
        } else if (ch === "," && !q) {
            out.push(cur);
            cur = "";
        } else {
            cur += ch;
        }
    }
    out.push(cur);
    return out;
}

/** Guess which column is what, from the header row if there is one, else from the values. */
export function guessMapping(rows) {
    const width = Math.max(...rows.map((r) => r.length), 0);
    const head = (rows[0] || []).map((h) => h.toLowerCase());
    const hasHeader = head.some((h) => /date|narration|description|particular|amount|debit|credit|withdraw|deposit|details/.test(h));
    const body = hasHeader ? rows.slice(1) : rows;
    const find = (re) => head.findIndex((h) => re.test(h));
    let date = hasHeader ? find(/date/) : -1;
    let label = hasHeader ? find(/narration|description|particular|details|remark|label/) : -1;
    let debit = hasHeader ? find(/withdraw|debit|\bdr\b|paid out/) : -1;
    let credit = hasHeader ? find(/deposit|credit|\bcr\b|paid in/) : -1;
    let amount = hasHeader ? find(/^amount|amount$/) : -1;
    const sample = body.slice(0, 20);
    if (date < 0) {
        date = [...Array(width).keys()].find((i) => sample.filter((r) => parseDate(r[i])).length >= sample.length * 0.6) ?? -1;
    }
    const numeric = [...Array(width).keys()].filter((i) => i !== date && sample.filter((r) => !r[i] || parseAmount(r[i]) !== null).length >= sample.length * 0.8
        && sample.some((r) => parseAmount(r[i]) !== null));
    if (amount < 0 && debit < 0 && credit < 0) {
        if (numeric.length >= 3) {             // debit, credit, balance
            debit = numeric[0];
            credit = numeric[1];
        } else if (numeric.length === 2) {     // amount, balance - or debit, credit
            const bothFilled = sample.filter((r) => r[numeric[0]] && r[numeric[1]]).length;
            if (bothFilled >= sample.length * 0.8) {
                amount = numeric[0];
            } else {
                debit = numeric[0];
                credit = numeric[1];
            }
        } else if (numeric.length === 1) {
            amount = numeric[0];
        }
    }
    if (label < 0) {
        const texts = [...Array(width).keys()].filter((i) => i !== date && !numeric.includes(i));
        label = texts.sort((a, b) => avgLen(sample, b) - avgLen(sample, a))[0] ?? -1;
    }
    return { hasHeader, date, label, amount, debit, credit, width };
}

function avgLen(rows, i) {
    return rows.reduce((t, r) => t + (r[i] || "").length, 0) / Math.max(rows.length, 1);
}

export function buildRows(rows, map) {
    const body = map.hasHeader ? rows.slice(1) : rows;
    const out = [];
    for (const r of body) {
        const date = parseDate(r[map.date]);
        let amount = null;
        if (map.amount >= 0) {
            amount = parseAmount(r[map.amount]);
        } else {
            const out_ = map.debit >= 0 ? parseAmount(r[map.debit]) : null;
            const in_ = map.credit >= 0 ? parseAmount(r[map.credit]) : null;
            if (in_) {
                amount = Math.abs(in_);
            } else if (out_) {
                amount = -Math.abs(out_);
            }
        }
        out.push({ date, label: map.label >= 0 ? r[map.label] || "" : "", amount, ok: !!date && !!amount });
    }
    return out;
}
