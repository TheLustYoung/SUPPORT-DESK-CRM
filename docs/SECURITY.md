# Security and privacy
What protects the platform, where it is configured and what the operator must do.

## Architecture boundaries
```
Telegram ──HTTPS──► web server of the host ──► bot (webhook, secret-checked) ──X-Service-Key──► API /internal/*
Browser  ──HTTPS──► web server of the host ──► dashboard nginx (static) ──► API /api/* (session cookie, Origin check)
                                                worker / beat ──► PostgreSQL, Redis (private Docker network, no internet)
```

The web server of the host is whatever the server has: Plesk, nginx, Caddy or Apache — ready configurations in
`infrastructure/plesk/` and `infrastructure/proxy/`; `server-setup.sh` detects it and prints the right one.

* The bot holds no business logic and never touches the database; it calls `/internal/*` with a service key.
  The web server returns 404 for `/internal/` and `/health/` from the internet, so the internal API is reachable
  only inside Docker and from the server itself.
* PostgreSQL, Redis and the backup service are on an internal Docker network without internet access. Only the
  bot and the worker can reach Telegram. Containers run as an unprivileged user, read-only, with all Linux
  capabilities dropped and `no-new-privileges`.
* API, dashboard and webhook ports are published on `127.0.0.1` only; the public side is the web server with its
  TLS certificate (Let's Encrypt). All traffic from users and staff is encrypted.
* **The visitor's address.** The web server puts the client's address into `X-Forwarded-For` (replacing anything
  the client sent); the dashboard's nginx believes it only from local and private addresses and passes it to the
  API as `X-Real-IP`; the API believes that only from `TRUSTED_PROXIES` (private networks by default). Login limits
  and the audit log therefore see each visitor's real address, and nobody can forge one.
* **No public map of the API.** With `ENVIRONMENT=production` the interactive docs (`/api/docs`) and the OpenAPI
  schema (`/api/openapi.json`) are switched off; the reference is in the repository ([API.md](API.md)).

## Staff authentication
| Measure | Detail |
|---|---|
| Passwords | argon2id hashes. At least 12 characters, not only letters or only digits, at least 5 different characters. |
| Two-factor sign-in | TOTP (Google Authenticator, 1Password, …) — Profile → Two-factor sign-in. Recommended for every account, required in practice for Super Administrators. |
| Brute force | After 5 wrong passwords the account is locked for 15 minutes; at most 10 sign-in attempts per visitor address in 5 minutes (the real address, see above — one attacker cannot lock colleagues out). Unknown usernames take the same time as known ones (no username probing). |
| Session | Signed token (HS256, `JWT_SECRET`) in an `HttpOnly`, `Secure`, `SameSite=Strict` cookie limited to `/api`; expires after `SESSION_HOURS` (12 by default). A new password, role or block ends all sessions of that person. |
| CSRF | Every changing request must come from the dashboard's own address (`Origin` = `ADMIN_ORIGIN`), on top of `SameSite=Strict`. |
| Authorization | Every request is checked against the role's permissions on the server (see [ROLES.md](ROLES.md)); a test fails if a route has no rule. |

## Web protection
* **XSS.** The dashboard is React (escaped output) with a strict Content-Security-Policy (`script-src 'self'`,
  `frame-ancestors 'none'`), `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`. Bot notifications
  that contain HTML are shown as plain text. Rich content for Telegram accepts only Telegram's HTML tags.
* **Injection.** All database access goes through SQLAlchemy with bound parameters; no SQL is built from input.
  Input is validated by Pydantic models (types, lengths, patterns).
* **Formulas in exports.** Report exports contain texts written by users (search queries, names). A cell starting
  with `=`, `+`, `-`, `@`, a tab or a line break is written with a leading apostrophe, so Excel and LibreOffice show
  it as text and never run it (tested on CSV and XLSX).
* **Uploads.** Files are recognised by their content (magic bytes), not by the name; executables and unknown
  binaries are refused; 20 MB limit. Files are stored outside the web root and served only to signed-in staff
  with the right permission — there are no public file URLs.
* **Abuse.** Users are limited to `messages_per_minute` (Settings); announcements respect Telegram's rate limits
  and pause on flood control; banned users are answered with a fixed text and nothing is stored.

## Telegram
* **Webhook validation.** With `WEBHOOK_BASE_URL` set, Telegram signs every update with `WEBHOOK_SECRET`
  (`X-Telegram-Bot-Api-Secret-Token`); calls without the exact secret get 401 and are never parsed (tested in
  `tests/integration/test_bot_scenarios.py`). Without a webhook the bot uses long polling and opens no port.
* **Duplicate updates.** Every update id is stored once; a retried update is ignored, so a message is never
  stored or answered twice, and an album opens one ticket.
* **Staff in Telegram.** A staff member links Telegram with a one-time code from their profile (HMAC, valid
  10–20 minutes). Answers from Telegram go through the same permission and visibility checks as the dashboard.
  Linking is audited.
* **Contacts.** Telegram never gives a bot a phone number or e-mail. The bot stores them only when the user
  shares them (`request_contact` button — only the user's own number is accepted — or typing an e-mail) and lets
  the user delete them. Staff need `users.sensitive` to see or write them down.

## Secrets
* All secrets live in `.env` on the server (`chmod 600`, owned by root), never in the repository: `.env` is in
  `.gitignore`, `.env.example` contains only `CHANGE_ME` placeholders, and the application refuses to start while
  any placeholder is left.
* The bot token is never logged: HTTP client logs are silenced (Telegram puts the token in the URL), and error
  reports drop request URLs.
* Rotate a secret by changing it in `.env` and restarting (`docker compose up -d`). A new `JWT_SECRET` signs
  everyone out; a new `BOT_SERVICE_KEY` needs a restart of api and bot together; a new bot token comes from
  @BotFather (`/revoke`).

## Personal data, retention, deletion
| Data | Rule |
|---|---|
| What is stored | Telegram id, username, names, Telegram language, chosen language, activity, tickets and messages, attachments copies, tags and notes; contacts only when the user supplied them. |
| Privacy notice | Settings → General → Data notice: shown once at the first `/start`, with a link to the privacy policy. |
| Export | Users → profile → Export: every record about the user as JSON (`users.export`, audited). |
| Anonymization | Users → profile → Anonymize: names, contacts, message texts and files (including stored copies) are erased; statistics remain (`users.delete`, audited). |
| Retention | Settings → General → Data retention: delete analytics events after N days; anonymize users inactive for N days; delete stored attachment copies of tickets closed N days ago. Applied every 6 hours. |
| Backups | Contain personal data: kept `BACKUP_KEEP` days on the server, downloadable only with `system.manage` (audited). See [BACKUP_RESTORE.md](BACKUP_RESTORE.md). |
| Audit log | Kept without time limit (accountability). |
| Error log, staff notifications | Removed after `retention_logs_days` (90 by default, Settings → General). |

## Monitoring and errors
| What | How it is watched |
|---|---|
| API, database, Redis | `/health/ready` (Docker healthcheck of `api`) |
| Bot | heartbeat every 30 s — System page *online/offline*; Docker healthcheck `python -m bots.health` |
| Workers | Docker healthcheck: the worker answers a ping through Redis |
| Scheduler (SLA, reminders, summaries, scheduled announcements) | the every-minute job records its run; System page *Scheduler and workers* turns red with instructions after 3 missed minutes; Docker healthcheck `python -m workers.health` |
| Deliveries | System page: queue by state, oldest waiting message; failures in the error log |
| Backups | System page: last archive and its automatic restore check |

`docker compose ps` shows every service's health. Docker restarts a service whose process stops
(`restart: unless-stopped`); an *unhealthy* one keeps running and is shown as such — check the System page or
`docker compose ps` daily, or point an external monitor at the server. Logs are structured JSON, rotated by Docker
(3 × 10 MB per service). Optional error tracking: set `SENTRY_DSN` (Sentry or self-hosted GlitchTip). The error log
and staff notifications are kept for `retention_logs_days` (90 by default); the audit log without time limit.

## Operator checklist
1. Fill `.env` with fresh random secrets (`infrastructure/scripts/server-setup.sh` does it) and keep it `600`.
2. Serve the dashboard only over HTTPS; keep `COOKIE_SECURE=true`.
3. Enable two-factor sign-in for every Super Administrator and Supervisor.
4. Give each person the smallest role that fits; review Team and the audit log monthly.
5. Write the data notice and set the retention periods required by your privacy policy.
6. Keep backups off the server too (download them or sync the backup volume), and run a restore drill quarterly.
7. Update the server and images regularly: `git pull && docker compose build && docker compose up -d`.
