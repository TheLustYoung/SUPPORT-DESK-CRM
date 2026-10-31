import os
import secrets
import tempfile

# Test-only configuration; never point tests at a production database.
os.environ["DATABASE_URL"] = "sqlite://"
os.environ.setdefault("JWT_SECRET", secrets.token_urlsafe(48))
os.environ.setdefault("BOT_SERVICE_KEY", secrets.token_urlsafe(32))
os.environ.setdefault("BOT_TOKEN", "123456:test-token-for-tests-only-xxxxxxxxxxxxx")
os.environ["ADMIN_ORIGIN"] = "http://testserver"
os.environ["COOKIE_SECURE"] = "false"
os.environ["STORAGE_DIR"] = tempfile.mkdtemp(prefix="support-files-")
os.environ["BACKUP_DIR"] = tempfile.mkdtemp(prefix="support-backups-")
os.environ["WEBHOOK_BASE_URL"] = ""
os.environ["ENVIRONMENT"] = "production"  # tests check production behaviour, whatever a local .env says
