# Ebshel AI Suite (`ebshel_ai_suite`)

An independently designed AI framework for **Odoo 19 Community**. It depends only on
`base`, `web` and `mail`, and works with any OpenAI-compatible endpoint (OpenAI, Ollama,
vLLM, LM Studio, LocalAI, OpenRouter, …), with Google Gemini, or with a built-in offline
mock engine for tests and demos.

## Features

| Area | What you get |
| --- | --- |
| Engines | Pluggable adapters (`openai_compatible`, `gemini`, `mock`) behind one gateway: retries, timeouts, usage logging, user-safe errors, streaming |
| Assistants | Instructions, reply style, language, creativity, per-assistant capabilities and knowledge, group/company restrictions, daily limits |
| Conversations | Side-panel console (systray), full-page console with history, streaming answers (NDJSON) with RPC fallback, retry, new conversation |
| Record context | Opt-in, privacy-filtered snapshot of the record open in the form view (plus recent chatter) |
| Capabilities | Registered, schema-validated operations: search/read/count/create/update records, open views, search knowledge, log notes, web lookup hook |
| Confirmation | Data-changing capabilities are prepared as *operations* and confirmed in the chat; confirmation is forced whenever untrusted data is involved |
| Natural-language search | Command palette "Find with AI: …" → validated domain → list view |
| Writing tools | Improve, rewrite, summarize, expand, shorten, translate, change tone, reply, describe, follow-up; `cai_assist` field widget; "AI Assist" button in the email composer |
| AI fields | Rules that generate a field value (char, text, html, numbers, dates, boolean, selection, many2one, many2many) from a prompt, from the Action menu or on record creation |
| Automations | Trigger on create/update/manual, structured output fields, manual / automatic / automatic-with-approval modes, background queue with retries |
| Knowledge (RAG) | Text, files (PDF/text/HTML/Markdown), URLs, Odoo records; keyword (BM25) or embedding retrieval; "answer from knowledge only"; citations |
| Governance | Usage & cost reporting, quotas, append-only audit log with retention, privacy rules, three security groups, multi-company rules |
| Images (optional) | Image generation wizard and in-chat image generation, stored as attachments (OpenAI-compatible engines) |
| Skill sets & presets | Reusable bundles of instructions + capabilities; context presets choosing the assistant, extra instructions and one-click prompts per model |
| Chat files & answer actions | Attach documents (relevant passages retrieved) and images (vision engines); send an answer as a message or log it as a note through the standard composer |
| Server actions as tools | "Offer to AI" turns a server action into a capability with declared arguments; **AI Decision** server actions let automation rules ask the AI which tools to run on a record (with or without approval) |
| Email templates | AI prompt blocks (`/AI Prompt`) generate personalised text per recipient at render time |
| Voice | Dictation in the console, meeting/voice transcripts with summaries (OpenAI-compatible and Gemini transcription) |
| Web search | Brave Search and SearXNG backends for the "Search the Web" capability |
| MCP server | `/ebshel_ai/mcp`: external AI clients read (and optionally write) Odoo data with the user's rights, using scoped API keys |
| Live chat | Companion module `ebshel_ai_suite_livechat`: an assistant answers website visitors first and hands over to human operators |

## Quick start

1. Put `ebshel_ai_suite` in your addons path and install it:
   `./odoo-bin -d mydb -i ebshel_ai_suite`
2. **Settings → Users**: give users the *Ebshel AI User* group (the installing admin is
   *Ebshel AI Administrator*).
3. **Ebshel AI → Configuration → Connections**: create a connection, paste the API key
   (or reference an environment variable), set the chat model, press **Test Connection**.
4. **Ebshel AI → Configuration → Settings**: pick the default connection and assistant.
5. Click the AI icon in the top bar, or press <kbd>Ctrl</kbd>+<kbd>K</kbd> and type a sentence.

Demo databases are preconfigured with the offline mock engine, a "Business Assistant",
a "Policy Helper" and a sample knowledge source.

## Documentation

* [Architecture](docs/architecture.md)
* [Installation](docs/installation.md)
* [Configuration](docs/configuration.md)
* [Security](docs/security.md)
* [Agents, tools and integrations](docs/agents_and_integrations.md): skill sets, presets, files, server-action tools, AI Decision, email template prompts, voice, web search, MCP, live chat
* [Developing an engine (provider) adapter](docs/provider_development.md)
* [Developing a capability](docs/capability_development.md)

## Tests

```bash
./odoo-bin -d test_ai_db -i ebshel_ai_suite --test-enable --test-tags /ebshel_ai_suite --stop-after-init
```

The suite (128 tests: unit, HTTP streaming and MCP endpoint, and three browser tours; plus
6 tests in `ebshel_ai_suite_livechat`, run with `--test-tags /ebshel_ai_suite_livechat`)
never calls a real AI service: it uses the deterministic mock engine and patched HTTP responses for the OpenAI-compatible
and Gemini adapters. Browser tours need Chrome/Chromium and the `websocket-client` package.

## Independence from Odoo Enterprise

This module does not depend on, import, or reuse code from Odoo Enterprise. All model,
field, method, XML ID, service, component and CSS names use the `community.ai.*`,
`ebshel_ai_suite.*`, `cai_*`, `Cai*`/`CommunityAI*` and `o_cai_*` vocabulary. See
[docs/architecture.md](docs/architecture.md#naming-conventions).

## License

LGPL-3.
