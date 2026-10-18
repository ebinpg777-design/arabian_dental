/** @odoo-module **/
/**
 * The sliced file, sent in pieces.
 *
 * A ChituBox .ctb for a full plate is 50-300 MB. Odoo refuses any request over
 * 128 MiB (web.max_file_upload_size, database-wide) and a proxy in front of it often less (Cloudflare: 100 MB), and a plain
 * binary field sends the whole file, base64, in one request. So this widget, on a
 * Many2one to ir.attachment, sends the file to /scan/slice/chunk 8 MiB at a time,
 * with a progress bar; the last piece answers the attachment the file became, and
 * the field takes it. On a saved plate the form saves at once, so the figures the
 * server reads from the file's head (time, resin, layers, the plate picture) show.
 *
 * The file can be downloaded from here, replaced, or taken off.
 */
import { Component, onWillStart, onWillUpdateProps, proxy, signal, useProps } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

export const CHUNK = 8 * 1024 * 1024;

export function humanSize(bytes) {
    if (!bytes) {
        return "";
    }
    const units = ["B", "KB", "MB", "GB"];
    let value = bytes;
    let unit = 0;
    while (value >= 1024 && unit < units.length - 1) {
        value /= 1024;
        unit += 1;
    }
    return `${unit ? value.toFixed(1) : value} ${units[unit]}`;
}

export class SliceUploadField extends Component {
    static template = "lab_scan_register.SliceUploadField";
    props = useProps({ ...standardFieldProps });

    setup() {
        this.input = signal.ref();
        this.orm = useService("orm");
        this.http = useService("http");
        this.notification = useService("notification");
        this.state = proxy({ busy: false, sent: 0, total: 0, name: "", size: 0 });
        this.humanSize = humanSize;
        onWillStart(() => this.describe(this.props));
        onWillUpdateProps((next) => this.describe(next));
    }

    get value() {
        return this.props.record.data[this.props.name];
    }
    get pct() {
        return this.state.total ? Math.round((this.state.sent / this.state.total) * 100) : 0;
    }
    get downloadUrl() {
        return `/web/content/${this.value.id}?download=true`;
    }

    /** The size of the file the field holds - read once per file. */
    async describe(props) {
        const value = props.record.data[props.name];
        const id = value && value.id;
        if (id === this.describedId) {
            return;
        }
        this.describedId = id;
        if (!id) {
            this.state.size = 0;
            return;
        }
        const [att] = await this.orm.read("ir.attachment", [id], ["file_size"]);
        this.state.size = att ? att.file_size : 0;
    }

    pick() {
        this.input().click();
    }
    cancel() {
        this.cancelled = true;
    }

    async onFile(ev) {
        const file = ev.target.files && ev.target.files[0];
        ev.target.value = "";
        if (!file) {
            return;
        }
        Object.assign(this.state, { busy: true, sent: 0, total: file.size, name: file.name });
        this.cancelled = false;
        try {
            let uploadId = "";
            let offset = 0;
            let done = null;
            while (!done) {
                if (this.cancelled) {
                    return;
                }
                const form = new FormData();
                form.append("csrf_token", odoo.csrf_token);
                form.append("upload_id", uploadId);
                form.append("offset", String(offset));
                form.append("total", String(file.size));
                form.append("name", file.name);
                form.append("chunk", file.slice(offset, offset + CHUNK), file.name);
                const res = await this.http.post("/scan/slice/chunk", form);
                if (res.error) {
                    throw new Error(res.error);
                }
                if (res.done) {
                    done = res;
                } else {
                    uploadId = res.upload_id;
                    // a piece lost or sent twice: the server says where it really is
                    offset = res.expected !== undefined ? res.expected : res.received;
                    this.state.sent = offset;
                }
            }
            this.state.sent = file.size;
            this.describedId = done.id;
            this.state.size = done.size;
            await this.props.record.update({ [this.props.name]: { id: done.id, display_name: done.name } });
            if (this.props.record.resId) {
                await this.props.record.save();
            }
        } catch (error) {
            this.notification.add(_t("The sliced file did not upload: %s", error.message || error), {
                type: "danger",
            });
        } finally {
            this.state.busy = false;
        }
    }

    async remove() {
        await this.props.record.update({ [this.props.name]: false });
        if (this.props.record.resId) {
            await this.props.record.save();
        }
    }
}

export const sliceUploadField = {
    component: SliceUploadField,
    displayName: _t("Sliced file upload"),
    supportedTypes: ["many2one"],
};

registry.category("fields").add("slice_upload", sliceUploadField);
