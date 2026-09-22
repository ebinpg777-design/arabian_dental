/** @odoo-module **/

/**
 * Installing Arabian Dental Lab from the public site.
 *
 * Chrome decides on its own whether a site may be installed, and it does not
 * always say so: `beforeinstallprompt` is withheld when the app is already
 * installed, when the visitor has dismissed it recently, before Chrome's own
 * engagement heuristic is satisfied, inside an embedded browser, and never at
 * all on iOS. That is why the button appeared on some phones and not others,
 * and it is not something a page can override.
 *
 * So the rule here is: the entry is always reachable, and it never does
 * nothing. With an offer in hand it prompts; without one it explains how THIS
 * browser installs. (client spec, 2026-09-08)
 */

const DISMISSED_KEY = "lab_pwa.dismissed";

// ---------------------------------------------------------------- diagnostics
// Silent in production. Turned on by Odoo's own debug mode or ?pwa-debug=1, so
// a phone that will not install can be questioned without a console full of
// noise for everybody else. (spec §14)
const DEBUG = (() => {
    try {
        const search = new URLSearchParams(window.location.search);
        if (search.has("pwa-debug")) {
            return true;
        }
        return Boolean(window.odoo && window.odoo.debug);
    } catch {
        return false;
    }
})();

function log(...args) {
    if (DEBUG) {
        console.info("[lab_pwa]", ...args);
    }
}

// ------------------------------------------------------------------- context
function standalone() {
    return (
        window.matchMedia("(display-mode: standalone)").matches ||
        window.navigator.standalone === true
    );
}

function isIos() {
    return (
        /iphone|ipad|ipod/i.test(window.navigator.userAgent) && !window.MSStream
    );
}

/**
 * An in-app browser, where installing is not offered at all.
 *
 * WhatsApp, Facebook, Instagram and friends open links in a WebView that has
 * no install machinery. Telling somebody to look for a menu item that cannot
 * exist there is worse than telling them to open Chrome. (spec §9)
 */
function embedded() {
    const ua = window.navigator.userAgent || "";
    if (/FBAN|FBAV|FB_IAB|Instagram|Line\/|Twitter|MicroMessenger/i.test(ua)) {
        return true;
    }
    if (/WhatsApp/i.test(ua)) {
        return true;
    }
    // Android WebView: "wv" in the token, or Version/x.y beside Chrome.
    return /\bwv\b/.test(ua) || /Version\/[\d.]+ Chrome/.test(ua);
}

function handheld() {
    const ua = window.navigator.userAgent || "";
    return (
        /android|mobile/i.test(ua) ||
        (window.matchMedia && window.matchMedia("(pointer: coarse)").matches)
    );
}

function dismissed() {
    try {
        return window.localStorage.getItem(DISMISSED_KEY) === "1";
    } catch {
        // Private browsing throws on the accessor itself. A banner that cannot
        // remember a dismissal must not therefore appear on every page.
        return true;
    }
}

function remember() {
    try {
        window.localStorage.setItem(DISMISSED_KEY, "1");
    } catch {
        // It will ask once more next time, which is the safe way to be wrong.
    }
}

// ------------------------------------------------------------ the help sheet
/** What to do, for the browser actually in use. (spec §7, §9) */
function instructions() {
    if (embedded()) {
        return {
            title: "Open in Chrome to install",
            steps: [
                "This page is open inside another app's browser, which cannot install apps.",
                "Tap the ⋮ or ••• menu and choose \"Open in browser\" or \"Open in Chrome\".",
                "Then use Install from Chrome's own ⋮ menu.",
            ],
        };
    }
    if (isIos()) {
        return {
            title: "Install on iPhone or iPad",
            steps: [
                "Open this page in Safari — other browsers on iOS cannot install it.",
                "Tap the Share button.",
                "Choose \"Add to Home Screen\".",
            ],
        };
    }
    if (handheld()) {
        return {
            title: "Install on Android",
            steps: [
                "Tap Chrome's ⋮ menu.",
                "Choose \"Install app\", or \"Add to Home screen\" on older versions.",
                "If neither is there yet, browse a page or two and try again — Chrome offers it once you have used the site.",
                // Shown to every Android visitor, not just Xiaomi ones,
                // because the phone cannot be identified: Chrome's user agent
                // on a Redmi carries a model code like "23129RAA4G" and never
                // the brand, so sniffing for "redmi" matches nothing. The line
                // names the phones it applies to and costs everybody else one
                // sentence. Reported from a Redmi on Android 15 where every
                // part of the web side checked out. (client, 2026-09-08)
                "On Xiaomi, Redmi and POCO phones the install can report success and leave no icon: MIUI blocks it unless Chrome may create one. Settings → Apps → Manage apps → Chrome → Other permissions → allow \"Display pop-up windows\" and \"Home screen shortcuts\", then install again.",
            ],
        };
    }
    return {
        title: "Install on this computer",
        steps: [
            "Look for the install icon at the right-hand end of the address bar.",
            "Or open Chrome's ⋮ menu and choose \"Cast, save and share\", then \"Install\".",
        ],
    };
}

function closeSheet() {
    const open = document.querySelector(".o_pwa_sheet_backdrop");
    if (open) {
        open.remove();
    }
}

function showSheet() {
    closeSheet();
    const help = instructions();
    log("showing instructions:", help.title);

    const backdrop = document.createElement("div");
    backdrop.className = "o_pwa_sheet_backdrop";
    backdrop.addEventListener("click", (ev) => {
        if (ev.target === backdrop) {
            closeSheet();
        }
    });

    const sheet = document.createElement("div");
    sheet.className = "o_pwa_sheet";
    sheet.setAttribute("role", "dialog");
    sheet.setAttribute("aria-modal", "true");
    sheet.setAttribute("aria-label", help.title);

    const heading = document.createElement("h5");
    heading.className = "o_pwa_sheet_title";
    heading.textContent = help.title;
    sheet.appendChild(heading);

    const list = document.createElement("ol");
    list.className = "o_pwa_sheet_steps";
    help.steps.forEach((text) => {
        const item = document.createElement("li");
        item.textContent = text;
        list.appendChild(item);
    });
    sheet.appendChild(list);

    const done = document.createElement("button");
    done.className = "o_pwa_sheet_close";
    done.textContent = "Got it";
    done.addEventListener("click", closeSheet);
    sheet.appendChild(done);

    backdrop.appendChild(sheet);
    document.body.appendChild(backdrop);
    document.addEventListener("keydown", function escape(ev) {
        if (ev.key === "Escape") {
            closeSheet();
            document.removeEventListener("keydown", escape);
        }
    });
}

// ------------------------------------------------------------------ the entry
function entry(itemClass, linkClass, onClick) {
    const item = document.createElement("li");
    item.className = `${itemClass} o_pwa_nav_item`;
    const link = document.createElement("a");
    link.className = `${linkClass} o_pwa_install`;
    link.href = "#";
    link.innerHTML = '<i class="fa fa-mobile me-1" aria-hidden="true"></i>Install App';
    link.addEventListener("click", (ev) => {
        ev.preventDefault();
        onClick();
    });
    item.appendChild(link);
    return item;
}

/**
 * The footer's list of ways to reach the lab.
 *
 * Found by what the footer SAYS: it is edited in the website builder, so a
 * column position would not survive somebody rearranging it.
 */
function footerList() {
    const foot = document.querySelector("footer");
    if (!foot) {
        return null;
    }
    const heading = [...foot.querySelectorAll("h1,h2,h3,h4,h5,h6")].find((h) =>
        /connect|reach|follow/i.test(h.textContent || "")
    );
    if (heading) {
        const near = heading.parentElement.querySelector("ul");
        if (near) {
            return near;
        }
    }
    return foot.querySelector("ul.list-unstyled") || foot.querySelector("ul");
}

function placeEntries(onClick) {
    const made = [];
    // The desktop bar and the mobile drawer are two different lists.
    document.querySelectorAll("ul.navbar-nav.top_menu").forEach((menu) => {
        if (!menu.querySelector(".o_pwa_nav_item")) {
            const item = entry("nav-item", "nav-link", onClick);
            menu.appendChild(item);
            made.push(item);
        }
    });
    const foot = footerList();
    if (foot && !foot.querySelector(".o_pwa_nav_item")) {
        const item = entry("o_pwa_foot_item", "o_pwa_foot_link", onClick);
        foot.appendChild(item);
        made.push(item);
    }
    return made;
}

function hideEntries() {
    document.querySelectorAll(".o_pwa_nav_item").forEach((el) => el.remove());
}

// --------------------------------------------------------------------- banner
function banner(message, onInstall) {
    const bar = document.createElement("div");
    bar.className = "o_pwa_bar";
    bar.setAttribute("role", "dialog");
    bar.setAttribute("aria-label", "Install this site as an app");

    const text = document.createElement("span");
    text.className = "o_pwa_text";
    text.textContent = message;
    bar.appendChild(text);

    const install = document.createElement("button");
    install.className = "o_pwa_btn";
    install.textContent = "Install";
    install.addEventListener("click", async () => {
        bar.remove();
        remember();
        await onInstall();
    });
    bar.appendChild(install);

    const close = document.createElement("button");
    close.className = "o_pwa_close";
    close.setAttribute("aria-label", "Not now");
    close.textContent = "×";
    close.addEventListener("click", () => {
        bar.remove();
        remember();
    });
    bar.appendChild(close);
    document.body.appendChild(bar);
}

// ----------------------------------------------------------------------- boot
function start() {
    // Module level, not an Odoo service: the services registry is a back-office
    // construct and a public page does not reliably start it, so a worker
    // registered from there never registers at all.
    if (!("serviceWorker" in window.navigator)) {
        log("no service worker support");
        return;
    }

    const register = () => {
        window.navigator.serviceWorker
            .register("/service-worker.js", { scope: "/" })
            .then((reg) => log("service worker registered, scope", reg.scope))
            .catch((error) => log("service worker failed", error));
    };
    if (document.readyState === "complete") {
        register();
    } else {
        window.addEventListener("load", register);
    }

    if (standalone()) {
        log("already running installed; no install UI");
        return;
    }

    let deferred = null;

    const onClick = async () => {
        if (!deferred) {
            // Chrome has not offered, so there is nothing to prompt with. Say
            // how this browser does it rather than swallowing the tap. The
            // prompt can never be forced from script. (spec §6, §7)
            showSheet();
            return;
        }
        log("prompting");
        deferred.prompt();
        const choice = await deferred.userChoice;
        log("choice:", choice && choice.outcome);
        // Chrome will not replay the same offer.
        deferred = null;
    };

    placeEntries(onClick);

    window.addEventListener("beforeinstallprompt", (ev) => {
        // Suppress Chrome's mini-infobar; it appears at the browser's
        // convenience rather than the visitor's. prompt() is only ever called
        // from the click handler above. (spec §6)
        ev.preventDefault();
        deferred = ev;
        log("beforeinstallprompt received");
        if (!dismissed()) {
            banner("Add this site to your phone", onClick);
        }
    });

    window.addEventListener("appinstalled", () => {
        log("appinstalled");
        deferred = null;
        hideEntries();
        const bar = document.querySelector(".o_pwa_bar");
        if (bar) {
            bar.remove();
        }
        remember();
    });

    log("ready; ios=%s embedded=%s handheld=%s", isIos(), embedded(), handheld());
}

start();
