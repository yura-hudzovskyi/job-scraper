"""Compact, section-shaped text for the models to read — see
docs/ai-pipeline-v3.md (A2, C1).

Two problems this solves at once. First, sending a whole parsed CV and a whole
job posting to every model wastes context on boilerplate ("we're a fast-growing
team", contact details) that says nothing about fit. Second, and more important:
one vector per document averages away structure. "5+ years of Kubernetes" and
"you will mentor two juniors" are different questions, and a single embedding of
the posting answers neither well.

So each side is rendered into the same few labelled sections, and retrieval
compares like with like — requirements against skills, responsibilities against
experience. The labels are part of the text on purpose: modern embedding and
rerank models use them, and they keep the representation readable when it shows
up in a debug view.

Nothing here invents content. Every line comes from already-extracted fields, so
a section is empty when the underlying data is missing rather than padded with a
guess.
"""

from enum import StrEnum

from app.domain.candidates.models import CandidateProfile, UserPreference
from app.domain.jobs.models import NormalizedJob, RequirementType

# The posting body is the only source for "what you'd actually do", and postings
# routinely run long with benefits and company blurb. This is enough to cover the
# responsibilities of any real vacancy without paying for the marketing.
_MAX_BODY_CHARS = 1200
_MAX_EXPERIENCE_ENTRIES = 6


class Section(StrEnum):
    """One comparable facet of a candidate or a vacancy. Both sides render the
    same set, so a section vector is only ever compared with its counterpart."""

    OVERVIEW = "overview"
    SKILLS_REQUIREMENTS = "skills_requirements"
    RESPONSIBILITIES_EXPERIENCE = "responsibilities_experience"
    PREFERENCES_CONSTRAINTS = "preferences_constraints"


def _joined(values: list[str], separator: str = "; ") -> str:
    return separator.join(value.strip() for value in values if value and value.strip())


def _line(label: str, value: str) -> str:
    return f"{label}: {value}" if value else ""


def _sections(lines: dict[Section, list[str]]) -> dict[Section, str]:
    """Drops empty sections rather than storing a vector for a label with no
    content behind it."""
    return {
        section: "\n".join(line for line in content if line)
        for section, content in lines.items()
        if any(line for line in content)
    }


def job_sections(job: NormalizedJob) -> dict[Section, str]:
    must = [skill.name for skill in job.skills if skill.required]
    nice = [
        skill.name for skill in job.skills if skill.requirement is RequirementType.OPTIONAL_EXPLICIT
    ]
    location = _joined([*job.location.countries, *job.location.cities], ", ")
    salary = ""
    if job.salary and (job.salary.min or job.salary.max):
        bounds = _joined([str(value) for value in (job.salary.min, job.salary.max) if value], "-")
        salary = f"{bounds} {job.salary.currency or ''}".strip()

    return _sections(
        {
            Section.OVERVIEW: [
                _line("TITLE", job.title),
                _line("COMPANY", job.company),
                _line("CATEGORY", job.category.value if job.category else ""),
                _line("SENIORITY", job.seniority or ""),
            ],
            Section.SKILLS_REQUIREMENTS: [
                _line("MUST", _joined(must)),
                _line("NICE", _joined(nice)),
                _line(
                    "YEARS",
                    f"{job.required_experience_years:g}+"
                    if job.required_experience_years
                    else "",
                ),
            ],
            Section.RESPONSIBILITIES_EXPERIENCE: [
                _line("RESPONSIBILITIES", job.description.strip()[:_MAX_BODY_CHARS])
            ],
            Section.PREFERENCES_CONSTRAINTS: [
                _line("WORK FORMAT", "remote" if job.location.remote else "on-site/hybrid"),
                _line("LOCATION", location),
                _line("EMPLOYMENT", job.employment_type.value),
                _line("COMPENSATION", salary),
            ],
        }
    )


def profile_sections(
    profile: CandidateProfile, preferences: UserPreference | None = None
) -> dict[Section, str]:
    skills = [
        f"{skill.name} {skill.years:g}y" if skill.years else skill.name for skill in profile.skills
    ]
    experience = [
        f"- {entry.title}, {entry.company}, {entry.start_date}-{entry.end_date or 'present'}"
        + (f"\n  {entry.description.strip()}" if entry.description.strip() else "")
        for entry in profile.experience[:_MAX_EXPERIENCE_ENTRIES]
    ]
    target = _joined(preferences.preferred_roles if preferences else [], ", ") or _joined(
        profile.roles, ", "
    )

    return _sections(
        {
            Section.OVERVIEW: [
                _line("TARGET", target),
                _line("ROLES", _joined(profile.roles, ", ")),
                _line("YEARS", f"{profile.experience_years:g}"),
                _line("DOMAINS", _joined(profile.domains, ", ")),
            ],
            Section.SKILLS_REQUIREMENTS: [
                _line("SKILLS", _joined(skills)),
                # The candidate's own preferred stack belongs here too: it is what
                # they want to be matched on, not a constraint.
                _line("PREFERRED STACK", _joined(preferences.preferred_stack, ", ") if preferences else ""),
            ],
            Section.RESPONSIBILITIES_EXPERIENCE: [
                "EXPERIENCE:" if experience else "",
                *experience,
                _line("ACHIEVEMENTS", _joined(profile.achievements)),
            ],
            Section.PREFERENCES_CONSTRAINTS: [
                _line(
                    "WORK FORMAT", _joined(preferences.work_formats, ", ") if preferences else ""
                ),
                _line("LOCATION", _joined(preferences.locations, ", ") if preferences else ""),
                _line(
                    "COMPENSATION",
                    f"{preferences.desired_salary_usd} USD"
                    if preferences and preferences.desired_salary_usd
                    else "",
                ),
            ],
        }
    )
