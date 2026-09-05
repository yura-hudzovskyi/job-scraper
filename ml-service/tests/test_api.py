"""The HTTP contract, with the model replaced by a stub.

The point of testing here rather than only in `test_entities` is the boundary
itself: batch order, the id echo, and what a caller sees when the weights are not
in memory. None of that needs a real forward pass, and making it need one would
mean it is only ever checked by hand.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import main
from app.entities import DocumentEntities, Entity


class _StubExtractor:
    """Returns one entity per document, quoting the document's first word."""

    def __init__(self) -> None:
        self.loaded = True
        self.load_seconds = 0.1
        self.calls: list[tuple[list[str], list[str], float | None]] = []

    async def extract(
        self, texts: list[str], labels: list[str], threshold: float | None = None
    ) -> list[DocumentEntities]:
        self.calls.append((texts, labels, threshold))
        results = []
        for text in texts:
            word = text.split(" ")[0] if text else ""
            results.append(
                DocumentEntities(
                    entities=[Entity(labels[0], word, 0, len(word), 0.9)] if word else [],
                    rejected_spans=0,
                    truncated=False,
                )
            )
        return results


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Any:
    stub = _StubExtractor()
    monkeypatch.setattr(main, "extractor", stub)
    monkeypatch.setattr(main.settings, "load_on_startup", False)
    with TestClient(main.app) as test_client:
        test_client.stub = stub  # type: ignore[attr-defined]
        yield test_client


def test_health_reports_whether_the_weights_are_in_memory(client: Any) -> None:
    """A deploy watching this has to tell "still loading" from "crashed", which
    is why loading is a field rather than a status code."""
    body = client.get("/health").json()

    assert body == {"status": "ok", "loaded": True}


def test_info_names_the_weights_the_process_holds(client: Any) -> None:
    """The backend writes a model_registry row saying what it believes is
    running (spec 7.4). This is how that belief gets checked against reality."""
    body = client.get("/info").json()

    assert body["extractor_model_id"] == "fastino/gliner2-multi-v1"
    assert len(body["extractor_revision"]) == 40


def test_each_result_is_addressed_by_id_not_by_position(client: Any) -> None:
    """Order across an HTTP boundary is a promise nobody can check. The id is
    what pairs a result with the revision it belongs to."""
    response = client.post(
        "/extract",
        json={
            "documents": [
                {"id": "first", "text": "Python developer"},
                {"id": "second", "text": "Kubernetes engineer"},
            ],
            "labels": ["technology"],
        },
    )

    documents = response.json()["documents"]
    assert [d["id"] for d in documents] == ["first", "second"]
    assert documents[0]["entities"][0]["text"] == "Python"
    assert documents[1]["entities"][0]["text"] == "Kubernetes"


def test_the_response_names_what_produced_it(client: Any) -> None:
    """So a stored profile can record the model and revision without the backend
    having to trust its own configuration to match the running container."""
    body = client.post(
        "/extract",
        json={"documents": [{"id": "a", "text": "Go"}], "labels": ["technology"]},
    ).json()

    assert body["extractor_model_id"] == "fastino/gliner2-multi-v1"
    assert body["extractor_revision"]


def test_an_unloaded_model_is_a_503_rather_than_a_wait(client: Any) -> None:
    """Loading takes ten seconds and 4 GB. A request that triggers it looks like
    a hung service; ten concurrent ones look like an OOM."""
    from app.extractor import ModelNotLoaded

    async def _refuse(*args: object, **kwargs: object) -> list[DocumentEntities]:
        raise ModelNotLoaded("the extractor has not finished loading")

    client.stub.extract = _refuse

    response = client.post(
        "/extract",
        json={"documents": [{"id": "a", "text": "Go"}], "labels": ["technology"]},
    )

    assert response.status_code == 503


def test_an_empty_batch_is_refused_by_the_schema(client: Any) -> None:
    response = client.post("/extract", json={"documents": [], "labels": ["technology"]})

    assert response.status_code == 422


def test_a_batch_larger_than_the_cap_is_refused(client: Any) -> None:
    """The cap is memory: every document in a batch is resident at once, and the
    caller is a worker loop that would happily send a thousand."""
    response = client.post(
        "/extract",
        json={
            "documents": [{"id": str(i), "text": "Go"} for i in range(33)],
            "labels": ["technology"],
        },
    )

    assert response.status_code == 422


def test_an_unknown_field_is_refused_rather_than_ignored(client: Any) -> None:
    """A caller sending `treshold` should be told, not quietly given the default
    while believing it set one."""
    response = client.post(
        "/extract",
        json={
            "documents": [{"id": "a", "text": "Go"}],
            "labels": ["technology"],
            "treshold": 0.9,
        },
    )

    assert response.status_code == 422
