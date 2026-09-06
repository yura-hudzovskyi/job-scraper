"""Every number the pipeline runs on, in one place.

These live in the database, not in .env: the whole point of this config is that a
user tunes it from the System page and the next run picks it up. `DEFAULTS` below
is what a fresh install starts from and what "Reset to defaults" restores.

Each field is documented here because the System page renders these descriptions
verbatim — the UI explaining a setting and the code using it should never be able
to drift apart.
"""

from dataclasses import dataclass, fields
from typing import Any

from app.integrations.voyage import DEFAULT_EMBEDDING_MODEL, DEFAULT_RERANK_MODEL


@dataclass(frozen=True)
class PipelineConfig:
    # --- models ---
    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    rerank_model: str = DEFAULT_RERANK_MODEL

    # --- scraping ---
    scrape_enabled: bool = True
    scrape_max_jobs_per_run: int = 100

    # --- matching ---
    # How many vacancies the embedding search keeps per user. Everything past
    # this rank gets no match row at all, so it is the size of the world the
    # user sees.
    retrieval_limit: int = 400
    # How many of those the reranker reads in full. This is the one part of a run
    # that costs real money per document, so it is deliberately much smaller.
    # 0 means every eligible vacancy. The reranker is the only thing that
    # reads a vacancy and a CV together, so limiting it limits the quality of
    # the ranking, not just its cost.
    rerank_top_k: int = 0
    # How much the reranker's opinion counts against raw embedding similarity,
    # 0-1. Only applies to jobs that were actually reranked.
    rerank_weight: float = 0.7

    # --- recommendation bands, on the final 0-100 score ---
    apply_threshold: float = 70.0
    consider_threshold: float = 45.0

    # --- retention ---
    # Days a vacancy stays after it was last seen in a scrape, before it (and its
    # matches, notifications and vectors) are deleted.
    job_retention_days: int = 18

    def replace(self, **changes: Any) -> "PipelineConfig":
        return PipelineConfig(**{**self.as_dict(), **changes})

    def as_dict(self) -> dict[str, Any]:
        return {field.name: getattr(self, field.name) for field in fields(self)}


DEFAULTS = PipelineConfig()

# Shown next to each field on the System page. Keyed by field name so a field
# without an explanation is a visible gap rather than a silent one.
DESCRIPTIONS: dict[str, str] = {
    "embedding_model": (
        "Voyage model used to turn every vacancy and your CV into a vector. "
        "Changing it invalidates every stored vector — the next run re-embeds the "
        "whole corpus, and matching pauses until it has."
    ),
    "rerank_model": (
        "Voyage model that reads your CV and a vacancy together and scores the "
        "fit. Sharper than comparing two vectors, and far more expensive, so it "
        "only ever sees the top of the search results."
    ),
    "scrape_enabled": "Whether the scheduled scrape tick does anything at all.",
    "scrape_max_jobs_per_run": (
        "Ceiling on how many listings one scrape tick will fetch details for. "
        "Already-known listings are skipped for free and don't count against it."
    ),
    "retrieval_limit": (
        "How many vacancies the embedding search keeps for you, best first. "
        "Anything ranked below this gets no match at all. Set it above the size "
        "of your corpus to keep everything."
    ),
    "rerank_top_k": (
        "How many of those top results the reranker reads in full. 0 means all "
        "of them. The rest keep their embedding-only score and are labelled as "
        "not reranked. Reranking is cached per vacancy and CV, so a full pass "
        "costs once and later runs only pay for what changed."
    ),
    "rerank_weight": (
        "0 = ignore the reranker and score purely on embedding similarity. "
        "1 = score purely on the reranker. Applies only to reranked jobs."
    ),
    "apply_threshold": "Score at or above which a match is recommended: apply.",
    "consider_threshold": (
        "Score at or above which a match is worth considering. Below it, skip — "
        "and skipped jobs are hidden from the jobs list by default."
    ),
    "job_retention_days": (
        "How long a vacancy survives after it stops appearing in scrapes. Past "
        "this, it and everything referencing it are deleted."
    ),
}

# (minimum, maximum) for the numeric fields, enforced by the API and rendered as
# input bounds by the UI.
BOUNDS: dict[str, tuple[float, float]] = {
    "scrape_max_jobs_per_run": (1, 1000),
    # Both ceilings are "the whole corpus, with room to grow" rather than a
    # cost control. Embedding is skipped when a document's text has not changed
    # and reranking is cached per (CV, vacancy, model), so the thing these used
    # to protect against — paying repeatedly for the same answer — is handled
    # where it belongs instead of by refusing to look at most of the corpus.
    "retrieval_limit": (1, 100_000),
    "rerank_top_k": (0, 100_000),
    "rerank_weight": (0.0, 1.0),
    "apply_threshold": (0.0, 100.0),
    "consider_threshold": (0.0, 100.0),
    "job_retention_days": (1, 365),
}
