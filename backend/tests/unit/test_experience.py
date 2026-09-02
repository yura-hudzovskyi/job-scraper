"""Experience from dates. The cases that matter are the ones where the stated
number is wrong: overlapping roles, an ongoing role, and dates nobody can read.
"""

from datetime import date

import pytest

from app.domain.candidates.experience import (
    entry_interval,
    merge,
    parse_month,
    skill_years,
    total_years,
)
from app.domain.candidates.models import ExperienceEntry

TODAY = date(2026, 9, 1)


def _entry(start: str, end: str | None, skills: list[str] | None = None) -> ExperienceEntry:
    return ExperienceEntry(
        company="Acme",
        title="Engineer",
        start_date=start,
        end_date=end,
        description="",
        skills=skills or [],
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2023-01", date(2023, 1, 1)),
        ("2023/3", date(2023, 3, 1)),
        ("07/2021", date(2021, 7, 1)),
        ("Jan 2020", date(2020, 1, 1)),
        ("september 2019", date(2019, 9, 1)),
        ("2022", date(2022, 1, 1)),
        ("present", None),
        ("some time ago", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_month_reads_what_cv_extraction_produces(raw, expected) -> None:
    assert parse_month(raw) == expected


def test_an_ongoing_role_runs_to_today() -> None:
    interval = entry_interval(_entry("2024-01", None), today=TODAY)

    assert interval is not None
    assert interval.end == date(2026, 9, 1)
    assert interval.months == 32


def test_a_role_with_unreadable_dates_is_unknown_rather_than_guessed() -> None:
    assert entry_interval(_entry("a while back", "recently"), today=TODAY) is None


def test_a_role_ending_before_it_starts_is_rejected() -> None:
    assert entry_interval(_entry("2024-01", "2023-01"), today=TODAY) is None


def test_overlapping_roles_count_once() -> None:
    # The failure this exists to prevent: two concurrent jobs read as double the
    # experience.
    years = total_years(
        [_entry("2020-01", "2023-01"), _entry("2022-01", "2024-01")], today=TODAY
    )

    assert years == 4.0


def test_separate_stretches_add_up() -> None:
    years = total_years(
        [_entry("2018-01", "2019-01"), _entry("2021-01", "2023-01")], today=TODAY
    )

    assert years == 3.0


def test_unreadable_entries_are_skipped_not_counted_as_zero() -> None:
    years = total_years([_entry("2020-01", "2022-01"), _entry("dunno", "dunno")], today=TODAY)

    assert years == 2.0


def test_no_readable_dates_means_unknown() -> None:
    # None is a usable answer: the caller falls back to the stated figure and
    # says its confidence is lower.
    assert total_years([_entry("dunno", None)], today=TODAY) is None
    assert total_years([], today=TODAY) is None


def test_merging_touching_intervals_produces_one_stretch() -> None:
    intervals = [
        interval
        for interval in (
            entry_interval(_entry("2020-01", "2021-01"), today=TODAY),
            entry_interval(_entry("2021-01", "2022-01"), today=TODAY),
        )
        if interval is not None
    ]

    assert len(merge(intervals)) == 1
    assert merge(intervals)[0].months == 24


def test_a_skill_cannot_outlast_the_roles_it_was_used_in() -> None:
    entries = [
        _entry("2020-01", "2024-01", skills=["Python"]),
        _entry("2023-01", "2024-01", skills=["Kubernetes"]),
    ]

    assert skill_years(entries, "Python", today=TODAY) == 4.0
    assert skill_years(entries, "kubernetes", today=TODAY) == 1.0
    assert skill_years(entries, "Rust", today=TODAY) is None
