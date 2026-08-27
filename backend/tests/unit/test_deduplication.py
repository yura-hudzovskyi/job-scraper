from app.domain.jobs.deduplication import DeduplicationService, normalize_company, normalize_title
from app.domain.jobs.models import (
    CanonicalJob,
    EmploymentType,
    JobLocation,
    NormalizedJob,
)


def _job(source: str, external_id: str, title: str, company: str, description: str) -> NormalizedJob:
    return NormalizedJob(
        source=source,
        external_id=external_id,
        url=f"https://example.com/{source}/{external_id}",
        title=title,
        company=company,
        description=description,
        employment_type=EmploymentType.FULL_TIME,
        location=JobLocation(remote=True),
        salary=None,
        seniority=None,
        required_experience_years=None,
    )


DESCRIPTION = (
    "We are looking for a Senior Python Developer to join our backend team working on "
    "high-throughput APIs, PostgreSQL data modelling and async background jobs."
)
DESCRIPTION_DOU_VARIANT = DESCRIPTION + "\n\nВідгукнутись на вакансію"


def test_normalize_company_collapses_legal_suffixes_and_case() -> None:
    assert normalize_company("Acme Inc.") == "acme"
    assert normalize_company("ACME, LLC") == "acme"
    assert normalize_company("acme") == "acme"


def test_normalize_title_strips_punctuation_and_case() -> None:
    assert normalize_title("Senior Python Developer!") == normalize_title("senior python developer")


def test_finds_match_for_same_job_posted_on_two_sources() -> None:
    canonical = CanonicalJob(
        id="c1",
        normalized=_job("dou", "1", "Senior Python Developer", "Acme Inc.", DESCRIPTION_DOU_VARIANT),
        source_records=["dou:1"],
    )
    incoming = _job("djinni", "2", "Senior Python Developer", "Acme LLC", DESCRIPTION)

    match = DeduplicationService().find_canonical_match(incoming, [canonical])

    assert match is canonical


def test_no_match_for_different_company() -> None:
    canonical = CanonicalJob(
        id="c1",
        normalized=_job("dou", "1", "Senior Python Developer", "Acme Inc.", DESCRIPTION),
        source_records=["dou:1"],
    )
    incoming = _job("djinni", "2", "Senior Python Developer", "Globex Corp", DESCRIPTION)

    assert DeduplicationService().find_canonical_match(incoming, [canonical]) is None


def test_no_match_for_different_title_same_company() -> None:
    canonical = CanonicalJob(
        id="c1",
        normalized=_job("dou", "1", "Senior Python Developer", "Acme Inc.", DESCRIPTION),
        source_records=["dou:1"],
    )
    incoming = _job("djinni", "2", "QA Automation Engineer", "Acme Inc.", DESCRIPTION)

    assert DeduplicationService().find_canonical_match(incoming, [canonical]) is None


def test_merge_appends_new_source_record_once() -> None:
    canonical = CanonicalJob(
        id="c1",
        normalized=_job("dou", "1", "Senior Python Developer", "Acme Inc.", DESCRIPTION),
        source_records=["dou:1"],
    )

    merged = DeduplicationService().merge(canonical, "djinni:2")
    assert merged.source_records == ["dou:1", "djinni:2"]

    merged_again = DeduplicationService().merge(merged, "djinni:2")
    assert merged_again.source_records == ["dou:1", "djinni:2"]
