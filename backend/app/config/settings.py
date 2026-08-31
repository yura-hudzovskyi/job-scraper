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
    # Ollama silently truncates the prompt to this many tokens rather than erroring —
    # see app/integrations/ai/llm/ollama_provider.py. Job descriptions and CV text are
    # embedded in full (no truncation in the prompt-building code itself), so this has
    # to comfortably cover the longest real postings/CVs, not just the typical case.
    # qwen2.5:14b/qwen3:14b (the recommended default, see docs/deployment.md) natively
    # support up to 32768 — raise toward that ceiling if truncation is still observed
    # in practice, trading more KV-cache RAM and somewhat slower inference for it.
    ollama_num_ctx: int = 16384
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None

    # Optional: when set, CV analysis (the "quality matters" call site) uses
    # Gemini's free tier first, falling back to Ollama on rate limit — see
    # app/integrations/ai/llm/factory.py::build_quality_llm_provider. Job skill
    # extraction always uses Ollama regardless of this, to keep the free-tier quota
    # for CV analysis.
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.0-flash"

    # Hard daily ceiling on LlmReranker calls (see app/integrations/ai/llm/budget.py
    # and app/domain/matching/llm_reranker.py) — independent of whatever Gemini's
    # own rate-limit/billing behavior is. Conservative default; tune to your actual
    # Gemini plan (free-tier daily quotas vary by model/tier and change over time,
    # so this isn't a number guaranteed correct for any particular plan).
    llm_rerank_daily_limit: int = 30

    embedding_provider: Literal["sentence_transformers", "openai"] = "sentence_transformers"
    embedding_model: str = "all-MiniLM-L6-v2"

    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    # How often the swipe-card Approve/Reject buttons are checked for taps — see
    # app/workers/tasks/telegram_poll.py. Short by design: each tick is a quick,
    # non-blocking getUpdates call (timeout=0), not a long-poll, so a short
    # interval doesn't tie up a worker slot the way a long-poll loop would.
    telegram_poll_interval_seconds: int = 5

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
