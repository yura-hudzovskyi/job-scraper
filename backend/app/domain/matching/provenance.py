"""What actually produced a match result — see docs/ai-pipeline-v3.md (3.4
"Results are immutable snapshots", 9.2 "Result details").

A score on its own is not explainable once models, prompts or documents change
underneath it. Every match therefore carries a snapshot of how it was made: which
engine ran, how deep the analysis got, which revision of the CV and of the job
posting it was computed against, which models were involved, and — when the LLM
layer didn't run — why. The snapshot is stored with the match, so it keeps
naming the model that really produced it even after the System page points the
app at a different one.

Serialization lives here rather than in the repository: the payload shape is part
of this contract, and reading an old row must not depend on today's defaults.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from app.domain.versioning import DocumentVersion

# Bump when the thing each one names changes shape or behaviour, so an old row
# keeps saying which version scored it instead of silently claiming the new one.
SCORER_VERSION = "1"  # DeterministicScorer weights + overall()
MATCH_PROMPT_VERSION = "1"  # LlmReranker's prompt/schema
SKILL_TAXONOMY_VERSION = "1"  # SkillMatcher's ontology


class MatchEngine(StrEnum):
    """Which engine produced the result. Only DETERMINISTIC exists today; the
    other two arrive with phases 6 and 7 of docs/ai-pipeline-v3.md, and both
    report through this same contract so the UI never branches on engine."""

    DETERMINISTIC = "deterministic"
    HYBRID = "hybrid"
    LLM_ENRICHED = "llm_enriched"


class AnalysisLevel(StrEnum):
    """How much of the pipeline actually had something to work with. Kept
    separate from the score on purpose: an 84 with LIMITED evidence is not the
    same claim as an 84 with FULL."""

    FULL = "full"  # an LLM judgment on top of the deterministic score
    STANDARD = "standard"  # deterministic score against extracted requirements
    LIMITED = "limited"  # nothing extracted to check against, or filtered out early


class FallbackReason(StrEnum):
    """Why the LLM layer didn't contribute. None means it did (or that nothing
    was expected to)."""

    NO_LLM_PROVIDER = "no_llm_provider"
    # Today's budget for this capability is spent, or every provider leg is
    # cooling down — see app/integrations/ai/routing/router.py::NoCapacity.
    LLM_NO_CAPACITY = "llm_no_capacity"
    BELOW_LLM_THRESHOLD = "below_llm_threshold"


@dataclass(frozen=True)
class PipelineModels:
    """Which models a MatchingService instance was built with. The factory reads
    them from Settings; the service only records them, so it stays free of
    configuration."""

    embedding: str | None = None
    cross_encoder: str | None = None


@dataclass(frozen=True)
class PipelineVersions:
    scorer: str = SCORER_VERSION
    match_prompt: str = MATCH_PROMPT_VERSION
    skill_taxonomy: str = SKILL_TAXONOMY_VERSION
    # No calibration layer exists yet — phase 6 introduces one and starts filling
    # this in. Stored as None rather than omitted so old rows read back the same.
    calibration: str | None = None


@dataclass(frozen=True)
class MatchProvenance:
    engine: MatchEngine
    analysis_level: AnalysisLevel
    profile: DocumentVersion | None = None
    job: DocumentVersion | None = None
    embedding_model: str | None = None
    cross_encoder_model: str | None = None
    skills_model: str | None = None  # which LLM extracted the job's requirements
    match_model: str | None = None  # which LLM produced the "should I apply?" verdict
    fallback_reason: FallbackReason | None = None
    versions: PipelineVersions = PipelineVersions()
    generated_at: datetime | None = None


def _document_payload(document: DocumentVersion | None) -> dict[str, Any] | None:
    if document is None:
        return None
    return {"version": document.version, "content_hash": document.content_hash}


def _document_from_payload(payload: dict[str, Any] | None) -> DocumentVersion | None:
    if payload is None:
        return None
    return DocumentVersion(version=payload["version"], content_hash=payload["content_hash"])


def provenance_payload(provenance: MatchProvenance) -> dict[str, Any]:
    """JSONB-ready dict. Timestamps go out as ISO strings, enums as their values."""
    return {
        "engine": provenance.engine.value,
        "analysis_level": provenance.analysis_level.value,
        "profile": _document_payload(provenance.profile),
        "job": _document_payload(provenance.job),
        "embedding_model": provenance.embedding_model,
        "cross_encoder_model": provenance.cross_encoder_model,
        "skills_model": provenance.skills_model,
        "match_model": provenance.match_model,
        "fallback_reason": (
            provenance.fallback_reason.value if provenance.fallback_reason else None
        ),
        "versions": {
            "scorer": provenance.versions.scorer,
            "match_prompt": provenance.versions.match_prompt,
            "skill_taxonomy": provenance.versions.skill_taxonomy,
            "calibration": provenance.versions.calibration,
        },
        "generated_at": (
            provenance.generated_at.isoformat() if provenance.generated_at else None
        ),
    }


def provenance_from_payload(payload: dict[str, Any] | None) -> MatchProvenance | None:
    """Reads back exactly what was stored — every version comes from the payload,
    never from this module's current constants, or an old result would start
    claiming it was scored by today's pipeline."""
    if payload is None:
        return None
    versions = payload.get("versions", {})
    generated_at = payload.get("generated_at")
    return MatchProvenance(
        engine=MatchEngine(payload["engine"]),
        analysis_level=AnalysisLevel(payload["analysis_level"]),
        profile=_document_from_payload(payload.get("profile")),
        job=_document_from_payload(payload.get("job")),
        embedding_model=payload.get("embedding_model"),
        cross_encoder_model=payload.get("cross_encoder_model"),
        skills_model=payload.get("skills_model"),
        match_model=payload.get("match_model"),
        fallback_reason=(
            FallbackReason(payload["fallback_reason"]) if payload.get("fallback_reason") else None
        ),
        versions=PipelineVersions(
            scorer=versions.get("scorer", SCORER_VERSION),
            match_prompt=versions.get("match_prompt", MATCH_PROMPT_VERSION),
            skill_taxonomy=versions.get("skill_taxonomy", SKILL_TAXONOMY_VERSION),
            calibration=versions.get("calibration"),
        ),
        generated_at=datetime.fromisoformat(generated_at) if generated_at else None,
    )


def now() -> datetime:
    """One place to stamp a result, so tests can reason about it and every engine
    stamps the same way (UTC, timezone-aware)."""
    return datetime.now(UTC)
