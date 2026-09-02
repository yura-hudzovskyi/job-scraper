"""A correction the user made must outlive the next re-analysis of their CV —
including a correction that says "this isn't one of my skills".
"""

from app.domain.candidates.models import CandidateSkill, SkillLevel, SkillOverride, SkillSource
from app.domain.candidates.skill_overrides import apply_overrides, normalize


def _skill(name: str, level: SkillLevel = SkillLevel.COMMERCIAL, years: float | None = None):
    return CandidateSkill(name=name, level=level, years=years)


def test_extracted_skills_get_canonical_names_and_dedupe() -> None:
    result = normalize([_skill("React.js"), _skill("reactjs"), _skill("Postgres")])

    assert [skill.name for skill in result] == ["React", "PostgreSQL"]


def test_an_untouched_skill_passes_through_unchanged() -> None:
    skills = [_skill("Python", SkillLevel.STRONG, 4.0)]

    assert apply_overrides(skills, []) == skills


def test_a_removed_skill_stays_removed_after_re_extraction() -> None:
    # The CV still mentions jQuery; the user has said it shouldn't count.
    skills = [_skill("Python"), _skill("jQuery")]
    overrides = [SkillOverride(skill_key="jquery", name="jQuery", removed=True)]

    result = apply_overrides(skills, overrides)

    assert [skill.name for skill in result] == ["Python"]


def test_an_edited_level_wins_over_the_extractor() -> None:
    skills = [_skill("Python", SkillLevel.AWARE, 1.0)]
    overrides = [
        SkillOverride(skill_key="python", name="Python", level=SkillLevel.EXPERT, years=6.0)
    ]

    [python] = apply_overrides(skills, overrides)

    assert python.level is SkillLevel.EXPERT
    assert python.years == 6.0
    assert python.source is SkillSource.USER


def test_an_override_that_only_confirms_a_skill_keeps_the_extracted_details() -> None:
    skills = [_skill("Python", SkillLevel.STRONG, 4.0)]
    overrides = [SkillOverride(skill_key="python", name="Python")]

    [python] = apply_overrides(skills, overrides)

    assert python.level is SkillLevel.STRONG
    assert python.years == 4.0
    assert python.source is SkillSource.USER


def test_a_user_added_skill_appears_even_when_the_cv_never_mentioned_it() -> None:
    overrides = [SkillOverride(skill_key="rust", name="Rust", level=SkillLevel.COMMERCIAL)]

    result = apply_overrides([_skill("Python")], overrides)

    assert [(skill.name, skill.source) for skill in result] == [
        ("Python", SkillSource.LLM),
        ("Rust", SkillSource.USER),
    ]


def test_an_override_matches_however_the_extractor_spelled_the_skill() -> None:
    # The user corrected "PostgreSQL"; this analysis called it "Postgres".
    skills = [_skill("Postgres", SkillLevel.AWARE)]
    overrides = [
        SkillOverride(skill_key="postgresql", name="PostgreSQL", level=SkillLevel.EXPERT)
    ]

    [postgres] = apply_overrides(skills, overrides)

    assert postgres.name == "PostgreSQL"
    assert postgres.level is SkillLevel.EXPERT
