"""The ontology has one job: the same requirement, however a posting spells it,
comes out under one stable name — without swallowing skills it has never heard of.
"""

import pytest

from app.domain.skills.normalizer import dedupe_key, lookup_key, normalize_skill, unique_skills
from app.domain.skills.ontology import SKILLS, by_id, by_key


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("React.js", "React"),
        ("reactjs", "React"),
        ("REACT", "React"),
        ("  react  ", "React"),
        ("Postgres", "PostgreSQL"),
        ("PostgreSQL", "PostgreSQL"),
        ("pg", "PostgreSQL"),
        ("Node.js", "Node.js"),
        ("NodeJS", "Node.js"),
        ("node", "Node.js"),
        ("Amazon Web Services", "AWS"),
        ("k8s", "Kubernetes"),
        ("golang", "Go"),
        ("C++", "C++"),
        ("c sharp", "C#"),
        ("ruby on rails", "Ruby on Rails"),
    ],
)
def test_aliases_collapse_to_one_display_name(raw: str, expected: str) -> None:
    assert normalize_skill(raw).name == expected


def test_an_unknown_skill_keeps_its_own_wording() -> None:
    normalized = normalize_skill("  Adobe After   Effects ")

    assert normalized.name == "Adobe After Effects"
    assert normalized.canonical_id is None
    assert normalized.known is False


def test_react_native_is_not_react() -> None:
    # The one alias collision that actually matters here: they are related, not
    # interchangeable, and matching them would fabricate web experience.
    assert normalize_skill("React Native").canonical_id == "reactnative"
    assert normalize_skill("React").canonical_id == "react"


def test_duplicates_and_aliases_collapse_in_a_list() -> None:
    names = ["React.js", "reactjs", "Postgres", "PostgreSQL", "Elixir", "Elixir"]

    assert [skill.name for skill in unique_skills(names)] == ["React", "PostgreSQL", "Elixir"]


def test_unknown_skills_still_dedupe_by_their_own_key() -> None:
    assert dedupe_key("Adobe After Effects") == dedupe_key("adobe after  effects")
    assert dedupe_key("Adobe After Effects") != dedupe_key("Adobe Premiere")


def test_typescript_implies_javascript_but_not_the_reverse() -> None:
    typescript = by_id("typescript")
    javascript = by_id("javascript")

    assert typescript is not None and javascript is not None
    assert "javascript" in typescript.implies
    assert "typescript" not in javascript.implies


def test_every_relation_points_at_a_real_skill() -> None:
    ids = {skill.id for skill in SKILLS}

    for skill in SKILLS:
        for related_id in (*skill.implies, *skill.related):
            assert related_id in ids, f"{skill.id} references unknown skill {related_id}"


def test_no_two_skills_claim_the_same_alias() -> None:
    # A duplicated alias would silently resolve to whichever entry is listed
    # first, which is exactly the kind of quiet wrong answer this list exists to
    # prevent.
    seen: dict[str, str] = {}
    for skill in SKILLS:
        for name in (skill.id, skill.display, *skill.aliases):
            key = lookup_key(name)
            assert key not in seen or seen[key] == skill.id, (
                f"{key!r} is claimed by both {seen.get(key)} and {skill.id}"
            )
            seen[key] = skill.id


def test_lookup_by_key_matches_the_normalizer() -> None:
    assert by_key(lookup_key("Postgres")) is by_id("postgresql")
