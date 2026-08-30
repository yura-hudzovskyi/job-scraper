from functools import lru_cache
from typing import Annotated, Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"
    secret_key: str = "change-me"
    log_level: str = "INFO"

    database_url: str = "postgresql+psycopg://job_scraper:job_scraper@localhost:5432/job_scraper"

    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    llm_provider: Literal["ollama", "openai", "anthropic"] = "ollama"
    llm_model: str = "llama3.1"
    ollama_base_url: str = "http://localhost:11434"
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None

    # Optional: when set, CV analysis (the "quality matters" call site) uses
    # Gemini's free tier first, falling back to Ollama on rate limit — see
    # app/integrations/ai/llm/factory.py::build_quality_llm_provider. Job skill
    # extraction always uses Ollama regardless of this, to keep the free-tier quota
    # for CV analysis.
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.0-flash"

    embedding_provider: Literal["sentence_transformers", "openai"] = "sentence_transformers"
    embedding_model: str = "all-MiniLM-L6-v2"

    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None

    # Each scrape tick covers one category (rotating through every configured
    # category over time — see app/integrations/sources/categories.py), capped at
    # this many listings per run. See app/workers/tasks/scrape.py.
    scrape_interval_seconds: int = 1800
    scrape_max_jobs_per_run: int = 100

    # How long a job stays in the DB after it was last seen in a scrape before
    # retention cleanup deletes it (and everything that references it — matches,
    # notifications). See app/services/job_retention_service.py.
    job_retention_days: int = 18

    sentry_dsn: str | None = None

    # NoDecode: pydantic-settings otherwise tries to json.loads() any list-typed env
    # var before validation ever runs — .env.example documents the plainer `a,b` form.
    api_cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]

    @field_validator("api_cors_origins", mode="before")
    @classmethod
    def _split_comma_separated(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
