"""Raw -> Normalized -> Canonical job shapes. See docs/domain-model.md.

Downstream modules (matching, dedup, notifications) must only ever depend on
NormalizedJob or CanonicalJob — never on a source's raw payload shape.

Every field here is parsed deterministically by a source adapter. Nothing on a
job is inferred by a model: what the vacancy asks for is read straight out of its
description text at match time, by the embedding and rerank models, rather than
being extracted into a structure first.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class EmploymentType(StrEnum):
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    CONTRACT = "contract"


@dataclass(frozen=True)
class RawJob:
    source: str
    external_id: str
    url: str
    payload: dict[str, Any]
    fetched_at: datetime


@dataclass(frozen=True)
class SalaryRange:
    min: float | None
    max: float | None
    currency: str | None
    gross: bool | None = None


@dataclass(frozen=True)
class JobLocation:
    remote: bool
    countries: list[str] = field(default_factory=list)
    cities: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class NormalizedJob:
    source: str
    external_id: str
    url: str
    title: str
    company: str
    description: str
    employment_type: EmploymentType
    location: JobLocation
    salary: SalaryRange | None = None
    seniority: str | None = None
    required_experience_years: float | None = None
    # The description's original markup, when the source had any. `description`
    # above is this flattened to text and stays the field everything existing
    # reads; this one exists because flattening throws away which lines were
    # headings and which were list items, and block parsing (Phase 2) needs that
    # structure. Markup is a format, not a source quirk, so carrying it here does
    # not leak DOU's or Djinni's payload shape into the normalized model.
    description_html: str | None = None


@dataclass(frozen=True)
class CanonicalJob:
    """One real-world vacancy, potentially backed by several source records."""

    id: str
    normalized: NormalizedJob
    source_records: list[str] = field(default_factory=list)  # JobSourceRecord ids
