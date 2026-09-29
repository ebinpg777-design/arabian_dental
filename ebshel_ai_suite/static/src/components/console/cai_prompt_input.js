import { Component, onMounted, useRef, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";
import { CaiVoiceRecorder } from "../../core/cai_voice";

/** Prompt input: Enter sends, Shift+Enter inserts a new line. */
export class CaiPromptInput extends Component {
    static template = "ebshel_ai_suite.CaiPromptInput";
    static props = {
        busy: Boolean,
        onSubmit: Function,
        placeholder: { type: String, optional: true },
        autofocus: { type: Boolean, optional: true },
        onAttach: { type: Function, optional: true },
        onRemoveFile: { type: Function, optional: true },
        stagedFiles: { type: Array, optional: true },
        uploading: { type: Boolean, optional: true },
    };

    setup() {
        this.state = useState({ text: "", recording: false, transcribing: false });
        this.bridge = useService("communityAiBridge");
        this.notification = useService("notification");
        this.canRecord = CaiVoiceRecorder.isSupported();
        this.inputRef = useRef("input");
        this.fileRef = useRef("file");
        onMounted(() => {
            if (this.props.autofocus) {
                this.inputRef.el?.focus();
            }
        });
    }

    onInput(ev) {
        this.state.text = ev.target.value;
        const el = ev.target;
        el.style.height = "auto";
        el.style.height = `${Math.min(el.scrollHeight, 180)}px`;
    }

    onKeydown(ev) {
        if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing) {
            ev.preventDefault();
            this.submit();
        }
    }

    async toggleDictation() {
        if (this.state.recording) {
            this.state.recording = false;
            this.state.transcribing = true;
            try {
                const blob = await this.recorder.stop();
                const result = await this.bridge.transcribe(blob);
                const text = (this.state.text ? this.state.text + " " : "") + result.text.trim();
                this.state.text = text;
                if (this.inputRef.el) {
                    this.inputRef.el.value = text;
                    this.inputRef.el.focus();
                }
            } catch (error) {
                this.notification.add(error?.data?.message || error?.message || _t("Transcription failed."), {
                    type: "danger",
                });
            } finally {
                this.state.transcribing = false;
            }
            return;
        }
        this.recorder = new CaiVoiceRecorder();
        try {
            await this.recorder.start();
            this.state.recording = true;
        } catch (error) {
            this.notification.add(error.message, { type: "warning" });
        }
    }

    pickFiles() {
        this.fileRef.el?.click();
    }

    onFilesPicked(ev) {
        const files = [...(ev.target.files || [])];
        ev.target.value = "";
        if (files.length && this.props.onAttach) {
            this.props.onAttach(files);
        }
    }

    submit() {
        const text = this.state.text.trim();
        if (!text || this.props.busy) {
            return;
        }
        this.state.text = "";
        if (this.inputRef.el) {
            this.inputRef.el.value = "";
            this.inputRef.el.style.height = "auto";
        }
        this.props.onSubmit(text);
    }
}
