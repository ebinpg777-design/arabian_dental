/** @odoo-module **/

import { loadJS } from "@web/core/assets";
import { _t } from "@web/core/l10n/translation";

/**
 * Read a barcode from a PHOTOGRAPH, with no camera API at all.
 *
 * `navigator.mediaDevices` - and therefore Odoo's live scanner - exists only in a
 * secure context. A phone opening this lab over http://192.168.x.x:8069 has no
 * camera API to grant permission to, so the live scanner can never run there.
 *
 * `<input type="file" capture="environment">` has no such restriction: it opens the
 * phone's own camera app, hands back a still, and the decoding happens here in the
 * page. That is how the courier-receipt scanner has been working over plain http all
 * along, and it is what makes barcode scanning possible on this lab's network today
 * rather than after a certificate. (client, 2026-08-28)
 *
 * The decoding is built for FIELD photographs, not scans: a sheet photographed by
 * hand is a few degrees off square, often dim, and carries a payment QR beside the
 * case barcode. ZXing's one-dimensional reader walks horizontal pixel rows, so a
 * tilt that a human never notices is the difference between reading and silence -
 * the sweep below therefore tries the frame straightened by a few degrees each way,
 * brightened, cropped and turned, and stops at the first hit. (client, 2026-08-28)
 */

// A receipt is legible at this size and the upload from a courier counter is on 4G.
const MAX_PX = 1600;
const JPEG_QUALITY = 0.85;

// Decoding wants more pixels than uploading: on an A4 sheet the barcode is a
// quarter of the width, and at 1600px that leaves ~3px a bar - the edge of
// what the reader survives. 2400 keeps bars comfortably wide without making
// each decode pass noticeably slow.
const DECODE_PX = 2400;

/**
 * Draw an image file onto a canvas no larger than maxPx on its long side.
 * Browsers apply the photo's EXIF rotation on decode, so a portrait receipt
 * arrives upright.
 */
async function toCanvas(file, maxPx = MAX_PX) {
    const url = URL.createObjectURL(file);
    try {
        const img = await new Promise((resolve, reject) => {
            const image = new Image();
            image.onload = () => resolve(image);
            image.onerror = () => reject(new Error(_t("That file is not an image.")));
            image.src = url;
        });
        const scale = Math.min(1, maxPx / Math.max(img.width, img.height));
        const canvas = document.createElement("canvas");
        canvas.width = Math.max(1, Math.round(img.width * scale));
        canvas.height = Math.max(1, Math.round(img.height * scale));
        const ctx = canvas.getContext("2d");
        // White under the image, always. A PNG with transparency - a screenshot,
        // a barcode image saved off a PDF - otherwise lands on the canvas's
        // default transparent-black ground: black bars on black, unreadable by
        // every decoder INCLUDING the server's, because the JPEG handed over has
        // no alpha either. Cost the field two silent "nothing found"s to learn.
        // (client, 2026-08-28)
        ctx.fillStyle = "#fff";
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
        return canvas;
    } finally {
        URL.revokeObjectURL(url);
    }
}

/** The same frame at no more than maxPx on its long side. */
function downscale(canvas, maxPx) {
    const factor = Math.min(1, maxPx / Math.max(canvas.width, canvas.height));
    if (factor >= 1) {
        return canvas;
    }
    const out = document.createElement("canvas");
    out.width = Math.max(1, Math.round(canvas.width * factor));
    out.height = Math.max(1, Math.round(canvas.height * factor));
    const ctx = out.getContext("2d");
    ctx.fillStyle = "#fff";
    ctx.fillRect(0, 0, out.width, out.height);
    ctx.drawImage(canvas, 0, 0, out.width, out.height);
    return out;
}

/** A sub-rectangle of a canvas, optionally turned a quarter turn. */
function crop(canvas, x, y, w, h, rotate = false) {
    const out = document.createElement("canvas");
    const ctx = out.getContext("2d");
    if (rotate) {
        out.width = h;
        out.height = w;
        ctx.translate(h, 0);
        ctx.rotate(Math.PI / 2);
    } else {
        out.width = w;
        out.height = h;
    }
    ctx.drawImage(canvas, x, y, w, h, 0, 0, w, h);
    return out;
}

/**
 * The canvas turned by a few degrees, on a white ground.
 *
 * White, not transparent: the corners a rotation uncovers decode as solid black
 * otherwise, and the binarizer wastes its threshold on them.
 */
function skew(canvas, degrees) {
    const rad = (degrees * Math.PI) / 180;
    const sin = Math.abs(Math.sin(rad));
    const cos = Math.abs(Math.cos(rad));
    const out = document.createElement("canvas");
    out.width = Math.round(canvas.width * cos + canvas.height * sin);
    out.height = Math.round(canvas.width * sin + canvas.height * cos);
    const ctx = out.getContext("2d");
    ctx.fillStyle = "#fff";
    ctx.fillRect(0, 0, out.width, out.height);
    ctx.translate(out.width / 2, out.height / 2);
    ctx.rotate(rad);
    ctx.drawImage(canvas, -canvas.width / 2, -canvas.height / 2);
    return out;
}

/** The same frame, pushed brighter and harder - for a photo taken in a dim hallway. */
function brighten(canvas) {
    const out = document.createElement("canvas");
    out.width = canvas.width;
    out.height = canvas.height;
    const ctx = out.getContext("2d");
    ctx.filter = "contrast(1.7) brightness(1.2) grayscale(1)";
    ctx.drawImage(canvas, 0, 0);
    return out;
}

/**
 * The regions worth trying, most likely first. A sheet usually carries more than
 * one barcode and a single-result decoder returns whichever it meets first, so the
 * full frame is followed by overlapping horizontal bands and half-width crops -
 * and the frame turned on its side, for a sheet photographed sideways.
 */
function regions(canvas) {
    const { width: w, height: h } = canvas;
    const bands = [
        [0, 0, w, h],
        [0, 0, w, Math.round(h * 0.4)],
        [0, Math.round(h * 0.3), w, Math.round(h * 0.4)],
        [0, Math.round(h * 0.6), w, h - Math.round(h * 0.6)],
        // Half-width: the invoice strip sits left on current prints and right on
        // the older ones still in circulation, in the upper half of the sheet.
        [0, 0, Math.round(w * 0.55), Math.round(h * 0.55)],
        [Math.round(w * 0.45), 0, w - Math.round(w * 0.45), Math.round(h * 0.55)],
    ];
    const out = [];
    for (const rotate of [false, true]) {
        for (const [x, y, bw, bh] of bands) {
            out.push(crop(canvas, x, y, bw, bh, rotate));
        }
    }
    return out;
}

let zxingHints = null;

async function zxingReader(formats) {
    await loadJS("/web/static/lib/zxing-library/zxing-library.js");
    const ZXing = window.ZXing;
    const reader = new ZXing.MultiFormatReader();
    const hints = new Map([[ZXing.DecodeHintType.TRY_HARDER, true]]);
    if (formats && formats.length) {
        hints.set(
            ZXing.DecodeHintType.POSSIBLE_FORMATS,
            formats.map((name) => ZXing.BarcodeFormat[name]).filter((f) => f !== undefined)
        );
    }
    zxingHints = hints;
    reader.setHints(hints);
    return reader;
}

function zxingDecodeOne(reader, region) {
    const ZXing = window.ZXing;
    try {
        const source = new ZXing.HTMLCanvasElementLuminanceSource(region);
        const bitmap = new ZXing.BinaryBitmap(new ZXing.HybridBinarizer(source));
        const result = reader.decodeWithState(bitmap);
        return (result && result.getText()) || null;
    } catch {
        return null; // NotFoundException: nothing in this region.
    } finally {
        reader.reset();
        if (zxingHints) {
            reader.setHints(zxingHints);
        }
    }
}

/**
 * Every barcode value in the photo, decoded in the browser.
 *
 * The native BarcodeDetector (Chrome on Android, Safari 17) returns all codes in
 * one pass. Anywhere else, Odoo's own ZXing build does one code per call, so it
 * is run over the regions above and the results merged. No server, no network.
 *
 * Options:
 *  - formats: ZXing format names (e.g. ["CODE_128"]) to look for; empty = any.
 *  - first: stop at the first value found instead of sweeping for all of them.
 */
async function decodeBarcodes(canvas, { formats = [], first = false, budgetMs = 0, single = false } = {}) {
    // The sweep below multiplies fast: regions x angles x brightness is dozens of
    // decode passes, and on a mid-range phone each pass over a big frame costs
    // real time. Unbounded, a photo with NO readable barcode ground for minutes
    // behind the spinner - which reads as a dead scanner, and starved the server
    // fallback that would have answered in fifty milliseconds. The budget makes
    // "nothing here" a fast answer. (client, 2026-08-28)
    const deadline = budgetMs ? performance.now() + budgetMs : 0;
    const overtime = () => deadline && performance.now() > deadline;
    const found = new Set();
    if ("BarcodeDetector" in window) {
        try {
            const detector = new window.BarcodeDetector();
            const bitmap = await createImageBitmap(canvas);
            for (const code of await detector.detect(bitmap)) {
                if (code.rawValue) {
                    found.add(code.rawValue);
                }
            }
        } catch {
            // Fall through to ZXing: a detector that exists but cannot run
            // (no camera permission model, unsupported format) is the same as none.
        }
    }
    if (!found.size) {
        const reader = await zxingReader(formats);
        // Straight crops first; then the same frames a few degrees either way -
        // the tilt of any handheld photo - and finally pushed brighter, for the
        // dim ones. Each round only runs if the previous rounds found nothing.
        // For a straightened frame, the full sheet plus its upper and middle
        // thirds: on a whole-sheet photo the barcode is a quarter of the width,
        // and a band gives the binarizer far less page to misjudge.
        const skewSweep = (base) => {
            const out = [];
            for (const deg of [-3, 3, -6, 6, -9, 9]) {
                const straightened = skew(base, deg);
                const { width: w, height: h } = straightened;
                out.push(
                    straightened,
                    crop(straightened, 0, 0, w, Math.round(h * 0.55)),
                    crop(straightened, 0, Math.round(h * 0.3), w, Math.round(h * 0.4))
                );
            }
            return out;
        };
        // `single`: the caller already cut the region of interest and wants ONE
        // fast pass - the live camera loop runs this ten times a second.
        const rounds = single
            ? [[canvas]]
            : [
                  regions(canvas),
                  () => skewSweep(canvas),
                  () => regions(brighten(canvas)),
                  () => skewSweep(brighten(canvas)),
              ];
        for (const round of rounds) {
            if (overtime()) {
                break;
            }
            const frames = typeof round === "function" ? round() : round;
            for (const region of frames) {
                if (overtime()) {
                    break;
                }
                const value = zxingDecodeOne(reader, region);
                if (value) {
                    found.add(value);
                    if (first) {
                        return [...found];
                    }
                }
            }
            if (found.size) {
                break;
            }
        }
    }
    return [...found];
}

export { toCanvas, crop, decodeBarcodes, JPEG_QUALITY, MAX_PX };

/** True of a value that is a payment QR or a link, never a case number. */
export function looksLikePayment(value) {
    const v = (value || "").toLowerCase();
    return v.includes("://") || v.startsWith("upi");
}

/**
 * The case codes readable in one photograph, best-effort, never throwing.
 *
 * Tuned for the printed paperwork of this lab: every case barcode is Code 128,
 * so the payment QR beside it is never even decoded - the executive cannot be
 * offered a upi:// string as if it were a case. Falls back to an any-format
 * sweep for a photo with no Code 128 at all (a courier label, an odd reprint),
 * and then filters the payment codes out unless they are all there is.
 */
export async function readCodesFromPhoto(file, { askServer } = {}) {
    let canvas = null;
    let codes = [];
    try {
        canvas = await toCanvas(file, DECODE_PX);
    } catch {
        return [];
    }
    try {
        // The quick look, on a smaller frame and against the clock: most photos
        // read here in well under a second. Anything harder is the server's job,
        // not a minute of spinner.
        const quick = downscale(canvas, MAX_PX);
        codes = await decodeBarcodes(quick, {
            formats: ["CODE_128"], first: true, budgetMs: 2000,
        });
        if (!codes.length) {
            codes = await decodeBarcodes(quick, { budgetMs: 1000 });
        }
    } catch {
        // A broken reader is the same as a reader that found nothing: the server
        // below still gets its chance. This catch is why.
        codes = [];
    }
    if (!codes.length && askServer) {
        // The reader in the page has a ceiling: a dim, blurred, tilted sheet
        // that zxing-cpp on the server still reads - in tens of milliseconds.
        // Hand over the full-size JPEG, a few hundred kilobytes on the lab's
        // own network, rather than telling the executive to take a better photo.
        try {
            const b64 = canvas.toDataURL("image/jpeg", JPEG_QUALITY).split(",")[1];
            codes = (await askServer(b64)) || [];
        } catch {
            codes = [];
        }
    }
    const cases = codes.filter((code) => !looksLikePayment(code));
    return cases.length ? cases : codes;
}
