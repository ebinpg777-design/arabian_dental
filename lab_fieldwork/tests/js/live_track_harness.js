// The browser recorder (static/src/js/live_track.js, LiveTracker) run in Node with a
// fake clock, fake timers and a fake phone, to see what it would post - the way
// tests/test_live_track_js.py runs it. No browser, no server: the module's imports
// are cut off and what they named is stubbed.
//
//     node live_track_harness.js <path to live_track.js>
//
// Exit 0 when every scenario posts what it should; the scenarios are at the bottom.
const fs = require("fs");
const vm = require("vm");
let src = fs.readFileSync(process.argv[2], "utf8");
src = src.replace(/^import .*$/gm, "").replace(/^export /gm, "");
src = src.replace(/^registry\.category\("services"\)\.add\(.*$/m, "");
src += "\n;globalThis.__LiveTracker = LiveTracker;";

function world() {
    const clock = { now: Date.UTC(2026, 10, 11, 4, 0, 0) };
    const timers = [];
    class FakeDate extends Date {
        constructor(...a) { if (a.length) { super(...a); } else { super(clock.now); } }
        static now() { return clock.now; }
    }
    const store = {};
    const phone = { watch: null, asked: 0, pendingNow: [] };
    const ctx = {
        Date: FakeDate, Math, JSON, Number, Set, Promise, Array, Object, String, console,
        setInterval: (fn, ms) => { timers.push({ fn, ms, due: clock.now + ms }); return timers.length; },
        clearInterval: (id) => { if (timers[id - 1]) { timers[id - 1].dead = true; } },
        setTimeout: () => 0, clearTimeout: () => {},
        window: { addEventListener() {}, removeEventListener() {} },
        document: { visibilityState: "visible", addEventListener() {} },
        navigator: {
            userAgent: "Mozilla/5.0 (Linux; Android 10; K) Chrome/153.0.0.0 Mobile",
            geolocation: {
                watchPosition: (ok) => { phone.watch = ok; return 1; },
                clearWatch: () => { phone.watch = null; },
                getCurrentPosition: (ok) => { phone.asked += 1; phone.pendingNow.push(ok); },
            },
        },
        localStorage: { getItem: (k) => store[k] || null, setItem: (k, v) => { store[k] = v; } },
        user: { userId: 7 }, usePlugin: () => null, NotificationPlugin: {}, registry: {}, warnNoPosition() {},
    };
    ctx.globalThis = ctx;
    vm.createContext(ctx);
    vm.runInContext(src, ctx);
    const posted = [];
    const orm = { silent: { call: async (_m, _f, [batch]) => { posted.push(...batch); return batch.length; } } };
    const tracker = new ctx.__LiveTracker(orm, null);
    const api = {
        clock, tracker, ctx, phone, posted,
        /** Let `seconds` pass with the page awake: timers tick on time. */
        awake(seconds) {
            const end = clock.now + seconds * 1000;
            for (;;) {
                const next = timers.filter((t) => !t.dead && t.due <= end).sort((a, b) => a.due - b.due)[0];
                if (!next) { break; }
                clock.now = next.due; next.due += next.ms; next.fn();
            }
            clock.now = end;
        },
        /** The page frozen for `seconds`: no timer runs; on waking each overdue one fires once. */
        asleep(seconds) { clock.now += seconds * 1000; },
        fireOverdue() {
            for (const t of timers.filter((t) => !t.dead && t.due <= clock.now)) {
                t.due = clock.now + t.ms; t.fn();
            }
        },
        fix(lat, lon, extra = {}) {
            return { coords: { latitude: lat, longitude: lon, accuracy: 8, speed: null, heading: null, ...extra },
                     timestamp: clock.now };
        },
        hardware(lat, lon, extra) { phone.watch(api.fix(lat, lon, extra)); },
        answerNow(lat, lon) { const fns = phone.pendingNow.splice(0); fns.forEach((fn) => fn(api.fix(lat, lon))); },
        hide() { ctx.document.visibilityState = "hidden"; tracker.wentBackground(); },
        show() { ctx.document.visibilityState = "visible"; tracker.cameToFront(); },
        kept() {
            return [...new Set(posted.concat(tracker.buffer))].sort((a, b) => (a.ts < b.ts ? -1 : 1))
                .map((p) => `${p.ts.slice(11)} ${p.latitude.toFixed(4)},${p.longitude.toFixed(4)}`);
        },
    };
    tracker.start();
    return api;
}

let failed = 0;
function check(name, got, want) {
    const ok = JSON.stringify(got) === JSON.stringify(want);
    console.log((ok ? "ok   " : "FAIL ") + name);
    if (!ok) { failed += 1; console.log("   got ", JSON.stringify(got)); console.log("   want", JSON.stringify(want)); }
}

// 1. Awake, phone face-down on a desk, hardware quiet: the stop is still said every five minutes.
{
    const w = world();
    w.hardware(11.0, 76.0);
    w.awake(11 * 60);
    check("awake and quiet: heartbeats repeat the place", w.kept(),
          ["04:00:00 11.0000,76.0000", "04:05:00 11.0000,76.0000", "04:10:00 11.0000,76.0000"]);
}
// 2. Locked for 14 min while riding; on waking the timer fires first, then the page is told, then the true fix.
{
    const w = world();
    w.hardware(11.0, 76.0);
    w.awake(30);
    w.hide(); w.asleep(14 * 60);
    w.fireOverdue(); w.show();
    w.clock.now += 1000; w.answerNow(11.05, 76.05);
    w.awake(6 * 60);
    check("woken, timer first: no fix for the place it left", w.kept(),
          ["04:00:00 11.0000,76.0000", "04:14:31 11.0500,76.0500", "04:20:30 11.0500,76.0500"]);
}
// 3. The same, the page told first and the timer after.
{
    const w = world();
    w.hardware(11.0, 76.0);
    w.awake(30);
    w.hide(); w.asleep(14 * 60);
    w.show(); w.fireOverdue();
    w.clock.now += 2000; w.answerNow(11.05, 76.05);
    check("woken, page told first: no fix for the place it left", w.kept(),
          ["04:00:00 11.0000,76.0000", "04:14:32 11.0500,76.0500"]);
}
// 4. Frozen without ever being told it was hidden (no visibilitychange): the stalled timer says so.
{
    const w = world();
    w.hardware(11.0, 76.0);
    w.awake(30);
    w.asleep(20 * 60);
    w.fireOverdue();
    check("frozen untold: the stalled timer repeats nothing", w.kept(), ["04:00:00 11.0000,76.0000"]);
    check("frozen untold: a fix is asked for at once", w.phone.asked, 1);
    w.clock.now += 3000; w.answerNow(11.08, 76.02);
    check("frozen untold: the true fix is kept", w.kept(), ["04:00:00 11.0000,76.0000", "04:20:33 11.0800,76.0200"]);
}
// 5. Hidden but timers still ticking (another app in front): nothing is repeated while hidden.
{
    const w = world();
    w.hardware(11.0, 76.0);
    w.hide(); w.awake(12 * 60);
    check("hidden, timers running: nothing repeated", w.kept(), ["04:00:00 11.0000,76.0000"]);
    w.show(); w.answerNow(11.0, 76.0);
    w.awake(6 * 60);
    check("back in front at the same place: the stop is said again", w.kept(),
          ["04:00:00 11.0000,76.0000", "04:12:00 11.0000,76.0000", "04:17:00 11.0000,76.0000"]);
}
process.exit(failed ? 1 : 0);
