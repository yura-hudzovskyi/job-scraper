"""What the profile schemas refuse to store.

The point of validating here is that `extracted_profile` is a JSONB column: once
a malformed profile is in it, nothing downstream will notice until an evidence
span quotes the wrong text to a user. So most of these test rejection.
"""

import pytest
from pydantic import ValidationError

from app.domain.profiles.schemas import (
    JOB_PROFILE_SCHEMA_VERSION,
    CandidateProfile,
    CompetencyCategory,
    ConceptMention,
    EvidenceSpan,
    JobProfile,
    LinkStatus,
    Necessity,
    Requirement,
    RequirementKind,
    spans_of,
)

TEXT = "Вимоги\nPython\n3 роки досвіду"


def _span(start: int, end: int) -> EvidenceSpan:
    return EvidenceSpan(start_char=start, end_char=end, text=TEXT[start:end])


# --- evidence spans ----------------------------------------------------------


def test_a_span_quoting_its_document_validates_against_it() -> None:
    span = _span(7, 13)

    assert span.text == "Python"
    assert span.validate_against(TEXT) is True


def test_a_self_consistent_span_pointing_at_the_wrong_place_is_caught() -> None:
    """Construction cannot catch this — the offsets and the quote agree with each
    other. Only the document settles it, which is why storage checks too."""
    span = EvidenceSpan(start_char=0, end_char=6, text="Python")

    assert span.validate_against(TEXT) is False


def test_a_span_that_ends_before_it_starts_is_rejected() -> None:
    with pytest.raises(ValidationError):
        EvidenceSpan(start_char=10, end_char=4, text="oops")


def test_a_zero_length_span_is_rejected() -> None:
    with pytest.raises(ValidationError):
        EvidenceSpan(start_char=5, end_char=5, text="")


def test_a_negative_offset_is_rejected() -> None:
    with pytest.raises(ValidationError):
        EvidenceSpan(start_char=-1, end_char=4, text="Pyth")


def test_a_span_whose_length_disagrees_with_its_quote_is_rejected() -> None:
    """The mismatch that would otherwise surface as a truncated quote in the UI."""
    with pytest.raises(ValidationError, match="covers 6 characters but quotes 4"):
        EvidenceSpan(start_char=0, end_char=6, text="Pyth")


# --- requirements ------------------------------------------------------------


def test_an_explicit_requirement_needs_evidence() -> None:
    """Marked explicit is a claim that the text says this. Without a span there
    is nothing to show a user who asks why."""
    with pytest.raises(ValidationError, match="explicit but carries no evidence"):
        Requirement(kind=RequirementKind.EXPERIENCE, value={"years_min": 3})


def test_a_derived_requirement_may_omit_evidence_if_it_says_so() -> None:
    requirement = Requirement(
        kind=RequirementKind.EMPLOYMENT_TYPE,
        value={"employment_type": "full_time"},
        explicit=False,
    )

    assert requirement.evidence is None
    assert requirement.explicit is False


def test_an_explicit_requirement_with_evidence_is_accepted() -> None:
    requirement = Requirement(
        kind=RequirementKind.COMPETENCY,
        necessity=Necessity.REQUIRED,
        value={"label": "Python"},
        evidence=_span(7, 13),
    )

    assert requirement.evidence is not None
    assert requirement.evidence.validate_against(TEXT)


def test_confidence_outside_zero_to_one_is_rejected() -> None:
    for bad in (-0.1, 1.5):
        with pytest.raises(ValidationError):
            Requirement(
                kind=RequirementKind.OTHER,
                explicit=False,
                confidence=bad,
            )


# --- profiles ----------------------------------------------------------------


def test_a_job_profile_carries_its_schema_version_by_default() -> None:
    assert JobProfile().schema_version == JOB_PROFILE_SCHEMA_VERSION


def test_an_unknown_field_is_rejected_rather_than_silently_stored() -> None:
    """An extractor emitting a field nobody reads should fail at the boundary,
    not have it quietly land in JSONB."""
    with pytest.raises(ValidationError):
        JobProfile(seniorityy="senior")  # type: ignore[call-arg]


def test_a_profile_round_trips_through_json() -> None:
    """This is how it reaches the JSONB column and comes back."""
    profile = JobProfile(
        display_title="Backend Engineer",
        requirements=[
            Requirement(
                kind=RequirementKind.COMPETENCY,
                value={"label": "Python"},
                evidence=_span(7, 13),
            )
        ],
    )

    restored = JobProfile.model_validate(profile.model_dump(mode="json"))

    assert restored == profile


def test_a_mention_starts_unmapped_until_phase_four_links_it() -> None:
    mention = ConceptMention(raw_text="Python", category=CompetencyCategory.TECHNOLOGY)

    assert mention.link_status is LinkStatus.UNMAPPED
    assert mention.concept_id is None


def test_spans_of_walks_requirements_and_competencies() -> None:
    profile = JobProfile(
        requirements=[
            Requirement(kind=RequirementKind.COMPETENCY, value={}, evidence=_span(0, 6))
        ],
        competencies=[ConceptMention(raw_text="Python", evidence=_span(7, 13))],
    )

    assert len(spans_of(profile)) == 2
    assert all(span.validate_against(TEXT) for span in spans_of(profile))


def test_spans_of_handles_a_candidate_profile() -> None:
    profile = CandidateProfile(competencies=[ConceptMention(raw_text="Python")])

    assert spans_of(profile) == []


def test_a_profile_is_not_marked_reviewed_until_someone_reviews_it() -> None:
    """Spec 3.5.2 condition 2 — extracted facts must not reach a score before a
    candidate has confirmed them."""
    assert CandidateProfile().quality.user_reviewed is False
