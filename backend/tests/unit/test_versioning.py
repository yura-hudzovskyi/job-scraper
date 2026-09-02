"""What the content hashes must and must not react to — the whole point is that a
re-scrape which changed nothing a match depends on doesn't invalidate the match.
"""

from dataclasses import replace

from app.domain.candidates.models import (
    CandidateProfile,
    CandidateSkill,
    ExperienceEntry,
    SkillLevel,
)
from app.domain.jobs.models import (
    EmploymentType,
    JobLocation,
    NormalizedJob,
    NormalizedJobSkill,
    RequirementType,
    SalaryRange,
)
from app.domain.versioning import job_content_hash, profile_content_hash


def _job(**overrides: object) -> NormalizedJob:
    defaults: dict[str, object] = {
        "source": "dou",
        "external_id": "1",
        "url": "https://jobs.dou.ua/1",
        "title": "Senior Python Engineer",
        "company": "Acme",
        "description": "FastAPI and PostgreSQL.",
        "employment_type": EmploymentType.FULL_TIME,
        "location": JobLocation(remote=True, countries=["Ukraine"], cities=[]),
        "salary": SalaryRange(min=4000, max=6000, currency="USD"),
        "seniority": "senior",
        "required_experience_years": 5.0,
        "skills": [NormalizedJobSkill(name="Python", requirement=RequirementType.REQUIRED_EXPLICIT)],
        "skills_extracted_by": "Groq (llama-3.3-70b-versatile)",
    }
    return NormalizedJob(**{**defaults, **overrides})  # type: ignore[arg-type]


def _profile(**overrides: object) -> CandidateProfile:
    defaults: dict[str, object] = {
        "id": "profile-1",
        "user_id": "user-1",
        "experience_years": 3.5,
        "roles": ["Backend Engineer"],
        "skills": [CandidateSkill(name="Python", level=SkillLevel.STRONG, years=3.0)],
        "experience": [
            ExperienceEntry(
                company="Acme",
                title="Engineer",
                start_date="2023-01",
                end_date=None,
                description="APIs",
                skills=["Python"],
            )
        ],
        "generated_by": "Gemini (gemini-2.0-flash)",
    }
    return CandidateProfile(**{**defaults, **overrides})  # type: ignore[arg-type]


def test_the_same_job_always_hashes_the_same() -> None:
    assert job_content_hash(_job()) == job_content_hash(_job())


def test_relisting_the_same_job_elsewhere_keeps_its_hash() -> None:
    # A different source/id/url is the same vacancy as far as matching is
    # concerned — it requires exactly the same things.
    relisted = _job(source="djinni", external_id="42", url="https://djinni.co/42")

    assert job_content_hash(relisted) == job_content_hash(_job())


def test_re_extracting_skills_with_another_model_keeps_the_hash() -> None:
    # Which model read the posting is provenance about the result, not a change
    # in the posting — MatchProvenance records that separately.
    assert job_content_hash(_job(skills_extracted_by="Gemini (gemini-2.0-flash)")) == job_content_hash(
        _job()
    )


def test_skill_order_does_not_change_the_hash() -> None:
    one_order = _job(
        skills=[
            NormalizedJobSkill(name="Python", requirement=RequirementType.REQUIRED_EXPLICIT),
            NormalizedJobSkill(name="Docker", requirement=RequirementType.OPTIONAL_EXPLICIT),
        ]
    )
    other_order = _job(
        skills=[
            NormalizedJobSkill(name="Docker", requirement=RequirementType.OPTIONAL_EXPLICIT),
            NormalizedJobSkill(name="Python", requirement=RequirementType.REQUIRED_EXPLICIT),
        ]
    )

    assert job_content_hash(one_order) == job_content_hash(other_order)


def test_a_changed_requirement_changes_the_hash() -> None:
    assert job_content_hash(_job(required_experience_years=2.0)) != job_content_hash(_job())
    assert job_content_hash(
        _job(skills=[NormalizedJobSkill(name="Python", requirement=RequirementType.OPTIONAL_EXPLICIT)])
    ) != job_content_hash(_job())
    assert job_content_hash(_job(description="Django only.")) != job_content_hash(_job())


def test_profile_hash_ignores_row_identity_and_which_model_extracted_it() -> None:
    other_row = _profile(id="profile-2", user_id="user-2", generated_by="Groq (llama-3.3-70b)")

    assert profile_content_hash(other_row) == profile_content_hash(_profile())


def test_editing_the_cv_changes_the_profile_hash() -> None:
    profile = _profile()
    edited = replace(
        profile, skills=[*profile.skills, CandidateSkill(name="Go", level=SkillLevel.AWARE)]
    )

    assert profile_content_hash(edited) != profile_content_hash(profile)
