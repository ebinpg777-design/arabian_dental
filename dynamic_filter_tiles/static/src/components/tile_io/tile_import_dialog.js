/** @odoo-module **/

import { Component, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";

/**
 * Bring a tile set in from somewhere else.
 *
 * A ribbon is worth building once: export it from the database it was designed
 * in, drop the file here, and it lands with its colours, icons, thresholds and
 * measures intact. Anything the target database does not have (a model that is
 * not installed, a field that was renamed) is reported rather than fatal.
 */
export class TileImportDialog extends Component {
    static template = "dynamic_filter_tiles.TileImportDialog";
    static components = { Dialog };
    static props = {
        close: Function,
        resModel: { type: String, optional: true },
        onImported: Function,
    };

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.state = useState({ payload: "", replace: false, busy: false, fileName: "" });
    }

    get canImport() {
        return Boolean(this.state.payload.trim()) && !this.state.busy;
    }

    async onFileSelected(ev) {
        const file = ev.target.files && ev.target.files[0];
        if (!file) {
            return;
        }
        this.state.fileName = file.name;
        this.state.payload = await file.text();
    }

    async confirm() {
        if (!this.canImport) {
            return;
        }
        this.state.busy = true;
        try {
            const result = await this.orm.call("filter.tile", "import_tiles", [
                this.state.payload,
                this.state.replace,
            ]);
            await this.props.onImported(result);
            if (result.skipped && result.skipped.length) {
                this.notification.add(result.skipped.join("\n"), {
                    title: _t("Skipped"),
                    type: "warning",
                    sticky: true,
                });
            }
            this.props.close();
        } finally {
            this.state.busy = false;
        }
    }
}
