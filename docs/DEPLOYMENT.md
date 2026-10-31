# Deployment guide
How to install, update and operate the platform on **any Linux server with Docker** — a VPS or a
dedicated server, with or without a control panel. Everything runs in Docker; the only thing that depends on the
server is the web server in front (Plesk, nginx, Caddy or Apache), and ready configurations exist for all of them.
The same procedure was used to run the full stack in Docker for acceptance (PostgreSQL 16, Redis 7, all services
healthy, backup and restore verified).

## What runs
| Service | Image | Role |
|---|---|---|
| `postgres` | postgres:16-alpine | Database (volume `postgres-data`) |
| `redis` | redis:7-alpine | Queue, rate limits, bot dialogue state (append-only, no eviction) |
| `migrate` | app | Runs `alembic upgrade head` and the first-start seed, then exits; everything else waits for it |
| `api` | app | FastAPI on `127.0.0.1:18100` (dashboard API, bot API, health) |
| `worker` | app | Celery: replies, notifications, announcements, attachment copies |
| `beat` | app | Celery scheduler — exactly one: SLA checks and early warnings, reminders to users, auto-close, the Monday summary, scheduled announcements, recovery of lost tasks, retention |
| `bot` | app | The Telegram bot: long polling, or webhook on `127.0.0.1:18181` |
| `dashboard` | nginx | The web dashboard on `127.0.0.1:18180`, proxies `/api` to the API |
| `backup` | postgres:16-alpine | Nightly and on-request backups with an automatic restore check |

Networks: `private` (internal, no internet: database, Redis, backups), `internet` (bot and worker reach Telegram),
`edge` (only for publishing api/dashboard ports on localhost). The Compose project name is `telegram-support`, so it
lives next to the media platform on the same server (its own volumes, ports 18100/18180/18181 vs 18000/18080).

## Requirements
* Linux, 64-bit (Ubuntu 22.04/24.04, Debian 12, or any distribution Docker supports), 2 vCPU, 2 GB RAM, 20 GB disk
  (more for many attachments). Any hosting provider.
* Docker Engine 24+ with the Compose plugin (`curl -fsSL https://get.docker.com | sh`); `openssl`, `curl`, `git`.
* A domain or subdomain for the dashboard (e.g. `support.example.com`) whose DNS A record points to the server.
* A web server for HTTPS in front: the one the server already has (Plesk, nginx, Apache), or Caddy if there is none.
* Outgoing internet access to `api.telegram.org` (port 443). For a webhook also incoming HTTPS (443).
* A bot token from @BotFather. One token = one running bot process (a second process with the same token —
  e.g. a forgotten test run — steals the updates).

## Install (one command)
```bash
cd /opt
git clone <repository> telegram-support
cd telegram-support
sudo bash infrastructure/scripts/server-setup.sh support.example.com
```

The script:

1. checks Docker and that the ports are free (other ports: `… support.example.com 28100 28180 28181`);
2. creates `.env` from `.env.example`, generates every secret, sets `ADMIN_ORIGIN=https://<domain>`, asks for the
   bot token (hidden input), keeps a copy of `.env` in `/var/lib/telegram-support` (mode 600);
3. builds the images;
4. backs up the existing database (on updates) to `/var/lib/telegram-support/backups`, runs migrations;
5. asks for the first Super Administrator's username and password (if there is no account yet);
6. starts everything and waits for `/health/ready`;
7. detects the web server of the machine and prints (or writes to `/var/lib/telegram-support/`) its configuration,
   filled in with your domain and ports.

### The web server in front (HTTPS)
Docker publishes the ports on `127.0.0.1` only, so the platform is reachable from the internet through the server's
web server. Whatever it is, it must: serve `https://<domain>` with a certificate, proxy `/` to the dashboard port
(18180) and `/telegram/webhook` to the webhook port (18181), refuse `/internal/` and `/health/`, allow 25 MB uploads,
keep `Host` and `Origin`, and **set `X-Forwarded-For` to the visitor's address** (login limits and the audit log
rely on it).

| The server has | Configuration | HTTPS |
|---|---|---|
| **Plesk** | Websites & Domains → domain → Apache & nginx Settings: untick *Proxy mode*, paste `infrastructure/plesk/support.nginx.conf` into *Additional nginx directives* | Plesk → SSL/TLS → Let's Encrypt, redirect HTTP → HTTPS |
| **nginx** | `infrastructure/proxy/nginx.conf` → `/etc/nginx/sites-available/`, enable, `nginx -t && systemctl reload nginx` | `certbot --nginx -d <domain>` |
| **Apache** | `a2enmod proxy proxy_http headers ssl rewrite`; `infrastructure/proxy/apache.conf` → `sites-available/`, `a2ensite` | `certbot --apache -d <domain>` |
| **Caddy** or nothing yet | install Caddy (`apt install caddy`), `infrastructure/proxy/Caddyfile` → `/etc/caddy/Caddyfile`, `systemctl reload caddy` | automatic |
| **Another panel** (cPanel, ISPmanager, aaPanel, …) | a "reverse proxy" site for the domain to `http://127.0.0.1:18180` with the rules above; the nginx file is the reference | the panel's Let's Encrypt |

Open `https://support.example.com`, sign in, then in Telegram send `/start` to the bot.

**Without a domain for now** (a test on a bare IP): `ssh -L 18180:127.0.0.1:18180 root@<server>` and open
`http://localhost:18180` on your computer — set `ADMIN_ORIGIN=http://localhost:18180` and `COOKIE_SECURE=false`
in `.env` for that test only. The bot works without a domain (long polling).

### First steps in the dashboard
1. Profile → enable **two-factor sign-in**; link **Telegram notifications** (`/link CODE` to the bot).
2. Settings → General: company name, data notice, retention. Business hours & SLA: hours, time zone, targets.
3. Settings → Languages, Departments and categories, Routing. Team: accounts and roles.
4. Bot content: welcome message, menu, texts; FAQ: build and **Publish**.
5. Remove `FIRST_ADMIN_PASSWORD` from `.env` if you set it there.

## Webhook instead of polling (optional)
Polling needs no open port and is fine for most loads. For a webhook:

```bash
sed -i 's|^WEBHOOK_BASE_URL=.*|WEBHOOK_BASE_URL=https://support.example.com|' .env
docker compose up -d bot
docker compose logs --tail=20 bot     # expect "webhook_ready"
```

The bot registers `https://<domain>/telegram/webhook` with Telegram together with `WEBHOOK_SECRET`; the web server forwards
that path to the bot; calls without the secret are refused (401). Back to polling: empty `WEBHOOK_BASE_URL`, restart
the bot — it removes the webhook itself.

## Update
```bash
cd /opt/telegram-support
git pull
sudo bash infrastructure/scripts/server-setup.sh support.example.com
```

Safe to repeat: keeps `.env`, data and accounts, backs up the database before migrating. Or by hand:
`docker compose build && docker compose up -d` (the `migrate` service runs before the others).

Roll back: `git checkout <previous tag>`, restore the pre-update dump if the schema changed
(`gunzip -c /var/lib/telegram-support/backups/db-….sql.gz | docker compose exec -T postgres psql -U support -d support`),
`docker compose build && docker compose up -d`.

## Operate
| Task | Command |
|---|---|
| Status | `docker compose ps` (all `healthy`/`Up`; `migrate` *Exited (0)* is normal) |
| Logs | `docker compose logs --tail=100 api` (JSON lines; also `worker`, `bot`, `beat`, `backup`) |
| Restart one service | `docker compose restart bot` |
| Shell in the database | `docker compose exec postgres psql -U support -d support` |
| New admin password | `docker compose run --rm api python -m backend.cli create-admin` |
| Backups | System page, or `docker compose exec backup ls -l /backups` — see [BACKUP_RESTORE.md](BACKUP_RESTORE.md) |
| Disk usage | `docker system df -v` |

Monitoring: the System page (bot, database, Redis, scheduler and workers, queue, errors, backups); `docker compose
ps` shows the health of every service; `/health/ready` for an external monitor via
`curl http://127.0.0.1:18100/health/ready` on the server; optional `SENTRY_DSN`. Details: [SECURITY.md](SECURITY.md#monitoring-and-errors).

Scaling: more `WORKER_CONCURRENCY` for big announcements; the API can run several replicas behind nginx;
`beat` and `bot` must stay single.

## Troubleshooting
| Symptom | Cause and fix |
|---|---|
| Sign-in fails with *Open the dashboard from its own address* | `ADMIN_ORIGIN` differs from the browser address (scheme, `www`, trailing `/`). Fix `.env`, `docker compose up -d api`. |
| 413 when uploading media | The web server limits uploads: allow 25 MB (`client_max_body_size 25m` in nginx/Plesk, `LimitRequestBody` in Apache, `max_size` in Caddy). |
| «duplicate location "/"» in Plesk | Proxy mode is still on, or an old `location /` is in the directives. |
| System page: *Scheduler and workers — stopped* | `docker compose ps worker beat`, `docker compose logs --tail=50 beat worker`, then `docker compose up -d worker beat`. |
| All audit entries show the same internal address | The web server does not send `X-Forwarded-For`: add it as in the templates. |
| The bot does not answer | System page → Bot: offline? `docker compose logs bot`. Another process uses the same token? Webhook set but the domain unreachable? |
| Replies stay *queued* | `docker compose ps worker`, `docker compose logs worker`; System → *Restart waiting*. |
| The API is unhealthy | `docker compose logs --tail=80 api migrate` — usually a migration error or a wrong `DATABASE_URL`. |
| After reinstalling, the database password is wrong | The old `.env` is in `/var/lib/telegram-support/.env` — copy it back. |

## Local development
```bash
python -m infrastructure.scripts.dev        # API + bot + worker in one process, SQLite, http://127.0.0.1:8100
cd dashboard && npm install && npm run dev  # http://localhost:5173
python -m pytest                            # tests (TEST_DATABASE_URL=… for PostgreSQL)
```

Test bot token and local secrets go to `local/.env` (git-ignored).
