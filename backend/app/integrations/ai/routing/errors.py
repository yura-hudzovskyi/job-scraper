"""What kind of failure a provider just had, and how long to stay away.

Every leg of the router reacts to a failure the same way today — try the next
one — which is wrong in three different directions: a 401 means the
configuration is broken and no amount of retrying fixes it, a 429 on a per-minute
window is over in seconds while a daily quota is over tomorrow, and a response
that didn't match the schema says nothing about the provider's health at all.
See docs/ai-pipeline-v3.md (5, "Provider state machine").

Everything here is duck-typed on purpose: no vendor SDK is imported, so this
module works for any provider (and is testable with plain fakes), exactly like
FallbackLLMProvider's predicate did before it.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from enum import StrEnum

# A window this app can afford to wait out inside the same run; anything longer
# is treated as "come back later" by the caller instead.
_DEFAULT_RATE_LIMIT_COOLDOWN = timedelta(seconds=60)
_DEFAULT_TRANSIENT_COOLDOWN = timedelta(seconds=30)
# A broken key or model id doesn't heal on its own. Long enough to stop hammering
# the endpoint, short enough that fixing the config takes effect the same day.
_FATAL_COOLDOWN = timedelta(minutes=30)

_DURATION = re.compile(
    r"^\s*(?:(?P<hours>[\d.]+)h)?(?:(?P<minutes>[\d.]+)m)?(?:(?P<seconds>[\d.]+)s?)?\s*$"
)
# Gemini reports its own hint inside the error payload rather than a header.
_RETRY_DELAY = re.compile(r"retry[_-]?delay['\"]?\s*[:=]\s*['\"]?(?P<value>[\dhms.]+)", re.IGNORECASE)
_DAILY_QUOTA_HINTS = ("perday", "per day", "daily", "requests per day", "quota_metric")


class FailureKind(StrEnum):
    RATE_LIMIT = "rate_limit"  # a short window; the leg comes back on its own
    QUOTA_EXHAUSTED = "quota_exhausted"  # a daily/monthly cap; closed until reset
    TRANSIENT = "transient"  # timeout, connection reset, 5xx
    SCHEMA = "schema"  # the call worked, the answer didn't validate
    FATAL = "fatal"  # auth, bad model id, malformed request — retrying can't help


@dataclass(frozen=True)
class ProviderFailure:
    kind: FailureKind
    # How long this leg should be skipped. Always set, so callers never have to
    # invent a policy for a kind they didn't think about.
    cooldown: timedelta
    status: int | None = None
    # The provider's own words. For logs only — never put this in an HTTP
    # response (docs/ai-pipeline-v3.md, 9.3).
    message: str = ""

    @property
    def is_retryable_elsewhere(self) -> bool:
        """Whether trying a different leg makes sense. A schema failure is about
        the answer, not the provider, so it earns one repair attempt on the same
        leg before moving on — the router owns that decision."""
        return self.kind is not FailureKind.SCHEMA


def parse_duration(text: str) -> timedelta | None:
    """"90", "7.66s", "2m59.56s", "1h2m3s" — the shapes providers actually use in
    reset headers. Returns None for anything else rather than guessing."""
    match = _DURATION.match(text)
    if match is None or not any(match.group(name) for name in ("hours", "minutes", "seconds")):
        return None
    return timedelta(
        hours=float(match.group("hours") or 0),
        minutes=float(match.group("minutes") or 0),
        seconds=float(match.group("seconds") or 0),
    )


def _headers(exc: Exception) -> dict[str, str]:
    response = getattr(exc, "response", None)
    raw = getattr(response, "headers", None)
    if raw is None:
        return {}
    try:
        return {str(key).lower(): str(value) for key, value in dict(raw).items()}
    except (TypeError, ValueError):
        return {}


def _status(exc: Exception) -> int | None:
    # openai/httpx expose status_code; google-genai's ClientError exposes code.
    for attribute in ("status_code", "code", "status"):
        value = getattr(exc, attribute, None)
        if isinstance(value, int):
            return value
    return None


def _retry_after(headers: dict[str, str], message: str) -> timedelta | None:
    raw = headers.get("retry-after")
    if raw:
        seconds = parse_duration(raw)
        if seconds is not None:
            return seconds
        try:
            # The other legal Retry-After form is an HTTP date.
            return max(timedelta(0), parsedate_to_datetime(raw) - datetime.now(UTC))
        except (TypeError, ValueError):
            pass

    # Groq (OpenAI-compatible) reports the exact reset for both windows; the
    # longer of the two is when a call can actually succeed again.
    resets = [
        parse_duration(headers[key])
        for key in ("x-ratelimit-reset-requests", "x-ratelimit-reset-tokens")
        if key in headers
    ]
    known = [reset for reset in resets if reset is not None]
    if known:
        return max(known)

    hint = _RETRY_DELAY.search(message)
    if hint:
        return parse_duration(hint.group("value"))
    return None


def seconds_until_next_utc_midnight(now: datetime | None = None) -> timedelta:
    moment = now or datetime.now(UTC)
    tomorrow = (moment + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(timedelta(seconds=1), tomorrow - moment)


def _is_daily_quota(message: str) -> bool:
    lowered = message.lower()
    return any(hint in lowered for hint in _DAILY_QUOTA_HINTS)


def classify(exc: Exception) -> ProviderFailure:
    """One failure -> what it means and how long to wait. Unknown failures are
    treated as transient: the leg is skipped briefly rather than disabled, since
    guessing "fatal" would take a working provider out of rotation over something
    this function simply hasn't seen yet."""
    message = str(exc)
    status = _status(exc)
    headers = _headers(exc)

    if status == 429:
        reported = _retry_after(headers, message)
        if _is_daily_quota(message):
            # A daily cap's own retryDelay hint is routinely far shorter than the
            # window it belongs to (tens of seconds against a day), so the reset
            # is trusted only when it's plausible for a daily quota.
            until_midnight = seconds_until_next_utc_midnight()
            cooldown = reported if reported and reported > timedelta(minutes=5) else until_midnight
            return ProviderFailure(FailureKind.QUOTA_EXHAUSTED, cooldown, status, message)
        return ProviderFailure(
            FailureKind.RATE_LIMIT,
            reported or _DEFAULT_RATE_LIMIT_COOLDOWN,
            status,
            message,
        )

    if status in (400, 401, 403, 404):
        return ProviderFailure(FailureKind.FATAL, _FATAL_COOLDOWN, status, message)

    if isinstance(exc, ValueError):
        # Pydantic's ValidationError and json.JSONDecodeError are both ValueErrors:
        # the provider answered, the answer just wasn't usable.
        return ProviderFailure(FailureKind.SCHEMA, timedelta(0), status, message)

    return ProviderFailure(FailureKind.TRANSIENT, _DEFAULT_TRANSIENT_COOLDOWN, status, message)
