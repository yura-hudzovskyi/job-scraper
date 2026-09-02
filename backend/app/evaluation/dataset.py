"""The labelled CV/job pairs everything else in this package measures against —
see docs/ai-pipeline-v3.md (12).

Public leaderboards say nothing about this domain: Ukrainian and English postings
mixed in one corpus, near-boundary cases, keyword-heavy vacancies that look like
matches and aren't. The only way to know whether a model or a weight change is an
improvement is a set of pairs someone has actually judged.

The format is plain JSON so it can be edited by hand and reviewed in a diff. A
pair carries what the pipeline would see (a posting and a profile) and what a
human said about it: the recommendation, and which requirements are genuinely
missing. Nothing here is generated — a synthetic label measures the generator,
not the pipeline.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.domain.candidates.models import (
    CandidateProfile,
    CandidateSkill,
    ExperienceEntry,
    SkillLevel,
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
from app.domain.matching.models import Recommendation


@dataclass(frozen=True)
class LabeledPair:
    id: str
    job: NormalizedJob
    profile: CandidateProfile
    # What a human said, not what any model said.
    recommendation: Recommendation
    missing_required: list[str] = field(default_factory=list)
    notes: str = ""


def _job(payload: dict[str, Any]) -> NormalizedJob:
    salary = payload.get("salary")
    return NormalizedJob(
        source=payload.get("source", "eval"),
        external_id=payload.get("external_id", "0"),
        url=payload.get("url", ""),
        title=payload["title"],
        company=payload.get("company", ""),
        description=payload.get("description", ""),
        employment_type=EmploymentType(payload.get("employment_type", "full_time")),
        location=JobLocation(
            remote=payload.get("remote", True),
            countries=payload.get("countries", []),
            cities=payload.get("cities", []),
        ),
        salary=(
            SalaryRange(
                min=salary.get("min"), max=salary.get("max"), currency=salary.get("currency")
            )
            if salary
            else None
        ),
        seniority=payload.get("seniority"),
        required_experience_years=payload.get("required_experience_years"),
        skills=[
            NormalizedJobSkill(
                name=skill["name"],
                requirement=RequirementType(skill.get("requirement", "required_explicit")),
                evidence=skill.get("evidence"),
            )
            for skill in payload.get("skills", [])
        ],
        skills_extracted_by=payload.get("skills_extracted_by", "labelled"),
        category=JobCategory(payload["category"]) if payload.get("category") else None,
        category_confidence=payload.get("category_confidence"),
    )


def _profile(payload: dict[str, Any]) -> CandidateProfile:
    return CandidateProfile(
        id=payload.get("id", "eval-profile"),
        user_id=payload.get("user_id", "eval-user"),
        experience_years=payload.get("experience_years", 0.0),
        roles=payload.get("roles", []),
        skills=[
            CandidateSkill(
                name=skill["name"],
                level=SkillLevel(skill.get("level", "commercial")),
                years=skill.get("years"),
            )
            for skill in payload.get("skills", [])
        ],
        experience=[
            ExperienceEntry(
                company=entry.get("company", ""),
                title=entry.get("title", ""),
                start_date=entry.get("start_date", ""),
                end_date=entry.get("end_date"),
                description=entry.get("description", ""),
                skills=entry.get("skills", []),
            )
            for entry in payload.get("experience", [])
        ],
        domains=payload.get("domains", []),
    )


def load_dataset(path: str | Path) -> list[LabeledPair]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return [
        LabeledPair(
            id=pair["id"],
            job=_job(pair["job"]),
            profile=_profile(pair["profile"]),
            recommendation=Recommendation(pair["label"]["recommendation"]),
            missing_required=pair["label"].get("missing_required", []),
            notes=pair["label"].get("notes", ""),
        )
        for pair in payload["pairs"]
    ]
