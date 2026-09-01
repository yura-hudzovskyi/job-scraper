"""Content identity for the documents a match is computed against — see
docs/ai-pipeline-v3.md (3.4 "Results are immutable snapshots", A1 "Persist
document versions").

A stored match is only explainable if it records *which* CV and *which* job text
produced it. Rather than snapshotting both documents onto every match row, each
document carries a (version, content_hash) pair and the match records that pair:
the hash covers only the fields that actually affect analysis, so a re-scrape
that moved a view counter or changed a URL doesn't invalidate anything, while a
changed requirement does.

The hash is short (16 hex chars) on purpose — it labels a version for whoever is
reading provenance, it is not a security boundary, and an accidental collision at
this corpus size is far below any rate that would matter.
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from app.domain.candidates.models import CandidateProfile
from app.domain.jobs.models import NormalizedJob

_HASH_LENGTH = 16


@dataclass(frozen=True)
class DocumentVersion:
    """Which revision of a CV or a job a result was computed against. `version`
    is the human-readable label ("job v3"); `content_hash` is the real identity."""

    version: int
    content_hash: str


def content_hash(payload: Any) -> str:
    """Stable hash of a JSON-serializable payload — key order and Python's dict
    insertion order can't change it, so the same content always hashes the same
    across processes and runs."""
    canonical = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:_HASH_LENGTH]


def job_content_hash(job: NormalizedJob) -> str:
    """Only what a match is actually derived from. Deliberately excluded:
    source/external_id/url (the same vacancy re-listed elsewhere requires the same
    things) and skills_extracted_by (which model read the posting is provenance
    about the *result*, not a change in the posting itself — it's recorded
    separately in MatchProvenance)."""
    return content_hash(
        {
            "title": job.title,
            "company": job.company,
            "description": job.description,
            "employment_type": job.employment_type.value,
            "remote": job.location.remote,
            "countries": sorted(job.location.countries),
            "cities": sorted(job.location.cities),
            "salary": (
                None
                if job.salary is None
                else {
                    "min": job.salary.min,
                    "max": job.salary.max,
                    "currency": job.salary.currency,
                }
            ),
            "seniority": job.seniority,
            "required_experience_years": job.required_experience_years,
            "skills": sorted(
                ([skill.name, skill.required] for skill in job.skills),
                key=lambda entry: (str(entry[0]), bool(entry[1])),
            ),
        }
    )


def profile_content_hash(profile: CandidateProfile) -> str:
    """Same rule on the CV side: what the candidate has done, not which row or
    which model recorded it (id/user_id/version/generated_by are all excluded)."""
    return content_hash(
        {
            "experience_years": profile.experience_years,
            "roles": profile.roles,
            "skills": [
                {"name": skill.name, "level": skill.level.value, "years": skill.years}
                for skill in profile.skills
            ],
            "experience": [
                {
                    "company": entry.company,
                    "title": entry.title,
                    "start_date": entry.start_date,
                    "end_date": entry.end_date,
                    "description": entry.description,
                    "skills": entry.skills,
                }
                for entry in profile.experience
            ],
            "achievements": profile.achievements,
            "domains": profile.domains,
            "ai_experience": profile.ai_experience,
        }
    )
