# Developing a capability

Most needs are covered by configuring a `community.ai.capability` record with one of the
built-in behaviours (search, read, count, create, update, open view, note, knowledge). Write
a new **handler** when you need behaviour that is not generic, e.g. "confirm a sales order".

## 1. Implement a handler

```python
# my_ai_sales/services/handlers.py
from odoo.addons.ebshel_ai_suite.services.capability_handlers import (
    CapabilityFailure, CapabilityHandler, register_handler)


@register_handler
class ConfirmQuotationHandler(CapabilityHandler):
    key = 'confirm_quotation'
    label = 'Confirm a quotation'
    mutates = True            # goes through the confirmation gate and the audit log

    def default_schema(self):
        return {
            'record_id': {'type': 'record_id', 'required': True, 'description': 'Quotation id'},
        }

    def access_operation(self):
        return 'write'        # checked on the model and on the record before anything runs

    def preview(self, env, capability, args):
        order = env['sale.order'].browse(args['record_id']).exists()
        return f'Confirm {order.name} ({order.amount_total} {order.currency_id.name})'

    def execute(self, env, capability, args, runner):
        order = self._record(env, capability, args['record_id'])
        if order.state not in ('draft', 'sent'):
            raise CapabilityFailure('Only quotations can be confirmed.')
        order.action_confirm()                  # runs with the user's rights
        return {'confirmed': {'id': order.id, 'name': order.name}}
```

Rules of thumb:

* Use the `env` argument for every data access — it is the user's environment. `capability`
  is configuration only (read with sudo).
* Raise `CapabilityFailure` for expected, user-presentable problems; `AccessError`,
  `UserError` and `ValidationError` are also converted into safe results. Unexpected
  exceptions are logged and the model only receives a generic failure.
* Return small JSON-serialisable dicts. Results are redacted, truncated (6 000 chars) and
  fenced as untrusted data before reaching the model. Add a `navigation` key with a window
  action built by `build_window_action()` to offer an "Open" button.
* Validate anything free-form yourself (domains → `DomainGuard`, values →
  `convert_value`).

## 2. Expose it in the selection

```python
from odoo import fields, models
from ..services import handlers  # noqa: F401  (registers the handler)


class CommunityAICapability(models.Model):
    _inherit = 'community.ai.capability'

    handler_key = fields.Selection(selection_add=[('confirm_quotation', 'Confirm a quotation')],
                                   ondelete={'confirm_quotation': 'cascade'})
```

## 3. Declare the capability record

```xml
<record id="capability_confirm_quotation" model="community.ai.capability">
    <field name="name">Confirm Quotation</field>
    <field name="technical_identifier">confirm_quotation</field>
    <field name="handler_key">confirm_quotation</field>
    <field name="model_id" ref="sale.model_sale_order"/>
    <field name="description">Confirm a draft quotation after the user agreed.</field>
    <field name="requires_confirmation" eval="True"/>
</record>
```

Then add it to an assistant. The model sees the capability as a tool named by
`technical_identifier`, described by `description` (+ target model and allowed fields), with
the JSON schema derived from `default_schema()` (administrators may override it in the
*Arguments* tab).

## Argument schema reference

```json
{
  "customer_name": {"type": "string", "required": true, "description": "Name of the customer"},
  "quantity": {"type": "integer", "minimum": 1, "maximum": 1000},
  "priority": {"type": "string", "enum": ["low", "normal", "high"]},
  "tags": {"type": "array", "items": {"type": "string"}},
  "values": {"type": "object"}
}
```

Types: `string` (≤ 500 chars unless `max_length`), `text` (≤ 20 000), `integer`,
`number`, `boolean`, `date` (`YYYY-MM-DD`), `datetime`, `record_id` (positive integer),
`array` (≤ 50 items), `object`. Unknown parameters are rejected; `default` fills optional
missing values.

## Testing

```python
from odoo.addons.ebshel_ai_suite.services.capability_runner import CapabilityRunner
from odoo.addons.ebshel_ai_suite.services.engines import ToolInvocation

runner = CapabilityRunner(env(user=user), assistant, session)
outcome = runner.invoke(ToolInvocation('t1', 'confirm_quotation', {'record_id': order.id}))
assert outcome.status == 'awaiting_confirmation'
outcome.operation.with_user(user).cai_decide(True)
```

See `tests/test_capability.py` for more patterns.
