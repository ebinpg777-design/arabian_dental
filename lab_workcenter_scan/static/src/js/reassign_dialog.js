/** @odoo-module **/

import { Component, proxy, useProps, t } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { _t } from "@web/core/l10n/translation";

/**
 * "Reassign" — a job somebody is working on, passed to somebody else.
 *
 * Not a correction. Correct writes the right name over a wrong one; this keeps
 * both: who worked on the job first, who has it now, and why it changed hands.
 * The lead ticks who takes it over - one person or several - and says why in
 * one tap. (client, 2026-11-26)
 */
export class ReassignDialog extends Component {
    static template = "lab_workcenter_scan.ReassignDialog";
    static components = { Dialog };
    props = useProps({
        close: t.function(),
        job: t.object(),            // the card
        people: t.array(),          // [{id, name, load, is_lead}] — this bench only
        onConfirm: t.function(),    // async (userIds, reason, note) => void
    });

    setup() {
        this.state = proxy({ selected: [], reason: "", note: "", busy: false, error: "" });
        // The keys are the server's (lab.workorder.reassign.reason).
        this.reasons = [
            { key: "quality", label: _t("Work not up to standard") },
            { key: "unavailable", label: _t("Technician not available") },
            { key: "load", label: _t("Too much work in hand") },
            { key: "skill", label: _t("Needs a more experienced hand") },
            { key: "other", label: _t("Other") },
        ];
    }

    /** Who has it now, as the card names them. */
    get current() {
        const team = this.props.job.bench_team || [];
        return team.length ? team.map((p) => p.name).join(", ") : this.props.job.bench_user;
    }

    get currentIds() {
        return (this.props.job.bench_team || []).map((p) => p.id);
    }

    isOn(p) {
        return this.state.selected.includes(p.id);
    }

    order(p) {
        return this.state.selected.indexOf(p.id) + 1;
    }

    tap(p) {
        const at = this.state.selected.indexOf(p.id);
        if (at >= 0) {
            this.state.selected.splice(at, 1);
        } else {
            this.state.selected.push(p.id);
        }
    }

    initials(name) {
        return (name || "?")
            .split(/\s+/)
            .slice(0, 2)
            .map((w) => w[0] || "")
            .join("")
            .toUpperCase();
    }

    get names() {
        return this.state.selected
            .map((id) => this.props.people.find((p) => p.id === id)?.name)
            .filter(Boolean);
    }

    /** The same people in the same order is not a reassignment. */
    get unchanged() {
        const now = this.currentIds;
        return (
            now.length === this.state.selected.length &&
            now.every((id, i) => id === this.state.selected[i])
        );
    }

    get ready() {
        return (
            this.state.selected.length > 0 &&
            !this.unchanged &&
            !!this.state.reason &&
            (this.state.reason !== "other" || !!this.state.note.trim()) &&
            !this.state.busy
        );
    }

    get goLabel() {
        if (!this.state.selected.length) {
            return _t("Tick who takes it over");
        }
        if (this.unchanged) {
            return _t("It is already with them");
        }
        if (!this.state.reason) {
            return _t("Say why");
        }
        if (this.state.reason === "other" && !this.state.note.trim()) {
            return _t("Write the reason");
        }
        return _t("Reassign to %s", this.names.join(", "));
    }

    async confirm() {
        if (!this.ready) {
            return;
        }
        this.state.busy = true;
        this.state.error = "";
        try {
            await this.props.onConfirm(
                [...this.state.selected],
                this.state.reason,
                this.state.note.trim()
            );
            this.props.close();
        } catch (error) {
            this.state.error =
                error.data?.message || error.message || _t("That could not be saved.");
        } finally {
            this.state.busy = false;
        }
    }
}
