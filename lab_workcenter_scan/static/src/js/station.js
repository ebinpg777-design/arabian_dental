/** @odoo-module **/

import { Component, onWillStart, onWillUnmount, useState, useRef } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { scanBarcode } from "@web/core/barcode/barcode_dialog";
import { isBarcodeScannerSupported } from "@web/core/barcode/barcode_video_scanner";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { DoingItDialog } from "@lab_workcenter_scan/js/doing_it_dialog";
import { WhereDialog } from "@lab_workcenter_scan/js/where_dialog";
import { CancelDialog } from "@lab_workcenter_scan/js/cancel_dialog";
import { FindDialog } from "@lab_workcenter_scan/js/find_dialog";
import { ChangeDialog } from "@lab_workcenter_scan/js/change_dialog";
import { RestartDialog } from "@lab_workcenter_scan/js/restart_dialog";
import { TargetsDialog } from "@lab_workcenter_scan/js/targets_dialog";
import { StationPickDialog } from "@lab_workcenter_scan/js/station_pick_dialog";

const REFRESH_MS = 60000;

/**
 * The board on the bench.
 *
 * Three lists and two buttons. A department head runs this on a tablet with gloves on
 * while doing something else, so every control is thumb-sized and every card says what
 * the job is in the lab's own words — patient, arch, colour — not a manufacturing
 * reference.
 *
 * It refreshes itself, because the whole point is that work ARRIVES from elsewhere: a
 * board that only updates when somebody reloads it is a board that misses the job
 * somebody just sent you.
 */
export class LabStation extends Component {
    static template = "lab_workcenter_scan.Station";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.dialog = useService("dialog");
        this.state = useState({
            data: null,
            loading: true,
            busy: 0,
            seen: "",
            // Whose bench the lead is looking at: a user id, "none" for work nobody has
            // been given yet, or false for the whole station.
            person: false,
            // Jobs ticked for handing out together.
            picked: [],
            // A job number typed instead of scanned: a torn label, a card left at the
            // other bench, a laptop with no camera. Same rule as a scan.
            typed: "",
            // A day other than today being read in the finished column.
            doneBusy: false,
            // What the bench is looking for, and how the queue is ordered.
            // Both go to the server: a column holds forty cards and the queue
            // behind them can be nine hundred deep, so searching the page
            // would find almost nothing. (client, 2026-09-10)
            query: "",
            sort: "oldest",
        });
        this.typedRef = useRef("typed");
        this.findRef = useRef("find");
        this.findTimer = null;
        onWillStart(() => this.load());
        // Polling only while the screen is actually being looked at. This board lives on
        // a phone in an apron pocket; refreshing every few seconds behind a locked
        // screen spends battery and mobile data on a page nobody can see. Coming back
        // to the tab refreshes at once, so it is never stale when it matters.
        this.tick = () => {
            // Not while another day is on screen: the poll would replace it with
            // today's every few seconds. (client, 2026-09-09)
            // Nor while the bench is searching, which a refresh would wipe.
            if (!document.hidden && !this.state.query
                    && (this.state.data?.done_is_today ?? true)) {
                this.load(true);
            }
        };
        this.timer = setInterval(this.tick, REFRESH_MS);
        document.addEventListener("visibilitychange", this.tick);
        onWillUnmount(() => {
            clearTimeout(this.findTimer);
            clearInterval(this.timer);
            document.removeEventListener("visibilitychange", this.tick);
        });
    }

    async load(quiet, workcenterId) {
        // A slow connection must not let auto-refreshes stack up behind each other.
        // Only the quiet ones yield: a refresh the user actually asked for must never
        // be silently dropped because a background poll happened to be in flight.
        if (this.inFlight && quiet) {
            return;
        }
        this.inFlight = true;
        if (!quiet) {
            this.state.loading = true;
        }
        const id = workcenterId ?? (this.state.data?.workcenter?.id || false);
        try {
            this.state.data = await this.orm.call("lab.station", "get_station", [
                id,
                this.state.person,
                (this.state.data && this.state.data.done_day) || false,
                this.state.query,
                this.state.sort,
            ]);
        } finally {
            this.inFlight = false;
        }
        this.state.loading = false;
        this.state.seen = new Date().toLocaleTimeString([], {
            hour: "2-digit",
            minute: "2-digit",
        });
    }

    get canScan() {
        return isBarcodeScannerSupported();
    }

    /** The people a job can actually be given to — the strip also carries the
     *  "not given out" pile, which is a filter and not a person. */
    get benchPeople() {
        return (this.state.data?.bench_people || []).filter((p) => p.id !== "none");
    }

    /**
     * Scan a job card. One scan, and the server decides what it means from the job's
     * state — accept it, hand it on, or say where it actually is.
     *
     * The camera needs HTTPS, so there is always a typed fallback: a shop floor cannot
     * be told the feature needs a different browser, and the code is printed under the
     * QR for exactly this.
     */
    async scanJob(keepGoing = false) {
        // A bench clearing a tray scans twenty cards in a row. Re-opening the camera,
        // waiting for focus and finding the code again between each one is most of the
        // time the job takes, so "Scan many" keeps going until the operator cancels.
        // (client, 2026-08-26)
        do {
            let code = null;
            if (this.canScan) {
                try {
                    code = await scanBarcode(this.env);
                } catch {
                    code = null;
                }
            }
            if (!code) {
                // No camera (plain http on the phone, a laptop without one): the typed
                // field is the way in. Never window.prompt — Android in-app browsers
                // and installed PWAs suppress it, so the person sees nothing at all.
                this.typedRef.el?.focus();
                return;
            }
            if (!(await this.submitCode(code, keepGoing))) {
                continue;
            }
        } while (keepGoing);
    }

    /** A job number typed into the field: exactly what a scan of it would do. */
    async typeJob() {
        const code = (this.state.typed || "").trim();
        if (!code || this.state.busy) {
            return;
        }
        this.state.busy = -1;
        try {
            if (await this.submitCode(code, false)) {
                this.state.typed = "";
            }
        } finally {
            this.state.busy = 0;
        }
    }

    onTypedKeydown(ev) {
        if (ev.key === "Enter") {
            ev.preventDefault();
            this.typeJob();
        }
    }

    /**
     * One code to the server; the job's state decides whether that is an accept, a
     * hand-over, or "it is somewhere else". Returns false when the code was refused
     * and the caller was in a run — so one bad card does not end the run.
     */
    /**
     * A yes/no the caller can await.
     *
     * ConfirmationDialog answers through callbacks, and "Scan many" would run
     * straight on to the next card while the question was still on screen. This
     * turns it into one promise that settles however the dialog closes - the
     * button, Escape, or the backdrop. (client, 2026-09-09)
     */
    askConfirm(props) {
        return new Promise((resolve) => {
            let answered = false;
            const settle = (value) => {
                if (!answered) {
                    answered = true;
                    resolve(value);
                }
            };
            this.dialog.add(
                ConfirmationDialog,
                { ...props, confirm: () => settle(true), cancel: () => settle(false) },
                { onClose: () => settle(false) }
            );
        });
    }

    /**
     * Look at what this bench passed on, on another day.
     *
     * Only the finished column moves: stepping back through last week must not
     * re-read the queues the bench is standing in front of, and must not leave a
     * head looking at Tuesday's arrivals believing they are this morning's.
     * Forward stops at today - tomorrow has passed nothing on. (client, 2026-09-09)
     */
    /**
     * Find a job anywhere at this bench. Typed, not scanned: the number on a
     * torn card, or the patient a doctor is asking about on the telephone.
     * Debounced, because it is a query over the whole queue and a bench types
     * with gloves on. (client, 2026-09-10)
     */
    findJobs(text) {
        this.state.query = text;
        clearTimeout(this.findTimer);
        this.findTimer = setTimeout(() => this.load(false), 350);
    }

    /** Oldest first, urgent first, newest first - over the whole queue. */
    sortQueue(key) {
        if (this.state.sort === key) {
            return;
        }
        this.state.sort = key;
        this.load(false);
    }

    /** How many jobs the search found, across every column of this bench. */
    get matchTotal() {
        const t = this.state.data && this.state.data.totals;
        if (!t) {
            return 0;
        }
        return (t.incoming || 0) + (t.working || 0) + (t.awaiting || 0);
    }

    get sortLabel() {
        const rows = (this.state.data && this.state.data.sorts) || [];
        const row = rows.find((s) => s.key === (this.state.data.sort || "oldest"));
        return (row ? row.label : "Oldest first").toLowerCase();
    }

    async showDone(day) {
        const data = this.state.data;
        if (!data || this.state.doneBusy) {
            return;
        }
        if (day > data.today) {
            day = data.today;
        }
        this.state.doneBusy = true;
        try {
            const answer = await this.orm.call("lab.station", "done_on", [
                data.workcenter.id, day, this.state.person, this.state.query]);
            Object.assign(this.state.data, {
                done_today: answer.rows,
                bench_people: answer.bench_people,
                bench_day: answer.bench_day,
                stations: answer.stations || this.state.data.stations,
                done_day: answer.done_day,
                done_is_today: answer.done_is_today,
                done_total: answer.done_total,
            });
        } finally {
            this.state.doneBusy = false;
        }
    }

    /** One day back or forward on the finished column. */
    stepDone(days) {
        const day = this.state.data && this.state.data.done_day;
        if (!day) {
            return;
        }
        const [y, m, d] = day.split("-").map((n) => +n);
        const moved = new Date(y, m - 1, d + days);
        const pad = (n) => String(n).padStart(2, "0");
        this.showDone(
            `${moved.getFullYear()}-${pad(moved.getMonth() + 1)}-${pad(moved.getDate())}`
        );
    }

    /** The day this column is showing, read out rather than left as an ISO date. */
    get doneLabel() {
        const data = this.state.data;
        if (!data || !data.done_day) {
            return "";
        }
        if (data.done_is_today) {
            return _t("today");
        }
        const [y, m, d] = data.done_day.split("-").map((n) => +n);
        return new Date(y, m - 1, d).toLocaleDateString(undefined, {
            weekday: "short", day: "numeric", month: "short",
        });
    }

    /**
     * Where is this job? Read, and nothing else.
     *
     * The board could already answer this, but only by refusing a scan - you had
     * to try to DO something to be told the job was elsewhere. A head with a
     * doctor on the phone wants the answer on its own. Typed number if there is
     * one, the camera otherwise. (client, 2026-09-09)
     */
    async findJob() {
        if (this.state.busy) {
            return;
        }
        let code = (this.state.typed || "").trim();
        if (!code && this.canScan) {
            try {
                code = await scanBarcode(this.env);
            } catch {
                code = null;
            }
        }
        // Searched the way Track Order searches - part of the number, the patient
        // or the doctor - rather than demanding one exact code. Whatever was typed
        // or scanned starts the search; the dialog narrows it as it is typed.
        // (client, 2026-09-14)
        this.state.typed = "";
        this.dialog.add(FindDialog, {
            term: code || "",
            workcenterId: this.state.data.workcenter.id,
            open: (id) => this.openWorkorder({ id }),
        });
    }

    /**
     * Scan a job card to start that case again. The scan only FINDS the job —
     * the same guarded dialog then asks why, exactly as the card's own button
     * does, because this is the one act on the board that cannot be undone and
     * a mis-scan must not be able to commit it. (client, 2026-09-12)
     */
    async restartByScan() {
        if (this.state.busy) {
            return;
        }
        let code = (this.state.typed || "").trim();
        if (!code && this.canScan) {
            try {
                code = await scanBarcode(this.env);
            } catch {
                code = null;
            }
        }
        if (!code) {
            this.typedRef.el?.focus();
            return;
        }
        this.state.busy = -1;
        let found;
        try {
            found = await this.orm.call("lab.station", "scan_to_restart", [
                code,
                this.state.data.workcenter.id,
                this.state.person,
            ]);
        } catch (error) {
            this.buzz(false);
            this.notification.add(error.data?.message || error.message, {
                type: "danger",
            });
            return;
        } finally {
            this.state.busy = 0;
        }
        this.buzz(true);
        this.state.typed = "";
        this.askRestart(found.job);
    }

    /**
     * Scan a job card to correct the wrong name or a double scan. The scan only
     * FINDS the job; the dialog changes nothing until it is confirmed, and will
     * not confirm until something has actually been changed.
     * (client, 2026-09-12)
     */
    async changeByScan() {
        if (this.state.busy) {
            return;
        }
        let code = (this.state.typed || "").trim();
        if (!code && this.canScan) {
            try {
                code = await scanBarcode(this.env);
            } catch {
                code = null;
            }
        }
        if (!code) {
            this.typedRef.el?.focus();
            return;
        }
        this.state.busy = -1;
        let found;
        try {
            found = await this.orm.call("lab.station", "scan_to_change", [
                code,
                this.state.data.workcenter.id,
                this.state.person,
            ]);
        } catch (error) {
            this.buzz(false);
            this.notification.add(error.data?.message || error.message, {
                type: "danger",
            });
            return;
        } finally {
            this.state.busy = 0;
        }
        this.buzz(true);
        this.state.typed = "";
        this.dialog.add(ChangeDialog, {
            job: found.job,
            people: found.people || [],
            status: found.status,
            wantsFinisher: !!found.wants_finisher,
            benchUserId: found.bench_user_id,
            finisherUserId: found.finisher_user_id,
            canAssign: !!found.can_assign,
            onConfirm: async ({ benchUserId, finisherUserId, status }) => {
                const result = await this.orm.call("lab.station", "apply_change", [
                    found.job.id,
                    benchUserId,
                    finisherUserId,
                    status,
                    this.state.person,
                ]);
                this.state.data = result.board;
                this.buzz(true);
                this.notification.add(result.message, { type: "success" });
            },
        });
    }

    /**
     * Scan a job card to cancel its step at this bench: work that does not belong
     * here. The scan only FINDS the job; the dialog asks why before anything is
     * cancelled. (client, 2026-09-14)
     */
    async cancelByScan() {
        if (this.state.busy) {
            return;
        }
        let code = (this.state.typed || "").trim();
        if (!code && this.canScan) {
            try {
                code = await scanBarcode(this.env);
            } catch {
                code = null;
            }
        }
        if (!code) {
            this.typedRef.el?.focus();
            return;
        }
        this.state.busy = -1;
        let found;
        try {
            found = await this.orm.call("lab.station", "scan_to_cancel", [
                code,
                this.state.data.workcenter.id,
                this.state.person,
            ]);
        } catch (error) {
            this.buzz(false);
            this.notification.add(error.data?.message || error.message, {
                type: "danger",
            });
            return;
        } finally {
            this.state.busy = 0;
        }
        this.buzz(true);
        this.state.typed = "";
        this.dialog.add(CancelDialog, {
            job: found.job,
            onConfirm: async (reason) => {
                const result = await this.orm.call("lab.station", "cancel_step", [
                    found.job.id,
                    reason,
                    this.state.person,
                ]);
                this.state.data = result.board;
                this.buzz(true);
                this.notification.add(result.message, { type: "warning" });
            },
        });
    }

    async submitCode(code, keepGoing, confirmed = false) {
        let result;
        try {
            result = await this.orm.call("lab.station", "scan", [
                code,
                this.state.data.workcenter.id,
                this.state.person,
                confirmed,
            ]);
        } catch (error) {
            this.buzz(false);
            if (!keepGoing) {
                throw error;
            }
            this.notification.add(error.data?.message || error.message, {
                type: "danger",
            });
            return false;
        }
        this.state.data = result.board;
        if (result.action === "confirm") {
            // Nothing has been written yet: this is the scan read back as a
            // question. The buzz still fires - the card WAS read, and on a loud
            // floor that is the half the operator was waiting for.
            this.buzz(true);
            const ok = await this.askConfirm({
                title: _t("Confirm this scan"),
                body: result.message,
                confirmLabel: result.intent === "accept"
                    ? _t("Accept it")
                    : _t("Finish and hand on"),
                cancelLabel: _t("Not this one"),
            });
            if (!ok) {
                this.notification.add(_t("Scan cancelled — nothing changed."), {
                    type: "info",
                });
                return true;
            }
            return await this.submitCode(result.code || code, keepGoing, true);
        }
        if (result.action === "needs_finisher") {
            // The second name, at a finishing bench. Same popup, same one tap.
            this.buzz(true);
            this.askDoingIt(result);
            return true;
        }
        if (result.action === "needs_person") {
            // Not a refusal: a question. The scan found the job; the popup finishes it.
            this.buzz(true);
            this.askDoingIt(result);
            return true;
        }
        this.buzz(result.action !== "elsewhere");
        this.notification.add(result.message, {
            type: result.action === "elsewhere" ? "warning" : "success",
        });
        return true;
    }

    /**
     * Confirmation you can feel. A workshop is loud and the phone is usually in a
     * pocket or a stand by the time the notification appears, so a scan that only
     * reports itself on screen is a scan people repeat.
     */
    buzz(good) {
        try {
            navigator.vibrate?.(good ? 60 : [40, 60, 40]);
        } catch {
            // A browser without the vibration API is not a reason to lose the scan.
        }
    }

    /**
     * Start the whole case again. Opens the guarded dialog — the tap itself changes
     * nothing, which is the point: this is the one act on the board that cannot be
     * undone. (client, 2026-08-29)
     */
    askRestart(card) {
        this.dialog.add(RestartDialog, {
            job: card,
            reasons: this.state.data.redo_reasons || [],
            onConfirm: async (reasonId, note) => {
                const result = await this.orm.call("lab.station", "restart", [
                    card.id,
                    reasonId,
                    note || false,
                    this.state.person,
                ]);
                this.state.data = result.board;
                this.buzz(true);
                this.notification.add(result.message, { type: "warning" });
            },
        });
    }

    /** Give a job to somebody at this bench. */
    async assign(card, ev) {
        const userId = parseInt(ev.target.value, 10) || false;
        this.state.data = await this.orm.call("lab.station", "assign", [
            card.id,
            userId,
            this.state.person,
        ]);
    }

    /**
     * Look at one person's bench — or at the pile nobody has been given yet, which is
     * the one a lead actually opens this board to empty.
     */
    async showPerson(person) {
        const next = this.state.person === person.id ? false : person.id;
        this.state.person = next;
        this.state.picked = [];
        await this.load(false);
    }

    /**
     * The bench's targets for the day being looked at, set where the day is
     * read. The dialog edits this bench's own numbers; the board comes back
     * re-read so the chips and the bench line show them at once. (client, 2026-09-10)
     */
    setTargets() {
        const data = this.state.data;
        if (!data || !data.workcenter || !data.can_set_targets) {
            return;
        }
        this.dialog.add(TargetsDialog, {
            people: this.benchPeople,
            dayLabel: this.doneLabel,
            station: data.workcenter.name,
            onSave: async (values) => {
                this.state.data = await this.orm.call("lab.station", "set_targets", [
                    data.workcenter.id,
                    data.done_day,
                    values,
                    this.state.person,
                ]);
                this.notification.add(_t("Targets set for %s.", this.doneLabel), {
                    type: "success",
                });
            },
        });
    }

    isPicked(card) {
        return this.state.picked.includes(card.id);
    }

    togglePick(card) {
        this.state.picked = this.isPicked(card)
            ? this.state.picked.filter((id) => id !== card.id)
            : [...this.state.picked, card.id];
    }

    /** Hand the ticked jobs to one technician in a single go. */
    async assignPicked(ev) {
        const userId = parseInt(ev.target.value, 10) || false;
        if (!userId || !this.state.picked.length) {
            return;
        }
        const count = this.state.picked.length;
        const who = this.state.data.bench_people.find((p) => p.id === userId);
        this.state.data = await this.orm.call("lab.station", "assign", [
            this.state.picked,
            userId,
            this.state.person,
        ]);
        this.state.picked = [];
        this.notification.add(
            _t("%(count)s job(s) given to %(who)s.", { count, who: who?.name || "" }),
            { type: "success" }
        );
    }

    /** Move the board to another bench. */
    async goToStation(stationId) {
        this.state.person = false;
        this.state.picked = [];
        await this.load(false, stationId || false);
    }

    /** Ask which bench this is, in the floor's own language. (client, 2026-09-10) */
    openStationPicker() {
        const data = this.state.data;
        if (!data) {
            return;
        }
        this.dialog.add(StationPickDialog, {
            stations: data.stations,
            currentId: (data.workcenter && data.workcenter.id) || false,
            dayLabel: this.doneLabel,
            onPick: (stationId) => this.goToStation(stationId),
        });
    }

    /** The bench's own colour, the same hue the picker gives it. */
    get stationHue() {
        const id = this.state.data?.workcenter?.id || 0;
        return (id * 47) % 360;
    }

    stationInitials(name) {
        return (name || "")
            .split(/[\s/]+/)
            .filter(Boolean)
            .slice(0, 2)
            .map((w) => w[0].toUpperCase())
            .join("");
    }

    /**
     * What a job is CALLED in a message. The bench works by the number printed on the
     * job card, so every confirmation leads with it — a toast that said "VED (SCAN)
     * accepted" named a patient the reader cannot look up, while the number they had
     * just scanned went unmentioned. The sales order follows it, because that is the
     * reference the office and the doctor use for the same case. (client, 2026-08-29)
     */
    jobLabel(card) {
        const mo = card.production || card.name;
        return card.order ? `${mo} · ${card.order}` : mo;
    }

    // No `accept(card)` here any more: the card's Accept button is gone, and a
    // method with no caller is one somebody later assumes is wired to something.
    // Taking a job is a scan; `lab.station.accept` still exists server-side for
    // that path and for the tests. (client, 2026-09-12)
    async handover(card) {
        const where = card.next_station || _t("the next step");
        const label = this.jobLabel(card);
        if (!card.bench_user_id) {
            // Straight to the question — no round trip to be told what we can see.
            this.askDoingIt({
                job: card,
                people: (this.state.data.bench_people || []).filter((p) => p.id !== "none"),
                can_assign: !!this.state.data.can_assign,
            });
            return;
        }
        await this.run(card, "handover",
                       _t("%(job)s handed on to %(where)s.", { job: label, where }));
    }

    /**
     * The "who did this?" popup. Picking a tile assigns the job and hands it on in
     * one server call, and the board is whatever comes back.
     */
    askDoingIt(ask) {
        const job = ask.job;
        const handsOn = ask.stage !== "accepted";
        const isFinisher = ask.stage === "finisher";
        this.dialog.add(DoingItDialog, {
            job,
            people: ask.people || [],
            canAssign: !!ask.can_assign,
            message: ask.message,
            stage: ask.stage || "handover",
            onPick: async (userId) => {
                const method = isFinisher
                    ? "assign_finisher_and_handover"
                    : handsOn
                    ? "assign_and_handover"
                    : "assign";
                const answer = await this.orm.call("lab.station", method, [
                    job.id, userId, this.state.person]);
                // Naming the technician at a finishing bench brings the SECOND
                // question back rather than a board: ask it, do not swallow it.
                if (answer && answer.action === "needs_finisher") {
                    this.state.data = answer.board;
                    this.askDoingIt(answer);
                    return;
                }
                const board = answer && answer.board ? answer.board : answer;
                this.state.data = board;
                this.buzz(true);
                const who = (ask.people || []).find((p) => p.id === userId);
                const name = who ? who.name : _t("somebody");
                const label = this.jobLabel(job);
                this.notification.add(
                    isFinisher
                        ? _t("%(job)s: finished by %(who)s — handed on to %(next)s.", {
                              job: label, who: name,
                              next: job.next_station || _t("the last step"),
                          })
                        : handsOn
                        ? _t("%(job)s: technician %(who)s — handed on to %(next)s.", {
                              job: label, who: name,
                              next: job.next_station || _t("the last step"),
                          })
                        : _t("%(job)s: technician %(who)s.", { job: label, who: name }),
                    { type: "success" }
                );
            },
        });
    }

    /**
     * Take back the last scan on this job — the card scanned by mistake.
     *
     * Asks first, unlike every other control here: the rest of the board is one
     * tap BECAUSE each tap is reversible, and this is the tap that does the
     * reversing. What it undoes is named in the question, so nobody has to
     * remember which scan was last. (client, 2026-09-09)
     */
    async undoScan(card) {
        if (this.state.busy) {
            return;
        }
        const label = this.jobLabel(card);
        const handed = !!card.handed_over_at;
        const question = handed
            ? _t("%(job)s was handed on. Take that back and put it on this bench again?",
                 { job: label })
            : _t("%(job)s was accepted here. Take that back and leave it waiting to be taken?",
                 { job: label });
        this.dialog.add(ConfirmationDialog, {
            title: _t("Undo that scan?"),
            body: question,
            confirmLabel: _t("Undo the scan"),
            cancelLabel: _t("Leave it"),
            confirm: async () => {
                this.state.busy = card.id;
                try {
                    const result = await this.orm.call("lab.station", "undo_scan", [
                        card.id, this.state.person]);
                    this.state.data = result.board;
                    this.buzz(true);
                    this.notification.add(result.message, { type: "success" });
                } finally {
                    this.state.busy = 0;
                }
            },
        });
    }

    async run(card, method, message) {
        if (this.state.busy) {
            return;
        }
        this.state.busy = card.id;
        try {
            const result = await this.orm.call("lab.station", method, [card.id]);
            if (result.action === "needs_person" || result.action === "needs_finisher") {
                this.state.data = result.board;
                this.askDoingIt(result);
                return;
            }
            this.state.data = result;
            this.notification.add(message, { type: "success" });
        } finally {
            this.state.busy = 0;
        }
    }

    /** Send a job somewhere other than the next step — a rework, or a broken machine. */
    async sendElsewhere(card) {
        let code = null;
        if (this.canScan) {
            try {
                code = await scanBarcode(this.env);
            } catch {
                code = null;
            }
        }
        if (!code) {
            code = window.prompt(_t("Station code (printed under the QR):"));
        }
        if (!code) {
            return;
        }
        const result = await this.orm.call("lab.station", "move_by_scan", [
            card.id,
            code,
        ]);
        this.state.data = result.board;
        this.notification.add(result.message, {
            type: result.moved ? "success" : "info",
        });
    }

    openWorkorder(card) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "mrp.workorder",
            res_id: card.id,
            views: [[false, "form"]],
        });
    }
}

registry.category("actions").add("lab_station", LabStation);
