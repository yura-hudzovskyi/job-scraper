"""Infrastructure settings, read from .env once at startup and never changed at
runtime — database, queue, the Voyage API key, the Telegram bot.

Everything about *how the pipeline behaves* (models, batch sizes, weights,
thresholds, retention) is deliberately NOT here: it lives in the database and is
edited from the System page, so tuning the pipeline never means a redeploy. See
app/domain/pipeline_config.py.
"""

from functools import lru_cache
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"
    secret_key: str = "change-me"
    log_level: str = "INFO"

    database_url: str = "postgresql+psycopg://job_scraper:job_scraper@localhost:5432/job_scraper"

    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # The only model credential this app has. Without it there is no embedding
    # search and no reranking, so the pipeline reports itself as not runnable
    # rather than quietly producing nothing (see /api/system/status).
    voyage_api_key: str | None = None

    # Where the self-hosted understanding models run (spec 3.5.3). Unset means
    # no neural extraction: the pipeline falls back to the structural extractor
    # and says so on each profile, rather than failing revisions. That is what
    # makes this safe to leave empty in local development.
    ml_service_url: str | None = None

    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    # Public HTTPS hostname Caddy fronts this API on (see Caddyfile,
    # docs/deployment.md) — used to register the Telegram webhook at startup.
    # None in local dev, where there is no public URL to register.
    api_domain: str | None = None
    # Shared secret Telegram echoes back in the X-Telegram-Bot-Api-Secret-Token
    # header on every webhook call. Derived from secret_key when unset.
    telegram_webhook_secret: str | None = None

    # How often Celery beat fires a scrape tick. Beat reads its schedule at
    # startup, so this is the one pipeline number that can't live in the database
    # — changing it needs a beat restart, and the System page says so.
    scrape_interval_seconds: int = 1800

    sentry_dsn: str | None = None

    # NoDecode: pydantic-settings otherwise tries to json.loads() a list-typed env
    # var before validation runs — .env.example documents the plainer `a,b` form.
    api_cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]

    @field_validator("api_cors_origins", mode="before")
    @classmethod
    def _split_comma_separated(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator(
        "voyage_api_key", "telegram_bot_token", "api_domain", "ml_service_url", mode="before"
    )
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        """`VOYAGE_API_KEY=` in a .env file arrives as an empty string, not as
        absent — without this, "configured" would be true for a blank line."""
        if isinstance(value, str) and not value.strip():
            return None
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
