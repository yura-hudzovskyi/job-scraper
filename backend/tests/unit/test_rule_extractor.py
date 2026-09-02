"""The rules path has to be useful without an LLM and honest about what it can't
know: it reads what the posting literally names, and never upgrades a mention
into a requirement on its own.
"""

from app.domain.jobs.models import RequirementType
from app.domain.skills.rule_extractor import extract_skills


def _named(skills, name):
    return next((skill for skill in skills if skill.name == name), None)


def test_it_finds_named_technologies_and_their_framing() -> None:
    skills = extract_skills(
        "Senior Backend Engineer",
        "Requirements: strong Python and PostgreSQL. Docker would be a plus. "
        "Our team also uses Grafana.",
    )

    python = _named(skills, "Python")
    docker = _named(skills, "Docker")
    grafana = _named(skills, "Grafana")

    assert python is not None and python.requirement is RequirementType.REQUIRED_EXPLICIT
    assert docker is not None and docker.requirement is RequirementType.OPTIONAL_EXPLICIT
    # No cue either way — a mention, not a requirement and not a nice-to-have.
    assert grafana is not None and grafana.requirement is RequirementType.UNKNOWN


def test_a_nice_to_have_sentence_is_not_a_requirement_even_when_it_says_experience() -> None:
    skills = extract_skills("QA Engineer", "Experience with Playwright is a plus.")

    playwright = _named(skills, "Playwright")

    assert playwright is not None
    assert playwright.requirement is RequirementType.OPTIONAL_EXPLICIT


def test_aliases_resolve_to_canonical_names() -> None:
    skills = extract_skills("Frontend Engineer", "We need React.js, TS and k8s knowledge.")

    assert {skill.name for skill in skills} >= {"React", "TypeScript", "Kubernetes"}


def test_evidence_is_the_sentence_the_skill_was_found_in() -> None:
    skills = extract_skills("Backend Engineer", "Django is required. Redis is a plus.")

    django = _named(skills, "Django")

    assert django is not None
    assert django.evidence == "Django is required."


def test_it_does_not_invent_skills_the_posting_never_names() -> None:
    skills = extract_skills("Backend Engineer", "You will work on our payments platform.")

    assert skills == []


def test_short_ambiguous_language_names_need_a_technology_context() -> None:
    # "go to production" is not the Go language; "Python, Go, Rust" is.
    prose = extract_skills("Engineer", "You will go to production every week.")
    stack = extract_skills("Engineer", "Our stack: Python, Go, Rust.")

    assert _named(prose, "Go") is None
    assert _named(stack, "Go") is not None


def test_c_is_not_matched_inside_cpp() -> None:
    skills = extract_skills("Engineer", "Strong C++ background, developer of low-level systems.")

    assert _named(skills, "C++") is not None
    assert _named(skills, "C") is None


def test_ukrainian_cues_are_understood() -> None:
    skills = extract_skills(
        "Розробник",
        "Вимоги: досвід роботи з Python. Знання Kubernetes буде плюсом.",
    )

    python = _named(skills, "Python")
    kubernetes = _named(skills, "Kubernetes")

    assert python is not None and python.requirement is RequirementType.REQUIRED_EXPLICIT
    assert kubernetes is not None and kubernetes.requirement is RequirementType.OPTIONAL_EXPLICIT


def test_repeated_mentions_keep_the_strongest_framing() -> None:
    skills = extract_skills(
        "Backend Engineer",
        "Nice to have: Kafka. Kafka experience is required for this role.",
    )

    kafka = _named(skills, "Kafka")

    assert kafka is not None
    assert kafka.requirement is RequirementType.REQUIRED_EXPLICIT
    assert len([skill for skill in skills if skill.name == "Kafka"]) == 1
