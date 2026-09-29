# Security model

## Groups

| Group | Can |
| --- | --- |
| Ebshel AI User | use the assistants allowed to them, read their own conversations, operations, jobs, usage and audit entries, use writing tools, natural-language search and AI fields/automations published in Action menus |
| Ebshel AI Manager | + configure assistants, capabilities, prompts, knowledge, AI fields, automations, search targets; read everyone's conversations, usage and audit; approve automation proposals |
| Ebshel AI Administrator | + connections and keys, privacy rules, settings, technical error details |

Users are **not** granted AI access automatically: administrators opt people in.

## The AI acts as the user

* Every data operation (capability, AI field, automation, retrieval of record sources,
  natural-language search) runs in the **effective user's environment**. `ir.model.access`,
  `ir.rule`, multi-company rules and field `groups` all apply; there is no `sudo()` around
  business data. Only framework configuration (capability field lists, rule settings) is
  read with `sudo()` because regular users cannot read `ir.model.fields`.
* Background jobs run with the rights of their `user_id` (the automation's *Run As* user or
  the author of an AI field rule).
* Conversations belong to their author: record rules restrict users to their own sessions,
  messages, operations, jobs, usage and audit entries; only the author can continue a
  conversation or confirm its operations.

## Nothing arbitrary is executed

* No `eval`/`exec` of model output, no SQL built from model output, no shell or file access.
* Models can only call **registered capabilities** of the current assistant that the user is
  allowed to use. Arguments are validated against an explicit schema (unknown parameters,
  wrong types, out-of-range values, unknown enums are rejected).
* Search domains proposed by a model are data: they are rebuilt leaf by leaf by
  `DomainGuard` (known, readable, searchable, non-sensitive fields; whitelisted operators;
  typed values; limited depth and size). Order clauses are validated too. Navigation actions
  are built server-side; the model never controls an action `context`.
* Template rendering has no expression evaluation.
* Capabilities cannot target security/technical models and cannot modify reference data.

## Confirmation of changes

Data-changing capabilities are turned into a pending **operation** (preview shown in the
chat) instead of being executed when:

* the capability or the assistant requires confirmation, or the global policy is *always*;
* **untrusted data is part of the turn** (record snapshot, knowledge excerpts, previous tool
  results) — this is enforced regardless of configuration;
* the call comes from an automation (then an AI manager approves it).

Approval re-validates arguments and access before execution. Every attempt, approval,
rejection and failure is written to the append-only audit log.

## Prompt-injection defences

* Message separation: platform rules (system) → assistant instructions (system) → history →
  reference material (user, fenced) → actual request (user) → tool results (fenced).
* Record data, documents, web/tool results are wrapped in `<untrusted_data kind=…>`; any
  fence-like tag inside the data is defanged so it cannot close the fence or forge a system
  message. Instruction-like phrases are flagged with an explicit "do not follow" notice.
* The structural defences above (allow-listed capabilities, forced confirmation, user
  rights) mean that even a successful injection cannot silently change data.

These defences reduce risk; no prompt-level measure can guarantee that a language model will
ignore malicious text. Keep write capabilities narrow and confirmation enabled.

## Agents and integrations

* **Server actions offered to the AI** run with the user's rights; the AI only fills the
  declared arguments and the record is validated by the framework (inside an **AI Decision**
  it is fixed to the processed record). They count as data-changing unless marked read-only.
* **AI Decision** treats the record as untrusted data. Without *Run Tools Without Approval*,
  changes are proposed operations approved by an AI manager; with it, they run and are
  audited as *pre-approved*. Only administrators allowed to edit server actions can create one.
* **Email template prompt blocks** are generated only for users with AI access; the
  instruction is never sent to recipients, and templates are not sent to the AI when saved.
* **Chat files** are attachments of the conversation (same record rules); document text is
  fenced as untrusted data. Audio for transcription is never stored.
* **MCP server**: disabled by default. Keys are Odoo API keys with the dedicated
  `ebshel_ai_mcp` scope (keys of other scopes are refused; unscoped global keys are
  accepted, as everywhere in Odoo), created after a password check. Calls run with the key
  owner's rights and need the *Ebshel AI User* group. Domains go through `DomainGuard`,
  field access through the privacy filter; security models are refused; write tools exist
  only when *Allow Changes Through MCP* is on and every write is audited. Treat an MCP key
  like a password: whoever holds it acts as that user.
* **Live chat**: the AI answers with the visitor's rights (public or portal user), has no
  data tools except the handover, and stops once a human operator joins.

## Data minimisation

Never sent automatically: passwords, API keys, tokens, TOTP secrets, session identifiers,
private keys, credentials, bank/card numbers, binary content (images, files) and technical
ORM fields. Administrators add further exclusions with **Privacy Rules**. Record snapshots
are capped (12 000 characters by default, 1 500 per field, 10 items per relation) and
text is passed through a secret-redaction filter.

## Secrets

* API keys are write-only in the UI, readable through RPC only by Settings administrators,
  or kept out of the database via environment variables (`CAI_*`/`ODOO_AI_*`).
* Keys are never logged; error details and audit arguments pass through `redact_secrets`.
* Raw provider errors are never shown to users: they get a translated, generic message;
  AI administrators see the redacted technical detail on the failed message.

## Network

URL knowledge sources only allow `http(s)`, refuse redirects, cap downloads at 5 MB and
refuse hostnames that resolve to private, loopback, link-local or reserved addresses
(SSRF). Note that the check happens before the request, so it does not defend against DNS
rebinding; keep the Odoo server's own egress restricted if that matters to you.

## Known limitations

* Heuristic injection detection is English-centric.
* An external MCP client decides by itself what to do with the data it reads; only grant MCP
  keys to users whose data may leave Odoo through that client.
* AI Decision runs synchronously inside the triggering transaction (slow engines slow the
  triggering action).
* Retrieval of record sources is a snapshot at indexing time; the read-access check happens
  on the referenced record at question time, not on each field.
* Usage lines of a failed background job are rolled back together with the job's savepoint.
