#!/usr/bin/env bash
# One-command server setup. Run from anywhere inside the project, as root:
#
#     bash infrastructure/scripts/server-setup.sh support.example.com            # ports 18100 / 18180 / 18181
#     bash infrastructure/scripts/server-setup.sh support.example.com 28100 28180 28181
#
# Any Linux server with Docker; the web server in front may be Plesk, nginx, Caddy or Apache (the script detects it
# and prints the matching configuration at the end).
# Safe to run again (updates): the existing .env, data and accounts are kept. Every run keeps a copy of .env in
# /var/lib/telegram-support and backs up the database there before migrating. The media platform may run on the same
# server: this project has its own Compose name, volumes, ports and state folder.
set -euo pipefail

DOMAIN="${1:-}"
API_PORT="${2:-18100}"
DASHBOARD_PORT="${3:-18180}"
WEBHOOK_PORT="${4:-18181}"
cd "$(dirname "$0")/../.."
PROJECT="$(pwd)"
STATE=/var/lib/telegram-support
SECRETS="POSTGRES_PASSWORD JWT_SECRET BOT_SERVICE_KEY WEBHOOK_SECRET"

step() { printf '\n\033[1;32m==> %s\033[0m\n' "$1"; }
fail() { printf '\n\033[1;31mERROR: %s\033[0m\n' "$1"; exit 1; }
set_var() {  # set_var KEY VALUE  (values here never contain "|" or "&")
  if grep -q "^$1=" .env; then sed -i "s|^$1=.*|$1=$2|" .env; else printf '%s=%s\n' "$1" "$2" >> .env; fi
}
get_var() { grep -m1 "^$1=" .env | cut -d= -f2- || true; }

[ -n "$DOMAIN" ] || fail "Usage: bash infrastructure/scripts/server-setup.sh <domain> [api_port] [dashboard_port] [webhook_port]"
[ "$(id -u)" -eq 0 ] || fail "Please run as root (or with sudo)."

step "1/7 Checking Docker"
command -v docker >/dev/null || fail "Docker is not installed (curl -fsSL https://get.docker.com | sh)."
docker compose version >/dev/null 2>&1 || fail "The Docker Compose plugin is not installed."
command -v openssl >/dev/null || fail "openssl is not installed (apt install openssl)."
echo "OK: $(docker compose version | head -n1)"

step "2/7 Checking ports $API_PORT, $DASHBOARD_PORT and $WEBHOOK_PORT"
if [ -n "$(docker compose ps -q api dashboard 2>/dev/null)" ]; then
  echo "OK: our containers already use them."
else
  for port in "$API_PORT" "$DASHBOARD_PORT" "$WEBHOOK_PORT"; do
    if ss -ltn "sport = :$port" | grep -q LISTEN; then
      fail "Port $port is used by another application (the media platform uses 18000/18080). Run again with other ports, e.g.: bash $0 $DOMAIN 28100 28180 28181"
    fi
  done
  echo "OK: the ports are free."
fi

step "3/7 Configuration file (.env)"
# A copy of .env lives outside the project folder, so an update through Plesk (Pull, a new deployment folder, a
# re-created repository) never loses the passwords that open the existing database.
mkdir -p "$STATE/backups" && chmod 700 "$STATE"
if [ ! -f .env ] && [ -f "$STATE/.env" ]; then
  cp "$STATE/.env" .env
  echo "Restored .env from $STATE (kept from the previous installation)."
fi
if [ ! -f .env ] && docker volume ls -q | grep -qx 'telegram-support_postgres-data'; then
  fail "The database of a previous installation exists, but its .env is missing. Copy the old .env into $PROJECT (or $STATE) and run this again; new passwords would lock you out of the existing data."
fi
if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example."
fi
sed -i 's/\r$//' .env  # a file edited on Windows
# A hand-made .env may be incomplete: add missing settings (comments are skipped), generate missing secrets.
while IFS= read -r line; do
  case "$line" in ''|\#*) continue ;; esac
  grep -q "^${line%%=*}=" .env || printf '%s\n' "$line" >> .env
done < .env.example
for key in $SECRETS; do
  case "$(get_var "$key")" in ''|CHANGE_ME*) set_var "$key" "$(openssl rand -hex 32)" ;; esac
done
if get_var DATABASE_URL | grep -q CHANGE_ME; then
  set_var DATABASE_URL "postgresql+psycopg://support:$(get_var POSTGRES_PASSWORD)@postgres:5432/support"
fi
set_var ADMIN_ORIGIN "https://$DOMAIN"
set_var COOKIE_SECURE true
set_var API_PORT "$API_PORT"
set_var DASHBOARD_PORT "$DASHBOARD_PORT"
set_var WEBHOOK_PORT "$WEBHOOK_PORT"
while ! get_var BOT_TOKEN | grep -Eq '^[0-9]+:[A-Za-z0-9_-]{30,}$'; do
  read -rsp "Paste the support bot token from @BotFather (hidden) and press Enter: " token </dev/tty; echo
  if printf '%s' "$token" | grep -Eq '^[0-9]+:[A-Za-z0-9_-]{30,}$'; then set_var BOT_TOKEN "$token"
  else echo "That doesn't look like a bot token (format 123456789:AA...). Please try again."; fi
done
echo "OK: the bot token is set."
chmod 600 .env
left="$(grep -E '^[A-Z_]+=.*CHANGE_ME' .env | cut -d= -f1 | tr '\n' ' ' || true)"
[ -z "$left" ] || fail ".env still contains CHANGE_ME values in: $left"
docker compose config --quiet || fail "docker-compose.yml or .env is invalid."
cp .env "$STATE/.env" && chmod 600 "$STATE/.env"
echo "OK: configuration is valid; a copy of .env is kept in $STATE."

step "4/7 Building (the first time takes several minutes)"
docker compose build

step "5/7 Database: backup of the current data, then migrations"
docker compose up -d postgres redis
for _ in $(seq 1 30); do docker compose exec -T postgres pg_isready -U support -d support >/dev/null 2>&1 && break; sleep 2; done
# Restore such a backup with:
#   gunzip -c /var/lib/telegram-support/backups/<file>.sql.gz | docker compose exec -T postgres psql -U support -d support
if docker compose exec -T postgres psql -U support -d support -tAc "select 1 from information_schema.tables where table_name='tickets'" 2>/dev/null | grep -q 1; then
  backup="$STATE/backups/db-$(date +%Y%m%d-%H%M%S).sql.gz"
  if docker compose exec -T postgres pg_dump -U support -d support --clean --if-exists | gzip > "$backup" && [ -s "$backup" ]; then
    echo "OK: database backup saved to $backup"
    ls -1t "$STATE"/backups/db-*.sql.gz | tail -n +11 | xargs -r rm -f
  else
    rm -f "$backup"; fail "Could not back up the database before the update; nothing was changed."
  fi
fi
docker compose run --rm migrate

step "6/7 Super Administrator account"
admins="$(docker compose run --rm -T api python -c 'from sqlalchemy import select, func
from backend.database import Session
from backend.models import Staff
with Session() as db: print(db.scalar(select(func.count()).select_from(Staff)))' | tail -n1)"
if [ "${admins:-0}" -gt 0 ] 2>/dev/null; then
  echo "OK: a staff account already exists."
else
  echo "Create the first dashboard login (password: at least 12 characters, letters and digits):"
  docker compose run --rm api python -m backend.cli create-admin </dev/tty
fi

step "7/7 Starting everything"
docker compose up -d
printf 'Waiting for the API'
for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:$API_PORT/health/ready" >/dev/null 2>&1; then ready=1; break; fi
  printf '.'; sleep 3
done
echo
[ "${ready:-0}" -eq 1 ] || fail "The API did not become ready. Send the output of: docker compose logs --tail=80 api migrate"
docker compose ps --format 'table {{.Service}}\t{{.Status}}'

fill() {  # a proxy template with this domain and these ports
  sed -e "s/support\.example\.com/$DOMAIN/g" -e "s/18180/$DASHBOARD_PORT/g" -e "s/18181/$WEBHOOK_PORT/g" \
      -e "s/__API_PORT__/$API_PORT/g" -e "s/__DASHBOARD_PORT__/$DASHBOARD_PORT/g" -e "s/__WEBHOOK_PORT__/$WEBHOOK_PORT/g" "$1"
}

printf '\n\033[1;32mDONE! The support platform is running.\033[0m\n\n'
echo "Last step: publish https://$DOMAIN through the web server of this machine (the ports above listen on 127.0.0.1 only)."
# The web server in front decides how; the platform itself works the same behind any of them.
if [ -d /usr/local/psa ]; then
  cat <<EOF

Plesk found. Websites & Domains -> $DOMAIN -> Apache & nginx Settings:
  1) UNTICK "Proxy mode"
  2) Paste this into "Additional nginx directives" and click OK; enable Let's Encrypt for the domain.
----------------------------------------------------------------
$(fill "$PROJECT/infrastructure/plesk/support.nginx.conf")
----------------------------------------------------------------
EOF
elif command -v caddy >/dev/null; then
  fill "$PROJECT/infrastructure/proxy/Caddyfile" > "$STATE/Caddyfile"
  echo "Caddy found: add $STATE/Caddyfile to /etc/caddy/Caddyfile, then: systemctl reload caddy (HTTPS is automatic)."
elif command -v nginx >/dev/null; then
  fill "$PROJECT/infrastructure/proxy/nginx.conf" > "$STATE/nginx-support.conf"
  echo "nginx found: cp $STATE/nginx-support.conf /etc/nginx/sites-available/support.conf"
  echo "  ln -s ../sites-available/support.conf /etc/nginx/sites-enabled/ && certbot --nginx -d $DOMAIN && systemctl reload nginx"
elif command -v apache2 >/dev/null || command -v httpd >/dev/null; then
  fill "$PROJECT/infrastructure/proxy/apache.conf" > "$STATE/apache-support.conf"
  echo "Apache found: a2enmod proxy proxy_http headers ssl rewrite; cp $STATE/apache-support.conf /etc/apache2/sites-available/support.conf"
  echo "  a2ensite support && certbot --apache -d $DOMAIN && systemctl reload apache2"
else
  fill "$PROJECT/infrastructure/proxy/Caddyfile" > "$STATE/Caddyfile"
  echo "No web server found. The simplest: install Caddy (https://caddyserver.com/docs/install), then"
  echo "  cp $STATE/Caddyfile /etc/caddy/Caddyfile && systemctl reload caddy   (HTTPS certificate is automatic)"
fi
cat <<EOF

Point the domain's DNS (A record) to this server, open https://$DOMAIN and sign in. In Telegram, send /start to the bot.
Webhook instead of polling (optional): set WEBHOOK_BASE_URL=https://$DOMAIN in .env, then: docker compose up -d bot
EOF
