/** @odoo-module **/

import { Component, proxy, useProps, t } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";

/**
 * "Amendment" — the case is waiting on a change, from the bench.
 *
 * The case is named first, so a wrong card is obvious. With no amendment
 * waiting, it asks what the amendment is and marks the order: that order then
 * cannot be invoiced until somebody says the amendment is done. With one
 * waiting, it shows what was asked, by whom and when, and offers "Amendment
 * Done" - or adds a further change to the same note. (client, 2026-11-26)
 */
export class AmendmentDialog extends Component {
    static template = "lab_workcenter_scan.AmendmentDialog";
    static components = { Dialog };
    props = useProps({
        close: t.function(),
        job: t.object(),            // lab.station._amendment_card
        onMark: t.function(),       // async (note) => void
        onDone: t.function(),       // async () => void
    });

    setup() {
        this.state = proxy({ note: "", busy: "", error: "" });
    }

    get hasNote() {
        return !!this.state.note.trim();
    }

    async run(what, call) {
        if (this.state.busy) {
            return;
        }
        this.state.busy = what;
        this.state.error = "";
        try {
            await call();
            this.props.close();
        } catch (error) {
            this.state.error =
                error.data?.message || error.message || "That could not be saved.";
        } finally {
            this.state.busy = "";
        }
    }

    mark() {
        if (this.hasNote) {
            this.run("mark", () => this.props.onMark(this.state.note.trim()));
        }
    }

    done() {
        this.run("done", () => this.props.onDone());
    }
}
