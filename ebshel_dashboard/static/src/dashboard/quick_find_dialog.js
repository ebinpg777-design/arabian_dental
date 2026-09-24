/** @odoo-module **/

import { Component, onMounted, useRef, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";
import { useDebounced } from "@web/core/utils/timing";

/**
 * Quick find: type a few letters, get boards and cards, press Enter.
 *
 * Boards are matched in the browser (the list is already loaded); cards are
 * asked of the server, which applies the same visibility the sidebar does.
 */
export class QuickFindDialog extends Component {
    static template = "ebshel_dashboard.QuickFindDialog";
    static components = { Dialog };
    static props = {
        boards: Array,
        actions: { type: Array, optional: true },
        onPick: Function,
        close: Function,
    };

    setup() {
        this.orm = useService("orm");
        this.inputRef = useRef("input");
        this.state = useState({ query: "", cards: [], cursor: 0, searching: false });
        this.searchCards = useDebounced(() => this.fetchCards(), 250);
        onMounted(() => this.inputRef.el && this.inputRef.el.focus());
    }

    get boardHits() {
        const query = this.state.query.trim().toLowerCase();
        if (!query) {
            return this.props.boards.slice(0, 8);
        }
        return this.props.boards.filter((board) => board.name.toLowerCase().includes(query)).slice(0, 8);
    }

    get actionHits() {
        const query = this.state.query.trim().toLowerCase();
        const actions = this.props.actions || [];
        return query ? actions.filter((action) => action.label.toLowerCase().includes(query)) : actions;
    }

    /** Everything the list offers, in order, so the keyboard can walk it. */
    get hits() {
        const boards = this.boardHits.map((board) => ({ type: "board", key: `b${board.id}`, board }));
        const cards = this.state.cards.map((card) => ({ type: "card", key: `c${card.id}`, card }));
        const actions = this.actionHits.map((action) => ({ type: "action", key: `a${action.key}`, action }));
        return [...boards, ...cards, ...actions];
    }

    async fetchCards() {
        const query = this.state.query.trim();
        if (query.length < 2) {
            this.state.cards = [];
            return;
        }
        this.state.searching = true;
        try {
            this.state.cards = await this.orm.silent.call("dashboard.item", "search_cards", [query]);
        } finally {
            this.state.searching = false;
        }
    }

    onInput(ev) {
        this.state.query = ev.target.value;
        this.state.cursor = 0;
        this.searchCards();
    }

    onKeydown(ev) {
        const hits = this.hits;
        if (ev.key === "ArrowDown") {
            ev.preventDefault();
            this.state.cursor = Math.min(this.state.cursor + 1, hits.length - 1);
        } else if (ev.key === "ArrowUp") {
            ev.preventDefault();
            this.state.cursor = Math.max(this.state.cursor - 1, 0);
        } else if (ev.key === "Enter") {
            ev.preventDefault();
            const hit = hits[this.state.cursor];
            if (hit) {
                this.pick(hit);
            }
        }
    }

    pick(hit) {
        this.props.onPick(hit);
        this.props.close();
    }

    get emptyLabel() {
        return this.state.query.trim().length < 2
            ? _t("Type to search cards; boards are listed right away.")
            : _t("Nothing matches.");
    }
}
