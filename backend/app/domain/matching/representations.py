"""Field-level projections of a vacancy and a candidate — spec 10.2.

Today both sides are one string each, and the evaluation set says what that
costs: P@10 is 1.00 and Recall@100 is 0.58. The top of the ranking is right and
two fifths of what the user wants never reaches the first hundred. A single
vector over four thousand characters is dominated by whichever part is longest,
which in a vacancy is the company's description of itself — so a match that
lives entirely in a skills list has to outshout the boilerplate around it.

Splitting the document into fields gives each aspect its own vector, so a
vacancy can surface on its competencies alone. That is the mechanism 10.2
describes and the one the recall number points at.

Two rules the templates obey.

Deterministic, and versioned. `TEMPLATE_VERSION` is part of a vector's identity
alongside the model id, because a changed template produces a different vector
from the same document, and a corpus embedded under two templates is a corpus
where distances mean two things.

No PII. Spec 18.1 forbids embedding names, email, phone or address, and this is
where that is enforced rather than hoped for — the free-text fields go through
`redact()` on the way in. That is not theoretical tidying: the representation
being replaced sends the candidate's email address and phone number to a third
party on every run.
"""

import re
from collections.abc import Iterable
from enum import StrEnum

from app.domain.candidates.models import UserPreference
from app.domain.documents.redaction import redact
from app.domain.jobs.models import NormalizedJob
from app.domain.profiles.schemas import CandidateProfile, JobProfile, Necessity

# Bumped whenever a template changes what it emits. Part of the stored vector's
# identity — see EmbeddingRepository.
TEMPLATE_VERSION = "repr/1.0"

# Per-field caps. Much smaller than the single-document limits they replace,
# because that is the point: a field is one aspect, and a field that runs to
# four thousand characters has stopped being one.
MAX_FIELD_CHARS = 1200
# `full_profile` stays long — it is the general-purpose vector and the one
# today's ranking uses, so shrinking it would change the baseline being measured
# against rather than adding to it.
MAX_FULL_CHARS = 4000


class Field(StrEnum):
    """The projections of 10.2. `full_profile` is what exists today."""

    OCCUPATION = "occupation"
    COMPETENCIES = "competencies"
    EXPERIENCE = "experience"
    RESPONSIBILITIES = "responsibilities"
    FULL_PROFILE = "full_profile"


def _collapse(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", text)).strip()


def _section(label: str, value: str) -> str:
    value = value.strip()
    return f"[{label}]\n{value}" if value else ""


def _joined(values: Iterable[str], separator: str = "; ") -> str:
    seen: list[str] = []
    for value in values:
        cleaned = (value or "").strip()
        # Deduplicated because a vacancy that says "Python" six times is one
        # fact about the vacancy, and repetition is what an embedding reads as
        # emphasis.
        if cleaned and cleaned.casefold() not in {item.casefold() for item in seen}:
            seen.append(cleaned)
    return separator.join(seen)


def _document(sections: Iterable[str], limit: int) -> str:
    body = "\n\n".join(section for section in sections if section)
    return _collapse(body)[:limit]


def _competency_sections(
    competencies: Iterable[object], canonical: Iterable[str] = ()
) -> list[str]:
    """Competencies grouped by necessity, plus the taxonomy's names for them.

    10.2 asks for raw *and* canonical: the raw mention is what the vacancy
    actually wrote, and the canonical label is what the taxonomy calls it. Both
    are kept because they retrieve differently — "M.E.Doc" matches a document
    that names it, "accounting software" matches one that does not.
    """
    required: list[str] = []
    preferred: list[str] = []
    unspecified: list[str] = []
    for mention in competencies:
        raw = getattr(mention, "raw_text", "")
        necessity = getattr(mention, "necessity", None)
        if necessity is Necessity.REQUIRED:
            required.append(raw)
        elif necessity is Necessity.PREFERRED:
            preferred.append(raw)
        else:
            unspecified.append(raw)

    return [
        _section("REQUIRED COMPETENCIES", _joined(required)),
        _section("PREFERRED COMPETENCIES", _joined(preferred)),
        _section("COMPETENCIES", _joined(unspecified)),
        _section("CANONICAL COMPETENCIES", _joined(canonical)),
    ]


def job_representations(
    job: NormalizedJob,
    profile: JobProfile | None = None,
    canonical_competencies: Iterable[str] = (),
    occupations: Iterable[str] = (),
) -> dict[Field, str]:
    """One text per field for a vacancy. Fields with nothing to say are absent.

    Absent rather than empty on purpose: an empty string still embeds, to a
    vector that means "nothing", and a corpus where half the vacancies have a
    nothing-vector in the competencies field would rank those vacancies against
    each other on it.
    """
    salary = ""
    if job.salary and (job.salary.min or job.salary.max):
        bounds = "-".join(str(int(v)) for v in (job.salary.min, job.salary.max) if v)
        salary = f"{bounds} {job.salary.currency or ''}".strip()

    experience = (
        f"{job.required_experience_years:g}+ years required"
        if job.required_experience_years
        else ""
    )
    description = redact(_collapse(job.description))

    fields = {
        Field.OCCUPATION: _document(
            [
                _section("ROLE", job.title),
                _section("SENIORITY", job.seniority or ""),
                _section("OCCUPATIONS", _joined(occupations)),
            ],
            MAX_FIELD_CHARS,
        ),
        Field.COMPETENCIES: _document(
            _competency_sections(profile.competencies if profile else (), canonical_competencies),
            MAX_FIELD_CHARS,
        ),
        Field.EXPERIENCE: _document([_section("EXPERIENCE", experience)], MAX_FIELD_CHARS),
        Field.RESPONSIBILITIES: _document(
            [_section("RESPONSIBILITIES", "\n".join(profile.responsibilities) if profile else "")],
            MAX_FIELD_CHARS,
        ),
        Field.FULL_PROFILE: _document(
            [
                _section("ROLE", job.title),
                _section("SENIORITY", job.seniority or ""),
                _section("EXPERIENCE", experience),
                _section("WORK FORMAT", "remote" if job.location.remote else "on-site or hybrid"),
                _section("COMPENSATION", salary),
                _section("DESCRIPTION", description),
            ],
            MAX_FULL_CHARS,
        ),
    }
    return {field: text for field, text in fields.items() if text}


def candidate_representations(
    cv_text: str,
    preferences: UserPreference | None = None,
    profile: CandidateProfile | None = None,
    canonical_competencies: Iterable[str] = (),
) -> dict[Field, str]:
    """One text per field for a candidate, with the PII taken out.

    The company name and job titles in an employment history are not PII and
    stay; the contact block at the top of every CV is, and goes. `redact`
    handles email and phone, which are what 18.1 names and what a CV actually
    carries — it does not attempt to find the person's name, because a name
    detector is the hardcoded language knowledge 25.3 rules out, and guessing
    wrong deletes a company or a technology instead.
    """
    roles = _joined(preferences.preferred_roles) if preferences else ""
    stack = _joined(preferences.preferred_stack) if preferences else ""
    formats = _joined(preferences.work_formats) if preferences else ""
    cv = redact(_collapse(cv_text))

    fields = {
        Field.OCCUPATION: _document(
            [_section("LOOKING FOR", roles), _section("WORK FORMAT", formats)],
            MAX_FIELD_CHARS,
        ),
        Field.COMPETENCIES: _document(
            [
                _section("PREFERRED STACK", stack),
                *_competency_sections(
                    profile.competencies if profile else (), canonical_competencies
                ),
            ],
            MAX_FIELD_CHARS,
        ),
        Field.EXPERIENCE: _document([_section("CV", cv)], MAX_FULL_CHARS),
        Field.FULL_PROFILE: _document(
            [
                _section("LOOKING FOR", roles),
                _section("PREFERRED STACK", stack),
                _section("WORK FORMAT", formats),
                _section("CV", cv),
            ],
            MAX_FULL_CHARS,
        ),
    }
    return {field: text for field, text in fields.items() if text}
