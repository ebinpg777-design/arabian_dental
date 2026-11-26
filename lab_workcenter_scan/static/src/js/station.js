/** @odoo-module **/

import { Component, onWillStart, onWillUnmount, proxy, usePlugin, signal, useProps } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { NotificationPlugin } from "@web/core/notifications/notification_plugin";
import { registry } from "@web/core/registry";
import { browser } from "@web/core/browser/browser";
import { user } from "@web/core/user";
import { useService } from "@web/core/utils/hooks";
import { scanBarcode } from "@web/core/barcode/barcode_dialog";
import { isBarcodeScannerSupported } from "@web/core/barcode/barcode_video_scanner";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { DoingItDialog } from "@lab_workcenter_scan/js/doing_it_dialog";
import { WhereDialog } from "@lab_workcenter_scan/js/where_dialog";
import { CancelDialog } from "@lab_workcenter_scan/js/cancel_dialog";
import { AmendmentDialog } from "@lab_workcenter_scan/js/amendment_dialog";
import { JobCodeDialog } from "@lab_workcenter_scan/js/job_code_dialog";
import { PendingDialog } from "@lab_workcenter_scan/js/pending_dialog";
import { ReassignDialog } from "@lab_workcenter_scan/js/reassign_dialog";
import { FindDialog } from "@lab_workcenter_scan/js/find_dialog";
import { ChangeDialog } from "@lab_workcenter_scan/js/change_dialog";
import { RestartDialog } from "@lab_workcenter_scan/js/restart_dialog";
import { TargetsDialog } from "@lab_workcenter_scan/js/targets_dialog";
import { StationPickDialog } from "@lab_workcenter_scan/js/station_pick_dialog";
import { ActionPlugin } from "@web/webclient/actions/action_plugin";

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
    props = useProps();

    setup() {
        this.orm = useService("orm");
        this.action = usePlugin(ActionPlugin);
        this.notification = usePlugin(NotificationPlugin);
        this.dialog = useService("dialog");
        this.state = proxy({
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
            // Who the lead is scanning FOR: the people working the tray in hand.
            // Chosen once, and every scan after it goes to them instead of asking
            // card by card. Kept on this device, for this bench, for today.
            // (client, 2026-11-25)
            team: [],
        });
        this.typedRef = signal.ref();
        this.findRef = signal.ref();
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
        this.recallTeam();
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

    // ------------------------------------------------------- who the scans go to
    /** Where this bench's team is remembered: per person, per bench, on this device. */
    get teamKey() {
        const id = this.state.data?.workcenter?.id;
        return id ? `lab_station_team_${user.userId}_${id}` : null;
    }

    /** Today, as the device reads it: a team is for a day's trays, not for ever. */
    get teamDay() {
        const now = new Date();
        return `${now.getFullYear()}-${now.getMonth() + 1}-${now.getDate()}`;
    }

    /**
     * The team chosen earlier today at this bench, if its people are still posted
     * here. Read whenever the board is: the lead may have changed bench, and the
     * bench its people.
     */
    recallTeam() {
        const key = this.teamKey;
        let ids = [];
        if (key && this.state.data?.can_assign) {
            try {
                const kept = JSON.parse(browser.localStorage.getItem(key) || "null");
                if (kept && kept.day === this.teamDay && Array.isArray(kept.ids)) {
                    ids = kept.ids;
                }
            } catch {
                ids = [];
            }
        }
        const here = new Set(this.benchPeople.map((p) => p.id));
        ids = ids.filter((id) => here.has(id));
        if (ids.join() !== this.state.team.join()) {
            this.state.team = ids;
        }
    }

    setTeam(ids) {
        this.state.team = [...ids];
        const key = this.teamKey;
        if (!key) {
            return;
        }
        try {
            if (ids.length) {
                browser.localStorage.setItem(key, JSON.stringify({ day: this.teamDay, ids }));
            } else {
                browser.localStorage.removeItem(key);
            }
        } catch {
            // storage refused (a private window): the team lasts until the page closes
        }
    }

    /** The people being scanned for, as the strip shows them. */
    get teamPeople() {
        return this.state.team
            .map((id) => this.benchPeople.find((p) => p.id === id))
            .filter(Boolean);
    }

    initials(name) {
        return (name || "")
            .split(/\s+/)
            .filter(Boolean)
            .slice(0, 2)
            .map((w) => w[0].toUpperCase())
            .join("");
    }

    /** Everybody on a card, by first name: three people fit where one full name did. */
    teamLine(card) {
        const team = card.bench_team || [];
        if (team.length < 2) {
            return card.bench_user || "";
        }
        return team.map((p) => p.name.split(/\s+/)[0]).join(", ");
    }

    teamTitle(card) {
        return (card.bench_team || []).map((p) => p.name).join(", ");
    }

    /** Choose, change or stop who the scans go to. */
    chooseTeam() {
        const data = this.state.data;
        if (!data || !data.can_assign) {
            return;
        }
        this.dialog.add(DoingItDialog, {
            people: this.benchPeople,
            canAssign: true,
            stage: "team",
            teams: data.recent_teams || [],
            preset: [...this.state.team],
            onPick: async (userIds) => {
                this.setTeam(userIds);
                this.notification.add(
                    _t("Scanning for %s.", this.teamPeople.map((p) => p.name).join(", ")),
                    { type: "success" }
                );
            },
        });
    }

    stopTeam() {
        this.setTeam([]);
        this.notification.add(_t("Scans will ask who is doing each job again."), {
            type: "info",
        });
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
                this.typedRef()?.focus();
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
        const code = await this.jobCode(_t("Start again"));
        if (!code) {
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
        const code = await this.jobCode(_t("Correction"));
        if (!code) {
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
            helperIds: found.helper_ids || [],
            finisherUserId: found.finisher_user_id,
            canAssign: !!found.can_assign,
            finished: !!found.finished,
            finishedAt: found.finished_at || "",
            onConfirm: async ({ benchUserId, helperIds, finisherUserId, status }) => {
                const result = await this.orm.call("lab.station", "apply_change", [
                    found.job.id,
                    benchUserId,
                    finisherUserId,
                    status,
                    this.state.person,
                    helperIds,
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
        const code = await this.jobCode(_t("Cancel a step"));
        if (!code) {
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

    /**
     * Scan a job card to mark its case as waiting on an amendment, or to say the
     * amendment is done. The scan only FINDS the case; the dialog asks what the
     * amendment is. Any job of the lab, not only one at this bench: the call
     * that brings an amendment often comes after the job has moved on.
     * (client, 2026-11-26)
     */
    async amendByScan() {
        if (this.state.busy) {
            return;
        }
        const code = await this.jobCode(_t("Amendment"));
        if (!code) {
            return;
        }
        this.state.busy = -1;
        let found;
        try {
            found = await this.orm.call("lab.station", "scan_to_amend", [
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
        this.showAmendment(found);
    }

    /** The AMENDMENT badge on a card, tapped: what is waiting, and Done. */
    async openAmendment(card) {
        let found;
        try {
            found = await this.orm.call("lab.station", "amendment_of_job", [
                card.id,
                this.state.data.workcenter.id,
            ]);
        } catch (error) {
            this.notification.add(error.data?.message || error.message, {
                type: "danger",
            });
            return;
        }
        this.showAmendment(found);
    }

    showAmendment(found) {
        const after = (result, type) => {
            this.state.data = result.board;
            this.buzz(true);
            this.notification.add(result.message, { type });
        };
        this.dialog.add(AmendmentDialog, {
            job: found,
            onMark: async (note) => {
                after(
                    await this.orm.call("lab.station", "mark_amendment", [
                        found.order_id,
                        note,
                        this.state.data.workcenter.id,
                        this.state.person,
                    ]),
                    "warning"
                );
            },
            onDone: async () => {
                after(
                    await this.orm.call("lab.station", "amendment_done", [
                        found.order_id,
                        this.state.data.workcenter.id,
                        this.state.person,
                    ]),
                    "success"
                );
            },
        });
    }

    /**
     * Which job? The number typed in the box if there is one; otherwise a small
     * dialog that takes the number OR opens the camera. Pressing Correction used
     * to go straight to the camera on a phone, and a card whose label would not
     * read left no way to say which job was meant - the number had to be typed
     * in the box BEFORE the button, which nobody guessed. Resolves with the code,
     * or with nothing when the question is closed. (client, 2026-11-26: "Correct
     * button also able to enter number along scanning")
     */
    jobCode(title) {
        const typed = (this.state.typed || "").trim();
        if (typed) {
            return Promise.resolve(typed);
        }
        return new Promise((resolve) => {
            let scanning = false;
            let settled = false;
            const done = (value) => {
                if (!settled) {
                    settled = true;
                    resolve(value || null);
                }
            };
            this.dialog.add(
                JobCodeDialog,
                {
                    title,
                    canScan: this.canScan,
                    onCode: (code) => done(code),
                    onScan: async () => {
                        scanning = true;
                        try {
                            done(await scanBarcode(this.env));
                        } catch {
                            done(null);
                        }
                    },
                },
                { onClose: () => !scanning && done(null) }
            );
        });
    }

    /**
     * Scan a job card to mark the job pending - it cannot go on for now - or to
     * say work on it goes on. Any open job of the lab. (client, 2026-11-26)
     */
    async pendByScan() {
        if (this.state.busy) {
            return;
        }
        const code = await this.jobCode(_t("Pending"));
        if (!code) {
            return;
        }
        this.state.busy = -1;
        let found;
        try {
            found = await this.orm.call("lab.station", "scan_to_pend", [
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
        this.showPending(found);
    }

    /** The pause button or the PENDING badge on a card. */
    async openPending(card) {
        let found;
        try {
            found = await this.orm.call("lab.station", "pending_of_job", [
                card.id,
                this.state.data.workcenter.id,
            ]);
        } catch (error) {
            this.notification.add(error.data?.message || error.message, {
                type: "danger",
            });
            return;
        }
        this.showPending(found);
    }

    showPending(found) {
        const after = (result, type) => {
            this.state.data = result.board;
            this.buzz(true);
            this.notification.add(result.message, { type });
        };
        this.dialog.add(PendingDialog, {
            job: found,
            onMark: async (reason) => {
                after(
                    await this.orm.call("lab.station", "mark_pending", [
                        found.production_id,
                        reason,
                        this.state.data.workcenter.id,
                        this.state.person,
                    ]),
                    "warning"
                );
            },
            onResume: async () => {
                after(
                    await this.orm.call("lab.station", "resume_pending", [
                        found.production_id,
                        this.state.data.workcenter.id,
                        this.state.person,
                    ]),
                    "success"
                );
            },
        });
    }

    /**
     * Scan a job card to pass the job to somebody else at this bench. The scan
     * only FINDS it; the dialog asks to whom and why. (client, 2026-11-26)
     */
    async reassignByScan() {
        if (this.state.busy) {
            return;
        }
        const code = await this.jobCode(_t("Reassign"));
        if (!code) {
            return;
        }
        this.state.busy = -1;
        let found;
        try {
            found = await this.orm.call("lab.station", "scan_to_reassign", [
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
        this.reassignJob(found.job);
    }

    /** The lead passes a job somebody has to somebody else, keeping both names. */
    reassignJob(card) {
        this.dialog.add(ReassignDialog, {
            job: card,
            people: this.benchPeople,
            onConfirm: async (userIds, reason, note) => {
                const result = await this.orm.call("lab.station", "reassign", [
                    card.id,
                    userIds,
                    reason,
                    note,
                    this.state.person,
                ]);
                this.state.data = result.board;
                this.buzz(true);
                this.notification.add(result.message, { type: "success" });
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
                // who this scan is for, where the lead has said so
                this.state.team.length ? [...this.state.team] : false,
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
    /**
     * The jobs ticked on the board, given out together - to one person or to the
     * people working them together. The same question the scan asks, so a lead
     * learns it once. (client, 2026-11-25)
     */
    assignPicked() {
        const picked = [...this.state.picked];
        if (!picked.length) {
            return;
        }
        this.dialog.add(DoingItDialog, {
            people: this.benchPeople,
            canAssign: !!this.state.data.can_assign,
            stage: "bulk",
            count: picked.length,
            teams: this.state.data.recent_teams || [],
            preset: [...this.state.team],
            onPick: async (userIds) => {
                this.state.data = await this.orm.call("lab.station", "assign", [
                    picked,
                    userIds,
                    this.state.person,
                ]);
                this.state.picked = [];
                this.buzz(true);
                this.notification.add(
                    _t("%(count)s job(s) given to %(who)s.", {
                        count: picked.length,
                        who: this.namesOf(userIds),
                    }),
                    { type: "success" }
                );
            },
        });
    }

    /** The names behind a list of ids, as a sentence wants them. */
    namesOf(userIds) {
        return userIds
            .map((id) => this.benchPeople.find((p) => p.id === id))
            .filter(Boolean)
            .map((p) => p.name)
            .join(", ") || _t("somebody");
    }

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
                teams: this.state.data.recent_teams || [],
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
            teams: ask.teams || this.state.data?.recent_teams || [],
            // The people already being scanned for are ticked: a team that was not
            // applied (the job was handed on from its card) is one tap to confirm.
            preset: isFinisher ? [] : [...this.state.team],
            // Naming who did it is the lead's call, so keeping the answer is too.
            canKeep: !isFinisher && !!this.state.data?.can_assign,
            onPick: async (userIds, keep) => {
                const method = isFinisher
                    ? "assign_finisher_and_handover"
                    : handsOn
                    ? "assign_and_handover"
                    : "assign";
                // One finisher; the work before it may have been several people's.
                const who = isFinisher ? userIds[0] : userIds;
                const answer = await this.orm.call("lab.station", method, [
                    job.id, who, this.state.person]);
                if (keep && !isFinisher) {
                    this.setTeam(userIds);
                }
                if (answer && answer.action === "needs_finisher") {
                    this.state.data = answer.board;
                    this.askDoingIt(answer);
                    return;
                }
                const board = answer && answer.board ? answer.board : answer;
                this.state.data = board;
                this.buzz(true);
                const name = this.namesOf(userIds);
                const label = this.jobLabel(job);
                this.notification.add(
                    isFinisher
                        ? _t("%(job)s: finished by %(who)s — handed on to %(next)s.", {
                              job: label, who: name,
                              next: job.next_station || _t("the last step"),
                          })
                        : handsOn
                        ? _t("%(job)s: %(who)s — handed on to %(next)s.", {
                              job: label, who: name,
                              next: job.next_station || _t("the last step"),
                          })
                        : _t("%(job)s: %(who)s.", { job: label, who: name }),
                    { type: "success" }
                );
            },
        });
    }

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
