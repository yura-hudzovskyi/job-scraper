from datetime import UTC, datetime, timedelta

from app.domain.jobs.scrape_rotation import pick_next_category

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_never_scraped_category_wins_over_any_previously_scraped_one() -> None:
    category = pick_next_category(
        ["Python", "Artist", "QA"],
        {"Python": _NOW, "QA": _NOW - timedelta(days=100)},
    )
    assert category == "Artist"


def test_first_never_scraped_category_wins_when_several_are_untried() -> None:
    category = pick_next_category(["Python", "Artist", "QA"], {"Python": _NOW})
    assert category == "Artist"


def test_oldest_last_scraped_wins_when_everything_has_run_before() -> None:
    category = pick_next_category(
        ["Python", "Artist", "QA"],
        {
            "Python": _NOW,
            "Artist": _NOW - timedelta(hours=1),
            "QA": _NOW - timedelta(days=5),
        },
    )
    assert category == "QA"


def test_single_category_is_always_picked() -> None:
    assert pick_next_category(["Python"], {}) == "Python"
    assert pick_next_category(["Python"], {"Python": _NOW}) == "Python"
