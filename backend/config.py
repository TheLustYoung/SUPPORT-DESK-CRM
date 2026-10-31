from functools import lru_cache
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)
    database_url: str
    redis_url: str = "redis://redis:6379/0"
    jwt_secret: SecretStr = Field(min_length=32)
    admin_origin: str = "http://localhost:8080"
    cookie_secure: bool = True
    api_internal_url: str = "http://api:8000"
    session_hours: int = 12
    # Proxies whose X-Real-IP is believed (the dashboard's nginx inside Docker). Comma-separated networks.
    trusted_proxies: str = "127.0.0.0/8,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,fc00::/7"
    # Telegram
    bot_token: SecretStr = SecretStr("")
    bot_id: str = Field(default="main", pattern=r"^[a-z0-9_-]{1,32}$")  # stamped on users, tickets and campaigns
    bot_service_key: SecretStr = Field(min_length=32)  # the bot process authenticates to /internal with it
    # Empty: long polling. Set to the public HTTPS address that Plesk forwards to the bot's webhook port to receive
    # updates by webhook, e.g. https://support.example.com — Telegram signs every call with WEBHOOK_SECRET.
    webhook_base_url: str = ""
    webhook_secret: SecretStr = SecretStr("")
    webhook_port: int = 8081
    # Files: dashboard uploads (welcome, FAQ, campaigns, replies) and copies of users' attachments
    storage_dir: str = "/data/files"
    backup_dir: str = "/data/backups"
    telegram_receive_bytes: int = 19_000_000  # the Bot API lets bots download files up to 20 MB
    telegram_send_bytes: int = 49_000_000  # …and send files up to 50 MB
    max_upload_bytes: int = 20_000_000  # dashboard uploads
    first_admin_username: str = "admin"
    first_admin_password: SecretStr = SecretStr("")  # creates the first Super Administrator on an empty database
    sentry_dsn: SecretStr = SecretStr("")  # optional error tracking (Sentry or GlitchTip), see backend/observability.py
    environment: str = "production"  # development, testing, production: shown in the dashboard and error reports
    default_language: str = "en"

    @property
    def trusted_networks(self) -> list:
        import ipaddress
        return [ipaddress.ip_network(item.strip(), strict=False) for item in self.trusted_proxies.split(",") if item.strip()]

    @model_validator(mode="after")
    def reject_example_secrets(self):
        values = [self.jwt_secret, self.bot_service_key, self.webhook_secret]
        if any("CHANGE_ME" in value.get_secret_value() for value in values) or "CHANGE_ME" in self.database_url:
            raise ValueError("Replace all example secrets before starting the application")
        if self.webhook_base_url and len(self.webhook_secret.get_secret_value()) < 16:
            raise ValueError("Set WEBHOOK_SECRET (at least 16 characters) to receive updates by webhook")
        return self


@lru_cache
def settings() -> Settings:
    return Settings()
