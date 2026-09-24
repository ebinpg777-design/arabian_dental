# Material Requests

A department (ceramic, acrylic, CAD/CAM, orthodontic, metal, wax-up, office, marketing)
raises a request for consumables from the main store. Approving it creates the internal
transfer; validating the transfer closes the request.

**Board** (kanban by stage) with priority, needed-by countdown, delivery progress and a
"short" badge · **list** with the same at a glance and a search panel by stage and
department · **form** that shows, on every line, what the source store has on hand and
lets the store approve less than asked · **reject with a reason** the requester reads on
the request · **request again** copies a past request into a new draft · **calendar** on
needed-by dates · **consumption analysis** (pivot/graph of delivered quantities by
product, department and month) · **requisition slip** PDF.

Settings (Inventory ▸ Configuration ▸ Settings ▸ Material Requests): the operation type
whose source location is the store, and an approver who gets an activity on submission.

Groups: *Material Requests: Request* sees and raises their own; *Approve* (the store)
sees everything, approves, rejects and cancels.

Rewritten for Odoo 19 from the lab's Odoo 17 module of the same name; the models keep
their names (`material.request`, `material.request.lines`) so the migration maps the
1,015 historical requests onto them.
