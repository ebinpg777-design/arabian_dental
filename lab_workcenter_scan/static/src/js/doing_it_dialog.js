/** @odoo-module **/

import { Component, onMounted, onWillUnmount, proxy, useProps, t } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { browser } from "@web/core/browser/browser";
import { _t } from "@web/core/l10n/translation";

const TOGETHER_KEY = "lab_station_together";
const KEEP_KEY = "lab_station_keep";

/** A remembered yes/no. A bench browser may refuse storage; then it is simply no. */
function recall(key) {
    try {
        return browser.localStorage.getItem(key) === "1";
    } catch {
        return false;
    }
}

function remember(key, value) {
    try {
        browser.localStorage.setItem(key, value ? "1" : "0");
    } catch {
        // a private window: the choice lasts as long as the dialog
    }
}

/**
 * "Who did this?" — asked at the one moment it cannot be skipped.
 *
 * A job cannot leave a bench without a name on it, but a rule that only says NO
 * sends the lead back to the card, down to the dropdown, and then back to the
 * button: three taps with gloves on, so the rule gets resented. This dialog is
 * the rule made into a single tap: the warning is a label at the top, the people
 * of the bench are tiles below it, and touching a tile assigns the job AND hands
 * it on. A keyboard bench presses the tile's number. (client, 2026-08-29)
 *
 * WORK DONE TOGETHER. A tray is not one person's: three technicians build up
 * twelve crowns between them, and "which ONE of you did this one" has no true
 * answer. So the question takes several names - "Working together" turns the
 * tiles into ticks, the groups the bench lately worked in are one tap each, and
 * "Keep for the next scans" stops the same question being asked for every card
 * in the tray. One person is still one tap. (client, 2026-11-25)
 */
export class DoingItDialog extends Component {
    static template = "lab_workcenter_scan.DoingItDialog";
    static components = { Dialog };
    props = useProps({
        close: t.function(),
        job: t.object().optional(), // the card (name, production, patient, clinic, next_station)
        people: t.array(),          // [{id, name, load, is_lead}] — this bench only
        canAssign: t.boolean(),     // a lead or manager; a technician only gets the label
        message: t.string().optional(),
        // 'accepted': the answer only names who is doing it (job stays on the bench);
        // 'handover': the answer names who did it and hands it on;
        // 'finisher': the answer names who FINISHED it and hands it on - asked at a
        // bench whose work centre records the two separately;
        // 'team': no job at all - who the lead is scanning FOR from now on;
        // 'bulk': the jobs ticked on the board, given out together.
        stage: t.string().optional(),
        count: t.number().optional(),   // 'bulk': how many jobs
        teams: t.array().optional(),    // [{ids, names}] — groups lately worked in here
        preset: t.array().optional(),   // ids already chosen (the team being scanned for)
        canKeep: t.boolean().optional(),// offer "Keep for the next scans"
        onPick: t.function(),       // async (userIds, keep) => void
    });

    setup() {
        const preset = (this.props.preset || []).filter((id) =>
            this.props.people.some((p) => p.id === id)
        );
        this.state = proxy({
            busy: false,
            picked: false,
            // Several names on one job. Remembered, because a bench that works in
            // groups does so every day; a preset of two or more says so by itself.
            together: this.allowsTeam && (preset.length > 1 || recall(TOGETHER_KEY)),
            // In the order they were tapped: the first is the job's technician.
            selected: preset,
            keep: this.isTeamSetup || (!!this.props.canKeep && recall(KEEP_KEY)),
        });
        // 1..9 on a keyboard bench picks that tile; Enter gives the job to the
        // people ticked; Escape is the dialog's own.
        this.onKey = (ev) => {
            if (ev.target && ["INPUT", "TEXTAREA", "SELECT"].includes(ev.target.tagName)) {
                return;
            }
            if (ev.key === "Enter" && this.state.together && this.state.selected.length) {
                ev.preventDefault();
                this.confirm();
                return;
            }
            const n = parseInt(ev.key, 10);
            if (n >= 1 && n <= 9 && this.props.people[n - 1] && this.props.canAssign) {
                ev.preventDefault();
                this.tap(this.props.people[n - 1]);
            }
        };
        onMounted(() => window.addEventListener("keydown", this.onKey, true));
        onWillUnmount(() => window.removeEventListener("keydown", this.onKey, true));
    }

    get handsOn() {
        return !["accepted", "team", "bulk"].includes(this.props.stage);
    }

    get isFinisher() {
        return this.props.stage === "finisher";
    }

    /** Choosing who the scans go to, with no job in hand. */
    get isTeamSetup() {
        return this.props.stage === "team";
    }

    get isBulk() {
        return this.props.stage === "bulk";
    }

    /** One finisher puts their name to a job; the work before it can be shared. */
    get allowsTeam() {
        return !this.isFinisher && this.props.canAssign && this.props.people.length > 1;
    }

    get title() {
        if (this.isFinisher) {
            return _t("Who finished this?");
        }
        if (this.isTeamSetup) {
            return _t("Who are you scanning for?");
        }
        if (this.isBulk) {
            return _t("Give %s job(s) to…", this.props.count || 0);
        }
        return this.handsOn ? _t("Who did this?") : _t("Who is doing this?");
    }

    get lead() {
        if (this.isFinisher) {
            return _t("Tap the finishing technician — it may be somebody other than "
                      + "whoever did the work, and one tap hands the job on.");
        }
        if (this.state.together) {
            return _t("Tick everybody working on it. Each of them counts the job.");
        }
        if (this.isTeamSetup) {
            return _t("Tap the technician — the jobs you scan go under their name.");
        }
        if (this.isBulk) {
            return _t("Tap the technician taking them.");
        }
        return this.handsOn
            ? _t("Tap the technician who did the work — one tap names them and hands it on.")
            : _t("Tap the technician taking it — the job stays on the bench under their name.");
    }

    get warning() {
        if (this.isTeamSetup || this.isBulk) {
            return "";
        }
        const job = this.props.job || {};
        return (
            this.props.message ||
            _t("No technician is named on %s. Choose who did the work and it hands on straight away.",
               job.production || job.name)
        );
    }

    /** Two letters, so a tile reads at arm's length. */
    initials(name) {
        return (name || "")
            .split(/\s+/)
            .filter(Boolean)
            .slice(0, 2)
            .map((w) => w[0].toUpperCase())
            .join("");
    }

    /** The lightest bench gets a nudge: spreading work is the lead's whole job. */
    get lightestId() {
        const real = this.props.people.filter((p) => typeof p.id === "number");
        if (real.length < 2) {
            return null;
        }
        const min = Math.min(...real.map((p) => p.load || 0));
        const lightest = real.filter((p) => (p.load || 0) === min);
        return lightest.length === 1 ? lightest[0].id : null;
    }

    isOn(person) {
        return this.state.selected.includes(person.id);
    }

    /** Where in the team this person was ticked - the first is the technician. */
    orderOf(person) {
        return this.state.selected.indexOf(person.id) + 1;
    }

    get chosen() {
        return this.state.selected
            .map((id) => this.props.people.find((p) => p.id === id))
            .filter(Boolean);
    }

    /** The groups offered as one tap: only those whose people are all still here. */
    get teams() {
        const here = new Set(this.props.people.map((p) => p.id));
        return (this.props.teams || []).filter((team) => team.ids.every((id) => here.has(id)));
    }

    isTeamOn(team) {
        return (
            team.ids.length === this.state.selected.length &&
            team.ids.every((id) => this.state.selected.includes(id))
        );
    }

    /** First names, so three people fit on one chip. */
    teamLabel(team) {
        return team.names.map((name) => name.split(/\s+/)[0]).join(" · ");
    }

    setTogether(on) {
        this.state.together = on;
        remember(TOGETHER_KEY, on);
        if (!on) {
            this.state.selected = [];
        }
    }

    setKeep(on) {
        this.state.keep = on;
        remember(KEEP_KEY, on);
    }

    pickTeam(team) {
        if (this.state.busy) {
            return;
        }
        this.state.selected = this.isTeamOn(team) ? [] : [...team.ids];
    }

    /** One tap: gives the job where one person does it, ticks them where several do. */
    tap(person) {
        if (this.state.busy || !this.props.canAssign) {
            return;
        }
        if (!this.state.together) {
            return this.give([person.id], person.id);
        }
        this.state.selected = this.isOn(person)
            ? this.state.selected.filter((id) => id !== person.id)
            : [...this.state.selected, person.id];
    }

    confirm() {
        if (this.state.selected.length) {
            return this.give([...this.state.selected], false);
        }
    }

    get confirmLabel() {
        const people = this.chosen;
        if (!people.length) {
            return _t("Tick who is working on it");
        }
        const who = people.length === 1
            ? people[0].name
            : _t("%s people", people.length);
        if (this.isTeamSetup) {
            return _t("Scan for %s", who);
        }
        if (this.isBulk) {
            return _t("Give %(count)s job(s) to %(who)s", { count: this.props.count || 0, who });
        }
        return this.handsOn
            ? _t("%s — hand it on", who)
            : _t("Give it to %s", who);
    }

    async give(userIds, spinner) {
        if (this.state.busy || !this.props.canAssign) {
            return;
        }
        this.state.busy = true;
        this.state.picked = spinner;
        try {
            await this.props.onPick(userIds, this.state.keep && !this.isFinisher);
            this.props.close();
        } catch (error) {
            this.state.busy = false;
            this.state.picked = false;
            throw error;
        }
    }
}
