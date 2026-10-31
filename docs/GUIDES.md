# Guides
None of the
first three needs a code change or a restart.

## Add a language
1. **Settings → Languages → Add language.** Pick from the list or type an IETF code (`fa`, `pt-br`, `uk`); set the
   native name, flag and **right-to-left** for Arabic-script and Hebrew languages. Enable it.
2. **Bot content → Bot texts**, choose the language and translate the system messages (missing ones fall back to
   the default language, then English — users never see a key). The 11 shipped languages (en, ru, ar, he, es, fr,
   pt, zh, hi, bn, ur) are already complete.
3. Translate the **welcome message**, **menu** labels, **FAQ** titles and answers (each has a tab per language;
   an orange dot marks missing translations), **statuses**, **departments and categories**, **templates**.
4. Publish the FAQ. The bot picks the language up within seconds: users whose Telegram is set to it get it
   automatically, others can choose it in «Language».

Default language: Settings → Languages → *Make default*. Switching a language off hides it from users; their
choice falls back to the default.

The dashboard itself is translated into the same 11 languages (the switch at the bottom of the sidebar). To add a
dashboard language, add `dashboard/src/services/locales/<code>.ts` with the keys of `TEXTS` in
`dashboard/src/services/i18n.ts` (a missing key shows the English text).

## Add a FAQ branch
1. **FAQ** → *New top-level topic* (a category) or select a question → *Add a question inside*.
2. Give it a title per language (the button text), an answer (text, photos, album, video, audio, voice, documents,
   buttons — per language) and search keywords.
3. Choose what opening it does: show the answer, open another question, open a link, start «Contact support» or
   show the main menu. Optionally send tickets from this answer to a department/category.
4. Reorder with the arrows or move a question to another parent (the tree keeps every child).
5. Check it in the **simulator** on the right, or *Preview in my Telegram*.
6. **Publish** with a note. Users see only published versions; *Versions* restores an earlier one into the draft.

Limits (depth, questions per level, total) are in Settings → General.

## Add a support agent
1. **Team → New account**: username, display name, temporary password, role **Support Agent** (or a custom role),
   departments, languages they speak, maximum open tickets for auto-assignment.
2. The agent signs in, changes the password, enables two-factor sign-in.
3. Profile → **Telegram notifications** → *Get link code* → send `/link CODE` to the bot. From then on the agent gets
   new tickets, replies, assignments and overdue alerts in Telegram, with buttons:
   * **✍️ Reply** — the next message (text, photo, file, voice) goes to the user as the agent's answer;
   * **📝 Note** — an internal note in the ticket;
   * a **swipe-reply** to the notification also answers the user;
   * **Open in dashboard** (when the dashboard has HTTPS).
   The same rules as in the dashboard apply: only visible tickets, only with `tickets.reply` / `tickets.notes`.
   Notifications come in the language the agent uses in the bot (in the dashboard bell — in the dashboard language):
   new tickets, replies, assignments, transfers, SLA warnings and breaches, escalations, tickets closed or reopened
   by users, and the Monday summary for those who see reports.
4. Routing (Settings → Routing) can assign new tickets to agents by department, category, language and load.

To remove someone: Team → account → *Block* (sessions and Telegram answering stop at once). Accounts with history
are kept blocked so the history keeps their name.

## Ask users for contact details
Bot content → Menu → switch on **My contacts** (off by default). Users can then share their phone number with
Telegram's button (only their own), type an e-mail, and delete both. Staff with `users.sensitive` see them on the
profile and can write down details a user gave in the conversation (*Edit contacts*, audited).

## Integrations
* **Dashboard API** — everything the dashboard does is an HTTP API ([API.md](API.md), `openapi.json`). Create a
  staff account with a custom role for the integration (minimum permissions), sign in, call the API. Typical uses:
  CRM sync of users and tickets, a nightly export of reports, a status page.
* **Reports** — Analytics exports CSV, Excel and PDF with documented rules ([ANALYTICS.md](ANALYTICS.md)).
* **Website** — link to the bot (`https://t.me/<bot>?start=site`); the welcome message can carry website buttons.
* **Error tracking** — `SENTRY_DSN`.

Where to put new code: a new backend feature is a service in `backend/services/` plus a router in `backend/api/`
(add its permission rule to `RULES` in `backend/services/team.py` — the test suite refuses unruled routes); background
work is a Celery task in `workers/tasks.py`; schema changes are an Alembic migration
(`alembic revision --autogenerate -m "…"`, review it, `alembic upgrade head`).

## Several bots and other channels
These can be added later without rebuilding the core. What is already in place:

* `users.bot_id`, `tickets.bot_id`, `campaigns.bot_id` — every record says which bot it belongs to (`BOT_ID` in
  `.env`, `main` by default). `tickets.channel` and `messages.channel` say where a conversation happens
  (`telegram` today).
* The bot process holds no business logic: it only calls the internal API. Tickets, routing, SLA, analytics,
  permissions and the dashboard do not depend on Telegram.
* Outgoing messages are rows with a delivery state, sent by the worker — a new channel is a new sender.

**A second Telegram bot** (e.g. another brand or country):

1. Add a `bots` table (id, token reference, name) and let `workers/telegram.py` pick the token by `bot_id` of the
   user/ticket/campaign instead of the single `BOT_TOKEN`.
2. Run one more `bot` service with its own `BOT_TOKEN` and `BOT_ID` (Compose: copy the `bot` service).
3. In the dashboard, add a bot filter to tickets, users and reports (the columns and the `ix_tickets_bot` index exist).

**Another channel** (web chat, e-mail, WhatsApp): a small adapter that (a) registers the contact and creates or
continues tickets through the same services as `/internal/*` with `channel=<name>`, and (b) a worker sender for
messages with that channel. Ticket workflow, SLA, routing, templates and analytics work unchanged.

**AI-assisted answers**: the template picker in the ticket view is the natural place for suggestions; a
service would read the conversation and the published FAQ and propose a draft that an agent edits and sends — the
agent always reviews.
