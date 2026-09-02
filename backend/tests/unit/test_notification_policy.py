from app.domain.matching.models import JobMatch, Recommendation
from app.domain.notifications.policy import NotificationPolicy, NotificationPolicyConfig


def _match(score: float, eligible: bool = True) -> JobMatch:
    return JobMatch(
        id="m1",
        user_id="u1",
        canonical_job_id="c1",
        eligible=eligible,
        score=score,
        similarity=score / 100,
        relevance=score / 100,
        recommendation=Recommendation.APPLY,
    )


def test_notifies_at_or_above_the_threshold() -> None:
    policy = NotificationPolicy()  # default min_score 75
    assert policy.should_notify(_match(75.0), hour=14) is True
    assert policy.should_notify(_match(92.0), hour=14) is True


def test_does_not_notify_below_the_threshold() -> None:
    policy = NotificationPolicy()
    assert policy.should_notify(_match(74.9), hour=14) is False


def test_ineligible_match_is_never_notified() -> None:
    policy = NotificationPolicy()
    assert policy.should_notify(_match(99.0, eligible=False), hour=14) is False


def test_disabled_notifications_stop_everything() -> None:
    policy = NotificationPolicy(NotificationPolicyConfig(enabled=False))
    assert policy.should_notify(_match(99.0), hour=14) is False


def test_quiet_hours_block_an_otherwise_qualifying_match() -> None:
    policy = NotificationPolicy()  # default 22:00-08:00
    assert policy.should_notify(_match(99.0), hour=23) is False
    assert policy.should_notify(_match(99.0), hour=9) is True


def test_quiet_hours_wraps_around_midnight() -> None:
    policy = NotificationPolicy()
    assert policy.is_quiet_hours(23) is True
    assert policy.is_quiet_hours(3) is True
    assert policy.is_quiet_hours(8) is False
    assert policy.is_quiet_hours(14) is False


def test_quiet_hours_same_day_range() -> None:
    policy = NotificationPolicy(NotificationPolicyConfig(quiet_hours_start=1, quiet_hours_end=5))
    assert policy.is_quiet_hours(3) is True
    assert policy.is_quiet_hours(6) is False


def test_equal_start_and_end_means_no_quiet_hours() -> None:
    """A start equal to the end has to mean "never quiet", not "always quiet" —
    the latter would silently disable notifications for anyone who set both to
    the same value expecting no window at all."""
    policy = NotificationPolicy(NotificationPolicyConfig(quiet_hours_start=0, quiet_hours_end=0))
    assert all(policy.is_quiet_hours(hour) is False for hour in range(24))
