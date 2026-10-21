/** @odoo-module **/

import { ORM } from "@web/core/orm_plugin";
import { patch } from "@web/core/utils/patch";
import { batchedOrmService } from "@analytic/services/batched_orm_service";

/**
 * analytic's RequestBatcherORM gathers the `read`s of many widgets into one request,
 * but runs that request with `this` bound to the copy of the service handed to the
 * FIRST widget (useService gives every component its own Object.create(service),
 * whose methods refuse once that component is destroyed). Close that widget before
 * the request leaves - open an invoice and move on, reload the list - and the shared
 * read is refused with "Component is destroyed", failing every widget waiting on it.
 *
 * The shared read now always runs on the service itself. A widget that is itself
 * destroyed when it CALLS read is still refused by useService, as before.
 */
patch(batchedOrmService, {
    start() {
        const orm = super.start(...arguments);
        orm.read = async function (resModel, resIds, fields, kwargs) {
            const records = await orm.batch(
                resIds,
                ["read", resModel, fields, kwargs],
                (ids) => ORM.prototype.read.call(orm, resModel, ids, fields, kwargs)
            );
            return records.filter((record) => resIds.includes(record.id));
        };
        return orm;
    },
});
