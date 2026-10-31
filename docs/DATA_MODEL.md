# Data model
PostgreSQL 16. The schema is defined by the SQLAlchemy models in `backend/models/` and created **only**
by Alembic migrations in `backend/database/migrations/versions/` (`0001` initial schema, `0002` SLA pause, `0003`
bot id / channel / «My contacts», `0004` reminders and translated staff notifications). The migrate service runs `alembic upgrade head` before every start; a test
(`tests/integration/test_migrations.py`) builds the schema from the migrations and fails on any difference from
the models. Every migration also has a working `downgrade` (checked on PostgreSQL 16: up → down → up).

All moments are `timestamptz` in UTC. Translatable texts are JSON objects `{"en": "…", "ru": "…", "ar": "…"}` —
adding a language never changes the schema. JSON columns are `jsonb` in PostgreSQL.

## Diagram
```
                         roles ─┐
departments ─┬─ staff_departments ─ staff ─┬─ staff_notifications
             │                             └─ (audit_log.staff_id)
             ├─ categories
             └─ tickets ─┬─ ticket_events
users ───────────────────┼─ messages ──(asset_id)── media_assets
  ├─ user_tags           │
  ├─ user_notes          └─(faq_node_id)── faq_nodes ─ faq_versions (published snapshots)
  ├─ user_events
  └─ campaign_deliveries ─ campaigns

ticket_statuses ─ status_transitions     sla_policies     assignment_rules
menu_items   templates   languages   bot_texts   settings
processed_updates   error_log   bot_state   alembic_version
```

## Users and their history
| Table | Purpose | Key columns |
|---|---|---|
| `users` | Telegram users of the bot | `id` = Telegram user id (PK); `bot_id`; `username`, `first_name`, `last_name`; `tg_language` (from Telegram), `language` (chosen in the bot); `first_seen`, `last_seen`, `interactions`; `banned`, `ban_reason`, `banned_at`, `banned_by`; `bot_blocked`, `bot_blocked_at`; `campaigns_opt_out`; `email`, `phone`, `country`, `timezone` (only when supplied by the user); `anonymized` |
| `user_tags` | Tags (PK user_id + tag) | `tag` |
| `user_notes` | Internal notes about a user | `staff_name`, `text` |
| `user_events` | Activity timeline and analytics | `kind` (start, faq_open, search, search_miss, ticket_created, language, rating, opt_out, opt_in, contacts), `ref_id` (FAQ node, ticket…), `data` (path, query…) |

## Tickets
| Table | Purpose | Key columns |
|---|---|---|
| `tickets` | One support request | `number` (unique, `T-000123`); `user_id`; `bot_id`, `channel`; `department_id`, `category_id`, `priority`, `status`, `assignee_id`; `language`; `subject`; `source` (bot, dashboard); `faq_node_id` (the FAQ answer it came from); `created_at`, `updated_at`, `first_response_at`, `resolved_at`, `closed_at`; `last_user_message_at`, `last_staff_message_at`; `due_response_at`, `due_resolution_at`, `sla_paused_at`, `sla_paused_seconds`, `response_breached`, `resolution_breached`; `reopen_count`; `reminded_at` (the user was reminded of the current wait); `rating`, `rating_comment`; `dedupe_key` (unique: one ticket per Telegram message or album) |
| `ticket_events` | History: who changed what and when | `kind` (created, status, priority, department, category, assignee, rating, sla, sla_warning, reminder), `old`, `new`, `actor_type` (staff, user, system), `actor_id`, `actor_name`, `comment` |
| `messages` | Every message of a conversation | `user_id`, `ticket_id` (null = direct message); `direction` (in, out, note, system); `channel`; `staff_id`, `author`; `kind` (text, photo, video, voice, audio, document, animation); `text`; `file_id` (Telegram file of the user's or agent's attachment), `asset_id` (dashboard upload), `file_name`, `file_size`; `markup` (buttons under a system message); `tg_message_id`; `delivery` (queued, sending, sent, failed, blocked), `attempts`, `error`, `sent_at`; `dedupe_key` (unique: one row per Telegram message) |
| `ticket_statuses` | Configurable statuses | `key` (PK), `name` (per language), `color`, `stage` (new, open, pending_agent, pending_user, escalated, resolved, closed, reopened), `position`, `enabled`, `builtin`, `notify_user` |
| `status_transitions` | Permitted transitions | PK (`from_key`, `to_key`) |
| `sla_policies` | Targets per priority | `priority` (PK), `first_response_minutes`, `resolution_minutes` |
| `assignment_rules` | Routing of new tickets | conditions: `department_id`, `category_id`, `language`, `priority`; actions: `set_department_id`, `set_priority`, `assign_to`, `strategy` (none, least_loaded, round_robin) |
| `departments`, `categories` | Teams and topics | names per language, `enabled`, `position`; category `default_priority` |

## Content
| Table | Purpose |
|---|---|
| `menu_items` | Main menu: `action` (faq, support, tickets, language, contacts, faq_node, url, content), `value`, `label` per language, `content` (rich content of a page), `enabled`, `position` |
| `faq_nodes` | The FAQ **draft**: `parent_id` (tree), `position`, `enabled`, `title` and `keywords` per language, `content` (rich content), `redirect` + `redirect_value`, `allow_ticket`, `department_id`, `category_id` |
| `faq_versions` | Published snapshots of the tree (what users see), `note`, `published_by` — restoring an old version copies it back into the draft |
| `templates` | Response templates: `name`, `shortcut`, `texts` per language, `department_id` |
| `media_assets` | Media library: `kind`, `filename`, `mime`, `size`, `sha256`, `path` (under `STORAGE_DIR`), `tg_file_id` (cached after the first upload) |
| `bot_texts` | System texts of the bot edited in the dashboard (unique `key` + `lang`); built-in texts in `backend/services/locales/*.json` are the fallback |
| `languages` | `code` (PK, IETF), `name`, `native_name`, `flag`, `rtl`, `enabled`, `is_default`, `position` |

**Rich content** (welcome message, FAQ answers, pages, announcements):

```json
{"blocks": [{"type": "text|photo|album|video|audio|voice|document|animation",
             "text": {"en": "…", "ar": "…"}, "assets": [12, 13], "enabled": true}],
 "buttons": [{"label": {"en": "…"}, "action": "url|node|faq|support|tickets|menu|language", "value": "…"}]}
```

## Announcements
| Table | Purpose |
|---|---|
| `campaigns` | `name`, `bot_id`, `content` (rich content), `filters` (segment), `status` (draft, scheduled, running, paused, done, cancelled), `scheduled_at`, `started_at`, `finished_at`, `rate_per_second`, `max_attempts`, `recipients` |
| `campaign_deliveries` | One row per recipient (unique campaign + user — a retry can never send twice): `language`, `status` (pending, sending, sent, failed, blocked, skipped), `attempts`, `next_attempt_at`, `error`, `sent_at` |

## Staff and system
| Table | Purpose |
|---|---|
| `staff` | Accounts: `username`, `display_name`, `password_hash` (argon2), `role_id`, `active`, `session_version`, `totp_secret`, `telegram_id`, `languages`, `notify`, `max_open_tickets`, `failed_logins`, `locked_until`, `last_login_at` |
| `roles` | `id`, `name`, `permissions` (list), `builtin` |
| `staff_departments` | Membership (PK staff + department) |
| `staff_notifications` | The dashboard bell: `kind` (new_ticket, reply, assigned, overdue, sla_warning, escalated, transferred, user_closed, reopened, report), `ticket_id`, `data` (the values; each person reads the text in their own language), `text` (English copy), `read` |
| `audit_log` | `staff_name`, `action`, `target_type`, `target_id`, `details`, `ip`, `created_at` |
| `settings` | Key → JSON value (Settings → General, business hours, SLA options, welcome message, content version) |
| `processed_updates` | Telegram update ids already handled (duplicate protection) |
| `error_log` | Errors shown on the System page |
| `bot_state` | Bot heartbeat: `username`, `mode`, `last_seen` |

## Indexes
Every list and filter of the dashboard has an index: tickets by user, status, assignee + status, department + status,
bot + status, creation time; messages by ticket, user, delivery state; events by user and kind + time; deliveries by
campaign + status + next attempt; audit log and errors by time; FAQ nodes by parent + position; users by last activity
and username. Lists are paginated on the server.

## Integrity rules worth knowing
* A ticket, a message and a delivery are each created once per Telegram update (unique dedupe keys) — retries are safe.
* A department, category or status that tickets use cannot be deleted — it is switched off instead, so old tickets
  keep their history and reports stay correct.
* Staff accounts with history are blocked and renamed instead of deleted.
* Anonymization keeps the rows (statistics) and clears personal fields and files.
