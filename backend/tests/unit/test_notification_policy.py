from app.domain.matching.models import JobMatch, Recommendation, ScoreBreakdown
from app.domain.notifications.policy import NotificationPolicy


def _match(practical_fit: float, salary: float = 100.0, location: float = 100.0) -> JobMatch:
    return JobMatch(
        id="m1",
        user_id="u1",
        canonical_job_id="c1",
        eligible=True,
        requirement_match=practical_fit,
        practical_fit=practical_fit,
        breakdown=ScoreBreakdown(
            skills=practical_fit,
            role=100,
            experience=100,
            semantic_fit=100,
            salary=salary,
            location=location,
            transferable_skills=100,
            preferences=100,
        ),
        recommendation=Recommendation.APPLY,
    )


def test_score_at_or_above_85_notifies_immediately() -> None:
    policy = NotificationPolicy()
    assert policy.should_notify_immediately(_match(85.0)) is True
    assert policy.should_notify_immediately(_match(92.0)) is True


def test_score_75_to_84_notifies_immediately_only_if_salary_and_location_also_match() -> None:
    policy = NotificationPolicy()
    assert policy.should_notify_immediately(_match(80.0, salary=100, location=100)) is True
    assert policy.should_notify_immediately(_match(80.0, salary=50, location=100)) is False
    assert policy.should_notify_immediately(_match(80.0, salary=100, location=50)) is False


def test_score_65_to_74_does_not_notify_immediately_but_joins_digest() -> None:
    policy = NotificationPolicy()
    match = _match(70.0)
    assert policy.should_notify_immediately(match) is False
    assert policy.should_include_in_digest(match) is True


def test_score_below_65_neither_notifies_nor_joins_digest() -> None:
    policy = NotificationPolicy()
    match = _match(50.0)
    assert policy.should_notify_immediately(match) is False
    assert policy.should_include_in_digest(match) is False


def test_ineligible_match_is_never_notified() -> None:
    policy = NotificationPolicy()
    match = JobMatch(
        id="m1",
        user_id="u1",
        canonical_job_id="c1",
        eligible=False,
        requirement_match=0.0,
        practical_fit=0.0,
        breakdown=ScoreBreakdown(0, 0, 0, 0, 0, 0, 0, 0),
    )
    assert policy.should_notify_immediately(match) is False
    assert policy.should_include_in_digest(match) is False


def test_quiet_hours_wraps_around_midnight() -> None:
    policy = NotificationPolicy()  # default 22:00-08:00
    assert policy.is_quiet_hours(23) is True
    assert policy.is_quiet_hours(3) is True
    assert policy.is_quiet_hours(8) is False
    assert policy.is_quiet_hours(14) is False


def test_quiet_hours_same_day_range() -> None:
    from app.domain.notifications.policy import NotificationPolicyConfig

    policy = NotificationPolicy(NotificationPolicyConfig(quiet_hours_start=1, quiet_hours_end=5))
    assert policy.is_quiet_hours(3) is True
    assert policy.is_quiet_hours(6) is False
