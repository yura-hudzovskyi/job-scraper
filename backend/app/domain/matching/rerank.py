"""Ordering the retrieved candidate set with a reranker — see
docs/ai-pipeline-v3.md (D).

Retrieval answers "which hundred vacancies are in the neighbourhood"; this
answers "in what order", by reading the candidate's document and each vacancy's
document together instead of comparing two independently-computed vectors.

Three rules the plan is emphatic about, and this module enforces:

- **One model per run.** Raw relevance scores are model-specific, so ranks 1-40
  from one model and 41-100 from another are not a ranking. If an engine fails
  part-way, everything it produced is discarded and the *whole* set is rerun on
  the next engine.
- **The query is not just the CV.** A short, versioned instruction goes in front
  of it, because "rank by realistic fit, penalise missing must-haves, don't
  reward keyword repetition" changes what a reranker returns. Changing that text
  changes results, so it carries a version that ends up in provenance.
- **Raw scores never reach the user.** They are calibrated per model
  (calibration.py) before anything else looks at them, and even then they are a
  relevance signal feeding the score — not a match percentage.
"""

import logging
import uuid
from dataclasses import dataclass

from app.domain.matching.calibration import calibrate_relevance
from app.integrations.ai.rerank.base import RerankEngine

logger = logging.getLogger(__name__)

# Version this: it changes ranking behaviour, so a stored result has to say which
# instruction produced it.
INSTRUCTION_VERSION = "1"
RERANK_INSTRUCTION = (
    "Rank jobs by realistic fit for this candidate. Prioritize mandatory skills, "
    "evidence of comparable responsibilities, seniority and years. Penalize missing "
    "must-have requirements. Do not reward keyword repetition alone."
)


@dataclass(frozen=True)
class RerankedJob:
    canonical_job_id: uuid.UUID
    # 0-1 after this model's calibration. Comparable across models only because
    # of that step — see calibration.py.
    relevance: float
    raw_score: float
    rank: int


@dataclass(frozen=True)
class RerankRun:
    """One complete pass over one candidate set by one model. `model_id` is None
    when no engine could do the set at all, in which case the caller keeps
    whatever order retrieval produced."""

    model_id: str | None
    instruction_version: str
    jobs: list[RerankedJob]

    @property
    def ran(self) -> bool:
        return self.model_id is not None


def rerank_query(candidate_document: str) -> str:
    return f"{RERANK_INSTRUCTION}\n\n{candidate_document}"


class RerankService:
    def __init__(self, engines: list[RerankEngine]):
        self._engines = engines

    async def rerank(
        self, candidate_document: str, documents: dict[uuid.UUID, str]
    ) -> RerankRun:
        if not documents or not self._engines:
            return RerankRun(model_id=None, instruction_version=INSTRUCTION_VERSION, jobs=[])

        job_ids = list(documents)
        texts = [documents[job_id] for job_id in job_ids]
        query = rerank_query(candidate_document)

        for engine in self._engines:
            try:
                raw_scores = await engine.rerank(query, texts)
            except Exception:
                logger.warning(
                    "rerank engine %s failed on a set of %d — rerunning the whole set on the "
                    "next engine",
                    engine.model_id,
                    len(texts),
                    exc_info=True,
                )
                continue

            if len(raw_scores) != len(texts):
                # A short answer is a partial ranking, which is exactly what must
                # not be stitched together.
                logger.warning(
                    "rerank engine %s returned %d scores for %d documents — discarding",
                    engine.model_id,
                    len(raw_scores),
                    len(texts),
                )
                continue

            scored = [
                (job_id, raw, calibrate_relevance(engine.model_id, raw))
                for job_id, raw in zip(job_ids, raw_scores, strict=True)
            ]
            # Ties broken by the candidate's id keeps the order deterministic for
            # the same input, which is what makes a fallback reproducible.
            scored.sort(key=lambda entry: (-entry[2], str(entry[0])))
            return RerankRun(
                model_id=engine.model_id,
                instruction_version=INSTRUCTION_VERSION,
                jobs=[
                    RerankedJob(
                        canonical_job_id=job_id, relevance=relevance, raw_score=raw, rank=rank
                    )
                    for rank, (job_id, raw, relevance) in enumerate(scored, start=1)
                ],
            )

        return RerankRun(model_id=None, instruction_version=INSTRUCTION_VERSION, jobs=[])
