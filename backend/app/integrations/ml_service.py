"""Client for the self-hosted model runtime (spec 3.5.3).

The counterpart to `voyage.py`, and deliberately shaped like it: one small class,
plain httpx, no swallowed errors. The difference is which side of the 3.5.1 line
it sits on — Voyage answers "are these two documents alike", this answers "what
does this document say", and only the second one runs on our own hardware.

Timeouts are generous because a forward pass is genuinely slow: 2.14 s per
Ukrainian vacancy measured, and a batch of eight is one request. This is
background work behind a queue, so waiting costs nothing and a premature timeout
costs a whole batch.
"""

from dataclasses import dataclass
from typing import Any

import httpx

# A cold container spends ~10 s loading weights before it answers anything, and
# a batch of 8 long vacancies is ~20 s of CPU. Both are normal, neither is a
# hang.
_TIMEOUT_SECONDS = 180.0


@dataclass(frozen=True)
class ExtractedEntity:
    """One mention the model found, located exactly in the text that was sent."""

    label: str
    text: str
    start_char: int
    end_char: int
    confidence: float


@dataclass(frozen=True)
class ExtractedDocument:
    id: str
    entities: list[ExtractedEntity]
    # Spans ml-service dropped because their offsets did not quote the document.
    # Carried through rather than logged and forgotten: a number that starts
    # climbing is how a model or tokeniser change announces itself.
    rejected_spans: int
    truncated: bool


@dataclass(frozen=True)
class ExtractionBatch:
    extractor_model_id: str
    extractor_revision: str
    documents: list[ExtractedDocument]

    @property
    def model_fingerprint(self) -> str:
        """What gets stored as `extractor_model_id` on the profile revision.

        Model and revision together, because the model name alone does not
        identify weights — spec 2.6 wants a stored profile to name what produced
        it precisely enough to reproduce.
        """
        return f"{self.extractor_model_id}@{self.extractor_revision[:12]}"


class MlServiceClient:
    def __init__(self, base_url: str, client: httpx.AsyncClient | None = None):
        self._base_url = base_url.rstrip("/")
        self._client = client

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self._client is not None:
            response = await self._client.post(f"{self._base_url}{path}", json=payload)
        else:
            async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
                response = await client.post(f"{self._base_url}{path}", json=payload)
        response.raise_for_status()
        result: dict[str, Any] = response.json()
        return result

    async def _get(self, path: str) -> dict[str, Any]:
        if self._client is not None:
            response = await self._client.get(f"{self._base_url}{path}")
        else:
            async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
                response = await client.get(f"{self._base_url}{path}")
        response.raise_for_status()
        result: dict[str, Any] = response.json()
        return result

    async def info(self) -> dict[str, Any]:
        """Which weights the running container actually holds."""
        return await self._get("/info")

    async def extract(
        self,
        documents: list[tuple[str, str]],
        labels: list[str],
        threshold: float | None = None,
    ) -> ExtractionBatch:
        """Find `labels` in each (id, text) pair, with offsets and model scores."""
        payload: dict[str, Any] = {
            "documents": [{"id": doc_id, "text": text} for doc_id, text in documents],
            "labels": labels,
        }
        if threshold is not None:
            payload["threshold"] = threshold

        body = await self._post("/extract", payload)
        return ExtractionBatch(
            extractor_model_id=body["extractor_model_id"],
            extractor_revision=body["extractor_revision"],
            documents=[
                ExtractedDocument(
                    id=document["id"],
                    entities=[
                        ExtractedEntity(
                            label=entity["label"],
                            text=entity["text"],
                            start_char=entity["start_char"],
                            end_char=entity["end_char"],
                            confidence=entity["confidence"],
                        )
                        for entity in document["entities"]
                    ],
                    rejected_spans=document["rejected_spans"],
                    truncated=document["truncated"],
                )
                for document in body["documents"]
            ],
        )
