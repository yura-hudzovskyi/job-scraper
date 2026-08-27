from app.domain.candidates.models import UserPreference
from app.domain.jobs.models import EmploymentType, JobLocation, NormalizedJob, SalaryRange
from app.domain.matching.filters import HardFilterService


def _job(
    title: str = "Senior Python Developer",
    description: str = "Build APIs with FastAPI and PostgreSQL.",
    company: str = "Acme",
    remote: bool = True,
    countries: list[str] | None = None,
    cities: list[str] | None = None,
    salary: SalaryRange | None = None,
    required_experience_years: float | None = None,
) -> NormalizedJob:
    return NormalizedJob(
        source="dou",
        external_id="1",
        url="https://example.com/1",
        title=title,
        company=company,
        description=description,
        employment_type=EmploymentType.FULL_TIME,
        location=JobLocation(remote=remote, countries=countries or [], cities=cities or []),
        salary=salary,
        seniority=None,
        required_experience_years=required_experience_years,
    )


def _preferences(**overrides) -> UserPreference:
    defaults = {"user_id": "u1", "desired_salary_usd": None}
    defaults.update(overrides)
    return UserPreference(**defaults)


def test_eligible_job_passes_with_no_reasons() -> None:
    result = HardFilterService().evaluate(_job(), _preferences())
    assert result.eligible is True
    assert result.reasons == []


def test_rejects_blacklisted_company_case_insensitively() -> None:
    prefs = _preferences(companies_blacklist=["acme"])
    result = HardFilterService().evaluate(_job(company="ACME"), prefs)
    assert result.eligible is False
    assert "blacklisted" in result.reasons[0]


def test_rejects_job_mentioning_blocked_stack_with_word_boundary() -> None:
    prefs = _preferences(blocked_stack=["Java"])
    result = HardFilterService().evaluate(
        _job(title="Java Backend Engineer", description="5 years of Java required."), prefs
    )
    assert result.eligible is False
    assert "Java" in result.reasons[0]


def test_blocked_stack_does_not_false_positive_on_substring() -> None:
    # "Java" must not match inside "JavaScript"
    prefs = _preferences(blocked_stack=["Java"])
    result = HardFilterService().evaluate(
        _job(title="JavaScript Developer", description="React and JavaScript."), prefs
    )
    assert result.eligible is True


def test_rejects_when_required_experience_exceeds_candidate_ceiling() -> None:
    prefs = _preferences(max_required_experience=5.0)
    result = HardFilterService().evaluate(_job(required_experience_years=8.0), prefs)
    assert result.eligible is False


def test_allows_when_required_experience_within_ceiling() -> None:
    prefs = _preferences(max_required_experience=5.0)
    result = HardFilterService().evaluate(_job(required_experience_years=3.0), prefs)
    assert result.eligible is True


def test_rejects_salary_below_desired_floor_in_usd() -> None:
    prefs = _preferences(desired_salary_usd=4000)
    job = _job(salary=SalaryRange(min=2000, max=3000, currency="USD"))
    result = HardFilterService().evaluate(job, prefs)
    assert result.eligible is False


def test_does_not_reject_salary_in_a_different_currency() -> None:
    prefs = _preferences(desired_salary_usd=4000)
    job = _job(salary=SalaryRange(min=50000, max=80000, currency="UAH"))
    result = HardFilterService().evaluate(job, prefs)
    assert result.eligible is True


def test_rejects_non_remote_job_when_candidate_wants_remote_only() -> None:
    prefs = _preferences(work_formats=["remote"])
    result = HardFilterService().evaluate(_job(remote=False), prefs)
    assert result.eligible is False


def test_allows_remote_job_when_candidate_wants_remote_only() -> None:
    prefs = _preferences(work_formats=["remote"])
    result = HardFilterService().evaluate(_job(remote=True), prefs)
    assert result.eligible is True


def test_rejects_job_restricted_to_unwanted_location() -> None:
    prefs = _preferences(locations=["Ukraine"])
    job = _job(remote=True, countries=["United States"])
    result = HardFilterService().evaluate(job, prefs)
    assert result.eligible is False


def test_allows_job_with_no_location_data_even_with_location_preference() -> None:
    prefs = _preferences(locations=["Ukraine"])
    job = _job(remote=True, countries=[], cities=[])
    result = HardFilterService().evaluate(job, prefs)
    assert result.eligible is True


def test_allows_job_matching_one_of_candidates_locations() -> None:
    prefs = _preferences(locations=["Ukraine", "Poland"])
    job = _job(remote=True, countries=["Ukraine"])
    result = HardFilterService().evaluate(job, prefs)
    assert result.eligible is True
