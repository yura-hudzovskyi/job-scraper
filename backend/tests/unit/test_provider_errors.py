"""Failure classification decides how long a provider is skipped, so the cases
that matter are the ones where the wrong answer is expensive: a daily quota
treated as a one-minute blip (hammering a dead leg all day) or a broken API key
treated as a transient hiccup (silently degrading forever).
"""

from datetime import timedelta

import pytest

from app.integrations.ai.routing.errors import FailureKind, classify, parse_duration


class _ProviderError(Exception):
    """Shaped like the SDK exceptions this has to read without importing them."""

    def __init__(self, message: str = "", status_code: int | None = None, headers: dict | None = None):
        super().__init__(message)
        if status_code is not None:
            self.status_code = status_code
        if headers is not None:
            self.response = type("_Response", (), {"headers": headers})()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("7.66s", timedelta(seconds=7.66)),
        ("2m59.56s", timedelta(minutes=2, seconds=59.56)),
        ("1h2m3s", timedelta(hours=1, minutes=2, seconds=3)),
        ("90", timedelta(seconds=90)),
        ("", None),
        ("soon", None),
    ],
)
def test_parse_duration_reads_the_shapes_providers_actually_send(raw, expected) -> None:
    assert parse_duration(raw) == expected


def test_a_rate_limit_uses_the_exact_reset_from_the_headers() -> None:
    failure = classify(
        _ProviderError("rate limit reached", status_code=429, headers={"retry-after": "12"})
    )

    assert failure.kind is FailureKind.RATE_LIMIT
    assert failure.cooldown == timedelta(seconds=12)


def test_the_longer_of_the_two_groq_reset_windows_wins() -> None:
    # A call needs both budgets; coming back when only the request window reset
    # just burns another 429.
    failure = classify(
        _ProviderError(
            "rate limit reached",
            status_code=429,
            headers={
                "x-ratelimit-reset-requests": "7.66s",
                "x-ratelimit-reset-tokens": "2m59.56s",
            },
        )
    )

    assert failure.cooldown == timedelta(minutes=2, seconds=59.56)


def test_a_rate_limit_without_any_hint_falls_back_to_a_short_cooldown() -> None:
    failure = classify(_ProviderError("429 Too Many Requests", status_code=429))

    assert failure.kind is FailureKind.RATE_LIMIT
    assert timedelta(seconds=1) <= failure.cooldown <= timedelta(minutes=5)


def test_a_daily_quota_is_closed_until_reset_not_for_a_minute() -> None:
    failure = classify(
        _ProviderError(
            "RESOURCE_EXHAUSTED: GenerateRequestsPerDayPerProjectPerModel-FreeTier",
            status_code=429,
        )
    )

    assert failure.kind is FailureKind.QUOTA_EXHAUSTED
    assert failure.cooldown > timedelta(minutes=5)


def test_a_daily_quotas_own_tiny_retry_hint_is_not_believed() -> None:
    # Gemini attaches a retryDelay of a few tens of seconds to a cap that really
    # resets at midnight — believing it means hitting the same wall all day.
    failure = classify(
        _ProviderError(
            "429 RESOURCE_EXHAUSTED ... quota_metric: requests per day ... retryDelay: '27s'",
            status_code=429,
        )
    )

    assert failure.kind is FailureKind.QUOTA_EXHAUSTED
    assert failure.cooldown > timedelta(minutes=5)


def test_a_plausible_reset_on_a_daily_quota_is_used_as_given() -> None:
    failure = classify(
        _ProviderError(
            "429 quota per day exceeded",
            status_code=429,
            headers={"retry-after": "2h"},
        )
    )

    assert failure.cooldown == timedelta(hours=2)


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_configuration_errors_are_fatal(status: int) -> None:
    # A bad key, a deprecated model id or a malformed request never fixes itself
    # — retrying is the failure mode this classification exists to prevent.
    failure = classify(_ProviderError("invalid api key", status_code=status))

    assert failure.kind is FailureKind.FATAL
    assert failure.cooldown > timedelta(minutes=1)


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_server_errors_are_transient(status: int) -> None:
    failure = classify(_ProviderError("upstream error", status_code=status))

    assert failure.kind is FailureKind.TRANSIENT


def test_a_timeout_is_transient() -> None:
    assert classify(TimeoutError("read timeout")).kind is FailureKind.TRANSIENT


def test_an_unusable_answer_is_a_schema_failure_not_a_provider_problem() -> None:
    failure = classify(ValueError("2 validation errors for _ExtractedJob"))

    assert failure.kind is FailureKind.SCHEMA
    assert failure.cooldown == timedelta(0)
    # Nothing is wrong with the provider, so it isn't taken out of rotation.
    assert failure.is_retryable_elsewhere is False


def test_an_unrecognized_failure_is_treated_as_transient() -> None:
    # Skipping a leg briefly is recoverable; calling something fatal that isn't
    # takes a working provider out of rotation over an unknown.
    assert classify(RuntimeError("something new")).kind is FailureKind.TRANSIENT
