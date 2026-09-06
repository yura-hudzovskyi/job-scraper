"""Field projections — spec 10.2 — and the PII rule they enforce.

The reason this module exists is a number: P@10 is 1.00 and Recall@100 is 0.58
on the real evaluation set. The top of the ranking is right and two fifths of
what the user wants never reaches the first hundred, which is what one vector
over a whole vacancy does — the longest section wins, and in a vacancy that is
the company describing itself.
"""

from app.domain.candidates.models import UserPreference
from app.domain.jobs.models import EmploymentType, JobLocation, NormalizedJob, SalaryRange
from app.domain.matching.representations import (
    TEMPLATE_VERSION,
    Field,
    candidate_representations,
    job_representations,
)
from app.domain.profiles.schemas import (
    ConceptMention,
    JobProfile,
    Necessity,
)


def _job(**overrides: object) -> NormalizedJob:
    defaults: dict[str, object] = {
        "source": "dou",
        "external_id": "1",
        "url": "https://example.com/1",
        "title": "Senior Full-Stack Engineer",
        "company": "Acme",
        "description": "We build things. " * 40,
        "employment_type": EmploymentType.FULL_TIME,
        "location": JobLocation(remote=True),
        "salary": SalaryRange(min=4000, max=6000, currency="USD"),
        "seniority": "senior",
        "required_experience_years": 5.0,
    }
    defaults.update(overrides)
    return NormalizedJob(**defaults)  # type: ignore[arg-type]


def _mention(text: str, necessity: Necessity = Necessity.UNSPECIFIED) -> ConceptMention:
    return ConceptMention(raw_text=text, necessity=necessity)


# --- the split itself --------------------------------------------------------


def test_a_vacancy_becomes_several_fields_not_one_blob() -> None:
    profile = JobProfile(competencies=[_mention("Python"), _mention("React")])

    fields = job_representations(_job(), profile)

    assert Field.OCCUPATION in fields
    assert Field.COMPETENCIES in fields
    assert Field.FULL_PROFILE in fields


def test_the_competencies_field_is_not_drowned_by_the_description() -> None:
    """The whole mechanism. A vacancy whose match lives in its skills list gets
    a vector that is only its skills list."""
    profile = JobProfile(competencies=[_mention("PostgreSQL")])

    fields = job_representations(_job(description="Company blurb. " * 300), profile)

    assert "PostgreSQL" in fields[Field.COMPETENCIES]
    assert "Company blurb" not in fields[Field.COMPETENCIES]


def test_competencies_are_grouped_by_necessity() -> None:
    """Spec 10.2 asks for held/required/preferred separately — "must have Python"
    and "nice to have Python" are different claims about a vacancy."""
    profile = JobProfile(
        competencies=[
            _mention("Python", Necessity.REQUIRED),
            _mention("Kubernetes", Necessity.PREFERRED),
        ]
    )

    text = job_representations(_job(), profile)[Field.COMPETENCIES]

    assert "[REQUIRED COMPETENCIES]\nPython" in text
    assert "[PREFERRED COMPETENCIES]\nKubernetes" in text


def test_canonical_labels_travel_beside_the_raw_ones() -> None:
    """They retrieve differently: "M.E.Doc" matches a document that names it,
    "accounting software" matches one that does not."""
    profile = JobProfile(competencies=[_mention("M.E.Doc")])

    text = job_representations(_job(), profile, canonical_competencies=["accounting software"])[
        Field.COMPETENCIES
    ]

    assert "M.E.Doc" in text
    assert "accounting software" in text


def test_a_field_with_nothing_to_say_is_absent_rather_than_empty() -> None:
    """An empty string still embeds, to a vector meaning "nothing" — and a corpus
    where half the vacancies carry that vector would rank them against each
    other on it."""
    fields = job_representations(_job(), JobProfile())

    assert Field.COMPETENCIES not in fields
    assert Field.RESPONSIBILITIES not in fields


def test_a_repeated_skill_is_one_fact_not_six() -> None:
    """Repetition is what an embedding reads as emphasis."""
    profile = JobProfile(competencies=[_mention("Python") for _ in range(6)])

    text = job_representations(_job(), profile)[Field.COMPETENCIES]

    assert text.count("Python") == 1


# --- PII (spec 18.1) ---------------------------------------------------------


def test_a_candidate_s_email_and_phone_never_reach_the_representation() -> None:
    """18.1 forbids embedding email or phone. The representation this replaces
    sends both to a third party on every run."""
    cv = "YURII HUDZOVSKYI\nygudzovski@gmail.com | +380 98 014 4822\nPython, React."

    fields = candidate_representations(cv)

    for text in fields.values():
        assert "ygudzovski@gmail.com" not in text
        assert "+380 98 014 4822" not in text
    assert "Python, React." in fields[Field.FULL_PROFILE]


def test_a_vacancy_s_contact_details_are_redacted_too() -> None:
    """Recruiter contact details are in the corpus, not just in CVs."""
    job = _job(description="Send your CV to hr@acme.com or call +380 44 123 4567.")

    text = job_representations(job)[Field.FULL_PROFILE]

    assert "hr@acme.com" not in text
    assert "+380 44 123 4567" not in text


def test_skills_that_look_like_nothing_else_survive_redaction() -> None:
    """Redaction that eats a technology is worse than the leak it prevents."""
    cv = "Experience with C++, .NET 8, Node.js 20 and PostgreSQL 16."

    text = candidate_representations(cv)[Field.FULL_PROFILE]

    for skill in ("C++", ".NET 8", "Node.js 20", "PostgreSQL 16"):
        assert skill in text


# --- versioning --------------------------------------------------------------


def test_the_template_version_is_declared() -> None:
    """Part of a vector's identity: a changed template makes a different vector
    from the same document, and a corpus embedded under two templates is one
    where distances mean two things."""
    assert TEMPLATE_VERSION.startswith("repr/")


def test_the_same_document_always_renders_the_same_text() -> None:
    """Otherwise the content hash changes and the corpus re-embeds itself for
    nothing, at a cost per call."""
    profile = JobProfile(competencies=[_mention("Go"), _mention("Rust")])

    assert job_representations(_job(), profile) == job_representations(_job(), profile)


# --- the candidate side ------------------------------------------------------


def test_preferences_and_cv_land_in_different_fields() -> None:
    preferences = UserPreference(
        user_id="u", preferred_roles=["Backend Engineer"], preferred_stack=["Python"]
    )

    fields = candidate_representations("Ten years of backend work.", preferences)

    assert "Backend Engineer" in fields[Field.OCCUPATION]
    assert "Python" in fields[Field.COMPETENCIES]
    assert "Ten years of backend work." in fields[Field.EXPERIENCE]
