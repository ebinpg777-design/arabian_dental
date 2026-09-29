import { Component, onWillUnmount, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";
import { CaiVoiceRecorder } from "../../core/cai_voice";

/** Record a meeting or a dictation, then get a transcript and an AI summary. */
export class CommunityAIVoiceDialog extends Component {
    static template = "ebshel_ai_suite.CommunityAIVoiceDialog";
    static components = { Dialog };
    static props = { close: Function, onInsert: { type: Function, optional: true } };

    setup() {
        this.bridge = useService("communityAiBridge");
        this.state = useState({ phase: "idle", transcript: "", summary: "", error: "", started: 0, elapsed: 0 });
        onWillUnmount(() => {
            clearInterval(this.timer);
            if (this.state.phase === "recording") {
                this.recorder.stop();
            }
        });
    }

    get elapsedLabel() {
        const seconds = Math.floor(this.state.elapsed / 1000);
        return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
    }

    async start() {
        this.state.error = "";
        this.recorder = new CaiVoiceRecorder();
        try {
            await this.recorder.start();
        } catch (error) {
            this.state.error = error.message;
            return;
        }
        this.state.phase = "recording";
        this.state.started = Date.now();
        this.timer = setInterval(() => (this.state.elapsed = Date.now() - this.state.started), 500);
    }

    async stop() {
        clearInterval(this.timer);
        this.state.phase = "processing";
        try {
            const blob = await this.recorder.stop();
            const result = await this.bridge.transcribe(blob, true);
            this.state.transcript = result.text;
            this.state.summary = result.summary;
            this.state.phase = "done";
        } catch (error) {
            this.state.error = error?.data?.message || error?.message || _t("Transcription failed.");
            this.state.phase = "idle";
        }
    }

    insert() {
        this.props.onInsert?.({ transcript: this.state.transcript, summary: this.state.summary });
        this.props.close();
    }
}
