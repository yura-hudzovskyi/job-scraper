"""Merges NormalizedJob records from different sources into one CanonicalJob.

Uses normalized company + normalized title + description similarity — never a single
exact-match heuristic, since the same vacancy is rarely byte-identical across sources
(different formatting, a source-specific CTA appended, etc). See docs/domain-model.md.

This is pure domain logic: `find_canonical_match` decides among a short list of
*candidates* the caller has already narrowed down (e.g. by normalized company) — it
never queries a database itself. That's the repository's job.
"""

import re
from dataclasses import replace
from difflib import SequenceMatcher

from app.domain.jobs.models import CanonicalJob, NormalizedJob

_LEGAL_SUFFIXES = frozenset(
    {
        "llc", "ltd", "inc", "gmbh", "corp", "co", "sro", "spzoo",
        "тов", "пп", "ооо", "фоп",
    }
)
_PUNCTUATION_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WHITESPACE_RE = re.compile(r"\s+")

TITLE_SIMILARITY_THRESHOLD = 0.86
DESCRIPTION_SIMILARITY_THRESHOLD = 0.5


def normalize_company(name: str) -> str:
    """Collapses variants like "React Inc.", "React, LLC" and "react" to "react"."""
    cleaned = _PUNCTUATION_RE.sub(" ", name.lower())
    words = [word for word in cleaned.split() if word not in _LEGAL_SUFFIXES]
    return _WHITESPACE_RE.sub(" ", " ".join(words)).strip()


def normalize_title(title: str) -> str:
    cleaned = _PUNCTUATION_RE.sub(" ", title.lower())
    return _WHITESPACE_RE.sub(" ", cleaned).strip()


def _similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


class DeduplicationService:
    def find_canonical_match(
        self, job: NormalizedJob, candidates: list[CanonicalJob]
    ) -> CanonicalJob | None:
        """Return whichever of `candidates` this job most likely duplicates, or None
        if it looks like a genuinely new vacancy."""
        job_company = normalize_company(job.company)
        job_title = normalize_title(job.title)

        best_match: CanonicalJob | None = None
        best_score = 0.0

        for candidate in candidates:
            if normalize_company(candidate.normalized.company) != job_company:
                continue

            title_score = _similarity(job_title, normalize_title(candidate.normalized.title))
            if title_score < TITLE_SIMILARITY_THRESHOLD:
                continue

            description_score = _similarity(job.description, candidate.normalized.description)
            if description_score < DESCRIPTION_SIMILARITY_THRESHOLD:
                continue

            combined_score = (title_score + description_score) / 2
            if combined_score > best_score:
                best_score = combined_score
                best_match = candidate

        return best_match

    def merge(self, canonical: CanonicalJob, source_record_id: str) -> CanonicalJob:
        """Attach a newly-persisted JobSourceRecord id to an existing canonical job."""
        if source_record_id in canonical.source_records:
            return canonical
        return replace(canonical, source_records=[*canonical.source_records, source_record_id])
