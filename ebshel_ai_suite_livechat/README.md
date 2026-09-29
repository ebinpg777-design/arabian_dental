# Ebshel AI Suite - Live Chat (`ebshel_ai_suite_livechat`)

Bridge between [Ebshel AI Suite](../ebshel_ai_suite/README.md) and Odoo **Live Chat**
(`im_livechat`), installed automatically when both are installed.

On a live chat channel (**Live Chat → Configuration → Channels**, tab **AI Assistant**, live
chat managers only):

* **AI Assistant**: answers visitors first, from its knowledge sources only;
* **Max AI Answers**: after that many answers the visitor is handed over to a human.

The assistant hands the conversation over to a human operator (Odoo's standard forwarding)
when the visitor asks for a person or a demo, when it cannot answer confidently, when the AI
engine fails, or when the answer cap is reached. When no operator is available, the visitor
is asked to leave their email address. Once a human has joined, the AI stays silent.

Security: answers are generated with the **visitor's** rights (public user, or the portal
user when logged in), the only tool available is the handover, visitor messages are treated
as untrusted input, and usage is recorded against the visitor.

Tests: `./odoo-bin -d db --test-enable --test-tags /ebshel_ai_suite_livechat --stop-after-init`
(mock engine only).

License: LGPL-3.
