"""Environment-backed application settings."""

from functools import lru_cache
from urllib.parse import quote_plus

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppConfig(BaseSettings):
    """Runtime configuration loaded from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    app_env: str = "development"
    public_url: str = "http://localhost:8000"
    api_base_url: str = "http://localhost:8000/api"
    cors_origins: str = ""
    secret_key: SecretStr = Field(default=SecretStr(""))
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = Field(default=60, ge=1, le=1440)
    admin_username: str = "admin"
    admin_password: SecretStr = Field(default=SecretStr(""))

    postgres_host: str = "localhost"
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    postgres_user: str = "threads_user"
    postgres_password: SecretStr = Field(default=SecretStr(""))
    postgres_db: str = "threads_monitor"

    meta_app_id: str = ""
    meta_app_secret: SecretStr = Field(default=SecretStr(""))
    threads_app_secret: SecretStr = Field(default=SecretStr(""))
    threads_access_token: SecretStr = Field(default=SecretStr(""))
    threads_user_id: str = ""
    threads_verify_token: SecretStr = Field(default=SecretStr(""))
    threads_api_base_url: str = "https://graph.threads.net"
    threads_poll_interval_minutes: int = Field(default=10, ge=1, le=1440)
    threads_daily_call_limit: int = Field(default=2200, ge=1)
    threads_token_refresh_interval_days: int = Field(default=7, ge=1, le=60)

    whatsapp_bridge_url: str = "http://whatsapp-bridge:8080"
    whatsapp_recipients: str = ""
    smtp_host: str = ""
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_user: str = ""
    smtp_pass: SecretStr = Field(default=SecretStr(""))
    smtp_from: str = ""
    smtp_use_tls: bool = True
    email_recipients: str = ""

    @property
    def database_url(self) -> str:
        """Build the async PostgreSQL connection string."""
        user = quote_plus(self.postgres_user)
        escaped_password = quote_plus(self.postgres_password.get_secret_value())
        return (
            f"postgresql+asyncpg://{user}:{escaped_password}@"
            f"{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_config() -> AppConfig:
    """Return the process-wide configuration object."""
    return AppConfig()


config = get_config()
