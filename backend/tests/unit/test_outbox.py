"""The outbox relay's delivery guarantee.

Testable without any business handler, which is the point: the property is about
delivery, not about what a handler does with an event. Phase 3 registers a
handler for `document_revision_created` and none of this changes.

The repository's own SQL is thin enough to read; what is worth pinning is the
relay's behaviour around failures — one bad event must not block the queue
behind it, and a failure must be counted rather than lost.
"""

import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.services.job_ingestion_service import DOCUMENT_REVISION_CREATED
from app.workers.tasks import outbox as relay_module


@dataclass
class _Event:
    id: int
    event_type: str
    aggregate_id: str = "revision-1"
    payload: dict[str, Any] = field(default_factory=dict)


class _FakeOutbox:
    def __init__(self, events: list[_Event]):
        self._events = events
        self.published: list[int] = []
        self.failures: list[tuple[int, str]] = []

    async def unpublished(self, limit: int = 100) -> list[_Event]:
        return self._events

    async def mark_published(self, event_ids: list[int]) -> int:
        self.published.extend(event_ids)
        return len(event_ids)

    async def record_failure(self, event_id: int, error: str) -> None:
        self.failures.append((event_id, error))

    async def count_pending(self) -> int:
        return len(self._events) - len(self.published) - len(self.failures)


@pytest.fixture(autouse=True)
def _clean_registry() -> Any:
    """Handlers are module-level state; a test that registers one must not leak
    it into the next."""
    original = dict(relay_module.HANDLERS)
    yield
    relay_module.HANDLERS.clear()
    relay_module.HANDLERS.update(original)


async def _run_relay(outbox: _FakeOutbox, monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    class _Session:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(relay_module, "session_scope", lambda: _Session())
    monkeypatch.setattr(relay_module, "OutboxRepository", lambda session: outbox)
    return await relay_module._relay()


@pytest.mark.asyncio
async def test_an_event_with_a_handler_is_delivered_and_marked_published(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    async def handler(aggregate_id: str, payload: dict[str, Any]) -> None:
        seen.append(aggregate_id)

    relay_module.HANDLERS["thing_happened"] = handler
    outbox = _FakeOutbox([_Event(id=1, event_type="thing_happened")])

    result = await _run_relay(outbox, monkeypatch)

    assert seen == ["revision-1"]
    assert outbox.published == [1]
    assert result["published"] == 1


@pytest.mark.asyncio
async def test_an_event_with_no_handler_is_still_marked_published(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Leaving it pending would build a backlog of events nobody will ever want,
    and hide a genuinely stuck one among them."""
    outbox = _FakeOutbox([_Event(id=1, event_type=DOCUMENT_REVISION_CREATED)])

    result = await _run_relay(outbox, monkeypatch)

    assert outbox.published == [1]
    assert result["unhandled"] == 1
    assert result["published"] == 0


@pytest.mark.asyncio
async def test_a_failing_event_is_counted_and_left_unpublished(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def handler(aggregate_id: str, payload: dict[str, Any]) -> None:
        raise RuntimeError("downstream is down")

    relay_module.HANDLERS["thing_happened"] = handler
    outbox = _FakeOutbox([_Event(id=1, event_type="thing_happened")])

    result = await _run_relay(outbox, monkeypatch)

    assert outbox.published == []
    assert outbox.failures == [(1, "downstream is down")]
    assert result["failed"] == 1


@pytest.mark.asyncio
async def test_one_failing_event_does_not_block_the_ones_behind_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A head-of-line block here would stall every later event behind one that
    can never succeed."""
    delivered: list[str] = []

    async def handler(aggregate_id: str, payload: dict[str, Any]) -> None:
        if aggregate_id == "bad":
            raise RuntimeError("nope")
        delivered.append(aggregate_id)

    relay_module.HANDLERS["thing_happened"] = handler
    outbox = _FakeOutbox(
        [
            _Event(id=1, event_type="thing_happened", aggregate_id="bad"),
            _Event(id=2, event_type="thing_happened", aggregate_id="good"),
        ]
    )

    result = await _run_relay(outbox, monkeypatch)

    assert delivered == ["good"]
    assert outbox.published == [2]
    assert result["failed"] == 1
    assert result["published"] == 1


@pytest.mark.asyncio
async def test_an_empty_outbox_is_a_clean_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
    """This runs every minute; doing nothing must cost nothing and report
    nothing alarming."""
    outbox = _FakeOutbox([])

    result = await _run_relay(outbox, monkeypatch)

    assert result == {"published": 0, "unhandled": 0, "failed": 0, "pending": 0}


@pytest.mark.asyncio
async def test_the_payload_reaches_the_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    received: list[dict[str, Any]] = []

    async def handler(aggregate_id: str, payload: dict[str, Any]) -> None:
        received.append(payload)

    relay_module.HANDLERS["thing_happened"] = handler
    outbox = _FakeOutbox(
        [_Event(id=1, event_type="thing_happened", payload={"revision_no": 2})]
    )

    await _run_relay(outbox, monkeypatch)

    assert received == [{"revision_no": 2}]


# --- the write side ----------------------------------------------------------


def test_append_does_not_flush() -> None:
    """The event must commit with the state change it describes. Flushing here
    would be harmless, but committing separately is exactly the gap the outbox
    exists to close, and `append` staying synchronous is what makes it obvious
    that it joins the caller's transaction rather than opening its own."""
    from app.repositories.outbox_repository import OutboxRepository

    added: list[object] = []

    class _Session:
        def add(self, entity: object) -> None:
            added.append(entity)

    OutboxRepository(_Session()).append(
        aggregate_type="document_revision",
        aggregate_id=str(uuid.uuid4()),
        event_type=DOCUMENT_REVISION_CREATED,
        payload={"revision_no": 1},
    )

    assert len(added) == 1
    assert added[0].event_type == DOCUMENT_REVISION_CREATED  # type: ignore[attr-defined]
    assert added[0].payload == {"revision_no": 1}  # type: ignore[attr-defined]
