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

    # Optional paid LLM leg (see app/integrations/ai/llm/factory.py::
    # build_configured_llm_provider) — used only when both are set, and only
    # after the Gemini/Groq free tiers. The pipeline runs on those two alone by
    # default; this exists for deployments that would rather not depend on a
    # free tier at all.
    llm_provider: Literal["openai", "anthropic"] | None = None
    llm_model: str | None = None
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None

    # Optional: when set, CV analysis and preferences AI-fill (the "quality
    # matters most, low volume" call sites) use Gemini's free tier first, falling
    # back to Groq on rate limit — see
    # app/integrations/ai/llm/factory.py::build_quality_llm_provider.
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.0-flash"

    # Optional: when set, the job pipeline (skill extraction, AI matching, "should
    # I apply?") uses Groq's free tier first — fast enough to actually churn
    # through a real backlog — falling back to Gemini on rate limit. See
    # app/integrations/ai/llm/factory.py::build_job_llm_provider and
    # docs/matching-engine.md.
    groq_api_key: str | None = None
    groq_model: str = "llama-3.3-70b-versatile"
    # Groq enforces both per-minute and per-day limits; a 429 during normal use is
    # far more likely to be the former, so this is a short, fixed cooldown rather
    # than GeminiCircuitBreaker's until-midnight one — see circuit_breaker.py.
    groq_circuit_breaker_cooldown_seconds: int = 60

    # Hard daily ceiling on LlmReranker calls (see app/integrations/ai/llm/budget.py
    # and app/domain/matching/llm_reranker.py) — independent of whatever the
    # configured quality/job provider's own rate-limit/billing behavior is.
    # LlmReranker is the only LLM call site left in the job-scoring pipeline (the
    # deterministic pipeline now scores every eligible job on its own) and runs on
    # CONSIDER+APPLY matches, not just APPLY — a wider tier than before, so this
    # default is higher accordingly. Still just a starting point: tune to your
    # actual plan and observed CONSIDER+APPLY volume (free-tier daily quotas vary
    # by provider/model/tier and change over time, so this isn't a number
    # guaranteed correct for any particular plan).
    llm_rerank_daily_limit: int = 150

    embedding_provider: Literal["sentence_transformers", "openai"] = "sentence_transformers"
    embedding_model: str = "all-MiniLM-L6-v2"

    # Second signal blended into SemanticScorer's semantic_fit (see
    # app/domain/matching/scoring.py), on top of the bi-encoder cosine similarity
    # above — cross-encoders jointly attend over both texts instead of comparing
    # two independently-computed vectors, which is generally more accurate for a
    # single query-document relevance judgment like "does this profile fit this
    # job". Runs locally via sentence-transformers' CrossEncoder (already a base
    # dependency, no extra package) — set to None to disable and fall back to pure
    # bi-encoder cosine similarity. The default is deliberately small/fast to keep
    # CPU cost low at real per-job volume; BAAI/bge-reranker-base is a heavier,
    # more accurate opt-in swap (~5-10x slower on CPU).
    cross_encoder_model: str | None = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    cross_encoder_weight: float = 0.5

    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    # Public HTTPS hostname Caddy fronts this API on (see Caddyfile, docs/deployment.md) —
    # also used, here in the Python app, to register the Telegram webhook
    # (https://{api_domain}/api/integrations/telegram/webhook) at startup. None in
    # local dev, where there's no public URL and the webhook is simply never registered.
    api_domain: str | None = None
    # Shared secret Telegram echoes back in the X-Telegram-Bot-Api-Secret-Token
    # header on every webhook call — see integrations/notifications/telegram_webhook.py.
    # Leave unset to derive one automatically from secret_key (still real
    # protection, without another required env var); set explicitly to rotate it
    # independently of secret_key.
    telegram_webhook_secret: str | None = None

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
