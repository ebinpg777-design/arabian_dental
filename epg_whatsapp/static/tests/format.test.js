import { describe, expect, test } from "@odoo/hoot";

import { waLines } from "@epg_whatsapp/desk/format";

describe.current.tags("headless");

function render(text, shortLinks = true) {
    const div = document.createElement("div");
    div.innerHTML = waLines(text, shortLinks)
        .map((line) => String(line.html))
        .join("<br/>");
    return div;
}

test("a quote typed into a tracked link stays inside its title", () => {
    // What a contact can send to the business number: the bubble shows a tracked
    // link as a chip whose title is the URL, and a quote used to end that title
    // and open an attribute of the contact's own. (review, 2026-10-04)
    const url = `https://x.example/wa/d/abc"onmouseover="alert(1)'x`;
    const chip = render(`see ${url}`).querySelector(".o_wa_link_short");
    expect(chip).not.toBe(null);
    expect(chip.getAttributeNames().sort()).toEqual(["class", "title"]);
    expect(chip.getAttribute("title")).toBe(url);
});

test("markup is escaped and WhatsApp's marks still apply", () => {
    const div = render("*Due* <img src=x onerror=alert(1)> _today_");
    expect(div.querySelector("img")).toBe(null);
    expect(div.querySelector("strong").textContent).toBe("Due");
    expect(div.querySelector("em").textContent).toBe("today");
    const link = render("https://x.example/a_b_c", false).querySelector(".o_wa_link");
    expect(link.textContent).toBe("https://x.example/a_b_c");
});
