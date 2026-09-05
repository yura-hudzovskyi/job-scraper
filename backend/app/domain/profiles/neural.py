"""The extractor that reads the text, wrapped around the one that does not.

Spec 24 Phase 3 task 2 is explicit that deterministic parsing keeps running
unchanged for the fields it already handles, so this does not replace
`StructuralExtractor` — it calls it, keeps every requirement it produced, and
adds the one thing it deliberately refused to produce: competencies.

That division is the same one 3.5.2 draws. Title, seniority, salary and
employment type were already parsed by a source adapter and need no model.
"which skills does this vacancy actually ask for" needs one, and inventing
keyword rules for it is what the structural extractor's docstring calls the
failure the previous extraction layer was removed for.

Degrades rather than fails. If `ml-service` is unreachable the profile is the
structural one, `extractor_model_id` says so, and a warning records that
competencies are missing rather than absent — the two are different facts, and
spec 5.1 step 10 is about never confusing them. Extraction is not allowed to take
the pipeline down with it (24.0 invariant 1).
"""

import logging

from app.domain.profiles.extraction import (
    DiscardedField,
    ExtractionInput,
    ExtractionResult,
    FieldOutcome,
)
from app.domain.profiles.schemas import (
    CandidateProfile,
    CompetencyCategory,
    ConceptMention,
    EvidenceSpan,
    JobProfile,
)
from app.domain.profiles.structural import StructuralExtractor
from app.integrations.ml_service import ExtractedEntity, MlServiceClient

logger = logging.getLogger(__name__)

# Spec 8.2's third bounded pass — "skill/tool/knowledge mentions" — and no more
# than that in one forward pass. These three are the labels the model comparison
# was measured with (17.6); adding a fourth changes recall on the other three,
# so the set and the measurement travel together.
COMPETENCY_LABELS = ["technology", "tool", "professional skill"]

# The model's label to ours. `CompetencyCategory` is deliberately not IT-specific
# (spec 2.1), so this is a narrowing: three labels a general-purpose extractor
# understands, mapped onto a vocabulary that also has to hold a nursing licence.
_CATEGORIES = {
    "technology": CompetencyCategory.TECHNOLOGY,
    "tool": CompetencyCategory.TOOL,
    "professional skill": CompetencyCategory.PROFESSIONAL_SKILL,
}

FAILURE_ML_SERVICE = "ml_service_unavailable"


def to_mention(entity: ExtractedEntity, parsed_text: str) -> ConceptMention | None:
    """One model entity as a profile competency, or nothing if it cannot be evidence.

    ml-service already checked that the span quotes the text it was given. This
    checks it again against the text actually stored, which is the only copy that
    matters: the two are the same string today, and this is what notices the day
    a caller starts sending a normalised or truncated version instead.
    """
    span = EvidenceSpan(
        start_char=entity.start_char,
        end_char=entity.end_char,
        text=entity.text,
    )
    if not span.validate_against(parsed_text):
        return None
    return ConceptMention(
        raw_text=entity.text,
        category=_CATEGORIES.get(entity.label, CompetencyCategory.OTHER),
        # The model's own score, not a stub (spec 3.5.2). A low-confidence
        # mention stays in the profile and stays visibly low-confidence; nothing
        # here decides what is good enough, because that is a scoring decision
        # and scoring does not read these yet.
        confidence=min(max(entity.confidence, 0.0), 1.0),
        evidence=span,
    )


class NeuralExtractor:
    """StructuralExtractor's fields, plus GLiNER2's competencies."""

    def __init__(
        self,
        client: MlServiceClient,
        structural: StructuralExtractor | None = None,
        labels: list[str] | None = None,
    ):
        self._client = client
        self._structural = structural or StructuralExtractor()
        self._labels = labels or COMPETENCY_LABELS

    async def _competencies(
        self, document: ExtractionInput
    ) -> tuple[list[ConceptMention], list[str], list[DiscardedField], str]:
        if not document.parsed_text.strip():
            return [], [], [], ""

        batch = await self._client.extract([("document", document.parsed_text)], self._labels)
        if not batch.documents:
            return [], ["ml-service returned no result for this document"], [], ""

        result = batch.documents[0]
        mentions: list[ConceptMention] = []
        discarded: list[DiscardedField] = []
        for entity in result.entities:
            mention = to_mention(entity, document.parsed_text)
            if mention is None:
                discarded.append(
                    DiscardedField(
                        kind="competency",
                        outcome=FieldOutcome.REJECTED,
                        reason="evidence span does not quote the stored document",
                        raw_value=entity.text,
                    )
                )
                continue
            mentions.append(mention)

        warnings: list[str] = []
        if result.rejected_spans:
            warnings.append(
                f"ml-service dropped {result.rejected_spans} span(s) that did not quote the text"
            )
        if result.truncated:
            warnings.append("document was truncated before extraction; its tail was not read")
        return mentions, warnings, discarded, batch.model_fingerprint

    async def extract_job(self, document: ExtractionInput) -> ExtractionResult:
        base = await self._structural.extract_job(document)
        assert isinstance(base.profile, JobProfile)

        try:
            mentions, warnings, discarded, fingerprint = await self._competencies(document)
        except Exception:
            # Structural extraction already succeeded and is worth storing. A
            # model outage must cost competencies, not the whole profile.
            logger.warning("ml-service extraction failed; storing structural only", exc_info=True)
            return self._degraded(base)

        profile = base.profile.model_copy(update={"competencies": mentions})
        if document.truncated:
            profile = profile.model_copy(
                update={"quality": profile.quality.model_copy(update={"document_truncated": True})}
            )
        return ExtractionResult(
            profile=profile,
            discarded=base.discarded + discarded,
            warnings=base.warnings + warnings,
            extractor_model_id=fingerprint or base.extractor_model_id,
        )

    async def extract_candidate(self, document: ExtractionInput) -> ExtractionResult:
        """A CV's competencies, from the same pass.

        The structural extractor produces nothing here — a CV is free text with
        no adapter upstream that already parsed it — so unlike the job side this
        is the only thing in the profile. That makes the degraded path more
        visible, not different: an empty competency list with a warning saying
        why, never an empty list that reads as "this person has no skills".
        """
        base = await self._structural.extract_candidate(document)
        assert isinstance(base.profile, CandidateProfile)

        try:
            mentions, warnings, discarded, fingerprint = await self._competencies(document)
        except Exception:
            logger.warning("ml-service extraction failed; storing structural only", exc_info=True)
            return self._degraded(base)

        return ExtractionResult(
            profile=base.profile.model_copy(update={"competencies": mentions}),
            discarded=base.discarded + discarded,
            warnings=base.warnings + warnings,
            extractor_model_id=fingerprint or base.extractor_model_id,
        )

    def _degraded(self, base: ExtractionResult) -> ExtractionResult:
        """The structural result, saying plainly that the model did not run.

        The warning is the point. Without it a profile with no competencies is
        indistinguishable from a document that mentioned no skills, and only one
        of those is worth re-running later.
        """
        return ExtractionResult(
            profile=base.profile,
            discarded=base.discarded
            + [
                DiscardedField(
                    kind="competencies",
                    outcome=FieldOutcome.REVIEW,
                    reason=FAILURE_ML_SERVICE,
                )
            ],
            warnings=base.warnings + ["ml-service unavailable; competencies were not extracted"],
            extractor_model_id=base.extractor_model_id,
        )
