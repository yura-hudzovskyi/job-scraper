"""Use case: turn a parsed document revision into an evidence-backed profile.

Runs off the outbox rather than inline with ingestion (spec 16). Extraction is
the step that will call a model once GLiNER2 lands, so it belongs off the scrape
path where a slow or failing model degrades throughput instead of taking the
scrape down with it — and where a retry is free because the state machine
already records where the revision got to.

Three properties this is built around, all from spec 3.5.2 and Phase 3's
definition of done:

- **A failure never corrupts the active profile.** Nothing is overwritten, ever;
  a failed extraction transitions the revision to FAILED with a reason and
  leaves whatever profile existed before exactly where it was.
- **Every accepted field has valid evidence.** Spans are re-checked against the
  revision's own `parsed_text` before storage, not against the extractor's idea
  of it.
- **Re-running is safe.** A revision not in PARSED is skipped, so a redelivered
  outbox event (delivery is at-least-once) does not extract twice.
"""

import logging
import uuid
from dataclasses import dataclass

from app.domain.documents.models import EntityKind, RevisionStatus
from app.domain.profiles.extraction import ExtractionInput, ProfileExtractor
from app.domain.profiles.models import ProfileKind, ProfileOrigin
from app.domain.profiles.schemas import spans_of
from app.repositories.document_repository import DocumentRepository
from app.repositories.profile_repository import ProfileRepository
from app.services.concept_linking_service import ConceptLinkingService

logger = logging.getLogger(__name__)

# Stored on the revision when a stage gives up, so an admin screen can group
# failures by cause rather than by message text.
FAILURE_EXTRACTION_ERROR = "extraction_error"
FAILURE_INVALID_EVIDENCE = "invalid_evidence"


@dataclass(frozen=True)
class ExtractionOutcome:
    revision_id: str
    extracted: bool
    skipped_reason: str | None = None
    profile_revision_id: str | None = None
    discarded: int = 0
    linked_concepts: int = 0


class ExtractionService:
    def __init__(
        self,
        document_repository: DocumentRepository,
        profile_repository: ProfileRepository,
        extractor: ProfileExtractor,
        linker: ConceptLinkingService | None = None,
    ):
        self._documents = document_repository
        self._profiles = profile_repository
        self._extractor = extractor
        self._linker = linker

    async def extract(self, revision_id: uuid.UUID) -> ExtractionOutcome:
        revision = await self._documents.get(revision_id)
        if revision is None:
            return ExtractionOutcome(
                revision_id=str(revision_id), extracted=False, skipped_reason="no such revision"
            )
        if revision.status is not RevisionStatus.PARSED:
            # Already extracted, or not ready. Either way this is the normal
            # outcome of a redelivered event rather than a problem.
            return ExtractionOutcome(
                revision_id=str(revision_id),
                extracted=False,
                skipped_reason=f"revision is {revision.status}, not parsed",
            )

        await self._documents.transition(
            revision_id, RevisionStatus.EXTRACTING, reason="extraction started"
        )

        parsed_text = revision.parsed_text or ""
        document = ExtractionInput(
            parsed_text=parsed_text,
            language=revision.language_code,
            known_fields=await self._documents.normalized_fields(revision_id),
        )

        try:
            if revision.entity_kind is EntityKind.JOB:
                result = await self._extractor.extract_job(document)
            else:
                result = await self._extractor.extract_candidate(document)
        except Exception as exc:
            logger.warning("extraction failed for revision %s", revision_id, exc_info=True)
            await self._documents.transition(
                revision_id,
                RevisionStatus.FAILED,
                failure_code=FAILURE_EXTRACTION_ERROR,
                failure_detail=str(exc)[:500],
            )
            return ExtractionOutcome(
                revision_id=str(revision_id), extracted=False, skipped_reason=str(exc)
            )

        # The extractor validated its spans against the text it was handed; this
        # checks them against the text that is actually stored. The two are the
        # same object today, and this is what keeps them the same when a future
        # extractor builds its own view of the document.
        unresolvable = [
            span for span in spans_of(result.profile) if not span.validate_against(parsed_text)
        ]
        if unresolvable:
            await self._documents.transition(
                revision_id,
                RevisionStatus.FAILED,
                failure_code=FAILURE_INVALID_EVIDENCE,
                failure_detail=(
                    f"{len(unresolvable)} evidence span(s) do not quote the stored "
                    "parsed_text; the profile was not written"
                ),
            )
            return ExtractionOutcome(
                revision_id=str(revision_id),
                extracted=False,
                skipped_reason="evidence spans do not resolve",
            )

        profile_revision = await self._profiles.save(
            document_revision_id=revision_id,
            profile_kind=(
                ProfileKind.JOB if revision.entity_kind is EntityKind.JOB else ProfileKind.CANDIDATE
            ),
            schema_version=result.profile.schema_version,
            origin=ProfileOrigin.STRUCTURAL_EXTRACTION,
            extracted_profile=result.profile.model_dump(mode="json"),
            extractor_model_id=result.extractor_model_id,
            overall_confidence=result.profile.quality.overall_confidence,
            validation_warnings=result.discarded_records(),
        )
        # Linking runs here rather than on its own event because the lexical
        # stage is a dictionary lookup per word — half a millisecond for a whole
        # vacancy. A failure to link must not undo a good extraction, though: the
        # profile is already written and the revision is already extracted, so a
        # taxonomy problem is logged and the mentions are simply absent.
        linked = 0
        if self._linker is not None:
            try:
                linking = await self._linker.link(uuid.UUID(profile_revision.id), parsed_text)
                linked = linking.linked
                if linking.skipped_reason:
                    logger.info("revision %s not linked: %s", revision_id, linking.skipped_reason)
            except Exception:
                logger.warning(
                    "concept linking failed for revision %s; the profile stands",
                    revision_id,
                    exc_info=True,
                )

        await self._documents.transition(
            revision_id, RevisionStatus.EXTRACTED, reason="extraction succeeded"
        )
        for warning in result.warnings:
            logger.info("revision %s: %s", revision_id, warning)

        return ExtractionOutcome(
            revision_id=str(revision_id),
            extracted=True,
            profile_revision_id=profile_revision.id,
            discarded=len(result.discarded),
            linked_concepts=linked,
        )
