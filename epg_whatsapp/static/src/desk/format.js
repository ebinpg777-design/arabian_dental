/** @odoo-module **/

import { markup } from "@odoo/owl";

const TRACKED = /\/wa\/[a-z]\/[^\s<]+$/;

// WhatsApp's marks only count at a word boundary: *bold* and _italic_, never the
// underscore inside amount_residual or a_b in a link.
const EDGE = "(^|[\\s(\\[{\"'])";
const AFTER = "(?=$|[\\s).,!?:;\\]}\"'])";
const MARKS = [
    [new RegExp(EDGE + "\\*(\\S(?:[^*\\n]*?\\S)?)\\*" + AFTER, "g"), "$1<strong>$2</strong>"],
    [new RegExp(EDGE + "_(\\S(?:[^_\\n]*?\\S)?)_" + AFTER, "g"), "$1<em>$2</em>"],
    [new RegExp(EDGE + "~(\\S(?:[^~\\n]*?\\S)?)~" + AFTER, "g"), "$1<s>$2</s>"],
];

/** WhatsApp's light markup on an already HTML-escaped line. */
export function waMarkup(escaped) {
    let out = escaped;
    for (const [re, rep] of MARKS) {
        out = out.replace(re, rep);
    }
    return out;
}

/**
 * WhatsApp's own light markup, as safe HTML lines for a bubble.
 *
 * Escaped first: the text is whatever was typed, and it is about to be rendered.
 * Links are handled in one pass - a second pass used to find the URL again inside
 * the title attribute the first had written, and break the markup. With
 * `shortLinks` a tracked link is shown as a chip, so a bubble is not three lines
 * of token; the send preview keeps them whole, since that is what the doctor gets.
 */
export function waLines(text, shortLinks = true) {
    return (text || "").split("\n").map((line) => {
        let out = line.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
        out = waMarkup(out);
        out = out.replace(/(https?:\/\/[^\s<]+)/g, (url) =>
            shortLinks && TRACKED.test(url)
                ? `<span class="o_wa_link o_wa_link_short" title="${url}">🔗 link</span>`
                : `<span class="o_wa_link">${url}</span>`
        );
        return { blank: !line.trim(), html: markup(out) };
    });
}

/** Two letters for an avatar: "DR ANJALI MENON" → "AM". */
export function initials(name) {
    return (name || "?")
        .replace(/^(dr\.?\s+)/i, "")
        .split(/\s+/)
        .filter(Boolean)
        .slice(0, 2)
        .map((w) => w[0].toUpperCase())
        .join("");
}
