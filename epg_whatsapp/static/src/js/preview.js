/** @odoo-module **/

import { Component, markup, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { standardWidgetProps } from "@web/views/widgets/standard_widget_props";
import { waMarkup } from "@epg_whatsapp/desk/format";

/**
 * The template as the doctor will actually see it.
 *
 * Writing a WhatsApp template blind is how labs end up sending messages with a stray
 * asterisk, a placeholder that never resolved, or a wall of text nobody reads. The body
 * is authored in a plain textarea; this renders it the way WhatsApp will — bubble,
 * bold, document header, footer, timestamp — beside the field, so the author sees the
 * result while they are still writing it.
 *
 * Preview only. Nothing here is sent, so it never has to agree with Meta about
 * anything; its job is to stop a bad message being written in the first place.
 */
export class EpgWaPreview extends Component {
    static template = "epg_whatsapp.Preview";
    static props = { ...standardWidgetProps };

    setup() {
        // Which of the texts is shown: the message, or what the second or third
        // send says instead.
        this.state = useState({ stage: 1 });
    }

    get data() {
        return this.props.record.data;
    }

    get stages() {
        const out = [{ n: 1, label: "1st" }];
        if (this.data.body_2 || this.data.body_3) {
            out.push({ n: 2, label: "2nd" });
        }
        if (this.data.body_3) {
            out.push({ n: 3, label: "3rd+" });
        }
        return out;
    }

    get stageBody() {
        const d = this.data;
        if (this.state.stage >= 3 && d.body_3) {
            return d.body_3;
        }
        if (this.state.stage >= 2 && (d.body_2 || d.body_3)) {
            return d.body_2 || d.body_3;
        }
        return d.body;
    }

    /** The answers, the buttons, the payment: what the doctor can tap. */
    get replies() {
        return (this.data.quick_replies || "").split("\n").map((l) => l.trim()).filter(Boolean);
    }

    get buttons() {
        return (this.data.buttons || "")
            .split("\n")
            .map((l) => l.split("|")[0].trim())
            .filter((label, i, arr) => label && (this.data.buttons || "").split("\n")[i].includes("|"));
    }

    get asPage() {
        return this.data.button_style === "page" && (this.replies.length || this.buttons.length || this.hasDocument || this.data.add_payment_link);
    }

    /** A record was chosen under "Try it": show the finished text, not the chips. */
    get sampled() {
        return Boolean(this.data.sample_res_id && this.data.sample_body);
    }

    get bodyLength() {
        return (this.data.body || "").length;
    }

    get overBudget() {
        return this.bodyLength > 1024;
    }

    /** WhatsApp's own light markup, plus the placeholders left visible as chips. */
    get lines() {
        const raw = (this.sampled && this.state.stage === 1 ? this.data.sample_body : this.stageBody) || "";
        return raw.split("\n").map((line) => ({
            blank: line.trim() === "",
            html: this.format(line),
        }));
    }

    format(line) {
        // Escape first: the body is author-supplied text, and it is about to be
        // rendered as markup.
        let out = line
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;");
        out = waMarkup(out);
        // A placeholder is shown as a chip rather than resolved: the author needs to see
        // WHICH field will land there, and a resolved sample hides a wrong field path.
        // The field as a chip, its format as a small tail: {{date_order|date}}.
        out = out.replace(
            /\{\{\s*([^}|]+?)\s*(?:\|\s*([a-zA-Z_]+)\s*)?\}\}/g,
            (_m, path, fmt) =>
                `<span class="o_wa_var">${path}${fmt ? `<i>|${fmt}</i>` : ""}</span>`
        );
        // Everything author-supplied was escaped above; what is left is our own
        // markup, which t-out only renders when it is told the string is trusted.
        return markup(out);
    }

    get hasDocument() {
        return Boolean(this.data.report_id);
    }

    get documentName() {
        const report = this.data.report_id;
        return (report && (report.display_name || report[1])) || "Document";
    }

    get headerText() {
        return this.data.header_text || "";
    }

    get now() {
        return new Date().toLocaleTimeString([], {
            hour: "2-digit",
            minute: "2-digit",
        });
    }
}

export const epgWaPreview = {
    component: EpgWaPreview,
    fieldDependencies: [
        { name: "body", type: "text" },
        { name: "header_text", type: "char" },
        { name: "footer_text", type: "char" },
        { name: "report_id", type: "many2one" },
        { name: "sample_res_id", type: "many2one_reference" },
        { name: "sample_body", type: "text" },
        { name: "sample_number", type: "char" },
        { name: "body_2", type: "text" },
        { name: "body_3", type: "text" },
        { name: "quick_replies", type: "text" },
        { name: "buttons", type: "text" },
        { name: "button_style", type: "selection" },
        { name: "add_payment_link", type: "boolean" },
    ],
};
registry.category("view_widgets").add("epg_wa_preview", epgWaPreview);
