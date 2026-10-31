#!/bin/sh
# Backup service (runs in the postgres:16-alpine image):
# - every night at BACKUP_HOUR (server time) and whenever the dashboard asks (System → Back up now: /backups/backup.request);
# - one archive per backup: the database (pg_dump custom format) + the files (media library, attachment copies);
# - keeps the last BACKUP_KEEP archives;
# - after each backup, restores the database into a scratch database and counts its tables and tickets: the result
#   is /backups/restore-check.json, shown on the System page. A backup that cannot be restored is reported as failed.
set -u
mkdir -p /backups
chown 10001:10001 /backups && chmod 750 /backups  # the API (uid 10001) lists backups and writes requests

backup() {
  stamp=$(date +%Y%m%d-%H%M%S)
  work=/backups/.work-$stamp
  mkdir -p "$work"
  if ! pg_dump -Fc -f "$work/database.dump"; then
    echo "{\"status\": \"failed\", \"checked_at\": \"$(date -Iseconds)\", \"error\": \"pg_dump failed\"}" > /backups/restore-check.json
    rm -rf "$work"; return 1
  fi
  tar -czf "$work/files.tar.gz" -C /files . 2>/dev/null || tar -czf "$work/files.tar.gz" -T /dev/null
  archive="/backups/support-$stamp.tar"
  tar -cf "$archive.part" -C "$work" database.dump files.tar.gz && mv "$archive.part" "$archive"
  chmod 640 "$archive" && chown 10001:10001 "$archive"
  check "$work/database.dump" "$(basename "$archive")"
  rm -rf "$work"
  ls -1t /backups/support-*.tar 2>/dev/null | tail -n +$((BACKUP_KEEP + 1)) | xargs -r rm -f
  echo "backup $archive done"
}

check() {
  dropdb --if-exists restore_check >/dev/null 2>&1
  if createdb restore_check && pg_restore --no-owner -d restore_check "$1" >/dev/null 2>&1; then
    tables=$(psql -d restore_check -tAc "select count(*) from information_schema.tables where table_schema='public'")
    tickets=$(psql -d restore_check -tAc "select count(*) from tickets" 2>/dev/null || echo 0)
    echo "{\"status\": \"ok\", \"checked_at\": \"$(date -Iseconds)\", \"backup\": \"$2\", \"tables\": $tables, \"tickets\": $tickets}" > /backups/restore-check.json
  else
    echo "{\"status\": \"failed\", \"checked_at\": \"$(date -Iseconds)\", \"backup\": \"$2\", \"error\": \"restore failed\"}" > /backups/restore-check.json
  fi
  dropdb --if-exists restore_check >/dev/null 2>&1
  chown 10001:10001 /backups/restore-check.json
}

ready() {  # the schema exists: on a fresh install the first nightly backup waits for the migrations
  [ "$(psql -tAc "select count(*) from information_schema.tables where table_schema='public' and table_name='alembic_version'" 2>/dev/null)" = "1" ]
}

last_day=""
while true; do
  today=$(date +%Y-%m-%d)
  hour=$(date +%H | sed 's/^0//')
  if [ -f /backups/backup.request ]; then
    rm -f /backups/backup.request
    backup
  elif [ "$today" != "$last_day" ] && [ "${hour:-0}" -ge "$BACKUP_HOUR" ] && ready; then
    backup && last_day=$today
  fi
  sleep 60
done
