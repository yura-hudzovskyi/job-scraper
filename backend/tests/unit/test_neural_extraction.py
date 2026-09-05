"""What the model is allowed to add to a profile, and what happens when it cannot.

Two properties carry the weight here. Spec 3.5.2 condition 1: a competency with
no valid evidence span is not stored, whatever the model said about it. And spec
24.0 invariant 1: a model outage costs competencies, never the pipeline — the
structural profile still lands, and says out loud that the model did not run,
because "no competencies found" and "competencies not looked for" are different
facts and only one is worth retrying.
"""

import uuid

import pytest

from app.domain.documents.parsing import parse_plain_text
from app.domain.profiles.extraction import ExtractionInput
from app.domain.profiles.neural import COMPETENCY_LABELS, FAILURE_ML_SERVICE, NeuralExtractor
from app.domain.profiles.schemas import CompetencyCategory, JobProfile, RequirementKind
from app.integrations.ml_service import (
    ExtractedDocument,
    ExtractedEntity,
    ExtractionBatch,
    MlServiceClient,
)

TEXT = (
    "Senior Python Developer\n\n"
    "We need strong Python and PostgreSQL skills. Docker is required.\n"
    "Salary 5000 USD."
)


def _document(text: str = TEXT, **kwargs: object) -> ExtractionInput:
    parsed = parse_plain_text(text)
    return ExtractionInput(
        parsed_text=parsed.text,
        blocks=parsed.blocks,
        language="en",
        known_fields=kwargs.get("known_fields", {"title": "Senior Python Developer"}),  # type: ignore[arg-type]
    )


def _entity(needle: str, text: str, label: str = "technology", confidence: float = 0.9):
    start = text.index(needle)
    return ExtractedEntity(
        label=label,
        text=needle,
        start_char=start,
        end_char=start + len(needle),
        confidence=confidence,
    )


class _FakeClient:
    def __init__(self, *entities: ExtractedEntity, rejected: int = 0, truncated: bool = False):
        self._entities = list(entities)
        self._rejected = rejected
        self._truncated = truncated
        self.requests: list[tuple[list[tuple[str, str]], list[str]]] = []

    async def extract(
        self,
        documents: list[tuple[str, str]],
        labels: list[str],
        threshold: float | None = None,
    ) -> ExtractionBatch:
        self.requests.append((documents, labels))
        return ExtractionBatch(
            extractor_model_id="fastino/gliner2-multi-v1",
            extractor_revision="c6296e25603e4d31f68ef8a9f4edb73421d1e45a",
            documents=[
                ExtractedDocument(
                    id=documents[0][0],
                    entities=self._entities,
                    rejected_spans=self._rejected,
                    truncated=self._truncated,
                )
            ],
        )


class _DeadClient:
    async def extract(self, *args: object, **kwargs: object) -> ExtractionBatch:
        raise ConnectionError("ml-service is not answering")


def _extractor(client: object) -> NeuralExtractor:
    return NeuralExtractor(client)  # type: ignore[arg-type]


# --- what the model adds -----------------------------------------------------


@pytest.mark.asyncio
async def test_competencies_come_from_the_model_with_its_own_confidence() -> None:
    """Not a stub value (spec 3.5.2) — a low-confidence mention has to stay
    visibly low-confidence all the way into storage."""
    document = _document()
    client = _FakeClient(
        _entity("Python", document.parsed_text, confidence=0.97),
        _entity("PostgreSQL", document.parsed_text, confidence=0.62),
    )

    result = await _extractor(client).extract_job(document)

    assert isinstance(result.profile, JobProfile)
    assert [(c.raw_text, c.confidence) for c in result.profile.competencies] == [
        ("Python", 0.97),
        ("PostgreSQL", 0.62),
    ]


@pytest.mark.asyncio
async def test_every_competency_quotes_the_document_it_came_from() -> None:
    document = _document()
    client = _FakeClient(_entity("Docker", document.parsed_text, label="tool"))

    result = await _extractor(client).extract_job(document)

    mention = result.profile.competencies[0]
    assert mention.evidence is not None
    span = mention.evidence
    assert document.parsed_text[span.start_char : span.end_char] == "Docker"
    assert mention.category is CompetencyCategory.TOOL


@pytest.mark.asyncio
async def test_the_structural_fields_survive_untouched() -> None:
    """Spec 24 Phase 3 task 2 — deterministic parsing keeps running unchanged for
    the fields it already handles. The model adds; it does not replace."""
    document = _document()
    structural_only = await _extractor(_FakeClient()).extract_job(
        ExtractionInput(parsed_text=document.parsed_text, known_fields={"salary_min": 5000.0})
    )
    with_model = await _extractor(_FakeClient(_entity("Python", document.parsed_text))).extract_job(
        ExtractionInput(parsed_text=document.parsed_text, known_fields={"salary_min": 5000.0})
    )

    assert [r.kind for r in structural_only.profile.requirements] == [
        r.kind for r in with_model.profile.requirements
    ]


@pytest.mark.asyncio
async def test_the_profile_names_the_model_and_revision_that_produced_it() -> None:
    """A model name alone does not identify weights (spec 2.6)."""
    document = _document()
    client = _FakeClient(_entity("Python", document.parsed_text))

    result = await _extractor(client).extract_job(document)

    assert result.extractor_model_id == "fastino/gliner2-multi-v1@c6296e25603e"


@pytest.mark.asyncio
async def test_only_the_bounded_competency_labels_are_requested() -> None:
    """Spec 8.2: too many competing labels in one pass lowers recall. The set is
    also what the model comparison was measured with, so it is not free to grow
    quietly."""
    client = _FakeClient()

    await _extractor(client).extract_job(_document())

    assert client.requests[0][1] == COMPETENCY_LABELS


# --- what it refuses ---------------------------------------------------------


@pytest.mark.asyncio
async def test_a_span_that_misquotes_the_stored_text_is_dropped_not_stored() -> None:
    """ml-service validated against the text it was handed. This validates
    against the text that is actually stored — the same string today, and the
    thing that notices the day it stops being."""
    document = _document()
    liar = ExtractedEntity(
        label="technology", text="PostgreSQL", start_char=0, end_char=10, confidence=0.99
    )

    result = await _extractor(_FakeClient(liar)).extract_job(document)

    assert result.profile.competencies == []
    assert result.discarded[-1].outcome == "rejected"


@pytest.mark.asyncio
async def test_dropped_spans_are_reported_rather_than_absorbed() -> None:
    """A count that starts climbing is how a model or tokeniser change announces
    itself; silence is how it goes unnoticed for a month."""
    document = _document()
    client = _FakeClient(_entity("Python", document.parsed_text), rejected=3)

    result = await _extractor(client).extract_job(document)

    assert any("3 span(s)" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_truncation_is_recorded_on_the_profile() -> None:
    """Otherwise the absence of a skill named in the tail reads as the vacancy
    not asking for it."""
    document = _document()
    client = _FakeClient(_entity("Python", document.parsed_text), truncated=True)

    result = await _extractor(client).extract_job(document)

    assert any("truncated" in warning for warning in result.warnings)


# --- degrading -------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_unreachable_model_still_produces_the_structural_profile() -> None:
    """Spec 24.0 invariant 1. Extraction is not allowed to take the pipeline down
    with it, and a revision that failed here would never be retried by anything."""
    document = _document()
    document = ExtractionInput(
        parsed_text=document.parsed_text,
        blocks=document.blocks,
        language="en",
        known_fields={"title": "Senior Python Developer", "required_experience_years": 5.0},
    )

    result = await _extractor(_DeadClient()).extract_job(document)

    assert isinstance(result.profile, JobProfile)
    assert result.profile.competencies == []
    # The deterministic half is untouched: the fields the adapter parsed are
    # still there, which is the difference between a thinner profile and none.
    assert result.profile.display_title == "Senior Python Developer"
    assert [r.kind for r in result.profile.requirements] == [RequirementKind.EXPERIENCE]


@pytest.mark.asyncio
async def test_a_degraded_profile_says_the_model_did_not_run() -> None:
    """ "No competencies found" and "competencies not looked for" are different
    facts (spec 5.1 step 10), and only one of them is worth re-running."""
    result = await _extractor(_DeadClient()).extract_job(_document())

    assert any(field.reason == FAILURE_ML_SERVICE for field in result.discarded)
    assert any("unavailable" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_a_degraded_profile_is_not_labelled_as_the_model_s_work() -> None:
    """Storing the model id for a profile it did not produce would make the
    corpus impossible to re-extract selectively later."""
    result = await _extractor(_DeadClient()).extract_job(_document())

    assert result.extractor_model_id == "structural/1.0"


@pytest.mark.asyncio
async def test_an_empty_document_does_not_reach_the_model() -> None:
    """A forward pass on whitespace costs two seconds and can only return
    nothing."""
    client = _FakeClient()

    await _extractor(client).extract_job(ExtractionInput(parsed_text="   "))

    assert client.requests == []


# --- the candidate side ------------------------------------------------------


@pytest.mark.asyncio
async def test_a_cv_gets_competencies_where_the_structural_extractor_gave_none() -> None:
    """The structural extractor produces nothing for a CV — there is no adapter
    upstream that already parsed it. This is the whole gain on that side."""
    cv = "I have worked with Python and Kubernetes for six years."
    document = ExtractionInput(parsed_text=cv)
    client = _FakeClient(_entity("Kubernetes", cv))

    result = await _extractor(client).extract_candidate(document)

    assert [c.raw_text for c in result.profile.competencies] == ["Kubernetes"]


@pytest.mark.asyncio
async def test_a_failed_cv_extraction_does_not_read_as_a_candidate_with_no_skills() -> None:
    document = ExtractionInput(parsed_text="I have worked with Python for six years.")

    result = await _extractor(_DeadClient()).extract_candidate(document)

    assert result.profile.competencies == []
    assert any("unavailable" in warning for warning in result.warnings)


def test_the_client_fingerprint_identifies_weights_not_just_a_name() -> None:
    batch = ExtractionBatch(
        extractor_model_id="fastino/gliner2-multi-v1",
        extractor_revision="c6296e25603e4d31f68ef8a9f4edb73421d1e45a",
        documents=[],
    )

    assert batch.model_fingerprint == "fastino/gliner2-multi-v1@c6296e25603e"


def test_the_client_url_tolerates_a_trailing_slash() -> None:
    """A trailing slash in an env var would otherwise produce //extract."""
    assert MlServiceClient("http://ml-service:8100/")._base_url == "http://ml-service:8100"
    assert str(uuid.uuid4())  # keeps the import honest
