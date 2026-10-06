import { Component, onMounted, proxy, signal, t, usePlugin, useProps } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { NotificationPlugin } from "@web/core/notifications/notification_plugin";
import { useService } from "@web/core/utils/hooks";
import { CaiVoiceRecorder } from "../../core/cai_voice";

/** Prompt input: Enter sends, Shift+Enter inserts a new line. */
export class CaiPromptInput extends Component {
    static template = "ebshel_ai_suite.CaiPromptInput";
    props = useProps({
        busy: t.boolean(),
        onSubmit: t.function(),
        placeholder: t.string().optional(),
        autofocus: t.boolean().optional(),
        onAttach: t.function().optional(),
        onRemoveFile: t.function().optional(),
        stagedFiles: t.array().optional(),
        uploading: t.boolean().optional(),
    });
    inputRef = signal.ref();
    fileRef = signal.ref();

    setup() {
        this.state = proxy({ text: "", recording: false, transcribing: false });
        this.bridge = useService("communityAiBridge");
        this.notification = usePlugin(NotificationPlugin);
        this.canRecord = CaiVoiceRecorder.isSupported();
        onMounted(() => {
            if (this.props.autofocus) {
                this.inputRef()?.focus();
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
                if (this.inputRef()) {
                    this.inputRef().value = text;
                    this.inputRef().focus();
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
        this.fileRef()?.click();
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
        if (this.inputRef()) {
            this.inputRef().value = "";
            this.inputRef().style.height = "auto";
        }
        this.props.onSubmit(text);
    }
}
