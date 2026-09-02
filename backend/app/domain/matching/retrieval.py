"""Which vacancies are worth looking at for one candidate — see
docs/ai-pipeline-v3.md (B2, C1, C5).

Today every eligible job is scored for every user, which is affordable only
because scoring is cheap. Everything after this phase — reranking, the hybrid
engine, LLM enrichment — costs real money per job, so something has to decide
which jobs deserve it. That is this: a ranked candidate set built from section
similarity inside one lane, adjusted by how well the vacancy's category matches
what the candidate is after.

Two things it deliberately does not do:

- **It doesn't re-run the hard filters.** Those live in HardFilterService and
  need the whole job; running a second copy here would be two places to keep in
  sync for a check the scoring path already performs.
- **It doesn't let the category classifier remove a vacancy quietly.** A hard
  mismatch drops out of the main list but stays eligible for the exploration
  slice, so a mislabelled or genuinely cross-functional posting can still
  surface. That slice is the difference between "we ranked this low" and "you
  never saw it".
"""

import logging
import uuid
from dataclasses import dataclass

from app.domain.candidates.models import CandidateProfile, UserPreference
from app.domain.categories import CategoryDecision, candidate_categories, decide
from app.domain.matching.documents import Section, profile_sections
from app.integrations.ai.embeddings.lanes import QUALITY, LaneSpec
from app.repositories.embedding_repository import JOB, EmbeddingRepository, SectionQuery

logger = logging.getLogger(__name__)

# Starting weights from the plan (C1). They are a hypothesis: requirements
# against skills is the strongest single signal, and stated preferences barely
# move a ranking because they're already enforced as filters elsewhere. The
# labelled set in phase 9 replaces these with measured values.
SECTION_WEIGHTS: dict[Section, float] = {
    Section.SKILLS_REQUIREMENTS: 0.45,
    Section.RESPONSIBILITIES_EXPERIENCE: 0.30,
    Section.OVERVIEW: 0.20,
    Section.PREFERENCES_CONSTRAINTS: 0.05,
}

# A soft mismatch is "probably not your field, but classifiers are wrong often
# enough" — a nudge down the list, not a removal.
SOFT_MISMATCH_PENALTY = 0.85
# Roughly a tenth of the set is kept for vacancies the category gate ruled out.
EXPLORATION_RATIO = 0.1
READY = "ready"


@dataclass(frozen=True)
class RetrievedJob:
    canonical_job_id: uuid.UUID
    score: float
    category: CategoryDecision
    # True when this job is here *because* of the exploration slice — it was
    # ruled out by category and kept anyway.
    exploration: bool = False


@dataclass(frozen=True)
class RetrievalResult:
    lane_id: str | None
    jobs: list[RetrievedJob]

    @property
    def usable(self) -> bool:
        return self.lane_id is not None


class RetrievalService:
    def __init__(
        self,
        embedding_repository: EmbeddingRepository,
        job_categories: "JobCategoryLookup",
        lanes: list[LaneSpec],
    ):
        self._repository = embedding_repository
        self._job_categories = job_categories
        self._lanes = {lane.id: lane for lane in lanes}

    async def retrieve(
        self,
        profile: CandidateProfile,
        preferences: UserPreference | None = None,
        limit: int = 150,
        exclude_ids: set[uuid.UUID] | None = None,
    ) -> RetrievalResult:
        lane = await self._ready_lane()
        if lane is None:
            # No lane covers the corpus yet. Saying so lets the caller keep its
            # current behaviour instead of acting on a thin candidate set.
            return RetrievalResult(lane_id=None, jobs=[])

        queries = await self._section_queries(lane, profile, preferences)
        if not queries:
            return RetrievalResult(lane_id=lane.id, jobs=[])

        # Over-fetch: exclusions and the category split both thin the list, and a
        # second round trip to top it up would cost more than the extra rows.
        candidates = await self._repository.search(lane.id, JOB, queries, limit=limit * 3)
        excluded = exclude_ids or set()
        candidates = [candidate for candidate in candidates if candidate.document_id not in excluded]

        categories = await self._job_categories.categories_for(
            [candidate.document_id for candidate in candidates]
        )
        wanted = candidate_categories(
            [*(preferences.preferred_roles if preferences else []), *profile.roles]
        )

        ranked: list[RetrievedJob] = []
        ruled_out: list[RetrievedJob] = []
        for candidate in candidates:
            category, confidence = categories.get(candidate.document_id, (None, None))
            decision = decide(category, confidence, wanted)
            score = candidate.score * (
                SOFT_MISMATCH_PENALTY if decision is CategoryDecision.SOFT_MISMATCH else 1.0
            )
            entry = RetrievedJob(
                canonical_job_id=candidate.document_id, score=score, category=decision
            )
            if decision is CategoryDecision.HARD_MISMATCH:
                ruled_out.append(entry)
            else:
                ranked.append(entry)

        ranked.sort(key=lambda job: job.score, reverse=True)
        exploration_slots = min(len(ruled_out), max(1, int(limit * EXPLORATION_RATIO)))
        exploration = [
            RetrievedJob(
                canonical_job_id=job.canonical_job_id,
                score=job.score,
                category=job.category,
                exploration=True,
            )
            for job in sorted(ruled_out, key=lambda job: job.score, reverse=True)[
                :exploration_slots
            ]
        ]
        return RetrievalResult(
            lane_id=lane.id, jobs=[*ranked[: limit - len(exploration)], *exploration]
        )

    async def _ready_lane(self) -> LaneSpec | None:
        """The best lane that can actually answer: quality if it is ready, else a
        ready durable one. A lane still building is skipped rather than queried —
        querying it would return a smaller world without saying so."""
        ready = [
            lane for lane in await self._repository.list_lanes()
            if lane.state == READY and lane.id in self._lanes
        ]
        if not ready:
            return None
        by_role = sorted(ready, key=lambda lane: 0 if lane.role == QUALITY else 1)
        return self._lanes[by_role[0].id]

    async def _section_queries(
        self,
        lane: LaneSpec,
        profile: CandidateProfile,
        preferences: UserPreference | None,
    ) -> list[SectionQuery]:
        sections = profile_sections(profile, preferences)
        if not sections:
            return []
        names = list(sections)
        try:
            vectors = await lane.build().embed([sections[name] for name in names])
        except Exception:
            logger.warning("lane %s could not embed the profile query", lane.id, exc_info=True)
            return []
        if len(vectors) != len(names):
            return []
        return [
            SectionQuery(section=str(name), weight=SECTION_WEIGHTS.get(name, 0.0), vector=vector)
            for name, vector in zip(names, vectors, strict=True)
            if SECTION_WEIGHTS.get(name, 0.0) > 0
        ]


class JobCategoryLookup:
    """The one thing retrieval needs from the job side: how each candidate
    vacancy was classified, and how sure the classifier was. Narrow on purpose —
    a domain service shouldn't take a whole repository to read two columns."""

    async def categories_for(
        self, canonical_job_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, tuple[object, float | None]]: ...  # pragma: no cover - protocol
