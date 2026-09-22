/** @odoo-module **/

import { Component, onMounted, onWillUnmount, useRef, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";
import { isBarcodeScannerSupported } from "@web/core/barcode/barcode_video_scanner";
import {
    decodeBarcodes,
    looksLikePayment,
    readCodesFromPhoto,
    toCanvas as toCanvasFromBlob,
} from "@lab_delivery/js/photo_barcode";

/**
 * The Scan Station: a full-screen hand-over counter, not a prompt.
 *
 * An executive at a clinic door rarely holds ONE box. The station stays open
 * across the whole doorstep visit: every scan lands as a card in a running feed
 * - raised, ready, taken over from a colleague, already delivered, unknown -
 * each in its own colour with its own buzz, and the hand-over wizard opens on
 * top for the cases that need confirming. The tally in the header is the
 * doorstep summarised: how many boxes this visit actually moved.
 *
 * Three ways in, because the field has three realities:
 *  - LIVE CAMERA where the page is served over https (the browser only exposes
 *    it in a secure context) - continuous, no button presses;
 *  - PHOTO everywhere else: `<input type="file" capture>` opens the phone's own
 *    camera with no secure-context rule, and the still is decoded in the page,
 *    then by the server's stronger reader if the page gives up;
 *  - TYPING / a Bluetooth wedge scanner, which "types" a code and presses Enter
 *    - caught even when no field is focused, so the cheap handheld guns the lab
 *    owns work here with zero setup.
 * (client, 2026-08-28)
 */
export class ScanStation extends Component {
    static template = "lab_delivery.ScanStation";
    static components = { Dialog };
    static props = {
        close: Function,
    };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.live = isBarcodeScannerSupported();
        this.state = useState({
            // 'camera' only ever offered when the live scanner can exist at all.
            mode: this.live ? "camera" : "photo",
            cameraReady: false,
            // What the loop is going through, said out loud: a camera that sees
            // the wrong thing and says nothing reads as a broken scanner.
            cameraStatus: "",
            mirror: false,
            canFlip: false,
            // the camera's own knobs, offered only when the device has them
            zoom: null,          // {min, max, step, value}
            torch: false,
            torchOn: false,
            // what the loop sees, said in two meters: how sharp, how barcode-like
            sharp: 0,
            bars: 0,
            width: 0,        // how much of the slot the bars span, 0-3
            busy: false,
            stage: "",
            manual: "",
            run: [],
            moved: 0,
        });
        this.fileRef = useRef("file");
        this.manualRef = useRef("manual");
        this.videoRef = useRef("video");
        this.stream = null;
        this.cameraTimer = null;
        this.cameraMisses = 0;
        this.cameraBusy = false;
        this.cameraDevices = [];
        this.cameraIndex = -1;
        this.serverInFlight = false;
        this.lastServerAt = 0;
        this.seen = new Map(); // code -> timestamp, so a lingering camera frame is one scan
        this.pending = new Set();

        // The wedge path: a handheld scanner is a keyboard that types fast and
        // presses Enter. Catch it at the window, so it works with nothing focused.
        this.wedgeBuffer = "";
        this.wedgeLast = 0;
        this.onWedgeKey = (ev) => {
            if (ev.target && ["INPUT", "TEXTAREA"].includes(ev.target.tagName)) {
                return; // the manual field handles itself
            }
            const now = performance.now();
            if (now - this.wedgeLast > 80) {
                this.wedgeBuffer = "";
            }
            this.wedgeLast = now;
            if (ev.key === "Enter") {
                const code = this.wedgeBuffer;
                this.wedgeBuffer = "";
                if (code.length >= 4) {
                    ev.preventDefault();
                    this.handleCode(code, "wedge");
                }
            } else if (ev.key.length === 1) {
                this.wedgeBuffer += ev.key;
            }
        };
        onMounted(() => {
            window.addEventListener("keydown", this.onWedgeKey, true);
            if (this.state.mode === "camera") {
                this.startCamera();
            }
        });
        onWillUnmount(() => {
            window.removeEventListener("keydown", this.onWedgeKey, true);
            this.stopCamera();
        });
    }

    // ------------------------------------------------------------- live camera
    /**
     * The live loop is OURS, not the stock one. The stock scanner runs one
     * plain decode per frame, which never survives a fixed-focus webcam or a
     * shaky hand. Here every grabbed frame goes through the same hardened
     * pipeline as an uploaded photo - straightened, brightened, banded - and
     * every few misses the frame is handed to the server's stronger reader,
     * which reads blur the page cannot. The camera keeps streaming; the person
     * just holds the sheet up. (client, 2026-08-28)
     */
    async startCamera(deviceId = null) {
        this.stopCamera();
        try {
            // "environment" is a WISH, not a guarantee: a laptop grants the only
            // camera it has - the selfie one above the screen - and a scan
            // pointed at the user's face never reads. So the actual facing is
            // checked after the grant, the preview is mirrored for a front
            // camera so aiming feels natural, and a flip button appears whenever
            // there is another camera to flip to. (client, 2026-08-28)
            const video = deviceId
                ? { deviceId: { exact: deviceId },
                    width: { ideal: 1920 }, height: { ideal: 1080 } }
                : { facingMode: "environment",
                    width: { ideal: 1920 }, height: { ideal: 1080 } };
            this.stream = await navigator.mediaDevices.getUserMedia({
                video, audio: false,
            });
        } catch (error) {
            this.onCameraError(error);
            return;
        }
        const el = this.videoRef.el;
        if (!el) {
            this.stopCamera();
            return;
        }
        el.srcObject = this.stream;
        try {
            await el.play();
        } catch {
            // autoplay refused: the stream still renders once metadata lands
        }
        const track = this.stream.getVideoTracks()[0];
        const settings = (track && track.getSettings && track.getSettings()) || {};
        const label = ((track && track.label) || "").toLowerCase();
        const isFront =
            settings.facingMode === "user" ||
            (!settings.facingMode && !/back|rear|environment/.test(label));
        this.state.mirror = isFront;
        this.state.cameraStatus = isFront
            ? _t("This is the front camera — hold the sheet up facing it, or use Photo.")
            : "";
        try {
            const devices = await navigator.mediaDevices.enumerateDevices();
            this.cameraDevices = devices.filter((d) => d.kind === "videoinput");
            this.state.canFlip = this.cameraDevices.length > 1;
            if (this.cameraIndex < 0) {
                this.cameraIndex = Math.max(0, this.cameraDevices.findIndex(
                    (d) => d.deviceId === settings.deviceId));
            }
        } catch {
            this.state.canFlip = false;
        }
        // The knobs a phone camera has and a webcam mostly lacks: continuous
        // focus (a fixed-focus webcam ignores it, harmlessly), optical zoom so the
        // sheet can stay at the distance the lens focuses at, and the torch for
        // a dim corridor. Offered only when the track reports them.
        // (client, 2026-08-28)
        this.track = track;
        this.state.zoom = null;
        this.state.torch = false;
        this.state.torchOn = false;
        try {
            await track.applyConstraints({ advanced: [{ focusMode: "continuous" }] });
        } catch {
            // no focus control here
        }
        try {
            const caps = (track.getCapabilities && track.getCapabilities()) || {};
            if (caps.zoom && caps.zoom.min !== undefined && caps.zoom.max > caps.zoom.min) {
                this.state.zoom = {
                    min: caps.zoom.min, max: caps.zoom.max,
                    step: caps.zoom.step || 0.1,
                    value: settings.zoom || caps.zoom.min,
                };
            }
            this.state.torch = Boolean(caps.torch);
        } catch {
            // capabilities unsupported: plain camera
        }
        this.imageCapture = null;
        if ("ImageCapture" in window) {
            try {
                this.imageCapture = new window.ImageCapture(track);
            } catch {
                this.imageCapture = null;
            }
        }
        this.state.cameraReady = true;
        this.cameraMisses = 0;
        this.scheduleTick(120);
    }

    /** Ten looks a second, like the stock scanner - a chain, never overlapping. */
    scheduleTick(ms) {
        if (this.cameraTimer) {
            clearTimeout(this.cameraTimer);
        }
        this.cameraTimer = setTimeout(async () => {
            this.cameraTimer = null;
            await this.cameraTick();
            if (this.stream && this.state.mode === "camera" && !this.state.busy) {
                this.scheduleTick(100);
            }
        }, ms);
    }

    async setZoom(value) {
        if (!this.track || !this.state.zoom) {
            return;
        }
        const zoom = Number(value);
        this.state.zoom.value = zoom;
        try {
            await this.track.applyConstraints({ advanced: [{ zoom }] });
        } catch {
            // a zoom the track refuses is simply not applied
        }
    }

    async toggleTorch() {
        if (!this.track || !this.state.torch) {
            return;
        }
        const torch = !this.state.torchOn;
        try {
            await this.track.applyConstraints({ advanced: [{ torch }] });
            this.state.torchOn = torch;
        } catch {
            this.state.torch = false;
        }
    }

    /**
     * The framed band of the frame - what the person is aiming with - cut at
     * full resolution. Decoding the slot instead of the whole frame is faster
     * and reads smaller bars: the sweep works on what the eye chose.
     */
    regionOfInterest(frame) {
        const x = Math.round(frame.width * 0.05);
        const w = Math.round(frame.width * 0.9);
        const y = Math.round(frame.height * 0.15);
        const h = Math.round(frame.height * 0.7);
        const out = document.createElement("canvas");
        out.width = w;
        out.height = h;
        out.getContext("2d").drawImage(frame, x, y, w, h, 0, 0, w, h);
        return out;
    }

    /** The slot at twice the size, contrast-hardened: the pass that reads small bars. */
    doubled(roi) {
        const out = document.createElement("canvas");
        out.width = roi.width * 2;
        out.height = roi.height * 2;
        const ctx = out.getContext("2d");
        ctx.imageSmoothingEnabled = true;
        ctx.imageSmoothingQuality = "high";
        ctx.filter = "grayscale(1) contrast(1.6)";
        ctx.drawImage(roi, 0, 0, out.width, out.height);
        return out;
    }

    /**
     * Two numbers about the slot, 0-3 each: SHARP (edge energy) and BARS (how
     * many dark/light alternations run across the middle - a barcode's
     * signature). Cheap, from a 160x40 thumbnail, and honest enough to tell
     * "move back a little" from "bring the barcode into the slot".
     */
    measure(roi) {
        try {
            const probe = document.createElement("canvas");
            probe.width = 160;
            probe.height = 40;
            const ctx = probe.getContext("2d");
            ctx.drawImage(roi, 0, 0, 160, 40);
            const data = ctx.getImageData(0, 0, 160, 40).data;
            const lum = (i) => 0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2];
            let edge = 0;
            let count = 0;
            for (let y = 8; y < 32; y += 4) {
                for (let x = 1; x < 160; x++) {
                    edge += Math.abs(lum((y * 160 + x) * 4) - lum((y * 160 + x - 1) * 4));
                    count++;
                }
            }
            const meanEdge = edge / count;
            const row = [];
            for (let x = 0; x < 160; x++) {
                row.push(lum((20 * 160 + x) * 4));
            }
            const mid = (Math.max(...row) + Math.min(...row)) / 2;
            let transitions = 0;
            let dark = row[0] < mid;
            for (const v of row) {
                const d = v < mid;
                if (d !== dark) {
                    transitions++;
                    dark = d;
                }
            }
            this.state.sharp = meanEdge > 18 ? 3 : meanEdge > 10 ? 2 : meanEdge > 5 ? 1 : 0;
            this.state.bars = transitions > 40 ? 3 : transitions > 22 ? 2 : transitions > 10 ? 1 : 0;
            // How wide the pattern sits in the slot: the bench says a code that
            // spans less than a third of a 720p frame cannot be read by anything,
            // and one that spans half reads raw. So WIDTH is the meter that
            // actually moves the read rate - "fill the slot" is the instruction.
            let first = -1;
            let last = -1;
            dark = row[0] < mid;
            for (let x = 1; x < row.length; x++) {
                const d = row[x] < mid;
                if (d !== dark) {
                    if (first < 0) {
                        first = x;
                    }
                    last = x;
                    dark = d;
                }
            }
            const span = first >= 0 && transitions > 10 ? (last - first) / row.length : 0;
            this.state.width = span > 0.6 ? 3 : span > 0.4 ? 2 : span > 0.2 ? 1 : 0;
        } catch {
            this.state.sharp = 0;
            this.state.bars = 0;
            this.state.width = 0;
        }
    }

    /** A full-sensor still where the browser offers one; the video frame otherwise. */
    async bestStill(fallbackFrame) {
        if (this.imageCapture) {
            try {
                const blob = await this.imageCapture.takePhoto();
                const canvas = await toCanvasFromBlob(blob, 2400);
                if (canvas) {
                    return canvas;
                }
            } catch {
                // takePhoto is flaky on some devices: the frame will do
            }
        }
        return fallbackFrame;
    }

    /** Mean luminance of the framed centre, from a 32x18 thumbnail: paper is bright. */
    frameBrightness(frame) {
        try {
            const probe = document.createElement("canvas");
            probe.width = 32;
            probe.height = 18;
            const ctx = probe.getContext("2d");
            ctx.drawImage(frame, 0, 0, 32, 18);
            const data = ctx.getImageData(4, 3, 24, 12).data;
            let sum = 0;
            for (let i = 0; i < data.length; i += 4) {
                sum += 0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2];
            }
            return sum / (data.length / 4);
        } catch {
            return 0;
        }
    }

    /** One frame to the lab's reader; never two at once. */
    async askServer(frame) {
        this.serverInFlight = true;
        this.lastServerAt = performance.now();
        try {
            const b64 = frame.toDataURL("image/jpeg", 0.85).split(",")[1];
            const codes = (await this.orm.silent.call(
                "lab.delivery", "decode_photo", [b64])) || [];
            return codes.filter((code) => !looksLikePayment(code));
        } catch {
            return [];
        } finally {
            this.serverInFlight = false;
        }
    }

    /**
     * Read THIS frame, now: the person holds the sheet steady and taps. The
     * frame gets the whole treatment - the full client sweep with a generous
     * budget, then the server - instead of whatever slice the live loop had
     * time for between two hand movements.
     */
    async readNow() {
        if (this.state.busy) {
            return;
        }
        // The live loop is busy most of the time (a decode pass lasts most of
        // its 900 ms interval), so a tap that bailed on `cameraBusy` was silently
        // dropped - the button did nothing, which is the one thing it must never
        // do. Grab the frame NOW, pause the loop, wait for the pass in flight to
        // end, then read. (client, 2026-08-28)
        const frame = this.grabFrame();
        if (!frame) {
            return;
        }
        this.state.busy = true;
        this.state.stage = _t("Reading this frame…");
        if (this.cameraTimer) {
            clearTimeout(this.cameraTimer);
            this.cameraTimer = null;
        }
        for (let waited = 0; this.cameraBusy && waited < 3000; waited += 100) {
            await new Promise((resolve) => setTimeout(resolve, 100));
        }
        this.cameraBusy = true;
        try {
            const still = await this.bestStill(frame);
            let codes = await decodeBarcodes(this.regionOfInterest(still), {
                formats: ["CODE_128"], first: true, budgetMs: 2500,
            });
            if (!codes.length) {
                this.state.stage = _t("Asking the lab's server to read it…");
                codes = await this.askServer(still);
            }
            if (codes.length) {
                this.cameraMisses = 0;
                this.state.cameraStatus = "";
                this.seen.delete(codes[0]);
                this.onCameraResult(codes[0]);
            } else {
                this.buzz([40, 60, 40]);
                this.state.cameraStatus = this.state.mirror
                    ? _t("Nothing readable in that frame. Hold the barcode flat, facing the front camera, filling the slot — or use Photo.")
                    : _t("Nothing readable in that frame. Fill the slot with the barcode and hold flat — or use Photo.");
            }
        } finally {
            this.cameraBusy = false;
            this.state.busy = false;
            this.state.stage = "";
            if (this.stream && !this.cameraTimer && this.state.mode === "camera") {
                this.scheduleTick(100);
            }
        }
    }

    /** The next camera on the device - one tap, no settings dialog. */
    flipCamera() {
        if (this.cameraDevices.length < 2) {
            return;
        }
        this.cameraIndex = (this.cameraIndex + 1) % this.cameraDevices.length;
        this.startCamera(this.cameraDevices[this.cameraIndex].deviceId);
    }

    stopCamera() {
        this.state.cameraStatus = "";
        this.state.sharp = 0;
        this.state.bars = 0;
        this.track = null;
        this.imageCapture = null;
        if (this.cameraTimer) {
            clearTimeout(this.cameraTimer);
            this.cameraTimer = null;
        }
        if (this.stream) {
            for (const track of this.stream.getTracks()) {
                track.stop();
            }
            this.stream = null;
        }
        this.state.cameraReady = false;
    }

    grabFrame() {
        const video = this.videoRef.el;
        if (!video || !video.videoWidth) {
            return null;
        }
        const canvas = document.createElement("canvas");
        canvas.width = video.videoWidth;
        canvas.height = video.videoHeight;
        const ctx = canvas.getContext("2d");
        ctx.fillStyle = "#fff";
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.drawImage(video, 0, 0);
        return canvas;
    }

    async cameraTick() {
        if (this.cameraBusy || this.state.busy || this.state.mode !== "camera") {
            return;
        }
        const frame = this.grabFrame();
        if (!frame) {
            return;
        }
        this.cameraBusy = true;
        try {
            const roi = this.regionOfInterest(frame);
            this.measure(roi);
            // One fast pass on the slot, ten times a second - the stock
            // scanner's cadence, on the region the person is actually aiming.
            // Every other look is the slot doubled and hardened: the bench
            // reads a code a third smaller that way than raw. (client, 2026-08-28)
            this.tickCount = (this.tickCount || 0) + 1;
            const target = this.tickCount % 2 ? roi : this.doubled(roi);
            let codes = await decodeBarcodes(target, {
                formats: ["CODE_128"], first: true, single: true,
            });
            if (!codes.length) {
                this.cameraMisses += 1;
                // The server reads what a webcam cannot focus - so it gets a
                // frame about once a second, one request in flight at a time,
                // and a BRIGHT frame (paper filling the view) goes at once.
                // Sampling only every fourth miss let every hand-held moment
                // fall between samples: ten kept frames, not one with the
                // sheet in it. (client, 2026-08-28)
                // Where the browser offers a full-sensor still, that goes to the
                // server instead of the video frame: sharper than any preview.
                // ...but only a frame with something in it: a face, a wall, a
                // dark room have nothing to read, and sending them once a second
                // filled the failure folder with the wrong evidence.
                const now = performance.now();
                const promising = this.state.bars >= 2;
                const worth = this.state.bars >= 1 || this.frameBrightness(frame) > 150;
                const due = now - this.lastServerAt > (promising ? 400 : 1000);
                if (worth && !this.serverInFlight && due) {
                    const still = promising ? await this.bestStill(frame) : frame;
                    codes = await this.askServer(still);
                }
            }
            if (codes.length) {
                this.cameraMisses = 0;
                this.state.cameraStatus = "";
                this.onCameraResult(codes[0]);
            } else if (this.cameraMisses % 70 === 0) {
                // ~7 seconds of nothing: say so, with the likeliest reason -
                // the two meters know which - instead of scanning silence.
                if (this.state.bars >= 2 && this.state.width <= 1) {
                    this.state.cameraStatus = this.state.zoom
                        ? _t("Bars in view but small — zoom in until they fill the slot.")
                        : _t("Bars in view but small — bring the sheet closer until the bars fill the slot, keeping it sharp.");
                } else if (this.state.bars >= 2 && this.state.sharp <= 1) {
                    this.state.cameraStatus = _t("Bars in view but blurry — move the sheet back a little, or zoom in instead of coming closer.");
                } else if (this.state.bars < 1) {
                    this.state.cameraStatus = this.state.mirror
                        ? _t("No barcode in the slot — this is the FRONT camera. Hold the sheet up facing it, or use Photo.")
                        : _t("No barcode in the slot — bring the bars inside the frame, filling its width.");
                } else {
                    this.state.cameraStatus = _t("Almost — hold flat and still for a second, or tap Read this frame.");
                }
            }
        } catch {
            // a failed frame is just the next frame's problem
        } finally {
            this.cameraBusy = false;
        }
    }

    // ------------------------------------------------------------------ helpers
    buzz(pattern) {
        try {
            if (navigator.vibrate) {
                navigator.vibrate(pattern);
            }
        } catch {
            // a browser that refuses to buzz is not a reason to stop
        }
    }

    /** A location fix, or nothing, but never a wait. */
    positionOrNothing(ms) {
        return Promise.race([
            new Promise((resolve) => {
                try {
                    if (!navigator.geolocation) {
                        return resolve(null);
                    }
                    navigator.geolocation.getCurrentPosition(
                        (position) => resolve(position.coords),
                        () => resolve(null),
                        { enableHighAccuracy: true, timeout: ms, maximumAge: 30000 }
                    );
                } catch {
                    resolve(null);
                }
            }),
            new Promise((resolve) => setTimeout(() => resolve(null), ms)),
        ]);
    }

    card(entry) {
        this.state.run.unshift({
            key: Date.now() + "-" + Math.random().toString(36).slice(2, 6),
            at: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
            action: null,
            codes: null,
            summary: null,
            choice: false,
            resolved: false,
            ...entry,
        });
    }

    // ------------------------------------------------------------------- inputs
    setMode(mode) {
        this.state.mode = mode;
        if (mode === "camera") {
            this.startCamera();
        } else {
            this.stopCamera();
        }
        if (mode === "type") {
            // after the click has settled, so the keyboard opens on the field
            setTimeout(() => this.manualRef.el && this.manualRef.el.focus(), 80);
        }
    }

    onCameraResult(code) {
        // The camera streams: the same barcode read three frames apart is one box.
        const last = this.seen.get(code) || 0;
        if (Date.now() - last < 5000) {
            return;
        }
        this.seen.set(code, Date.now());
        this.handleCode(code, "camera");
    }

    onCameraError(error) {
        this.state.mode = "photo";
        this.card({
            tone: "info",
            title: _t("Live camera unavailable"),
            detail: (error && error.message) || _t("Switched to photo scanning."),
        });
    }

    takePhoto() {
        if (this.fileRef.el) {
            this.fileRef.el.click();
        }
    }

    async onPhoto(ev) {
        const file = ev.target.files && ev.target.files[0];
        ev.target.value = "";
        if (!file) {
            return;
        }
        this.state.busy = true;
        this.state.stage = _t("Reading the photo…");
        try {
            const codes = await readCodesFromPhoto(file, {
                askServer: (b64) => {
                    this.state.stage = _t("Asking the lab's server to read it…");
                    return this.orm.silent.call("lab.delivery", "decode_photo", [b64]);
                },
            });
            if (!codes.length) {
                this.buzz([40, 60, 40]);
                this.card({
                    tone: "bad",
                    title: _t("No barcode in that photo"),
                    detail: _t(
                        "Fill the frame and hold steady. If ONE sheet keeps failing, " +
                        "its print is probably damaged — type the number printed " +
                        "under the bars instead."
                    ),
                });
                return;
            }
            if (codes.every(looksLikePayment)) {
                this.buzz([40, 60, 40]);
                this.card({
                    tone: "bad",
                    title: _t("That is the payment QR"),
                    detail: _t("Aim at the black bars near the top of the invoice, not the SCAN TO PAY square."),
                });
                return;
            }
            if (codes.length > 1) {
                this.card({
                    tone: "info",
                    title: _t("More than one code in the photo"),
                    detail: _t("Tap the case in your hand."),
                    codes,
                });
                return;
            }
            await this.handleCode(codes[0], "photo");
        } finally {
            this.state.busy = false;
            this.state.stage = "";
        }
    }

    submitManual() {
        const code = (this.state.manual || "").trim();
        if (!code) {
            return;
        }
        this.state.manual = "";
        this.handleCode(code, "typed");
    }

    onManualKeydown(ev) {
        if (ev.key === "Enter") {
            ev.preventDefault();
            this.submitManual();
        }
    }

    // ---------------------------------------------------------------- the scan
    async handleCode(code, origin) {
        const token = (code || "").trim();
        if (!token || this.pending.has(token)) {
            return;
        }
        this.pending.add(token);
        this.buzz([15]);
        this.state.busy = true;
        this.state.stage = _t("Looking for %s…", token);
        try {
            // The fix is taken at the scan - the moment the executive is at the
            // door - and must never hold the hand-over up.
            const coords = await this.positionOrNothing(4000);
            const action = await this.orm.call("lab.delivery", "scan", [
                token,
                coords ? coords.latitude : false,
                coords ? coords.longitude : false,
                coords ? coords.accuracy : false,
            ]);
            this.consume(token, action);
        } catch (error) {
            this.buzz([40, 60, 40]);
            this.card({
                tone: "bad",
                title: token,
                detail: (error && error.data && error.data.message) || String(error),
            });
        } finally {
            this.pending.delete(token);
            this.state.busy = false;
            this.state.stage = "";
        }
    }

    /** Every outcome the server can answer with, as a card - and a wizard when
     *  there is a hand-over to confirm. */
    consume(token, action) {
        const found = action && action.context && action.context.lab_scan_summary;
        if (found && (found.state === "raised" || found.state === "ready")) {
            const who = [found.clinic, found.patient].filter(Boolean).join(" · ");
            if (found.claimed_from) {
                this.buzz([40, 60, 40]);
                this.notification.add(
                    _t("This case was assigned to %s — the scan has handed it to you.", found.claimed_from),
                    { type: "warning", title: _t("%s — taken over", found.delivery) }
                );
            }
            if (found.stage === "first") {
                // FIRST scan: the bag is open at the lab. The box is staged and
                // the choice is the executive's - onto the run, or straight into
                // the doctor's hands. The second scan of the same box delivers.
                this.buzz([12, 40, 12]);
                this.card({
                    tone: found.claimed_from ? "warn" : found.state === "raised" ? "new" : "ok",
                    title:
                        found.state === "raised"
                            ? _t("%s — delivery raised", found.delivery)
                            : _t("%s — ready", found.delivery),
                    detail:
                        (who ? who + ". " : "") +
                        (found.claimed_from
                            ? _t("Was assigned to %s — now yours. ", found.claimed_from)
                            : ""),
                    summary: found,
                    choice: true,
                });
                this.notification.add(
                    (who ? who + ". " : "") +
                        _t("Load it for delivery, or deliver it now — scanning it again later also delivers it."),
                    {
                        type: "info",
                        title:
                            found.state === "raised"
                                ? _t("%s — delivery raised", found.delivery)
                                : _t("%s — ready", found.delivery),
                    }
                );
                return;
            }
            // SECOND scan (or "deliver now"): the box is out on the run and this
            // is the door. Straight to the hand-over.
            this.buzz([12, 40, 12]);
            const title = _t("%s — second scan, delivering", found.delivery);
            this.card({ tone: "ok", title, detail: who });
            this.notification.add(
                (who ? who + ". " : "") + _t("Confirm the hand-over."),
                { type: "success", title }
            );
            this.state.moved += 1;
            this.action.doAction(action);
            return;
        }
        if (found && found.state === "delivered") {
            this.buzz([80, 60, 80]);
            let detail = found.when ? _t("Delivered on %s", found.when) : _t("Already delivered");
            if (found.who) {
                detail += " " + _t("by %s", found.who);
            }
            this.card({
                tone: "warn",
                title: _t("%s — already delivered", found.delivery),
                detail: detail + ".",
                action,
            });
            return;
        }
        if (found && found.state === "several") {
            this.buzz([15, 40, 15, 40, 15]);
            this.card({
                tone: "info",
                title: _t("%s cases answer to %s", found.count, found.token || token),
                detail: _t("Open the list and pick the one in your hand."),
                action,
            });
            return;
        }
        if (action && action.tag === "display_notification") {
            // a colleague's finished box: the server already worded it
            this.buzz([80, 60, 80]);
            this.card({
                tone: "warn",
                title: _t("Already handled"),
                detail: (action.params && action.params.message) || "",
            });
            return;
        }
        // Whatever else came back is something to show, not to hide.
        this.card({ tone: "info", title: token, detail: _t("Opened.") });
        this.action.doAction(action);
    }

    /** First scan, first answer: onto the run - the door's scan will deliver. */
    async loadBox(entry) {
        if (!entry.summary || entry.resolved) {
            return;
        }
        entry.resolved = true;
        try {
            await this.orm.call("lab.delivery", "action_scan_load", [[entry.summary.id]]);
            this.buzz([12]);
            entry.tone = "loaded";
            entry.title = _t("%s — on the run", entry.summary.delivery);
            entry.detail = _t("Loaded for delivery. Scan it again at the door to hand it over.");
            entry.choice = false;
            this.state.moved += 1;
            this.notification.add(
                _t("%s loaded — scan it again at the door to deliver.", entry.summary.delivery),
                { type: "success" }
            );
        } catch (error) {
            entry.resolved = false;
            this.notification.add(
                (error && error.data && error.data.message) || String(error),
                { type: "danger" }
            );
        }
    }

    /** First scan, second answer: the doctor is right here - deliver it now. */
    async deliverBox(entry) {
        if (!entry.summary || entry.resolved) {
            return;
        }
        entry.resolved = true;
        try {
            const coords = await this.positionOrNothing(4000);
            const action = await this.orm.call("lab.delivery", "action_mark_delivered", [
                [entry.summary.id],
                coords ? coords.latitude : false,
                coords ? coords.longitude : false,
                coords ? coords.accuracy : false,
            ]);
            entry.tone = "ok";
            entry.title = _t("%s — delivering", entry.summary.delivery);
            entry.detail = _t("Confirm the hand-over.");
            entry.choice = false;
            this.state.moved += 1;
            this.action.doAction(action);
        } catch (error) {
            entry.resolved = false;
            this.notification.add(
                (error && error.data && error.data.message) || String(error),
                { type: "danger" }
            );
        }
    }

    openCard(entry) {
        if (entry.action) {
            this.action.doAction(entry.action);
        }
    }

    pickCode(entry, code) {
        entry.codes = null;
        this.handleCode(code, "photo");
    }

    get emptyHint() {
        if (this.state.mode === "camera") {
            return _t("Point the camera at the barcode on the invoice.");
        }
        if (this.state.mode === "photo") {
            return _t("Photograph the barcode on the invoice — it is read on this phone.");
        }
        return _t("Type the invoice, order or delivery number — or use a handheld scanner.");
    }
}
