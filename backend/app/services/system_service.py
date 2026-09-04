"""What the System page reads and what its buttons do.

Two responsibilities, kept together because they answer the same question from
opposite ends: `status` reports exactly what state the pipeline is in, and the
`reset_*` methods put a chosen part of it back to nothing.

Every reset is written out in explicit dependency order rather than relying on
cascades, because the order is the part that can be wrong: notification
deliveries reference notifications, which reference matches, which reference
canonical jobs. Each one returns what it actually deleted, so the UI reports
"1,204 vacancies, 3,010 matches" instead of "done".
"""

from dataclasses import dataclass, field

from app.domain.documents.models import EntityKind
from app.domain.pipeline_config import PipelineConfig
from app.repositories.candidate_repository import CandidateRepository
from app.repositories.document_repository import DocumentRepository
from app.repositories.embedding_repository import JOB, PROFILE, EmbeddingRepository
from app.repositories.job_repository import JobRepository
from app.repositories.match_repository import MatchRepository
from app.repositories.notification_repository import NotificationRepository
from app.repositories.pipeline_run_repository import PipelineRun, PipelineRunRepository


@dataclass(frozen=True)
class EmbeddingStatus:
    """Coverage under the *configured* model, plus anything left over from a
    previous one. The stale count is the number that explains an empty jobs list
    after a model change, so it is reported rather than inferred."""

    model: str
    jobs_embedded: int
    jobs_total: int
    profiles_embedded: int
    stale_vectors: int


@dataclass(frozen=True)
class SystemStatus:
    voyage_configured: bool
    telegram_configured: bool
    scrape_interval_seconds: int
    config: PipelineConfig
    embeddings: EmbeddingStatus
    counts: dict[str, int] = field(default_factory=dict)
    sources: dict[str, int] = field(default_factory=dict)
    active_run: PipelineRun | None = None
    recent_runs: list[PipelineRun] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        """Whether a run would actually produce matches. False is always
        accompanied by `blockers`, never left for the user to work out."""
        return not self.blockers

    @property
    def blockers(self) -> list[str]:
        problems = []
        if not self.voyage_configured:
            problems.append("VOYAGE_API_KEY is not set — no embedding search and no reranking")
        if self.counts.get("cvs", 0) == 0:
            problems.append("no CV uploaded — there is nothing to match vacancies against")
        if self.counts.get("canonical_jobs", 0) == 0:
            problems.append("no vacancies scraped yet — run the pipeline to fetch some")
        return problems


class SystemService:
    def __init__(
        self,
        job_repository: JobRepository,
        match_repository: MatchRepository,
        notification_repository: NotificationRepository,
        embedding_repository: EmbeddingRepository,
        candidate_repository: CandidateRepository,
        run_repository: PipelineRunRepository,
        document_repository: DocumentRepository | None = None,
    ):
        self._jobs = job_repository
        self._matches = match_repository
        self._notifications = notification_repository
        self._embeddings = embedding_repository
        self._candidates = candidate_repository
        self._runs = run_repository
        self._documents = document_repository

    async def status(
        self,
        config: PipelineConfig,
        voyage_configured: bool,
        telegram_configured: bool,
        scrape_interval_seconds: int,
    ) -> SystemStatus:
        jobs_total = await self._jobs.count_canonical_jobs()
        raw_by_source = await self._jobs.count_raw_jobs_by_source()
        stored = await self._embeddings.models_in_use()
        stale = sum(rows for _, model, rows in stored if model != config.embedding_model)
        return SystemStatus(
            voyage_configured=voyage_configured,
            telegram_configured=telegram_configured,
            scrape_interval_seconds=scrape_interval_seconds,
            config=config,
            embeddings=EmbeddingStatus(
                model=config.embedding_model,
                jobs_embedded=await self._embeddings.count(JOB, config.embedding_model),
                jobs_total=jobs_total,
                profiles_embedded=await self._embeddings.count(PROFILE, config.embedding_model),
                stale_vectors=stale,
            ),
            counts={
                "canonical_jobs": jobs_total,
                "raw_jobs": sum(raw_by_source.values()),
                "matches": await self._matches.count_all(),
                "notifications": await self._notifications.count_all(),
                "cvs": await self._candidates.count_cvs(),
            },
            sources=raw_by_source,
            active_run=await self._runs.active(),
            recent_runs=await self._runs.latest(10),
        )

    # --- resets ---

    async def reset_notifications(self) -> dict[str, int]:
        """Delivery history only. The matches stay, so anything still above the
        notification threshold will be delivered again on the next run."""
        return await self._notifications.delete_all()

    async def reset_matches(self) -> dict[str, int]:
        """Every match, and the notifications that reference them. Vacancies and
        their vectors survive, so re-matching costs one rerank pass, not a
        re-scrape."""
        deleted = await self._notifications.delete_all()
        return {**deleted, "matches": await self._matches.delete_all()}

    async def reset_embeddings(self) -> dict[str, int]:
        """Every vector. The next run re-embeds the whole corpus — this is the
        button for "I changed the model and want a clean index"."""
        return {"embeddings": await self._embeddings.delete_all()}

    async def reset_jobs(self) -> dict[str, int]:
        """Every vacancy, and everything that only exists because of one:
        matches, notifications, vectors, document revisions, scrape history.

        Revisions are dropped by entity kind rather than wholesale: the CVs'
        revisions belong to the account, which every reset deliberately keeps.
        """
        deleted = await self.reset_matches()
        deleted.update(await self.reset_embeddings())
        if self._documents is not None:
            deleted.update(await self._documents.delete_for_kind(EntityKind.JOB))
        deleted.update(await self._jobs.delete_all_jobs())
        return deleted

    async def reset_all(self) -> dict[str, int]:
        """Back to a clean slate for the pipeline. Deliberately keeps the account
        itself — user, CV, preferences, Telegram connection and settings — because
        wiping those turns "start over" into "set everything up again"."""
        deleted = await self.reset_jobs()
        deleted["pipeline_runs"] = await self._runs.delete_all()
        return deleted
