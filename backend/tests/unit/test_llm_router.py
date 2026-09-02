"""The router's job is to spend the next call on a leg that can actually serve
it, and to say so plainly when none can. These cover the decisions that used to
be spread across every call site.
"""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import BaseModel

from app.integrations.ai.llm.base import LLMResult
from app.integrations.ai.routing.errors import FailureKind, ProviderFailure
from app.integrations.ai.routing.router import Capability, LlmRouter, ModelLeg, NoCapacity
from app.integrations.ai.routing.state import LegState


class _Answer(BaseModel):
    ok: bool = True


class _FakeProvider:
    def __init__(self, label: str, error: Exception | None = None, errors: list[Exception] | None = None):
        self.label = label
        self._errors = errors if errors is not None else ([error] if error else [])
        self.calls: list[str] = []

    async def structured_completion(self, prompt, schema):
        self.calls.append(prompt)
        if self._errors:
            raise self._errors.pop(0)
        return LLMResult(data=schema(), model_label=self.label)


class _FakeStateStore:
    def __init__(self, unavailable: dict[str, LegState] | None = None):
        self._unavailable = unavailable or {}
        self.failures: list[tuple[str, ProviderFailure]] = []
        self.successes: list[str] = []

    async def state(self, leg_key: str) -> LegState:
        return self._unavailable.get(leg_key, LegState(available=True))

    async def record_failure(self, leg_key: str, failure: ProviderFailure) -> None:
        self.failures.append((leg_key, failure))

    async def record_success(self, leg_key: str) -> None:
        self.successes.append(leg_key)


class _RateLimited(Exception):
    status_code = 429


class _BadKey(Exception):
    status_code = 401


def _leg(provider_name: str, model: str, fake: _FakeProvider) -> ModelLeg:
    return ModelLeg(provider=provider_name, model=model, build=lambda: fake)


def _router(legs, state=None, budget=None) -> LlmRouter:
    return LlmRouter(
        Capability.JOB_EXTRACTION,
        legs,
        state or _FakeStateStore(),  # type: ignore[arg-type]
        budget,
    )


@pytest.mark.asyncio
async def test_the_first_healthy_leg_answers() -> None:
    first = _FakeProvider("groq")
    second = _FakeProvider("gemini")
    state = _FakeStateStore()

    result = await _router([_leg("groq", "a", first), _leg("gemini", "b", second)], state).structured_completion(
        "prompt", _Answer
    )

    assert result.model_label == "groq"
    assert second.calls == []
    assert state.successes == ["groq:a"]


@pytest.mark.asyncio
async def test_a_rate_limited_leg_parks_itself_and_the_next_one_answers() -> None:
    first = _FakeProvider("groq", error=_RateLimited("429"))
    second = _FakeProvider("gemini")
    state = _FakeStateStore()

    result = await _router([_leg("groq", "a", first), _leg("gemini", "b", second)], state).structured_completion(
        "prompt", _Answer
    )

    assert result.model_label == "gemini"
    assert [key for key, _ in state.failures] == ["groq:a"]
    assert state.failures[0][1].kind is FailureKind.RATE_LIMIT


@pytest.mark.asyncio
async def test_a_leg_already_cooling_down_is_not_called_at_all() -> None:
    # The point of remembering a 429: the next call pays no round trip to
    # rediscover it.
    parked = _FakeProvider("groq")
    healthy = _FakeProvider("gemini")
    state = _FakeStateStore(
        {
            "groq:a": LegState(
                available=False,
                cooldown_until=datetime.now(UTC) + timedelta(seconds=30),
                reason=FailureKind.RATE_LIMIT,
            )
        }
    )

    result = await _router([_leg("groq", "a", parked), _leg("gemini", "b", healthy)], state).structured_completion(
        "prompt", _Answer
    )

    assert parked.calls == []
    assert result.model_label == "gemini"


@pytest.mark.asyncio
async def test_a_broken_key_is_recorded_as_fatal_rather_than_retried_forever() -> None:
    broken = _FakeProvider("groq", error=_BadKey("invalid api key"))
    state = _FakeStateStore()

    with pytest.raises(NoCapacity):
        await _router([_leg("groq", "a", broken)], state).structured_completion("prompt", _Answer)

    assert state.failures[0][1].kind is FailureKind.FATAL
    # One attempt, not a retry loop.
    assert len(broken.calls) == 1


@pytest.mark.asyncio
async def test_an_unparseable_answer_gets_one_repair_attempt_on_the_same_leg() -> None:
    flaky = _FakeProvider("groq", errors=[ValueError("validation error")])
    state = _FakeStateStore()

    result = await _router([_leg("groq", "a", flaky)], state).structured_completion("prompt", _Answer)

    assert result.model_label == "groq"
    assert len(flaky.calls) == 2
    assert "valid JSON" in flaky.calls[1]
    # The provider was never unhealthy, so it isn't parked.
    assert state.failures[0][1].cooldown == timedelta(0)


@pytest.mark.asyncio
async def test_a_leg_that_keeps_answering_badly_hands_over_to_the_next_one() -> None:
    stubborn = _FakeProvider("groq", errors=[ValueError("bad"), ValueError("still bad")])
    second = _FakeProvider("gemini")

    result = await _router(
        [_leg("groq", "a", stubborn), _leg("gemini", "b", second)]
    ).structured_completion("prompt", _Answer)

    assert len(stubborn.calls) == 2
    assert result.model_label == "gemini"


@pytest.mark.asyncio
async def test_no_capacity_reports_when_the_soonest_leg_reopens() -> None:
    # A Celery task can then come back exactly then instead of guessing.
    state = _FakeStateStore(
        {
            "groq:a": LegState(
                available=False,
                cooldown_until=datetime.now(UTC) + timedelta(seconds=120),
                reason=FailureKind.RATE_LIMIT,
            ),
            "gemini:b": LegState(
                available=False,
                cooldown_until=datetime.now(UTC) + timedelta(seconds=40),
                reason=FailureKind.QUOTA_EXHAUSTED,
            ),
        }
    )

    with pytest.raises(NoCapacity) as raised:
        await _router(
            [_leg("groq", "a", _FakeProvider("groq")), _leg("gemini", "b", _FakeProvider("gemini"))],
            state,
        ).structured_completion("prompt", _Answer)

    assert raised.value.retry_after is not None
    assert timedelta(seconds=30) <= raised.value.retry_after <= timedelta(seconds=45)


class _ExhaustedBudget:
    async def try_consume(self) -> bool:
        return False

    async def retry_after(self) -> timedelta:
        return timedelta(hours=3)


@pytest.mark.asyncio
async def test_an_exhausted_budget_stops_the_call_before_any_provider_is_touched() -> None:
    provider = _FakeProvider("groq")

    with pytest.raises(NoCapacity) as raised:
        await _router([_leg("groq", "a", provider)], budget=_ExhaustedBudget()).structured_completion(
            "prompt", _Answer
        )

    assert provider.calls == []
    assert raised.value.retry_after == timedelta(hours=3)
