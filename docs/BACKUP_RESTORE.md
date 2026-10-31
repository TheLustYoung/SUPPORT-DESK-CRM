# Backup and restoration
Every command below was run on a Docker
installation of this version: the database and all files were deleted and fully restored from an archive.

## What is backed up
The `backup` service (`infrastructure/backup/backup.sh`, image `postgres:16-alpine`) writes one archive per backup to
the `backups` Docker volume:

```
support-YYYYMMDD-HHMMSS.tar
├── database.dump    PostgreSQL custom-format dump (pg_dump -Fc): every table — users, tickets, messages, FAQ,
│                    settings, staff, audit log…
└── files.tar.gz     the files volume: media library and stored copies of users' attachments
```

`.env` (secrets) is **not** in the archive — keep a copy of it in your password manager.

## When
* **Nightly** at `BACKUP_HOUR` (server time, default 3), once per day.
* **On request:** System → *Back up now* (needs `system.manage`, audited).
* The last `BACKUP_KEEP` archives are kept (default 14), older ones are deleted.

## Automatic restore check
After every backup the service restores the dump into a scratch database `restore_check`, counts its tables and
tickets, and drops it. The result is shown on the **System** page (*Last restore check*) and stored in
`restore-check.json`. A backup that cannot be restored is shown as **failed** — act on it at once.

## Keep a copy off the server
The archives are on the same disk as the database. Copy them elsewhere regularly, e.g. download from System →
Backups, or sync the volume from the host:

```bash
docker run --rm -v telegram-support_backups:/backups:ro -v /srv/offsite:/out postgres:16-alpine \
  sh -c 'cp -n /backups/support-*.tar /out/'
```

Archives contain personal data: store the copies encrypted and with restricted access.

## Restore (full disaster recovery)
Run on the server in the project directory (`/opt/telegram-support`). Replace the archive name with the one you
restore (`docker compose exec backup ls /backups`).

```bash
ARCHIVE=support-20261025-123737.tar

# 1. Stop everything that writes (database, Redis and the backup service keep running)
docker compose stop api worker beat bot

# 2. Database: recreate it and load the dump
docker compose exec -T backup sh -c "set -e; rm -rf /tmp/r; mkdir /tmp/r; tar -xf /backups/$ARCHIVE -C /tmp/r; \
  dropdb --if-exists --force support; createdb support; pg_restore --no-owner -d support /tmp/r/database.dump; rm -rf /tmp/r"

# 3. Files: replace the files volume with the archive's copy
docker run --rm --entrypoint sh -v telegram-support_backups:/backups:ro -v telegram-support_files-data:/files \
  postgres:16-alpine -c "set -e; find /files -mindepth 1 -delete; cd /tmp; tar -xf /backups/$ARCHIVE files.tar.gz; \
  tar -xzf files.tar.gz -C /files; chown -R 10001:10001 /files"

# 4. Start (migrations run first and bring an older backup up to the current schema)
docker compose up -d
docker compose ps
```

Check: sign in to the dashboard, open a few tickets with attachments, the media library and System (bot online,
queue empty). Staff sessions stay valid (same `JWT_SECRET`); messages that were waiting in the queue at backup
time are re-sent by the recovery job within a minute.

### On a new server
Install as in [DEPLOYMENT.md](DEPLOYMENT.md) with the **same `.env`**, start once (`docker compose up -d`), copy the
archive into the backups volume, then follow the steps above:

```bash
docker run --rm -v telegram-support_backups:/backups -v "$PWD":/in postgres:16-alpine cp /in/$ARCHIVE /backups/
```

### Restore only the database or one table
Restore the dump into a separate database and copy what you need — the live database stays untouched:

```bash
docker compose exec -T backup sh -c "set -e; rm -rf /tmp/r; mkdir /tmp/r; tar -xf /backups/$ARCHIVE -C /tmp/r; \
  dropdb --if-exists inspect; createdb inspect; pg_restore --no-owner -d inspect /tmp/r/database.dump"
docker compose exec backup psql -d inspect      # look, export what is needed, then: dropdb inspect
```

## Restore drill (quarterly)
1. Download the latest archive (System → Backups).
2. On a test machine or a staging server: install, copy the archive, restore as above.
3. Sign in, compare ticket counts with production (Analytics → Tickets, same period), open attachments.
4. Note the date and result. The automatic restore check covers the database every night; the drill also proves
   the files, the procedure and the people.
