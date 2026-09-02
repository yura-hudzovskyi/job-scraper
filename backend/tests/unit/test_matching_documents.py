"""Section documents are what every later stage reads, so the properties that
matter are: the same facet lands in the same section on both sides, nothing is
invented when a field is missing, and requirement framing survives into the text.
"""

from app.domain.candidates.models import (
    CandidateProfile,
    CandidateSkill,
    ExperienceEntry,
    SkillLevel,
    UserPreference,
)
from app.domain.categories import JobCategory
from app.domain.jobs.models import (
    EmploymentType,
    JobLocation,
    NormalizedJob,
    NormalizedJobSkill,
    RequirementType,
    SalaryRange,
)
from app.domain.matching.documents import Section, job_sections, profile_sections


def _job(**overrides: object) -> NormalizedJob:
    defaults: dict[str, object] = {
        "source": "dou",
        "external_id": "1",
        "url": "https://example.com/1",
        "title": "Senior Backend Engineer",
        "company": "Acme",
        "description": "You will own the payments API and mentor two juniors.",
        "employment_type": EmploymentType.FULL_TIME,
        "location": JobLocation(remote=True, countries=["Ukraine"], cities=[]),
        "salary": SalaryRange(min=4000, max=6000, currency="USD"),
        "seniority": "senior",
        "required_experience_years": 5.0,
        "skills": [
            NormalizedJobSkill(name="Python", requirement=RequirementType.REQUIRED_EXPLICIT),
            NormalizedJobSkill(name="Docker", requirement=RequirementType.OPTIONAL_EXPLICIT),
            NormalizedJobSkill(name="Grafana", requirement=RequirementType.CONTEXT),
        ],
        "category": JobCategory.BACKEND,
    }
    return NormalizedJob(**{**defaults, **overrides})  # type: ignore[arg-type]


def _profile(**overrides: object) -> CandidateProfile:
    defaults: dict[str, object] = {
        "id": "p1",
        "user_id": "u1",
        "experience_years": 3.5,
        "roles": ["Backend Engineer"],
        "skills": [CandidateSkill(name="Python", level=SkillLevel.STRONG, years=3.0)],
        "experience": [
            ExperienceEntry(
                company="Forex Tester",
                title="Software Engineer",
                start_date="2023-01",
                end_date=None,
                description="Order management and APIs.",
                skills=["Python"],
            )
        ],
        "domains": ["fintech"],
    }
    return CandidateProfile(**{**defaults, **overrides})  # type: ignore[arg-type]


def test_a_job_renders_every_section_with_its_own_facet() -> None:
    sections = job_sections(_job())

    assert "TITLE: Senior Backend Engineer" in sections[Section.OVERVIEW]
    assert "CATEGORY: backend" in sections[Section.OVERVIEW]
    assert "MUST: Python" in sections[Section.SKILLS_REQUIREMENTS]
    assert "YEARS: 5+" in sections[Section.SKILLS_REQUIREMENTS]
    assert "mentor two juniors" in sections[Section.RESPONSIBILITIES_EXPERIENCE]
    assert "WORK FORMAT: remote" in sections[Section.PREFERENCES_CONSTRAINTS]
    assert "COMPENSATION: 4000-6000 USD" in sections[Section.PREFERENCES_CONSTRAINTS]


def test_requirement_framing_survives_into_the_text() -> None:
    # A nice-to-have listed as a must would quietly turn into a gap two stages
    # later, and a "the team also uses X" mention is neither.
    requirements = job_sections(_job())[Section.SKILLS_REQUIREMENTS]

    assert "MUST: Python" in requirements
    assert "NICE: Docker" in requirements
    assert "Grafana" not in requirements


def test_a_missing_field_leaves_its_line_out_rather_than_guessing() -> None:
    sections = job_sections(
        _job(category=None, seniority=None, salary=None, required_experience_years=None)
    )

    assert "CATEGORY" not in sections[Section.OVERVIEW]
    assert "SENIORITY" not in sections[Section.OVERVIEW]
    assert "COMPENSATION" not in sections[Section.PREFERENCES_CONSTRAINTS]


def test_a_section_with_nothing_in_it_is_absent_entirely() -> None:
    # Storing a vector for an empty label would make an unknown look like a
    # comparable signal.
    sections = job_sections(_job(skills=[], required_experience_years=None))

    assert Section.SKILLS_REQUIREMENTS not in sections


def test_a_profile_renders_the_same_sections_as_a_job() -> None:
    sections = profile_sections(_profile())

    assert set(sections) <= set(Section)
    assert "YEARS: 3.5" in sections[Section.OVERVIEW]
    assert "SKILLS: Python 3y" in sections[Section.SKILLS_REQUIREMENTS]
    assert "Software Engineer, Forex Tester, 2023-01-present" in sections[
        Section.RESPONSIBILITIES_EXPERIENCE
    ]


def test_preferences_shape_the_target_and_the_constraints() -> None:
    preferences = UserPreference(
        user_id="u1",
        desired_salary_usd=5000,
        preferred_roles=["Platform Engineer"],
        preferred_stack=["Go"],
        work_formats=["remote"],
        locations=["Ukraine"],
    )

    sections = profile_sections(_profile(), preferences)

    # What the candidate wants to be matched on leads the overview, ahead of what
    # their CV happens to say they were called.
    assert "TARGET: Platform Engineer" in sections[Section.OVERVIEW]
    assert "PREFERRED STACK: Go" in sections[Section.SKILLS_REQUIREMENTS]
    assert "COMPENSATION: 5000 USD" in sections[Section.PREFERENCES_CONSTRAINTS]


def test_a_profile_without_preferences_falls_back_to_its_own_roles() -> None:
    sections = profile_sections(_profile())

    assert "TARGET: Backend Engineer" in sections[Section.OVERVIEW]
