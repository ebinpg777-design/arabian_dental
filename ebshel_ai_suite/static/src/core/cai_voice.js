import { _t } from "@web/core/l10n/translation";

/**
 * Thin wrapper around the browser MediaRecorder. The recording only lives in
 * memory until it is sent for transcription; nothing is stored server-side.
 */
export class CaiVoiceRecorder {
    static isSupported() {
        return Boolean(navigator.mediaDevices?.getUserMedia && window.MediaRecorder);
    }

    async start() {
        if (!CaiVoiceRecorder.isSupported()) {
            throw new Error(_t("This browser cannot record audio."));
        }
        try {
            this.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        } catch {
            throw new Error(_t("Microphone access was refused. Allow it in the browser and try again."));
        }
        this.chunks = [];
        this.recorder = new MediaRecorder(this.stream);
        this.recorder.ondataavailable = (ev) => ev.data.size && this.chunks.push(ev.data);
        this.recorder.start();
    }

    stop() {
        return new Promise((resolve) => {
            this.recorder.onstop = () => {
                this.stream.getTracks().forEach((track) => track.stop());
                resolve(new Blob(this.chunks, { type: this.recorder.mimeType || "audio/webm" }));
            };
            this.recorder.stop();
        });
    }
}

export function blobToBase64(blob) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result).split(",")[1] || "");
        reader.onerror = () => reject(reader.error);
        reader.readAsDataURL(blob);
    });
}
