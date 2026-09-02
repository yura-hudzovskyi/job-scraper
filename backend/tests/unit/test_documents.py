from app.domain.candidates.models import UserPreference
from app.domain.jobs.models import EmploymentType, JobLocation, NormalizedJob, SalaryRange
from app.domain.matching.documents import (
    MAX_CV_CHARS,
    MAX_JOB_CHARS,
    RERANK_INSTRUCTION,
    job_document,
    profile_document,
    rerank_query,
    text_hash,
)


def _job(**overrides: object) -> NormalizedJob:
    defaults: dict[str, object] = {
        "source": "dou",
        "external_id": "1",
        "url": "https://dou.ua/jobs/1",
        "title": "Senior Backend Engineer",
        "company": "Acme",
        "description": "You will build APIs in Python.",
        "employment_type": EmploymentType.FULL_TIME,
        "location": JobLocation(remote=True, countries=["Ukraine"], cities=["Kyiv"]),
        "salary": SalaryRange(min=4000, max=5500, currency="USD"),
        "seniority": "Senior",
        "required_experience_years": 5.0,
    }
    defaults.update(overrides)
    return NormalizedJob(**defaults)  # type: ignore[arg-type]


def test_job_document_carries_the_fields_a_model_needs_to_judge_fit() -> None:
    document = job_document(_job())

    assert "TITLE: Senior Backend Engineer" in document
    assert "COMPANY: Acme" in document
    assert "SENIORITY: Senior" in document
    assert "EXPERIENCE REQUIRED: 5+ years" in document
    assert "WORK FORMAT: remote" in document
    assert "LOCATION: Ukraine, Kyiv" in document
    assert "COMPENSATION: 4000-5500 USD" in document
    assert "DESCRIPTION: You will build APIs in Python." in document


def test_missing_fields_produce_no_line_rather_than_a_placeholder() -> None:
    """An empty label reads to a model as a real, empty answer ("seniority: none")
    rather than as an absent one, so a missing field has to leave no trace."""
    document = job_document(_job(seniority=None, salary=None, required_experience_years=None))

    assert "SENIORITY" not in document
    assert "COMPENSATION" not in document
    assert "EXPERIENCE REQUIRED" not in document


def test_long_postings_are_capped() -> None:
    document = job_document(_job(description="x " * 10_000))

    assert len(document) < MAX_JOB_CHARS + 500


def test_profile_document_leads_with_what_the_candidate_wants() -> None:
    preferences = UserPreference(
        user_id="u1",
        preferred_roles=["backend engineer"],
        preferred_stack=["Python", "PostgreSQL"],
        work_formats=["remote"],
        # Constraints are enforced by the hard filters instead; repeating them
        # here would apply the same fact twice.
        blocked_stack=["PHP"],
        companies_blacklist=["Acme"],
    )
    document = profile_document("15 years of Python.", preferences)

    assert "LOOKING FOR: backend engineer" in document
    assert "PREFERRED STACK: Python, PostgreSQL" in document
    assert "WORK FORMAT: remote" in document
    assert "CV: 15 years of Python." in document
    assert "PHP" not in document
    assert "Acme" not in document


def test_profile_document_works_without_preferences() -> None:
    document = profile_document("15 years of Python.", None)

    assert document == "CV: 15 years of Python."


def test_long_cvs_are_capped() -> None:
    document = profile_document("x " * 20_000, None)

    assert len(document) < MAX_CV_CHARS + 100


def test_the_rerank_query_puts_the_instruction_in_front_of_the_cv() -> None:
    query = rerank_query("CV: 15 years of Python.")

    assert query.startswith(RERANK_INSTRUCTION)
    assert query.endswith("CV: 15 years of Python.")


def test_text_hash_is_stable_for_the_same_text_and_differs_otherwise() -> None:
    assert text_hash("abc") == text_hash("abc")
    assert text_hash("abc") != text_hash("abd")


def test_whitespace_noise_does_not_change_the_document() -> None:
    """A re-scrape that only reflows whitespace must not look like a changed
    posting — otherwise every scrape re-embeds the whole corpus."""
    spaced = job_document(_job(description="You  will   build\n\n\n\nAPIs in Python."))
    plain = job_document(_job(description="You will build\n\nAPIs in Python."))

    assert text_hash(spaced) == text_hash(plain)
