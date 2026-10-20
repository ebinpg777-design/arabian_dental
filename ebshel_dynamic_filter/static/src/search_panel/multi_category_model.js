/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { SearchModel } from "@web/search/search_model";

/**
 * Several values ticked in one search panel category (2026-10-20: "able to
 * select multiple filters in the search panel").
 *
 * Odoo's categories (Sales Route, Product Category, Department, ...) hold ONE
 * active value. Here a category may also hold a list, `activeValueIds`: one
 * value reads `field = id` (or `child_of id` for a tree) exactly as before,
 * several read `field in ids` (or `child_of ids`). `activeValueId` stays the
 * first of the list, so everything in Odoo that reads it - the filters'
 * domains, the panel's own highlight - keeps working. Each category's counters
 * are still computed without its own selection (as Odoo does), so the numbers
 * beside the values stay meaningful while several are ticked.
 *
 * Who may tick several values is the smart search panel's "Select several
 * values" option (smart_search_panel.js); this file only teaches the model.
 */
patch(SearchModel.prototype, {
    /** The category's selected value ids, in the order they were picked. */
    dftCategoryValueIds(category) {
        if (!category || !category.activeValueId) {
            return [];
        }
        const ids = (Array.isArray(category.activeValueIds) ? category.activeValueIds : []).filter(Boolean);
        // anything that set activeValueId directly (Odoo's own code, a default
        // from the action) wins over a stale list
        return ids.length && ids.includes(category.activeValueId) ? ids : [category.activeValueId];
    },

    /** Picking a value the usual way selects that value only. */
    toggleCategoryValue(sectionId, valueId) {
        const category = this.sections.get(sectionId);
        if (category) {
            category.activeValueIds = valueId ? [valueId] : [];
        }
        return super.toggleCategoryValue(...arguments);
    },

    /** Add a value to a category's selection, or take it out, keeping the others. */
    toggleCategoryValues(sectionId, valueId) {
        const category = this.sections.get(sectionId);
        if (!category || !valueId) {
            return;
        }
        const current = this.dftCategoryValueIds(category);
        const next = current.includes(valueId)
            ? current.filter((id) => id !== valueId)
            : [...current, valueId];
        category.activeValueIds = next;
        category.activeValueId = next[0] || false;
        this._notify();
    },

    /** Several values at once (a saved selection, the option being switched off). */
    setCategoryValues(sectionId, valueIds) {
        const category = this.sections.get(sectionId);
        if (!category) {
            return;
        }
        const ids = [...new Set((valueIds || []).filter(Boolean))];
        category.activeValueIds = ids;
        category.activeValueId = ids[0] || false;
        this._notify();
    },

    clearSections(sectionIds) {
        for (const sectionId of sectionIds) {
            const section = this.sections.get(sectionId);
            if (section && section.type === "category") {
                section.activeValueIds = [];
            }
        }
        return super.clearSections(...arguments);
    },

    /** Values that disappeared from the panel leave the selection; the rest stay. */
    _ensureCategoryValue(category, valueIds) {
        const ids = (Array.isArray(category.activeValueIds) ? category.activeValueIds : []).filter(Boolean);
        super._ensureCategoryValue(...arguments);
        if (ids.length > 1) {
            const kept = ids.filter((id) => valueIds.includes(id));
            category.activeValueIds = kept;
            category.activeValueId = kept[0] || category.activeValueId;
        }
    },

    _getCategoryDomain(excludedCategoryId) {
        const domain = [];
        for (const category of this.categories) {
            if (category.id === excludedCategoryId) {
                continue;
            }
            const ids = this.dftCategoryValueIds(category);
            if (!ids.length) {
                continue;
            }
            const field = this.searchViewFields[category.fieldName];
            const tree = field.type === "many2one" && category.parentField;
            if (ids.length === 1) {
                domain.push([category.fieldName, tree ? "child_of" : "=", ids[0]]);
            } else {
                domain.push([category.fieldName, tree ? "child_of" : "in", ids]);
            }
        }
        return domain;
    },
});
