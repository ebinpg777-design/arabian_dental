# Analytic Widget: No "Component is destroyed"

Fixes an Odoo 20 error in the analytic distribution widget:

```
UncaughtPromiseError > Component is destroyed
    at useService.handleCallWhenDestroyed
    at RequestBatcherORM.protectedMethod [as call]
    at RequestBatcherORM.read
```

It appears when invoices, bills or journal items with analytic distributions are
opened and left before their lines have loaded (open the next record, reload the
list, change page).

**Cause.** `analytic/static/src/services/batched_orm_service.js` gathers the reads of
all lines into one request but sends it through the service copy of the first line
that asked (`useService` gives each component its own copy, which refuses calls
once the component is destroyed). When that line's widget is gone, the shared
request is refused and every line waiting on it fails.

**Fix.** The shared request is sent on the service itself
(`static/src/batched_orm.js`, a patch of `batchedOrmService.start`). Auto-installs
with `analytic`; no configuration.
