"""Use case: read one canonical job's posting once and record what it actually
asks for — each skill with how the posting framed it, the quote backing that, and
the role category. Runs once per canonical job (see
workers/tasks/extract_job_skills.py — at ingestion time, not per-user, not
per-match). Same shape as CvService.analyze_cv's CV-side extraction.

Everything the posting yields comes out of a *single* call: requirement framing
and the category are fields on the same schema, never a second request (see
docs/ai-pipeline-v3.md, A3). No LLM configured -> best-effort no-op, same
"degrade gracefully" philosophy as CV analysis, so a missing LLM doesn't fail the
scrape pipeline.

The LLM call itself is also wrapped in a try/except (returns None instead of
raising): extract_job_skills.delay's task fans out score_job_for_user for every
onboarded user right after this call, so letting a bad response (timeout,
provider outage, malformed JSON against the schema) propagate out of here would
fail the whole Celery task and silently skip scoring for every user on that job —
extraction failing is a reason to score with an empty skill list
(DeterministicScorer handles that fine), not a reason to not score at all.

Evidence is verified, not trusted: a quote the posting doesn't contain is dropped
rather than stored, so a hallucinated justification can't end up presented to the
user as if it came from the vacancy (docs/ai-pipeline-v3.md, 3.3).
"""

import logging
import re
import uuid
from typing import Literal

from pydantic import BaseModel, Field

from app.domain.categories import JobCategory
from app.domain.jobs.models import NormalizedJobSkill, RequirementType
from app.domain.skills import requirements
from app.integrations.ai.llm.base import LLMProvider
from app.repositories.job_repository import JobRepository

logger = logging.getLogger(__name__)

_MAX_EVIDENCE_CHARS = 160


class _ExtractedSkill(BaseModel):
    name: str
    requirement: Literal[
        "required_explicit",
        "required_inferred",
        "optional_explicit",
        "context",
        "unknown",
    ] = "unknown"
    evidence: str | None = None
    confidence: float | None = None


class _ExtractedJob(BaseModel):
    skills: list[_ExtractedSkill] = Field(default_factory=list)
    category: (
        Literal[
            "backend",
            "frontend",
            "full_stack",
            "mobile",
            "qa",
            "devops",
            "data",
            "ml_ai",
            "security",
            "embedded",
            "gamedev",
            "design",
            "product",
            "project_management",
            "support",
            "marketing",
            "sales",
            "hr",
            "finance",
            "other",
        ]
        | None
    ) = None
    category_confidence: float | None = None


_EXTRACTION_PROMPT = """Read this job posting and record what it actually asks for.

For every technical skill or technology the posting mentions:
- name: as the posting names it (e.g. "React", "PostgreSQL", "Kubernetes") — keep \
it short, never invent a skill the posting doesn't mention.
- requirement: how the posting frames it.
    required_explicit — stated as a must ("required", "must have", "3+ years of X")
    required_inferred — not labelled a must, but the role plainly can't be done without it
    optional_explicit — listed as nice-to-have, a bonus, a plus
    context — mentioned as background (the team's stack, the product), not asked of the candidate
    unknown — mentioned, but the posting gives no indication which of the above applies
- evidence: the shortest quote from the posting supporting your choice, copied \
verbatim, at most {max_evidence} characters. Leave it empty rather than paraphrasing.
- confidence: 0-1, how sure you are about `requirement`.

Then classify the role itself:
- category: one of {categories}
- category_confidence: 0-1

Job title: {title}
Job description:
---
{description}
---
"""


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _verified_evidence(quote: str | None, description: str, title: str) -> str | None:
    """Keep a quote only if the posting really contains it. A model that
    paraphrases (or invents) its justification gets no evidence recorded rather
    than a claim nothing can be checked against."""
    if not quote:
        return None
    trimmed = quote.strip()[:_MAX_EVIDENCE_CHARS]
    haystack = _normalize_whitespace(f"{title}\n{description}")
    return trimmed if _normalize_whitespace(trimmed) in haystack else None


class JobSkillExtractionService:
    def __init__(self, job_repository: JobRepository, llm_provider: LLMProvider | None):
        self._job_repository = job_repository
        self._llm_provider = llm_provider

    async def extract_and_save(self, canonical_job_id: uuid.UUID) -> list[NormalizedJobSkill] | None:
        if self._llm_provider is None:
            return None

        job = await self._job_repository.get_normalized_job_for_canonical(canonical_job_id)
        if job is None:
            raise LookupError(f"canonical job {canonical_job_id} has no normalized source record")

        prompt = _EXTRACTION_PROMPT.format(
            title=job.title,
            description=job.description,
            max_evidence=_MAX_EVIDENCE_CHARS,
            categories=", ".join(category.value for category in JobCategory),
        )
        try:
            result = await self._llm_provider.structured_completion(prompt, _ExtractedJob)
        except Exception:
            logger.warning(
                "skill extraction failed for canonical job %s — scoring will proceed "
                "with no extracted skills",
                canonical_job_id,
                exc_info=True,
            )
            return None

        extracted = result.data
        skills = requirements.merge(
            requirements.normalize(
                name=skill.name,
                requirement=RequirementType(skill.requirement),
                evidence=_verified_evidence(skill.evidence, job.description, job.title),
                confidence=skill.confidence,
            )
            for skill in extracted.skills
        )
        await self._job_repository.update_skills_for_canonical(
            canonical_job_id,
            skills,
            result.model_label,
            category=JobCategory(extracted.category) if extracted.category else None,
            category_confidence=extracted.category_confidence,
        )
        return skills
