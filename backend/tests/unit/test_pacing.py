"""Rescheduling has to use the provider's real reset when there is one, spread
tasks out so they don't all wake together, and never park a task so far out that
the worker holds it in memory for hours.
"""

from datetime import timedelta

from app.workers.pacing import MAX_COUNTDOWN_SECONDS, retry_countdown


def test_the_providers_own_reset_is_used() -> None:
    countdown = retry_countdown(timedelta(seconds=45))

    assert 45 <= countdown <= 60  # the reset, plus a little jitter


def test_a_long_reset_is_capped_into_several_shorter_waits() -> None:
    # Celery keeps countdown tasks in the worker's memory, so an eight-hour wait
    # is a memory leak with extra steps — it re-checks hourly instead.
    countdown = retry_countdown(timedelta(hours=8))

    assert countdown <= MAX_COUNTDOWN_SECONDS + 10


def test_without_a_reset_it_backs_off() -> None:
    first = retry_countdown(None, attempt=0)
    later = retry_countdown(None, attempt=2)

    assert later > first


def test_countdowns_are_jittered_so_parked_tasks_do_not_all_wake_together() -> None:
    countdowns = {retry_countdown(timedelta(seconds=300)) for _ in range(25)}

    assert len(countdowns) > 1
