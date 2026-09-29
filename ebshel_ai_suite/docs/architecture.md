# Architecture

The suite separates **configuration** (thin ORM models) from **behaviour** (plain Python
services that receive an Odoo environment). Business code never talks to an AI vendor
directly: it goes through the gateway, which picks an engine adapter.

```
                ┌──────────────────────── Web client (OWL) ────────────────────────┐
                │ CommunityAILauncher (systray)   CommunityAIConsole (panel / page) │
                │ command palette provider        cai_assist field widget           │
                │                 communityAiBridge service                         │
                └───────────────┬──────────────────────────────┬────────────────────┘
                     ORM calls (JSON-RPC)            /ebshel_ai/stream (NDJSON)
                                │                              │
   ┌────────────────────────────▼──────────────────────────────▼───────────────────┐
   │ models/ (configuration & records)          services/ (behaviour)              │
   │  community.ai.session / .exchange  ───────► ConversationRunner                 │
   │  community.ai.assistant                     ├─ RecordContextBuilder            │
   │  community.ai.capability / .operation       ├─ RetrievalEngine (RAG)           │
   │  community.ai.source / .source.chunk        ├─ CapabilityRunner ─► handlers    │
   │  community.ai.prompt                        │                    DomainGuard   │
   │  community.ai.field.rule / .automation      └─ LLMGateway ─► UsageGuard        │
   │  community.ai.job (queue)                                  └► EngineAdapter    │
   │  community.ai.usage / .audit                                  (openai, gemini, │
   │  community.ai.privacy.rule / .search.target                    mock, yours…)   │
   └───────────────────────────────────────────────────────────────────────────────┘
```

## Layers

| Layer | Location | Responsibility |
| --- | --- | --- |
| Engine adapters | `services/engines/` | Vendor wire formats only. Receive an immutable `EngineSettings` snapshot, return `EngineReply` / `StreamPiece`. No ORM access. |
| Gateway | `services/llm_gateway.py` | Connection resolution (assistant → company default → first available), quotas, retries, latency, usage lines, error normalisation. |
| Context | `services/context_builder.py` | Privacy-filtered, size-limited record snapshots; safe dotted-path resolution for templates. |
| Prompts | `services/prompt_renderer.py` | Tiny template language (`{{ }}`, filters, `{% if %}`) with no expression evaluation. |
| Capabilities | `services/capability_*.py`, `domain_guard.py`, `value_converter.py` | Schema validation, access checks, confirmation gate, execution in a savepoint, sanitisation, audit. |
| Conversations | `services/conversation_runner.py` | One assistant turn as an event generator: reference material, history, tool rounds, streaming. |
| Features | `nl_search.py`, `field_generator.py`, `automation_engine.py`, `text_tools.py`, `retrieval_engine.py` | Natural-language search, AI fields, automations, writing tools, RAG. |
| Agents | `decision_engine.py`, `template_prompts.py`, `extensions.py` | AI Decision server actions (bound record, pre-approval), email template prompt blocks, web search backends. |
| MCP | `services/mcp_server.py`, `controllers/mcp.py` | JSON-RPC 2.0 MCP server (initialize, tools/list, tools/call) over HTTP POST with scoped API keys. |
| HTTP | `controllers/assistant_api.py` | Console bootstrap and NDJSON streaming. |

## A conversation turn

1. `community.ai.session.cai_send(text)` (RPC) or `POST /ebshel_ai/stream` creates the
   user exchange and a running assistant exchange.
2. `ConversationRunner` builds the messages:
   * `system`: platform rules (always first, not editable) + the assistant's instructions,
     style and language;
   * previous exchanges (bounded by count and characters);
   * a `user` message holding **reference material**: the record snapshot and retrieved
     knowledge excerpts, each fenced in `<untrusted_data>`;
   * the user's actual request.
3. The gateway calls the engine with the tool specs of the assistant's capabilities.
4. For each tool call, `CapabilityRunner` validates, checks access, asks for confirmation or
   executes, sanitises, audits, and returns a fenced tool result. Up to
   `max_tool_rounds` rounds; the last round is sent without tools.
5. The final answer, citations and optional navigation action are stored on the exchange.
   Engine failures mark the exchange *failed* with a user-safe message; the technical detail
   (redacted) is only visible to AI administrators. **Retry** re-answers the last question.

Streaming: Odoo delivers bus notifications only after the request transaction commits, so
the bus cannot carry token-by-token output. The streaming route instead returns a generator
that opens its own cursor and flushes one JSON event per line (`user`, `start`, `delta`,
`step`, `tool`, `confirmation`, `sources`, `navigation`, `done`, `error`). The same event
generator serves the non-streaming RPC path, so both behave identically. The bus is used
for background job notifications (`ebshel_ai/job`).

## Background work

`community.ai.job` is a small queue processed by the *run background jobs* cron (every 5
minutes, one commit per job). Jobs run **with the rights of their `user_id`**, retry
transient engine errors (up to 3 attempts, waiting 2 then 10 minutes) and notify the user
on the bus. Automation triggers are detected by a scheduled scan of `create_date` /
`write_date` (with an overlap window and per-record de-duplication) instead of overriding
`create`/`write` of business models: business transactions never wait for, or fail
because of, an AI call.

## Retrieval

`source → extractor → normalisation → chunking (~900 chars, paragraph-aware, overlap) →
[embedding] → index → search`. Index backends are registered with
`register_index_backend(key)`; `keyword` (BM25 with coverage threshold) and `embedding`
(cosine over JSON vectors) ship with the module and need no PostgreSQL extension. A
pgvector or external vector-store backend can be added by another module.

## Caching

* Privacy patterns per model and prompt lookups by code use `ormcache` (cleared on change).
* Model lists fetched from an engine are stored on the connection (`available_models`).
* Embeddings are stored on chunks and recomputed only when a source is re-indexed.
* No user-specific conversation data is cached outside the database.

## Naming conventions

| Kind | Convention | Example |
| --- | --- | --- |
| Module | `ebshel_ai_suite` | |
| Models | `community.ai.<noun>` | `community.ai.exchange` |
| Custom methods on models | `cai_*` (public RPC), `_cai_*` (private), `action_cai_*` (buttons) | `cai_send`, `_cai_payload` |
| Fields on standard models | `cai_*` | `res.company.cai_connection_id` |
| Security groups | `group_cai_user/manager/admin` | |
| Config parameters | `ebshel_ai_suite.<key>` | `ebshel_ai_suite.limit_monthly_tokens` |
| JS service | `communityAiBridge` | |
| OWL components / templates | `CommunityAI*`, `Cai*` in `ebshel_ai_suite.*` | `CommunityAIConsole` |
| CSS | `o_cai_*` | `.o_cai_console` |
| Bus notification | `ebshel_ai/<topic>` | `ebshel_ai/job` |
