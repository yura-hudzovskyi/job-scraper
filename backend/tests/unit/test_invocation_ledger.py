"""The ledger must never be able to break the call it is describing, and must not
grow without bound if nothing drains it.
"""

import pytest
import redis.asyncio as redis

from app.integrations.ai.quota.ledger import OK, InvocationLog, InvocationRecord


class _FakePipeline:
    def __init__(self, store: list[str]):
        self._store = store
        self._ops: list[tuple] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def rpush(self, key: str, value: str):
        self._ops.append(("rpush", value))
        return self

    def ltrim(self, key: str, start: int, end: int):
        self._ops.append(("ltrim", start))
        return self

    async def execute(self):
        for op, value in self._ops:
            if op == "rpush":
                self._store.append(value)
            elif op == "ltrim":
                del self._store[: max(0, len(self._store) + value)]
        self._ops.clear()


class _FakeRedis:
    def __init__(self, failing: bool = False):
        self.store: list[str] = []
        self._failing = failing

    def pipeline(self):
        if self._failing:
            raise redis.RedisError("down")
        return _FakePipeline(self.store)

    async def lpop(self, key: str, count: int):
        if self._failing:
            raise redis.RedisError("down")
        taken = self.store[:count]
        del self.store[:count]
        return taken


def _record(outcome: str = OK) -> InvocationRecord:
    return InvocationRecord(
        capability="job_extraction",
        provider="groq",
        model="llama-3.3-70b-versatile",
        outcome=outcome,
        latency_ms=812,
        prompt_chars=2400,
        status=None,
    )


@pytest.mark.asyncio
async def test_a_recorded_call_comes_back_intact() -> None:
    log = InvocationLog(_FakeRedis())  # type: ignore[arg-type]
    await log.record(_record())

    [drained] = await log.drain()

    assert drained.capability == "job_extraction"
    assert drained.provider == "groq"
    assert drained.outcome == OK
    assert drained.latency_ms == 812
    assert drained.prompt_chars == 2400
    assert drained.at is not None  # stamped on write


@pytest.mark.asyncio
async def test_draining_takes_records_off_the_buffer() -> None:
    client = _FakeRedis()
    log = InvocationLog(client)  # type: ignore[arg-type]
    await log.record(_record())
    await log.record(_record("rate_limit"))

    first = await log.drain(limit=1)
    second = await log.drain(limit=10)

    assert len(first) == 1
    assert len(second) == 1
    assert await log.drain() == []


@pytest.mark.asyncio
async def test_a_redis_failure_never_reaches_the_caller() -> None:
    # The call this describes already succeeded; failing here would turn an audit
    # trail into an outage.
    log = InvocationLog(_FakeRedis(failing=True))  # type: ignore[arg-type]

    await log.record(_record())

    assert await log.drain() == []


@pytest.mark.asyncio
async def test_unreadable_entries_are_dropped_rather_than_blocking_the_drain() -> None:
    client = _FakeRedis()
    client.store.append("not json at all")
    log = InvocationLog(client)  # type: ignore[arg-type]
    await log.record(_record())

    drained = await log.drain()

    assert [record.outcome for record in drained] == [OK]
