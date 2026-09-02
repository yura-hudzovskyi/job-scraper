import logging
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

logger = logging.getLogger(__name__)

# Values LLM_PROVIDER used to accept. A .env that still names one must not take
# the app down: the option is gone, the deployment is otherwise fine, and a
# crash-on-boot over a retired setting is the worst possible way to say so.
_RETIRED_LLM_PROVIDERS = {"ollama"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"
    secret_key: str = "change-me"
    log_level: str = "INFO"

    database_url: str = "postgresql+psycopg://job_scraper:job_scraper@localhost:5432/job_scraper"

    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # Optional paid LLM leg (the PAID entry in
    # app/integrations/ai/routing/policy.py) — used only when both are set, and only
    # after the Gemini/Groq free tiers. The pipeline runs on those two alone by
    # default; this exists for deployments that would rather not depend on a
    # free tier at all.
    llm_provider: Literal["openai", "anthropic"] | None = None
    llm_model: str | None = None
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None

    # Optional: when set, CV analysis and preferences AI-fill (the "quality
    # matters most, low volume" call sites) use Gemini's free tier first, falling
    # back to Groq on rate limit — see app/integrations/ai/routing/policy.py.
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.0-flash"

    # Optional: when set, the job pipeline (skill extraction, AI matching, "should
    # I apply?") uses Groq's free tier first — fast enough to actually churn
    # through a real backlog — falling back to Gemini on rate limit. See
    # app/integrations/ai/routing/policy.py and docs/matching-engine.md.
    groq_api_key: str | None = None
    groq_model: str = "llama-3.3-70b-versatile"

    # Hard daily ceilings, one per capability — see
    # app/integrations/ai/quota/budget.py. Separate counters *are* the
    # interactive reserve: a backlog run burning through job extraction cannot
    # eat what CV analysis has left, because they never share a budget.
    #
    # These are starting points, not measured limits: free-tier daily quotas vary
    # by provider, model and account and change over time, so tune them to what
    # the System page reports actually getting used.
    llm_daily_limit_profile_extraction: int = 50  # user-triggered, rare, protected
    llm_daily_limit_job_extraction: int = 400  # once per newly scraped job
    llm_daily_limit_match_enrichment: int = 150  # the "should I apply?" verdict

    embedding_provider: Literal["sentence_transformers", "openai"] = "sentence_transformers"
    embedding_model: str = "all-MiniLM-L6-v2"

    # Optional embedding lanes (app/integrations/ai/embeddings/lanes.py). Each one
    # is a separate vector space with its own stored vectors; retrieval uses the
    # best *ready* lane and never mixes two. The local model above is always
    # available as the durable lane, so neither of these is required.
    #
    # Voyage: the quality lane candidate — strong multilingual model, currently a
    # large one-time free token pool rather than a recurring allowance.
    voyage_api_key: str | None = None
    voyage_embedding_model: str = "voyage-4-large"
    # Cloudflare Workers AI: a hosted BGE-M3 on a recurring daily allowance, so it
    # keeps working after a one-off pool runs out. Both values are needed.
    cloudflare_account_id: str | None = None
    cloudflare_api_token: str | None = None
    cloudflare_embedding_model: str = "@cf/baai/bge-m3"
    # Rerank models on the same accounts (app/integrations/ai/rerank/factory.py).
    # A reranker reads the candidate and a vacancy together, which is sharper than
    # comparing two vectors and too expensive for the whole corpus — it runs over
    # the retrieved top-K only.
    voyage_rerank_model: str = "rerank-3"
    cloudflare_rerank_model: str = "@cf/baai/bge-reranker-base"

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

    # AI pipeline v3 rollout flags — see docs/ai-pipeline-v3.md. Each one stays
    # off until the phase that implements it lands, so half-built pipeline stages
    # can ship dark instead of living on a long-running branch.
    #
    # Route scoring through the v3 orchestrator (hybrid engine + priority
    # scheduler) instead of MatchingService.evaluate. Phase 6.
    matching_pipeline_v3: bool = False
    # Let that orchestrator upgrade a hybrid match with an LLM judgment when
    # quota allows. Phase 7; no effect while matching_pipeline_v3 is off.
    llm_enrichment: bool = False
    # Build and query the versioned embedding lanes instead of the single
    # on-demand embedding SemanticScorer computes today. Phase 4.
    multi_embedding_lanes: bool = False

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

    @field_validator("llm_provider", mode="before")
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        """`LLM_PROVIDER=` in a .env file arrives as an empty string, not as
        absent — without this the documented "leave it blank" form fails
        validation and the app won't start."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("embedding_provider", mode="before")
    @classmethod
    def _blank_is_default(cls, value: object) -> object:
        """Same problem, different answer: this one has no "unset" state, so a
        blank line means "whatever the default is" rather than None — which would
        only produce a more confusing error than the empty string did."""
        if isinstance(value, str) and not value.strip():
            return "sentence_transformers"
        return value

    @field_validator("llm_provider", mode="before")
    @classmethod
    def _drop_retired_provider(cls, value: object) -> object:
        """A retired provider name is ignored loudly rather than fatally. Typos
        still fail: only the names this app used to accept are swallowed."""
        if isinstance(value, str) and value.strip().lower() in _RETIRED_LLM_PROVIDERS:
            logger.warning(
                "LLM_PROVIDER=%s is no longer supported and is being ignored — the "
                "pipeline runs on Groq/Gemini. Clear it from .env to silence this.",
                value,
            )
            return None
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
