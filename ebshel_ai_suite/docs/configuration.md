# Configuration

Everything lives under **Ebshel AI → Configuration**. The global options are in
**Settings → AI Configuration** (AI administrators).

## Connections (AI administrators)

| Field | Notes |
| --- | --- |
| Engine | *OpenAI-compatible API*, *Google Gemini*, or *Offline mock engine*. |
| Endpoint URL | Empty = vendor default (`https://api.openai.com/v1`, `https://generativelanguage.googleapis.com/v1beta`). Local examples: `http://localhost:11434/v1` (Ollama), `http://localhost:1234/v1` (LM Studio), `http://vllm:8000/v1`. |
| API key | Type it in **New API Key**. It is stored in `secret_key`, readable only by Settings administrators through RPC and never displayed; the form shows `•••• last4`. Alternatively choose *Server environment variable* and give a name starting with `CAI_` or `ODOO_AI_`. |
| Chat / Embedding / Image model | e.g. `gpt-4o-mini`, `text-embedding-3-small`, `gpt-image-1`; `gemini-2.0-flash`, `text-embedding-004`. **Fetch Models** lists what the key can use. |
| Temperature / Send Temperature | Untick *Send Temperature* for reasoning models that reject it. |
| Max Output Tokens / parameter name | `0` lets the engine decide. Some newer OpenAI models require `max_completion_tokens`. |
| Native JSON Mode | Asks for strict JSON when a feature needs structured output. Untick for servers that do not support `response_format`. |
| Transcription Model | e.g. `whisper-1`, `gpt-4o-mini-transcribe`; used by dictation and voice transcripts. |
| Retries, Timeout | Rate-limit and outage retries (0–5); timeouts are not retried. |
| Costs | Price per million input/output tokens, used for cost estimates in *Usage*. |

**Test Connection** validates the credentials; the result and a redacted diagnostic are kept
on the record.

## Assistants (AI managers)

* **Instructions**: role and business rules. Platform safety rules are always prepended.
* **Connection / Model / Creativity**: leave empty to inherit from the company default.
* **Reply style**, **Response language** (user's, question's, or fixed).
* **Capabilities** tab: allowed capabilities, *Confirm All Changes*, max tool rounds, whether
  the record open in the form view is shared (and how many chatter messages).
* **Knowledge** tab: sources, *Answer From Knowledge Only*, citations, excerpts per question.
* **Access & Limits**: groups allowed to use it, company, daily request limit.

## Capabilities (AI managers)

A capability = a behaviour (handler) + a target model + allowed fields + policy:

| Behaviour | Effect | Needs fields |
| --- | --- | --- |
| Search records | name search + validated filters on allowed fields | optional (filters/returned fields) |
| Read one record | privacy-filtered snapshot (+ recent messages) | optional (limits the snapshot) |
| Count / group records | counts, optionally grouped by an allowed field | yes for grouping |
| Open a view | offers a button opening a validated list/kanban/form view | optional |
| Create / Update a record | writes allowed fields only | **yes** |
| Log an internal note | posts a note in the chatter | no |
| Search knowledge sources | retrieval on the assistant's sources | no model |
| Search the web | needs a web search backend (Settings) | no model |
| Run a server action | executes the linked server action with the declared arguments (`cai_args` / `cai_output`) | no |
| Generate an image | image attached to the conversation (needs an image model) | no model |

Capabilities can also be bundled in **Skill Sets** and exposed to external clients with
**Available through MCP**; see [Agents, tools and integrations](agents_and_integrations.md).

Capabilities cannot target technical/security models (`ir.*`, `res.users`, `res.groups`,
`community.ai.*`, …) and cannot modify reference data (companies, currencies, countries,
languages, messages). The **Arguments** tab shows (and lets you override) the JSON argument
schema.

## Prompt templates

Syntax: `{{ variable }}`, `{{ record.partner_id.name }}`, filters
`default("…") upper lower strip truncate(N) striphtml join(", ")`, and
`{% if [not] path %}…{% else %}…{% endif %}`. Record paths only reach fields allowed by
the privacy filter. The `text.*` templates drive the writing tools and can be edited.

## Knowledge sources

Types: text, file (PDF, TXT, MD, CSV, JSON, HTML), URL (public addresses only by default),
Odoo record (snapshot of its allowed fields; used only for users who can still read the
record). Choose *Keyword* (no engine call) or *Semantic* (needs an embedding model).
Press **Index Now**; URLs and big files are indexed by the background queue.

## AI fields

Pick the model, target field, instruction or template, and input fields. **Add to Action
Menu** creates an *AI: …* entry on that model; *When a record is created* queues generation
for new records. *Only fill empty values* avoids overwriting user input.

## Automations

Trigger model + event (*created*, *created or updated*, *manual*), a trigger condition
(domain), an instruction and the **output fields** the AI may set. Modes: *Manual* (Action
menu only), *Automatic* (values written directly), *Automatic with approval* (proposal waits
in **Approvals → Automation Proposals**). Jobs run with the rights of **Run As** (only AI
administrators may choose someone else; a non-administrator who edits an automation becomes
its Run As user).

## Search targets & privacy rules

* **Search Targets** list the models natural-language search may query (default: contacts,
  activities), with optional keywords and a field whitelist.
* **Privacy Rules** add field names/patterns that are never sent to an engine, on top of the
  built-in list (passwords, tokens, keys, credentials, TOTP, bank and card numbers, …).

## Settings

| Setting | Parameter |
| --- | --- |
| Default connection / assistant | per company (`res.company.cai_connection_id`, `cai_assistant_id`) |
| Stream answers | `ebshel_ai_suite.streaming` |
| Confirmation of changes (per capability / always) | `ebshel_ai_suite.confirmation_policy` |
| Record context size | `ebshel_ai_suite.context_max_chars` |
| Requests / user / day, tokens / user / day | `ebshel_ai_suite.limit_user_daily_requests`, `…_tokens` |
| Requests / assistant / day, tokens / company / month | `ebshel_ai_suite.limit_assistant_daily_requests`, `…limit_monthly_tokens` |
| Audit read-only capabilities | `ebshel_ai_suite.audit_read_operations` |
| Audit / usage retention (days) | `ebshel_ai_suite.audit_retention_days`, `…usage_retention_days` |
| Allow private network URLs | `ebshel_ai_suite.allow_private_urls` |
| Web search backend (`brave`, `searxng` or a custom key) | `ebshel_ai_suite.web_backend`, `…brave_api_key`, `…searxng_url` |
| MCP server / allow changes through MCP | `ebshel_ai_suite.mcp_enabled`, `…mcp_allow_write` |

## Using the AI

* **Console**: AI icon in the top bar (side panel) or *Ebshel AI → Assistant* (full page
  with history). The chip under the header shows which record is shared; click *Don't use*
  to leave it out before sending the first message.
* **Command palette** (<kbd>Ctrl</kbd>+<kbd>K</kbd>): type a sentence, then *Ask Assistant*
  or *Find with AI*.
* **Writing widget**: add `widget="cai_assist"` to any char/text/html field in a view
  (contacts' internal notes use it out of the box).
* **Email composer**: *AI Assist* button; nothing is ever sent automatically.
