# Configuration
Two levels:

1. **Environment (`.env`)** — infrastructure and secrets, read at start. Template: [`.env.example`](../.env.example)
   (no real secrets). Change → `docker compose up -d` (restarts what changed).
2. **Dashboard settings** — everything about the service itself, stored in the database, applied to the bot within
   seconds without a restart and recorded in the audit log.

## 1. Environment variables
| Variable | Required | Default | Meaning |
|---|---|---|---|
| `POSTGRES_PASSWORD` | yes | — | Password of the `support` database user (used by Postgres and the backup service). |
| `DATABASE_URL` | yes | — | `postgresql+psycopg://support:<password>@postgres:5432/support` |
| `REDIS_URL` | | `redis://redis:6379/0` | Queue, rate limits, the bot's dialogue state. |
| `JWT_SECRET` | yes | — | ≥ 32 random characters; signs staff sessions and Telegram link codes. Changing it signs everyone out. |
| `BOT_TOKEN` | yes | — | Token from @BotFather. |
| `BOT_SERVICE_KEY` | yes | — | ≥ 32 random characters; the bot's key for the internal API. |
| `BOT_ID` | | `main` | Name of this bot in the data (users, tickets, campaigns). Keep `main` unless you run several bots — see [GUIDES.md](GUIDES.md#several-bots-and-other-channels). |
| `WEBHOOK_BASE_URL` | | empty | Empty: long polling. Set to the public HTTPS address (e.g. `https://support.example.com`) to receive updates by webhook at `/telegram/webhook`. |
| `WEBHOOK_SECRET` | with webhook | — | ≥ 16 characters; Telegram sends it with every update, other calls are refused. |
| `ADMIN_ORIGIN` | yes | `http://localhost:8080` | Public address of the dashboard exactly as in the browser, no trailing `/`. Used for the CSRF check and links in Telegram notifications. |
| `TRUSTED_PROXIES` | | private networks | Networks whose `X-Real-IP` the API believes (the dashboard's nginx in Docker). Change only for an unusual network layout. |
| `COOKIE_SECURE` | | `true` | Keep `true` in production (HTTPS). `false` only for a local run over plain HTTP. |
| `API_INTERNAL_URL` | | `http://api:8000` | How the bot reaches the API inside Docker. |
| `API_PORT`, `DASHBOARD_PORT`, `WEBHOOK_PORT` | | 18100, 18180, 18181 | Ports published on `127.0.0.1` for Plesk (the media platform on the same server uses 18000/18080). |
| `STORAGE_DIR`, `BACKUP_DIR` | | `/data/files`, `/data/backups` | Paths inside the containers (Docker volumes). |
| `BACKUP_KEEP` | | 14 | How many backup archives to keep. |
| `BACKUP_HOUR` | | 3 | Hour (server time) of the nightly backup. |
| `FIRST_ADMIN_USERNAME`, `FIRST_ADMIN_PASSWORD` | first start | `admin` | Creates the first Super Administrator on an empty database. Remove the password from `.env` after the first sign-in. |
| `DEFAULT_LANGUAGE` | | `en` | Default language on first start (later: Settings → Languages). |
| `SESSION_HOURS` | | 12 | Lifetime of a dashboard session. |
| `WORKER_CONCURRENCY` | | 2 | Parallel worker processes (deliveries, announcements). |
| `SENTRY_DSN` | | empty | Optional error tracking (Sentry or GlitchTip). |
| `ENVIRONMENT` | | `production` | `development`, `testing` or `production`; shown on the System page and in error reports. In `production` the interactive API docs are switched off. |

The application refuses to start while any `CHANGE_ME` placeholder is left or when a webhook address is set
without a secret.

### Environments
| Environment | How |
|---|---|
| Development | `python -m infrastructure.scripts.dev` — SQLite, in-process queue, the bot in polling mode, dashboard by `npm run dev`. No Docker needed. Secrets in `local/.env` (git-ignored). |
| Testing | `pytest` — in-memory SQLite by default; `TEST_DATABASE_URL=postgresql+psycopg://…` runs the same tests on PostgreSQL. A staging server is a normal installation with `ENVIRONMENT=testing` and its own test bot token. |
| Production | Docker Compose on the server ([DEPLOYMENT.md](DEPLOYMENT.md)), `ENVIRONMENT=production`. |

Never share a bot token or a database between environments.

## 2. Dashboard settings
### Settings → General
| Setting | Default | Meaning |
|---|---|---|
| Company name | Support | Shown to users in the welcome text and direct messages. |
| Ticket number prefix | `T-` | Ticket numbers look like `T-000123`. |
| Ask for the department | on | «Contact support» asks for a department when more than one is enabled. |
| Ask for the category | on | …and for a category when the department has categories. |
| Reopen period | 7 days | A user can reopen their closed ticket for this long. |
| Auto-close resolved tickets | 72 hours | Resolved tickets close by themselves (0 = never). |
| Ask for a rating | on | 1–5 stars when a ticket is resolved or closed by the user. |
| FAQ search | on | Free text without an open ticket searches the FAQ. |
| Ask the language first | off | Language choice on the very first `/start`. |
| Messages per minute | 20 | Flood limit per user. |
| Largest attachment | 19 MB | Telegram lets bots download files up to 20 MB. |
| FAQ limits | depth 6, 40 per level, 1000 in total | Keep the tree fast and usable. |
| Announcement speed | 20 / second | Default for new announcements (Telegram allows about 30). |
| Data notice, privacy policy link | empty | Shown once at the first `/start`, per language. |
| Remind the user | 24 hours | A ticket waiting for the user's answer this long gets one reminder in the user's language (0 = never). A new status or the user's reply starts a new wait. |
| Warn the agent | 80 % | When this share of an SLA target's time has passed, the agent (or the department) is warned once (0 = never). |
| Weekly summary | on | Mondays 09:00 (business-hours time zone): last week's numbers to everyone with `analytics.view`. |
| Data retention | events 365 days; users and attachments: keep; error log and staff notifications 90 days | See [SECURITY.md](SECURITY.md#personal-data-retention-deletion). |

### Business hours & SLA
Working days and hours, time zone (default `Asia/Jerusalem`, any IANA zone can be chosen), the «we're away» auto-reply,
SLA targets per priority, whether SLA counts business hours only and pauses while waiting for the user. Saving
recalculates the deadlines of open tickets. Rules: [ANALYTICS.md](ANALYTICS.md#how-sla-deadlines-are-computed).

### Settings → Languages, departments and categories, statuses, routing
* **Languages** — enable/disable, default, add any IETF code (e.g. `pt-br`), right-to-left flag.
* **Departments and categories** — names per language, default priority per category, order, on/off.
* **Statuses and transitions** — names per language, colour, meaning (stage), whether the user is told; the matrix
  of permitted transitions. Built-in statuses can be renamed and switched off, not deleted.
* **Routing rules** — first matching rule picks department, priority and agent (fixed, least loaded, round robin).

### Bot content
Welcome message (text, photos, albums, video, audio, voice, documents, buttons — per language), main menu (add,
rename, reorder, enable/disable, including «My contacts», which is off by default), every system text of the bot per
language, media library, response templates. See [GUIDES.md](GUIDES.md).

### Team, Profile
Accounts, roles and departments ([ROLES.md](ROLES.md)); each person's password, two-factor sign-in and Telegram
notifications (with answering from Telegram).
