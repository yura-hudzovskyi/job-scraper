"""The lock behind every pipeline button. A greyed-out button is a suggestion; a
second tab, a stale page or a curl call all reach the endpoint anyway, so the
refusal has to live here.
"""

import pytest
import redis.asyncio as redis

from app.services.pipeline_state import PipelineRunState, PipelineStage


class _FakeRedis:
    """Enough of the Redis surface to exercise the lock, including the one
    behaviour that matters: SET NX only succeeds when the key is absent."""

    def __init__(self, failing: bool = False):
        self.keys: dict[str, int] = {}  # key -> ttl
        self._failing = failing

    def _guard(self) -> None:
        if self._failing:
            raise redis.RedisError("down")

    async def set(self, key: str, value: str, ex: int, nx: bool = False):
        self._guard()
        if nx and key in self.keys:
            return None
        self.keys[key] = ex
        return True

    async def expire(self, key: str, ttl: int) -> bool:
        self._guard()
        if key not in self.keys:
            return False
        self.keys[key] = ttl
        return True

    async def delete(self, key: str) -> int:
        self._guard()
        return 1 if self.keys.pop(key, None) is not None else 0

    async def exists(self, key: str) -> int:
        self._guard()
        return 1 if key in self.keys else 0


@pytest.mark.asyncio
async def test_a_second_start_is_refused_while_the_first_is_running() -> None:
    state = PipelineRunState(_FakeRedis())  # type: ignore[arg-type]

    assert await state.start(PipelineStage.EMBEDDINGS) is True
    assert await state.start(PipelineStage.EMBEDDINGS) is False


@pytest.mark.asyncio
async def test_stages_do_not_block_each_other() -> None:
    # Rebuilding embeddings and rescoring are independent work; only *the same*
    # stage twice is the problem.
    state = PipelineRunState(_FakeRedis())  # type: ignore[arg-type]

    assert await state.start(PipelineStage.EMBEDDINGS) is True
    assert await state.start(PipelineStage.SCORING) is True


@pytest.mark.asyncio
async def test_finishing_lets_the_next_run_start() -> None:
    state = PipelineRunState(_FakeRedis())  # type: ignore[arg-type]
    await state.start(PipelineStage.RETRIEVAL)

    await state.finish(PipelineStage.RETRIEVAL)

    assert await state.start(PipelineStage.RETRIEVAL) is True


@pytest.mark.asyncio
async def test_running_reports_each_stage() -> None:
    client = _FakeRedis()
    state = PipelineRunState(client)  # type: ignore[arg-type]
    await state.start(PipelineStage.SCORING)

    running = await state.running()

    assert running == {"embeddings": False, "scoring": True, "retrieval": False}


@pytest.mark.asyncio
async def test_a_heartbeat_pushes_the_expiry_out() -> None:
    # Without this a long backfill would look abandoned while it is still working.
    client = _FakeRedis()
    state = PipelineRunState(client, ttl_seconds=60)  # type: ignore[arg-type]
    await state.start(PipelineStage.EMBEDDINGS)
    client.keys["ai:pipeline:running:embeddings"] = 5

    await state.heartbeat(PipelineStage.EMBEDDINGS)

    assert client.keys["ai:pipeline:running:embeddings"] == 60


@pytest.mark.asyncio
async def test_a_broker_hiccup_does_not_lock_the_operator_out() -> None:
    # The tasks are idempotent; refusing to run because Redis blinked would be
    # worse than running twice.
    state = PipelineRunState(_FakeRedis(failing=True))  # type: ignore[arg-type]

    assert await state.start(PipelineStage.EMBEDDINGS) is True
    assert await state.running() == {"embeddings": False, "scoring": False, "retrieval": False}
    await state.finish(PipelineStage.EMBEDDINGS)  # must not raise
