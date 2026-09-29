# Agents, tools and integrations

This page covers the features that turn assistants into task-oriented agents and connect
them to the rest of Odoo and to external AI clients.

## Skill sets

**Ebshel AI → Configuration → Skill Sets** bundle *instructions* and *capabilities*
under one name (e.g. "Record Lookup", "Navigation", "Record Changes", "Knowledge").
Add skill sets to an assistant on its **Skill Sets** tab: the assistant then gets the union
of its own capabilities and those of its active skill sets, and each skill set's
instructions are added to the system prompt (only when tool use is enabled). Archiving a
skill set removes it from every assistant at once.

Assistants also offer two extra reply styles: *Lively* and *Rigorous*. The **Test** button
on the assistant form opens a conversation with it straight away.

## Context presets and quick prompts

**Configuration → Context Presets** decide what the console proposes depending on where it is
opened.

| Field | Effect |
| --- | --- |
| Purpose | *Assist* (console) or *Write* (the `cai_assist` writing widget) |
| Models / Needs a record | Where the preset applies; empty models = everywhere |
| Assistant | Assistant selected when the console opens from there |
| Context Instructions | Extra system instructions for conversations started from there |
| Buttons | Quick prompts shown as one-click suggestions (console) or shortcuts (writing widget) |
| Groups / Company | Who sees the preset |

The most specific preset wins (record-bound and model-specific before generic ones, then
by sequence). A preset id sent by the browser is re-checked on the server, so it cannot be
used outside its models or groups.

## Files and images in the chat

The paperclip in the console stages up to 5 files (10 MB each) for the next message.

* Text documents (PDF, text, Markdown, CSV, JSON, HTML) are extracted; the passages most
  relevant to the question are sent as fenced *untrusted* reference material.
* Images are sent to engines that support vision (OpenAI-compatible and Gemini adapters).
* Files are stored as attachments of the conversation and follow its access rules.

## Answer actions

Under each answer: **Copy**, and when the conversation is linked to a record with a chatter,
**Send as message** / **Log as note**. Both open the standard composer prefilled with the answer
(converted from Markdown to safe HTML). Nothing is sent without the user pressing *Send*.

## Server actions as AI tools

On any server action (*Run Python code*, *Update record*, *Create record*, *Multi actions*)
press **Offer to AI**. This creates a capability with the *Run a server action* behaviour:

* declare its arguments on the capability (**Arguments** tab); the code receives them in the
  `cai_args` dictionary and may fill the `cai_output` dictionary, which is returned to the AI;
* the target record is chosen by the framework: `record_id` is validated and, inside an AI
  Decision, fixed to the record being processed;
* the tool counts as *modifying data* (confirmation rules apply) unless you tick
  **Read-only Tool**.

The server action runs with the user's rights, like every capability.

## AI Decision server actions

A server action of type **AI Decision** lets an automation rule (or a manual action) ask
the AI what to do with a record:

* **Instruction**: a prompt template rendered on the record (`{{ record.name }}` …);
* **Tools the AI May Use**: capabilities on the same model, typically server actions offered
  to the AI;
* **Run Tools Without Approval**: when unticked, data-changing tool calls become *proposed
  operations* that an AI manager approves in **Approvals**; when ticked, they run directly and
  the audit log records them as *pre-approved*.

The record's data is fenced as untrusted input and the AI cannot redirect a tool to another
record. The decision runs synchronously in the transaction of the triggering action (up to
4 tool rounds).

## Prompt blocks in email templates

In the HTML editor of an email template, type `/` and choose **AI Prompt** to insert a block
containing an instruction ("Write one sentence thanking them for their last order").
When the template is rendered for a recipient, each block is replaced by text generated from
the instruction and the recipient record's privacy-filtered data. If generation fails, or the
sending user has no AI access, the block is removed: the instruction itself is never sent.
Templates are not sent to the AI when saved (Odoo's syntax check skips generation).

## AI fields: refresh button and daily fill

On an AI field rule:

* **Add AI Button to Form** adds a small refresh button next to the field in the model's
  default form view (an inherited view you can remove with **Remove AI Button**). Clicking it
  regenerates the value, even if the field is filled.
* **Fill Empty Values Daily** queues generation for records whose field is still empty
  (the *Ebshel AI: fill empty AI fields* scheduled action).

## Voice

* **Dictation**: the microphone button in the console input records audio, transcribes it and
  inserts the text.
* **Voice transcript** in the writing widget records a meeting or a note and returns the
  transcript and, optionally, a structured summary (discussion points, decisions, action
  items, next steps).

Audio is sent to the connection's **Transcription model** (e.g. `whisper-1`,
`gpt-4o-mini-transcribe`, or a Gemini model) and is never stored. Limit: 25 MB per
recording. Browsers only allow the microphone on HTTPS (or `localhost`).

## Image generation in the chat

Enable the *Generate Image* capability on an assistant (it needs a connection with an
image model). Generated images are stored as attachments of the conversation and shown
under the answer.

## Web search

**Settings → Web Search Backend**: *Brave Search* (API key) or *SearXNG* (URL of your instance,
with the JSON output format enabled). Then enable the *Search the Web* capability on an
assistant. Results are fenced as untrusted data. Other providers can be added with
`register_web_backend` (see `services/extensions.py`).

## MCP server

External AI clients that speak the [Model Context Protocol](https://modelcontextprotocol.io)
(Streamable HTTP transport) can use Odoo data through this server.

1. **Settings → MCP Server**: enable it. The endpoint URL is shown
   (`https://<your-odoo>/ebshel_ai/mcp`).
2. Press **Generate MCP Key** (you confirm your password). The key only works for the MCP
   endpoint; copy it, since it is shown once. The user needs the *Ebshel AI User* group.
3. Configure the client with the header `Authorization: Bearer <key>`. With several
   databases on the server, also send `X-Odoo-Database: <db name>`.

Examples:

```bash
# Claude Code
claude mcp add --transport http odoo https://erp.example.com/ebshel_ai/mcp \
  --header "Authorization: Bearer $ODOO_MCP_KEY"
```

```json
// Clients that only launch local (stdio) servers, via the mcp-remote bridge
{
  "mcpServers": {
    "odoo": {
      "command": "npx",
      "args": ["mcp-remote", "https://erp.example.com/ebshel_ai/mcp",
               "--header", "Authorization: Bearer ${ODOO_MCP_KEY}"],
      "env": {"ODOO_MCP_KEY": "<key>"}
    }
  }
}
```

Tools:

| Tool | Notes |
| --- | --- |
| `get_context` | user, language, time zone, companies, today |
| `list_models`, `describe_model`, `list_menus` | discovery, limited to what the user can read |
| `search_records`, `aggregate_records` | validated domains (`DomainGuard`), privacy-filtered fields, at most 200 rows |
| `create_records`, `update_records` | only when **Allow Changes Through MCP** is enabled; audited |
| `cap_<identifier>` | capabilities marked **Available through MCP** (data-changing ones only when changes are allowed) |

Every call runs with the key owner's rights. Security models (`ir.*`, `res.groups`, API keys,
`community.ai.*`, …) are refused, credential fields are never returned, and write calls are
logged in the audit log. The transport supports POST only (no server-initiated stream);
notifications are acknowledged with `202 Accepted`.

## Live chat (`ebshel_ai_suite_livechat`)

Installed automatically when both *Ebshel AI Suite* and *Live Chat* are installed. On a
live chat channel, the **AI Assistant** tab (live chat managers) sets:

* **AI Assistant**: answers visitors first, from its knowledge sources only;
* **Max AI Answers**: after that many answers the visitor is handed over to a human.

The assistant hands over when the visitor asks for a person, when it cannot answer, when the
engine fails, or when the reply cap is reached. It uses Odoo's normal operator forwarding. If no
operator is available, the visitor is asked to leave their email address. Once a human
operator has joined, the AI stays silent. The AI runs with the visitor's rights (public user
or portal user), never with an employee's, and its only tool is the handover.
