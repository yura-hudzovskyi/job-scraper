"""Years of experience derived from dates rather than taken on trust — see
docs/ai-pipeline-v3.md (E3).

`CandidateProfile.experience_years` is whatever the extraction model said, and
models routinely add up overlapping roles ("2020-2023 at A" plus "2022-2024 at B"
becomes seven years) or round a mention into a duration. The dates on the
experience entries are checkable, so where they parse, they win.

Two rules do most of the work:

- **Overlapping intervals merge.** Two jobs held at once are one stretch of
  working time, not two.
- **A skill can't have more years than the role it was used in.** "Kubernetes"
  on a two-year role is at most two years of Kubernetes, whatever the CV implies.

Anything unparseable stays unknown. Returning None is a usable answer here —
the caller falls back to the stated figure and lowers its confidence — while a
guessed duration would quietly become a scoring input nobody can trace.
"""

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime

from app.domain.candidates.models import ExperienceEntry

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_ONGOING = ("present", "current", "now", "today", "поточний", "теперішній", "дотепер")
_ISO = re.compile(r"^(?P<year>\d{4})[-/.](?P<month>\d{1,2})")
_YEAR_ONLY = re.compile(r"^(?P<year>\d{4})$")
_MONTH_YEAR = re.compile(r"^(?P<month>[a-zA-Z]{3,})\.?\s+(?P<year>\d{4})$")
_SLASHED = re.compile(r"^(?P<month>\d{1,2})[-/.](?P<year>\d{4})$")

_MONTHS_PER_YEAR = 12


@dataclass(frozen=True)
class Interval:
    start: date
    end: date

    @property
    def months(self) -> int:
        return (self.end.year - self.start.year) * 12 + (self.end.month - self.start.month)


def parse_month(value: str | None, *, default_month: int = 1) -> date | None:
    """The shapes CV extraction actually produces: "2023-01", "2023/1", "2023",
    "Jan 2023", "01/2023". Anything else is unknown rather than assumed."""
    if not value:
        return None
    text = value.strip().lower()
    if any(marker in text for marker in _ONGOING):
        return None

    iso = _ISO.match(text)
    if iso:
        return _safe_date(int(iso.group("year")), int(iso.group("month")))

    slashed = _SLASHED.match(text)
    if slashed:
        return _safe_date(int(slashed.group("year")), int(slashed.group("month")))

    month_year = _MONTH_YEAR.match(text)
    if month_year:
        month = _MONTHS.get(month_year.group("month")[:3])
        if month:
            return _safe_date(int(month_year.group("year")), month)

    year_only = _YEAR_ONLY.match(text)
    if year_only:
        # A bare year is a real signal with unreal precision; anchoring start and
        # end at opposite ends of the year avoids inventing months in either
        # direction.
        return _safe_date(int(year_only.group("year")), default_month)
    return None


def _safe_date(year: int, month: int) -> date | None:
    if not 1 <= month <= 12 or not 1900 <= year <= 2200:
        return None
    return date(year, month, 1)


def _is_ongoing(value: str | None) -> bool:
    return value is None or any(marker in value.strip().lower() for marker in _ONGOING)


def entry_interval(entry: ExperienceEntry, today: date | None = None) -> Interval | None:
    """One role as a closed interval, or None when its dates can't be read."""
    start = parse_month(entry.start_date)
    if start is None:
        return None
    end = (
        (today or datetime.now(UTC).date()).replace(day=1)
        if _is_ongoing(entry.end_date)
        else parse_month(entry.end_date, default_month=12)
    )
    if end is None or end < start:
        return None
    return Interval(start=start, end=end)


def merge(intervals: list[Interval]) -> list[Interval]:
    """Overlapping and touching stretches become one. Two roles held at the same
    time are one period of working time."""
    if not intervals:
        return []
    ordered = sorted(intervals, key=lambda interval: interval.start)
    merged = [ordered[0]]
    for interval in ordered[1:]:
        last = merged[-1]
        if interval.start <= last.end:
            if interval.end > last.end:
                merged[-1] = Interval(start=last.start, end=interval.end)
        else:
            merged.append(interval)
    return merged


def total_years(entries: list[ExperienceEntry], today: date | None = None) -> float | None:
    """Total working time across all readable roles, overlaps counted once.
    None when no entry has usable dates."""
    intervals = [
        interval
        for interval in (entry_interval(entry, today) for entry in entries)
        if interval is not None
    ]
    if not intervals:
        return None
    months = sum(interval.months for interval in merge(intervals))
    return round(months / _MONTHS_PER_YEAR, 1)


def skill_years(
    entries: list[ExperienceEntry], skill_name: str, today: date | None = None
) -> float | None:
    """How long the candidate has actually used one skill: the merged duration of
    the roles that list it. Capped by construction — a skill cannot outlast the
    roles it appears in."""
    key = skill_name.strip().lower()
    intervals = [
        interval
        for entry in entries
        if any(key == skill.strip().lower() for skill in entry.skills)
        for interval in [entry_interval(entry, today)]
        if interval is not None
    ]
    if not intervals:
        return None
    months = sum(interval.months for interval in merge(intervals))
    return round(months / _MONTHS_PER_YEAR, 1)
