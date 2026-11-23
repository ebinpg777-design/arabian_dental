// Field Live stepping from day to day, run in Node: the real LiveMap and FieldLive
// classes (static/src/js/live_map.js, field_live.js) with their imports cut off and
// stubbed, a fake server, and no browser.
//
//     node field_live_day_harness.js <live_map.js> <field_live.js>
//
// The bug (2026-11-23, Abdul Latheef on 11 Nov): with a person in focus, a day with no
// positions (a day off) left its empty trail held, and the next day picked was drawn
// with that empty trail - stops and story, no line. Stepping back a day also kept the
// later day's roads for everybody. Exit 0 when every scenario draws the day picked.
const fs = require("fs");
const vm = require("vm");

function stub() {
    const fn = function () {};
    return new Proxy(fn, {
        get: (target, key) => (key === "prototype" ? {} : key === Symbol.toPrimitive ? () => "" : stub()),
        apply: () => stub(),
        construct: () => stub(),
    });
}

function moduleSource(path) {
    let src = fs.readFileSync(path, "utf8");
    const names = [];
    src = src.replace(/^import\s+\{([^}]*)\}\s+from\s+["'][^"']+["'];?\s*$/gm, (_m, list) => {
        for (const part of list.split(",")) {
            const name = part.split(/\s+as\s+/).pop().trim();
            if (name) {
                names.push(name);
            }
        }
        return "";
    });
    src = src.replace(/^export /gm, "");
    return { src, names };
}

const liveMap = moduleSource(process.argv[2]);
const fieldLive = moduleSource(process.argv[3]);
const ctx = { console, Promise, Math, JSON, Date, Number, Object, Array, Set, Map, String, Boolean,
              setTimeout, clearTimeout, setInterval, clearInterval, window: stub(), document: stub() };
for (const name of [...liveMap.names, ...fieldLive.names]) {
    if (name !== "LiveMap") {
        ctx[name] = stub();
    }
}
vm.createContext(ctx);
// both files in one script: field_live.js names LiveMap, which live_map.js defines
vm.runInContext(liveMap.src + "\n" + fieldLive.src.replace(/^const (MOBILE|STATUS_RANK) /gm, "var $1_FL ")
    + "\n;globalThis.__FieldLive = FieldLive; globalThis.__LiveMap = LiveMap;", ctx);

const DAYS = {
    // a working day: a thin overview trail, the full one on request, roads
    "2026-11-11": { thin: [[[11.1, 76.1, 1000, 20], [11.2, 76.2, 2000, 20]]],
                    full: [[[11.1, 76.1, 1000, 20], [11.15, 76.15, 1500, 20], [11.2, 76.2, 2000, 20]]],
                    road: [[[11.1, 76.1, 1000, 20, "vehicle"], [11.2, 76.2, 2000, 20, "vehicle"]]],
                    last: 2000 },
    "2026-11-12": { thin: [[[12.1, 76.1, 5000, 20], [12.2, 76.2, 6000, 20]]],
                    full: [[[12.1, 76.1, 5000, 20], [12.15, 76.15, 5500, 20], [12.2, 76.2, 6000, 20]]],
                    road: [[[12.1, 76.1, 5000, 20, "vehicle"], [12.2, 76.2, 6000, 20, "vehicle"]]],
                    last: 6000 },
    // a day off: nothing recorded
    "2026-11-13": { thin: [], full: [], road: [], last: null },
};
const USER = 7;

function server(calls) {
    return {
        call: async (model, method, _args, kw) => {
            calls.push(`${method} ${kw.day}`);
            const day = DAYS[kw.day];
            const ex = (legs) => ({ user_id: USER, legs: JSON.parse(JSON.stringify(legs)),
                                    last: day.last ? { epoch: day.last } : null });
            if (method === "get_board" || (method === "get_live_map" && !kw.user_ids)) {
                return { day: kw.day, is_today: false, executives: [ex(day.thin)] };
            }
            if (method === "get_live_map") {
                return { day: kw.day, executives: [ex(day.full)] };
            }
            if (method === "get_road_trail") {
                return { legs: JSON.parse(JSON.stringify(day.road)), through: day.last, complete: true, state: "ok" };
            }
            if (method === "get_roads") {
                return {};
            }
            return {};
        },
    };
}

function make(cls, calls) {
    const page = Object.create(cls.prototype);
    page.state = { day: "", data: null, focusId: null, isToday: false, view: "map", show: "all",
                   loading: false, lost: false, replayAt: null };
    page.orm = server(calls);
    page.redraw = () => {};
    page.rangeOf = () => null;
    page.fetchRoads = async () => ({});
    page.config = {};
    return page;
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));
let failed = 0;
function check(name, ok, detail) {
    console.log(`${ok ? "ok  " : "FAIL"} ${name}${ok ? "" : " - " + detail}`);
    if (!ok) {
        failed++;
    }
}

async function main() {
    // 1. Field Live, a person in focus, a day off, then a working day
    {
        const calls = [];
        const page = make(ctx.__FieldLive, calls);
        page.state.day = "2026-11-12";
        await page.load();
        page.state.focusId = USER;
        await page.loadTrail(page.state.data.executives[0]);
        await settle();
        page.state.day = "2026-11-13";
        await page.load();
        await settle();
        page.state.day = "2026-11-11";
        calls.length = 0;
        await page.load();
        await settle();
        await settle();
        const ex = page.state.data.executives[0];
        check("the day after a day off draws its own trail",
              ex.legs.length === 1 && ex.legs[0].length === 3 && ex.legs[0][0][0] === 11.1,
              JSON.stringify(ex.legs));
        check("...fetched for that day",
              calls.includes("get_live_map 2026-11-11"), calls.join(", "));
        check("...with its roads",
              Array.isArray(ex.road_legs) && ex.road_legs.length === 1 && ex.road_legs[0][0][0] === 11.1,
              JSON.stringify(ex.road_legs));
    }
    // 2. Field Live, a person in focus, stepping back from one working day to another
    {
        const calls = [];
        const page = make(ctx.__FieldLive, calls);
        page.state.day = "2026-11-12";
        await page.load();
        page.state.focusId = USER;
        await page.loadTrail(page.state.data.executives[0]);
        await settle();
        page.state.day = "2026-11-11";
        await page.load();
        await settle();
        await settle();
        const ex = page.state.data.executives[0];
        check("stepping back draws the earlier day, not the later one",
              ex.legs[0][0][0] === 11.1 && ex.road_legs && ex.road_legs[0][0][0] === 11.1,
              JSON.stringify([ex.legs, ex.road_legs]));
    }
    // 3. the same day reloaded keeps the full trail it holds
    {
        const calls = [];
        const page = make(ctx.__FieldLive, calls);
        page.state.day = "2026-11-12";
        await page.load();
        page.state.focusId = USER;
        await page.loadTrail(page.state.data.executives[0]);
        await settle();
        calls.length = 0;
        await page.load(true);
        await settle();
        const ex = page.state.data.executives[0];
        check("a reload of the same past day keeps the full trail without asking again",
              ex.legs[0].length === 3 && !calls.includes("get_live_map 2026-11-12"),
              JSON.stringify([ex.legs, calls]));
    }
    // 4. the Live Map overview: roads held only for the same day
    {
        const calls = [];
        const page = make(ctx.__LiveMap, calls);
        page.state.day = "2026-11-12";
        await page.load();
        page.state.data.executives[0].road_legs = DAYS["2026-11-12"].road;
        page.state.data.executives[0].road_through = 6000;
        page.state.day = "2026-11-11";
        await page.load();
        const ex = page.state.data.executives[0];
        check("the overview does not carry a later day's roads back",
              !ex.road_legs && !ex.road_through, JSON.stringify(ex.road_legs));
    }
    process.exit(failed ? 1 : 0);
}

main().catch((error) => {
    console.log("FAIL harness - " + (error && error.stack || error));
    process.exit(1);
});
