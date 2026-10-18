/** @odoo-module **/
/**
 * The browser half of the scan register's WhatsApp wizard: put the message on the
 * clipboard (a group cannot be pre-filled), open WhatsApp Web, close the wizard.
 *
 * The clipboard is written before the tab opens: once the new tab has focus this
 * page may no longer write to it. Both happen inside the click's activation window,
 * the same one Odoo's own "open URL in a new tab" relies on.
 */
import { browser } from "@web/core/browser/browser";
import { registry } from "@web/core/registry";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";

async function openWhatsApp(env, action) {
    const notification = useService("notification");
    const { url, text, copy, where } = action.params || {};
    let copied = false;
    if (copy && text) {
        try {
            await browser.navigator.clipboard.writeText(text);
            copied = true;
        } catch {
            copied = false;
        }
    }
    if (url) {
        browser.open(url, "_blank");
    }
    if (copy) {
        notification.add(
            copied
                ? _t("Message copied. Paste it in %s with Ctrl+V.", where || _t("the group"))
                : _t("The message could not be copied by itself. Open the wizard again and use Copy."),
            { type: copied ? "success" : "warning", sticky: !copied }
        );
    }
    return { type: "ir.actions.act_window_close" };
}

registry.category("actions").add("lab_scan_whatsapp_open", openWhatsApp);
