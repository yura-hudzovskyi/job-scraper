"""The extractor that ships before the model does.

It adds no understanding of its own. Every value it emits was already parsed by
a source adapter — title, seniority, salary, employment type, remote, required
years — and all this does is give each one the `Requirement` shape, an evidence
span where the document actually says it, and `explicit=False` where it does
not. Spec 24 Phase 3 task 2: deterministic parsing keeps running unchanged for
the fields it already handles.

What it deliberately does not do is read the text. No competencies, no
responsibilities, no necessity from section headings — those need semantic
recognition, that is GLiNER2's job (spec 3.5.2), and inventing keyword rules for
them here is exactly what the phase's definition of done forbids. So a profile
from this extractor is thin and honest rather than full and guessed.

The candidate side extracts nothing at all, for the same reason: a CV is free
text, and there is no adapter upstream that has already parsed facts out of it.
Saying so costs nothing; pretending otherwise would put a fabricated skill list
in front of a user, which is the failure that got the previous extraction layer
removed.
"""

from typing import Any

from app.domain.profiles.extraction import (
    DiscardedField,
    ExtractionInput,
    ExtractionResult,
    FieldOutcome,
    find_span,
    reject_unresolvable_spans,
)
from app.domain.profiles.schemas import (
    CandidateProfile,
    JobProfile,
    Necessity,
    ProfileQuality,
    Requirement,
    RequirementKind,
    RequirementOperator,
)

# Bump when the rules below change what they emit, so a stored revision can be
# told apart from one produced by different logic. Plays the role
# `extractor_model_id` plays for a model.
RULESET_VERSION = "structural/1.0"


class StructuralExtractor:
    """Implements ProfileExtractor over facts the adapter already established."""

    async def extract_job(self, document: ExtractionInput) -> ExtractionResult:
        known = document.known_fields
        requirements: list[Requirement] = []
        discarded: list[DiscardedField] = []

        for build in (
            _experience_requirement,
            _seniority_requirement,
            _employment_type_requirement,
            _remote_requirement,
            _salary_requirement,
        ):
            requirement, discard = build(known, document.parsed_text)
            if requirement is not None:
                requirements.append(requirement)
            if discard is not None:
                discarded.append(discard)

        requirements, span_discards = reject_unresolvable_spans(
            requirements, document.parsed_text
        )
        discarded.extend(span_discards)

        warnings: list[str] = []
        if document.truncated:
            warnings.append(
                "the document was truncated before parsing finished; requirements "
                "after the cut-off were never seen"
            )

        profile = JobProfile(
            language=document.language,
            display_title=known.get("title") or None,
            seniority=known.get("seniority") or None,
            requirements=requirements,
            quality=ProfileQuality(
                # Every value here came from deterministic parsing, so there is no
                # model uncertainty to report. This is not a claim that the values
                # are right — only that nothing guessed at them.
                overall_confidence=1.0 if requirements else 0.0,
                warnings=warnings,
                document_truncated=document.truncated,
            ),
        )
        return ExtractionResult(
            profile=profile,
            discarded=discarded,
            warnings=warnings,
            extractor_model_id=RULESET_VERSION,
        )

    async def extract_candidate(self, document: ExtractionInput) -> ExtractionResult:
        """Nothing is extracted from a CV until there is a model to read it.

        The revision is still created, carrying the detected language and an
        explicit warning, so the review flow has something to show and the gap is
        visible rather than looking like an extraction that found nothing.
        """
        return ExtractionResult(
            profile=CandidateProfile(
                language=document.language,
                quality=ProfileQuality(
                    overall_confidence=0.0,
                    warnings=[
                        (
                            "no candidate extraction is configured yet; competencies "
                            "are read at match time from the CV text, as before"
                        )
                    ],
                    document_truncated=document.truncated,
                ),
            ),
            extractor_model_id=RULESET_VERSION,
        )


def _experience_requirement(
    known: dict[str, Any], parsed_text: str
) -> tuple[Requirement | None, DiscardedField | None]:
    years = known.get("required_experience_years")
    if years is None:
        return None, None
    if years < 0:
        return None, DiscardedField(
            kind=RequirementKind.EXPERIENCE.value,
            outcome=FieldOutcome.REJECTED,
            reason="negative years of experience is not a possible value",
            raw_value=str(years),
        )

    # A whole number is written "3", not "3.0", and the document says whichever
    # the source wrote. Try the natural rendering first.
    rendered = f"{years:g}"
    span = find_span(parsed_text, rendered)
    return (
        Requirement(
            kind=RequirementKind.EXPERIENCE,
            necessity=Necessity.REQUIRED,
            operator=RequirementOperator.AT_LEAST,
            value={"years_min": years},
            explicit=span is not None,
            evidence=span,
        ),
        None,
    )


def _seniority_requirement(
    known: dict[str, Any], parsed_text: str
) -> tuple[Requirement | None, DiscardedField | None]:
    seniority = known.get("seniority")
    if not seniority:
        return None, None
    span = find_span(parsed_text, str(seniority))
    return (
        Requirement(
            kind=RequirementKind.EXPERIENCE,
            necessity=Necessity.UNSPECIFIED,
            value={"seniority": seniority},
            # The adapter guessed this from the title, and the title is not part
            # of the description text, so a span is usually absent. Marked
            # explicit only when the body really says the word.
            explicit=span is not None,
            evidence=span,
        ),
        None,
    )


def _employment_type_requirement(
    known: dict[str, Any], parsed_text: str
) -> tuple[Requirement | None, DiscardedField | None]:
    employment_type = known.get("employment_type")
    if not employment_type:
        return None, None
    return (
        Requirement(
            kind=RequirementKind.EMPLOYMENT_TYPE,
            value={"employment_type": employment_type},
            # Adapters currently set this to a constant rather than reading it,
            # so it is derived by definition and can never be a hard filter.
            explicit=False,
        ),
        None,
    )


def _remote_requirement(
    known: dict[str, Any], parsed_text: str
) -> tuple[Requirement | None, DiscardedField | None]:
    if "remote" not in known:
        return None, None
    return (
        Requirement(
            kind=RequirementKind.LOCATION,
            value={"remote": bool(known["remote"])},
            explicit=False,
        ),
        None,
    )


def _salary_requirement(
    known: dict[str, Any], parsed_text: str
) -> tuple[Requirement | None, DiscardedField | None]:
    minimum, maximum = known.get("salary_min"), known.get("salary_max")
    if minimum is None and maximum is None:
        return None, None
    if minimum is not None and maximum is not None and minimum > maximum:
        return None, DiscardedField(
            kind=RequirementKind.COMPENSATION.value,
            outcome=FieldOutcome.REJECTED,
            reason="salary floor is above its ceiling",
            raw_value=f"{minimum}-{maximum}",
        )

    anchor = maximum if maximum is not None else minimum
    span = find_span(parsed_text, f"{anchor:g}") if anchor is not None else None
    return (
        Requirement(
            kind=RequirementKind.COMPENSATION,
            value={
                "min": minimum,
                "max": maximum,
                "currency": known.get("salary_currency"),
            },
            explicit=span is not None,
            evidence=span,
        ),
        None,
    )
