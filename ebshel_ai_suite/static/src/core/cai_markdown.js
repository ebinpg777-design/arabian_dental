import { markup } from "@odoo/owl";

/**
 * Minimal, safe Markdown renderer for assistant answers.
 *
 * The input is HTML-escaped first; only a small set of constructs is then
 * turned into markup (paragraphs, lists, headings, emphasis, inline code,
 * code blocks, http(s) links and [S1]-style source citations). Nothing coming
 * from the model can inject raw HTML.
 */
function escapeHtml(text) {
    return text
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}

function inline(text) {
    return text
        .replace(/`([^`\n]+)`/g, "<code>$1</code>")
        .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
        .replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s).,;:!?]|$)/g, "$1<em>$2</em>")
        .replace(
            /\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g,
            '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>'
        )
        .replace(/\[(S\d{1,2})\]/g, '<span class="o_cai_cite">$1</span>');
}

export function renderCaiMarkdown(source) {
    const blocks = [];
    const codeBlocks = [];
    let text = escapeHtml(source || "").replace(/```[a-zA-Z0-9_-]*\n?([\s\S]*?)```/g, (_m, code) => {
        codeBlocks.push(`<pre class="o_cai_code"><code>${code.replace(/\n$/, "")}</code></pre>`);
        return `\u0000${codeBlocks.length - 1}\u0000`;
    });
    text = text.replace(/\r\n/g, "\n");
    for (const chunk of text.split(/\n{2,}/)) {
        const lines = chunk.split("\n");
        if (/^\u0000\d+\u0000$/.test(chunk.trim())) {
            blocks.push(chunk.trim());
        } else if (lines.every((l) => /^\s*([-*•]|\d+[.)])\s+/.test(l) || !l.trim())) {
            const ordered = /^\s*\d+[.)]\s+/.test(lines[0]);
            const items = lines
                .filter((l) => l.trim())
                .map((l) => `<li>${inline(l.replace(/^\s*([-*•]|\d+[.)])\s+/, ""))}</li>`)
                .join("");
            blocks.push(ordered ? `<ol>${items}</ol>` : `<ul>${items}</ul>`);
        } else if (/^#{1,4}\s/.test(lines[0]) && lines.length === 1) {
            blocks.push(`<p class="o_cai_heading">${inline(lines[0].replace(/^#{1,4}\s/, ""))}</p>`);
        } else {
            blocks.push(`<p>${lines.map(inline).join("<br/>")}</p>`);
        }
    }
    const html = blocks
        .join("")
        .replace(/\u0000(\d+)\u0000/g, (_m, index) => codeBlocks[Number(index)]);
    return markup(html);
}
