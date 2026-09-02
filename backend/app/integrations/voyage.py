"""Voyage AI — the one external model provider this app uses.

Two endpoints, one API key, one HTTP client each: `embed` turns text into a
vector, `rerank` reads a query and a batch of documents *together* and scores how
well each answers it. The whole matching pipeline is those two calls and nothing
else (see docs/pipeline.md).

Plain httpx rather than a vendor SDK: each call is one POST, and a dependency to
save a dozen lines isn't a trade worth making. Errors are raised as httpx status
errors — callers decide what a failed batch means, this layer never swallows one.

`input_type` is deliberately not sent to the embeddings endpoint. Voyage uses it
to distinguish a short search query from a stored document; this app compares one
document (a CV) against another (a posting), so tagging either side as a "query"
would skew the comparison.
"""

from typing import Any

import httpx

_BASE_URL = "https://api.voyageai.com/v1"
_TIMEOUT_SECONDS = 60.0

DEFAULT_EMBEDDING_MODEL = "voyage-4-large"
DEFAULT_RERANK_MODEL = "rerank-3"


class VoyageClient:
    def __init__(
        self,
        api_key: str,
        embedding_model: str = DEFAULT_EMBEDDING_MODEL,
        rerank_model: str = DEFAULT_RERANK_MODEL,
        client: httpx.AsyncClient | None = None,
    ):
        self._api_key = api_key
        self.embedding_model = embedding_model
        self.rerank_model = rerank_model
        self._client = client

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._api_key}"}
        if self._client is not None:
            response = await self._client.post(f"{_BASE_URL}{path}", json=payload, headers=headers)
        else:
            async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
                response = await client.post(f"{_BASE_URL}{path}", json=payload, headers=headers)
        response.raise_for_status()
        body: dict[str, Any] = response.json()
        return body

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """One vector per input text, in the order given."""
        if not texts:
            return []
        body = await self._post(
            "/embeddings", {"input": texts, "model": self.embedding_model}
        )
        # Results are documented as coming back in request order; sorting by the
        # index the API echoes back costs nothing and removes the assumption.
        ordered = sorted(body.get("data", []), key=lambda item: item.get("index", 0))
        return [item["embedding"] for item in ordered]

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        """Relevance of each document to `query`, in the order the documents were
        given. Voyage returns them ranked, not in input order — the echoed index
        is what puts them back."""
        if not documents:
            return []
        body = await self._post(
            "/rerank", {"query": query, "documents": documents, "model": self.rerank_model}
        )
        scores = [0.0] * len(documents)
        for item in body.get("data", []):
            scores[int(item["index"])] = float(item["relevance_score"])
        return scores
