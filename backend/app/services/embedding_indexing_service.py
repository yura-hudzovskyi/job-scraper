"""Builds and refreshes the section vectors a lane holds for one document — see
docs/ai-pipeline-v3.md (C1, C4).

Three things it is careful about, all of them about not paying twice:

- **Unchanged sections are not re-embedded.** Each stored vector carries the hash
  of the text it came from, so a re-scrape that only moved a view counter writes
  nothing at all.
- **Every lane is filled independently.** They are different vector spaces; a
  document indexed in one is not indexed in another, and a lane that fails
  doesn't stop the others.
- **A lane is only declared ready when it can actually answer.** Coverage is
  counted live against the active corpus, and a lane below the threshold stays
  "building" — retrieval then keeps using whichever lane still covers everything
  rather than silently returning a thin result set.
"""

import logging
import uuid
from dataclasses import dataclass

from app.domain.candidates.models import CandidateProfile, UserPreference
from app.domain.jobs.models import NormalizedJob
from app.domain.matching.documents import job_sections, profile_sections
from app.domain.versioning import content_hash
from app.integrations.ai.embeddings.lanes import LaneSpec
from app.repositories.embedding_repository import (
    JOB,
    PROFILE,
    EmbeddingLane,
    EmbeddingRepository,
    SectionVector,
)

logger = logging.getLogger(__name__)

# A lane serves queries only once it covers effectively the whole corpus: a lane
# at 60% doesn't return worse results, it returns a different (smaller) world,
# which is much harder to notice than an outage.
READY_COVERAGE = 0.99

BUILDING = "building"
READY = "ready"


@dataclass(frozen=True)
class IndexingResult:
    lane_id: str
    written: int
    unchanged: int
    failed: bool = False


class EmbeddingIndexingService:
    def __init__(self, embedding_repository: EmbeddingRepository, lanes: list[LaneSpec]):
        self._repository = embedding_repository
        self._lanes = lanes

    async def index_job(
        self, canonical_job_id: uuid.UUID, job: NormalizedJob, version: int
    ) -> list[IndexingResult]:
        return await self._index(JOB, canonical_job_id, version, job_sections(job))

    async def index_profile(
        self, profile: CandidateProfile, preferences: UserPreference | None = None
    ) -> list[IndexingResult]:
        return await self._index(
            PROFILE,
            uuid.UUID(profile.id),
            profile.version,
            profile_sections(profile, preferences),
        )

    async def _index(
        self,
        document_type: str,
        document_id: uuid.UUID,
        version: int,
        sections: dict[str, str],
    ) -> list[IndexingResult]:
        results = []
        for lane in self._lanes:
            results.append(await self._index_one_lane(lane, document_type, document_id, version, sections))
        return results

    async def _index_one_lane(
        self,
        lane: LaneSpec,
        document_type: str,
        document_id: uuid.UUID,
        version: int,
        sections: dict[str, str],
    ) -> IndexingResult:
        stored = await self._repository.stored_hashes(document_type, document_id, lane.id)
        pending = {
            str(section): (text, content_hash(text))
            for section, text in sections.items()
            if stored.get(str(section)) != content_hash(text)
        }
        unchanged = len(sections) - len(pending)
        if not pending:
            return IndexingResult(lane_id=lane.id, written=0, unchanged=unchanged)

        names = list(pending)
        try:
            vectors = await lane.build().embed([pending[name][0] for name in names])
        except Exception:
            # One lane being unreachable must not stop the others: the document
            # stays indexed wherever it could be, and the next pass retries here.
            logger.warning("lane %s could not embed %s %s", lane.id, document_type, document_id, exc_info=True)
            return IndexingResult(lane_id=lane.id, written=0, unchanged=unchanged, failed=True)

        if len(vectors) != len(names):
            logger.warning(
                "lane %s returned %d vectors for %d sections — skipping this document",
                lane.id,
                len(vectors),
                len(names),
            )
            return IndexingResult(lane_id=lane.id, written=0, unchanged=unchanged, failed=True)

        await self._repository.upsert_lane(
            EmbeddingLane(
                id=lane.id,
                provider=lane.provider,
                model=lane.model,
                # Observed, not declared: the model itself is the authority on how
                # many dimensions it produces.
                dimension=len(vectors[0]),
                role=lane.role,
                state=BUILDING,
            )
        )
        written = await self._repository.save_vectors(
            document_type,
            document_id,
            version,
            lane.id,
            [
                SectionVector(section=name, content_hash=pending[name][1], vector=vector)
                for name, vector in zip(names, vectors, strict=True)
            ],
        )
        return IndexingResult(lane_id=lane.id, written=written, unchanged=unchanged)

    async def refresh_lane_readiness(self, active_job_count: int) -> dict[str, str]:
        """Promote lanes that now cover the corpus, demote ones that no longer do
        (a batch of new jobs arrived, or a backfill is still running). Returns the
        state each lane ended up in."""
        states: dict[str, str] = {}
        for lane in await self._repository.list_lanes():
            covered = await self._repository.documents_with_vectors(lane.id, JOB)
            coverage = covered / active_job_count if active_job_count else 0.0
            state = READY if coverage >= READY_COVERAGE else BUILDING
            if state != lane.state:
                await self._repository.set_lane_state(lane.id, state)
                logger.info(
                    "lane %s is now %s (%d/%d jobs covered)",
                    lane.id,
                    state,
                    covered,
                    active_job_count,
                )
            states[lane.id] = state
        return states
