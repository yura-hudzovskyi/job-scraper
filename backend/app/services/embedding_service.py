"""Keeping the vector index in step with the corpus.

Two jobs, both idempotent and both cheap to re-run: give every vacancy a vector
under the currently configured model, and give the user's CV one too.

Nothing is re-embedded without cause. Each stored vector carries the hash of the
exact text it came from, so a re-scrape that only moved a view counter costs no
API call. Changing the configured model is the one thing that does invalidate
everything — vectors from two models are not comparable, so the old rows simply
stop matching the query and the whole corpus is re-embedded before matching runs
again. The System page says so explicitly rather than letting it look like an
outage.
"""

import logging
import uuid
from dataclasses import dataclass

from app.domain.candidates.models import UserPreference
from app.domain.matching.documents import job_document, profile_document, text_hash
from app.integrations.voyage import VoyageClient
from app.repositories.embedding_repository import JOB, PROFILE, EmbeddingRepository
from app.repositories.job_repository import JobRepository

logger = logging.getLogger(__name__)

# How many documents go to Voyage in one request. Small enough that one failure
# costs little, large enough that a full corpus doesn't turn into thousands of
# round trips.
BATCH_SIZE = 32


@dataclass(frozen=True)
class IndexResult:
    model: str
    total: int
    embedded: int
    unchanged: int
    failed: int

    @property
    def complete(self) -> bool:
        return self.failed == 0 and self.embedded + self.unchanged == self.total


class EmbeddingService:
    def __init__(
        self,
        embedding_repository: EmbeddingRepository,
        job_repository: JobRepository,
        voyage: VoyageClient,
    ):
        self._embeddings = embedding_repository
        self._jobs = job_repository
        self._voyage = voyage

    async def index_jobs(self, limit: int | None = None) -> IndexResult:
        """Embed every vacancy that doesn't already have a current vector under
        the configured model. `limit` caps one pass so a first run over a large
        corpus can be done in chunks."""
        model = self._voyage.embedding_model
        canonical_job_ids = await self._jobs.list_all_canonical_job_ids()
        stored = await self._embeddings.stored_hashes(JOB, model, canonical_job_ids)
        jobs = await self._jobs.list_normalized_jobs_for_canonical(canonical_job_ids)

        pending: list[tuple[uuid.UUID, str, str]] = []
        unchanged = 0
        for canonical_job_id in canonical_job_ids:
            job = jobs.get(canonical_job_id)
            if job is None:
                # A canonical job with no source record has no text to embed.
                continue
            document = job_document(job)
            content_hash = text_hash(document)
            if stored.get(canonical_job_id) == content_hash:
                unchanged += 1
                continue
            pending.append((canonical_job_id, document, content_hash))

        if limit is not None:
            pending = pending[:limit]

        embedded, failed = 0, 0
        for start in range(0, len(pending), BATCH_SIZE):
            batch = pending[start : start + BATCH_SIZE]
            try:
                vectors = await self._voyage.embed([document for _, document, _ in batch])
            except Exception:
                # One bad batch must not abandon the rest: the next pass retries
                # exactly these documents, because nothing was written for them.
                logger.warning("embedding batch failed (%d documents)", len(batch), exc_info=True)
                failed += len(batch)
                continue
            if len(vectors) != len(batch):
                logger.warning(
                    "embedding returned %d vectors for %d documents — skipping the batch",
                    len(vectors),
                    len(batch),
                )
                failed += len(batch)
                continue
            for (canonical_job_id, _, content_hash), vector in zip(batch, vectors, strict=True):
                await self._embeddings.save_vector(
                    JOB, canonical_job_id, model, content_hash, vector
                )
                embedded += 1

        return IndexResult(
            model=model,
            total=len(canonical_job_ids),
            embedded=embedded,
            unchanged=unchanged,
            failed=failed,
        )

    async def index_profile(
        self, user_id: uuid.UUID, cv_text: str, preferences: UserPreference | None
    ) -> tuple[str, bool]:
        """Embed this user's CV. Returns the document text and whether an API call
        was actually made — the text is returned because the caller needs the very
        same string for the rerank query, and rebuilding it twice invites drift."""
        model = self._voyage.embedding_model
        document = profile_document(cv_text, preferences)
        content_hash = text_hash(document)

        stored = await self._embeddings.stored_hashes(PROFILE, model, [user_id])
        if stored.get(user_id) == content_hash:
            return document, False

        vectors = await self._voyage.embed([document])
        if not vectors:
            raise RuntimeError("Voyage returned no vector for the profile document")
        await self._embeddings.save_vector(PROFILE, user_id, model, content_hash, vectors[0])
        return document, True

    async def get_profile_vector(self, user_id: uuid.UUID) -> list[float] | None:
        return await self._embeddings.get_vector(
            PROFILE, user_id, self._voyage.embedding_model
        )
