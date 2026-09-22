/** @odoo-module **/

import { onWillUnmount, useEffect } from "@odoo/owl";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { ListRenderer } from "@web/views/list/list_renderer";
import { KanbanRenderer } from "@web/views/kanban/kanban_renderer";

// How many more records a scroll to the bottom fetches when the view does not
// state its own initial limit.
const FALLBACK_STEP = 80;

/**
 * On a phone, scrolling to the bottom of a list loads the next records.
 *
 * An executive looking up a doctor scrolls, reaches the end, and has to find and
 * press a pager arrow the width of a fingernail at the top of the screen - and then
 * loses their place, because the next page REPLACES the records rather than
 * continuing them. This grows the view's own limit instead, which is exactly what
 * the kanban's "Load more" button does for a grouped board; what has already been
 * read stays where it was.
 *
 * Both renderers, because a phone gets whichever the action offers: the doctors
 * action is list,kanban and Odoo picks kanban on a small screen. Phone only - on a
 * desktop the pager is a good control and an endless list makes the footer
 * unreachable. Grouped boards are left alone: their records come from per-group
 * lists and growing the top-level limit would not extend them. (client, 2026-08-28)
 */
// A FRESH object per class, never one shared between them.
//
// `patch` rewires the patch object's own prototype so that `super` inside it reaches
// the class being patched. Handing the same object to two classes rebinds it to the
// second, so ListRenderer's `super.setup()` began calling KanbanRenderer's - and every
// list view in the database rendered a template against kanban state and threw
// "Invalid loop expression: undefined is not iterable". (client, 2026-08-28)
const infiniteScroll = () => ({
    setup() {
        super.setup(...arguments);
        this.fwUi = this.uiService || useService("ui");
        this.fwLoading = false;

        useEffect(
            (sentinel) => {
                if (!sentinel) {
                    return;
                }
                // An observer rather than a scroll listener: the scrolling ancestor
                // differs between the phone web client, a dialog and a view embedded
                // in another screen, and this works in all three without naming one.
                const observer = new IntersectionObserver(
                    (entries) => {
                        if (entries.some((e) => e.isIntersecting)) {
                            this.fwLoadMore();
                        }
                    },
                    { rootMargin: "300px" }
                );
                observer.observe(sentinel);
                this.fwObserver = observer;
                return () => {
                    observer.disconnect();
                    this.fwObserver = null;
                };
            },
            () => [this.fwSentinelEl, this.fwShown]
        );

        onWillUnmount(() => {
            if (this.fwObserver) {
                this.fwObserver.disconnect();
                this.fwObserver = null;
            }
        });
    },

    get fwSentinelEl() {
        const root = this.rootRef && this.rootRef.el;
        return root ? root.querySelector(".o_fw_more_sentinel") : null;
    },

    get fwList() {
        return this.props.list;
    },

    get fwShown() {
        const list = this.fwList;
        return (list && list.records && list.records.length) || 0;
    },

    /** Small screen, flat view, more records to come, nothing being edited. */
    get fwCanAutoLoad() {
        const list = this.fwList;
        if (!this.fwUi || !this.fwUi.isSmall || !list || list.isGrouped) {
            return false;
        }
        if (list.model && list.model.useSampleModel) {
            return false;                 // sample records are a picture, not data
        }
        if (list.editedRecord) {
            return false;                 // never re-fetch under an open editor
        }
        // A pager the reader has moved off page one is a deliberate choice; growing
        // the limit under them would silently drag them back to the top.
        return !list.offset && this.fwShown < (list.count || 0);
    },

    get fwRemaining() {
        return Math.max(0, (this.fwList.count || 0) - this.fwShown);
    },

    async fwLoadMore() {
        if (this.fwLoading || !this.fwCanAutoLoad) {
            return;
        }
        this.fwLoading = true;
        try {
            const list = this.fwList;
            const step = (list.model && list.model.initialLimit) || FALLBACK_STEP;
            await list.load({ limit: this.fwShown + step });
        } catch {
            // A failed fetch leaves what has been read intact; the pager is still
            // there to try again.
        } finally {
            this.fwLoading = false;
        }
    },
});

patch(ListRenderer.prototype, infiniteScroll());
patch(KanbanRenderer.prototype, infiniteScroll());
