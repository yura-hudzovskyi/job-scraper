"""The extractor that ships before the model does.

Two things it must get right: never claim a requirement is explicit when the
document does not say it (an explicit requirement is allowed to become a hard
filter, so a wrong one silently removes vacancies), and never invent a fact it
was not handed.
"""

import pytest

from app.domain.profiles.extraction import (
    ExtractionInput,
    FieldOutcome,
    find_span,
    reject_unresolvable_spans,
)
from app.domain.profiles.schemas import (
    CandidateProfile,
    EvidenceSpan,
    JobProfile,
    Requirement,
    RequirementKind,
)
from app.domain.profiles.structural import RULESET_VERSION, StructuralExtractor

TEXT = "Потрібен розробник з досвідом 3 роки. Вилка 4000-6000 USD. Формат remote."


def _input(**known: object) -> ExtractionInput:
    return ExtractionInput(parsed_text=TEXT, language="uk", known_fields=dict(known))


# --- locating evidence -------------------------------------------------------


def test_a_value_present_in_the_document_gets_a_span_that_quotes_it() -> None:
    span = find_span(TEXT, "3")

    assert span is not None
    assert span.validate_against(TEXT)
    assert span.text == "3"


def test_locating_is_case_insensitive_but_quotes_the_original_casing() -> None:
    span = find_span("We need REMOTE work", "remote")

    assert span is not None
    assert span.text == "REMOTE"
    assert span.validate_against("We need REMOTE work")


def test_a_value_absent_from_the_document_gets_no_span() -> None:
    assert find_span(TEXT, "Kubernetes") is None


def test_locating_survives_case_folding_that_changes_length() -> None:
    """"İ".casefold() is two characters. An offset taken from the folded string
    would point into the wrong place in the original, so the fallback matters."""
    text = "İstanbul office"

    span = find_span(text, "office")

    assert span is not None
    assert span.validate_against(text)


def test_locating_nothing_in_nothing_is_not_an_error() -> None:
    assert find_span("", "x") is None
    assert find_span("text", "") is None


# --- explicit vs derived -----------------------------------------------------


@pytest.mark.asyncio
async def test_experience_stated_in_the_text_is_explicit_with_evidence() -> None:
    result = await StructuralExtractor().extract_job(_input(required_experience_years=3.0))
    profile = result.profile
    assert isinstance(profile, JobProfile)

    requirement = next(
        r for r in profile.requirements if r.kind is RequirementKind.EXPERIENCE
    )
    assert requirement.explicit is True
    assert requirement.evidence is not None
    assert requirement.evidence.validate_against(TEXT)
    assert requirement.value == {"years_min": 3.0}


@pytest.mark.asyncio
async def test_a_value_the_document_never_states_is_not_explicit() -> None:
    """It may still be right — it just cannot become a hard filter, because
    there is no sentence to point at when it removes a vacancy."""
    result = await StructuralExtractor().extract_job(_input(required_experience_years=9.0))
    profile = result.profile
    assert isinstance(profile, JobProfile)

    requirement = profile.requirements[0]
    assert requirement.explicit is False
    assert requirement.evidence is None


@pytest.mark.asyncio
async def test_employment_type_is_always_derived() -> None:
    """The adapters set it to a constant rather than reading it, so calling it
    explicit would be a claim the source never made."""
    result = await StructuralExtractor().extract_job(_input(employment_type="full_time"))
    profile = result.profile
    assert isinstance(profile, JobProfile)

    requirement = next(
        r for r in profile.requirements if r.kind is RequirementKind.EMPLOYMENT_TYPE
    )
    assert requirement.explicit is False


@pytest.mark.asyncio
async def test_a_salary_written_in_the_text_carries_its_evidence() -> None:
    result = await StructuralExtractor().extract_job(
        _input(salary_min=4000.0, salary_max=6000.0, salary_currency="USD")
    )
    profile = result.profile
    assert isinstance(profile, JobProfile)

    requirement = next(
        r for r in profile.requirements if r.kind is RequirementKind.COMPENSATION
    )
    assert requirement.explicit is True
    assert requirement.evidence is not None
    assert requirement.evidence.text == "6000"


# --- what it refuses ---------------------------------------------------------


@pytest.mark.asyncio
async def test_an_impossible_salary_range_is_rejected_with_a_reason() -> None:
    result = await StructuralExtractor().extract_job(_input(salary_min=6000.0, salary_max=1.0))
    profile = result.profile
    assert isinstance(profile, JobProfile)

    assert not [r for r in profile.requirements if r.kind is RequirementKind.COMPENSATION]
    assert result.discarded[0].outcome is FieldOutcome.REJECTED
    assert "above its ceiling" in result.discarded[0].reason


@pytest.mark.asyncio
async def test_negative_experience_is_rejected_rather_than_stored() -> None:
    result = await StructuralExtractor().extract_job(_input(required_experience_years=-1.0))

    assert result.profile.quality.overall_confidence == 0.0
    assert result.discarded[0].reason.startswith("negative years")


@pytest.mark.asyncio
async def test_nothing_known_produces_an_empty_profile_rather_than_a_guess() -> None:
    result = await StructuralExtractor().extract_job(_input())
    profile = result.profile
    assert isinstance(profile, JobProfile)

    assert profile.requirements == []
    assert profile.competencies == []


@pytest.mark.asyncio
async def test_it_does_not_read_competencies_from_the_text() -> None:
    """Recognising "розробник" as an occupation is semantic work, and inventing
    keyword rules for it is what this phase's definition of done forbids."""
    result = await StructuralExtractor().extract_job(_input(required_experience_years=3.0))
    profile = result.profile
    assert isinstance(profile, JobProfile)

    assert profile.competencies == []
    assert profile.responsibilities == []


# --- truncation and provenance ----------------------------------------------


@pytest.mark.asyncio
async def test_a_truncated_document_says_so_on_the_profile() -> None:
    document = ExtractionInput(parsed_text=TEXT, truncated=True)

    result = await StructuralExtractor().extract_job(document)

    assert result.profile.quality.document_truncated is True
    assert any("truncated" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_the_ruleset_version_identifies_what_produced_the_profile() -> None:
    result = await StructuralExtractor().extract_job(_input())

    assert result.extractor_model_id == RULESET_VERSION


@pytest.mark.asyncio
async def test_the_detected_language_reaches_the_profile() -> None:
    result = await StructuralExtractor().extract_job(_input(required_experience_years=3.0))

    assert result.profile.language == "uk"


# --- the candidate side ------------------------------------------------------


@pytest.mark.asyncio
async def test_a_cv_extracts_nothing_and_says_why() -> None:
    """Pretending otherwise would put a fabricated skill list in front of a user,
    which is the failure that got the previous extraction layer removed."""
    result = await StructuralExtractor().extract_candidate(
        ExtractionInput(parsed_text="Experience with Python", language="en")
    )
    profile = result.profile

    assert isinstance(profile, CandidateProfile)
    assert profile.competencies == []
    assert profile.quality.overall_confidence == 0.0
    assert profile.quality.warnings


# --- the last line of defence ------------------------------------------------


def test_a_requirement_whose_span_misquotes_the_document_is_dropped() -> None:
    """Self-consistent but wrong: the offsets and the quote agree with each other
    and point at the wrong place. Only the document settles it."""
    misquoting = Requirement(
        kind=RequirementKind.COMPETENCY,
        value={"label": "Python"},
        evidence=EvidenceSpan(start_char=0, end_char=6, text="Python"),
    )

    kept, discarded = reject_unresolvable_spans([misquoting], TEXT)

    assert kept == []
    assert discarded[0].outcome is FieldOutcome.REJECTED
    assert "does not quote the document" in discarded[0].reason


def test_a_requirement_with_a_correct_span_survives() -> None:
    correct = Requirement(
        kind=RequirementKind.COMPETENCY,
        value={},
        evidence=EvidenceSpan(start_char=0, end_char=8, text=TEXT[0:8]),
    )

    kept, discarded = reject_unresolvable_spans([correct], TEXT)

    assert kept == [correct]
    assert discarded == []
