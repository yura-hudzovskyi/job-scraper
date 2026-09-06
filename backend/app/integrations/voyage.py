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

# Voyage rejects a rerank batch over 600 000 tokens with TOO_MANY_TOKENS_IN_BATCH.
# The budget here is in UTF-8 *bytes*, not characters, and that is the whole
# point: a byte-level BPE tokenizer emits at most one token per byte, so a
# byte-budgeted batch is safe in any script, while a character budget is not.
#
# Measured, because the difference is not small. 500 real vacancies came to
# 1 548 088 characters — about 387 000 tokens by the usual chars/4 rule of thumb
# — and Voyage counted 931 442. Cyrillic costs two to three times what the rule
# assumes, so a corpus that is 36% Ukrainian breaks a character-based estimate
# by more than the headroom anyone would leave.
MAX_RERANK_BATCH_BYTES = 500_000


class VoyageError(httpx.HTTPStatusError):
    """A Voyage error that carries what Voyage said about it."""


def _byte_size(text: str) -> int:
    return len(text.encode("utf-8"))


def _batches(documents: list[str], query: str) -> list[list[int]]:
    """Document indices grouped into requests that fit the batch limit.

    The query is counted against every batch because it is sent with every
    request. A single document larger than the budget still gets its own batch:
    refusing it here would silently drop a vacancy from the ranking, and the
    provider truncating it is the better failure — it is visible in the score
    and the document is capped upstream anyway.
    """
    budget = MAX_RERANK_BATCH_BYTES - _byte_size(query)
    if budget <= 0:
        # A query this large leaves no room for documents; one per request is
        # the most that can be attempted, and the provider decides.
        return [[index] for index in range(len(documents))]

    batches: list[list[int]] = []
    current: list[int] = []
    used = 0
    for index, document in enumerate(documents):
        size = _byte_size(document)
        if current and used + size > budget:
            batches.append(current)
            current, used = [], 0
        current.append(index)
        used += size
    if current:
        batches.append(current)
    return batches


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
        if response.is_error:
            # The body is where Voyage says which limit was hit and by how much
            # — "max allowed tokens per submitted batch is 600000, your batch has
            # 931442". raise_for_status() alone reports a bare 400, which is how
            # a rerank that never once succeeded went unnoticed in production.
            raise VoyageError(
                f"{response.status_code} from {path}: {response.text[:500]}",
                request=response.request,
                response=response,
            )
        body: dict[str, Any] = response.json()
        return body

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """One vector per input text, in the order given."""
        if not texts:
            return []
        body = await self._post("/embeddings", {"input": texts, "model": self.embedding_model})
        # Results are documented as coming back in request order; sorting by the
        # index the API echoes back costs nothing and removes the assumption.
        ordered = sorted(body.get("data", []), key=lambda item: item.get("index", 0))
        return [item["embedding"] for item in ordered]

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        """Relevance of each document to `query`, in the order the documents were given.

        Split into batches that fit the provider's token limit. Splitting is safe
        for the scores themselves: a reranker returns a score per (query,
        document) pair, so a document's relevance does not depend on which other
        documents travelled with it. Batching changes the number of requests, not
        the answer.

        Voyage returns each batch ranked rather than in input order — the echoed
        index is what puts it back.
        """
        if not documents:
            return []

        scores = [0.0] * len(documents)
        for batch in _batches(documents, query):
            body = await self._post(
                "/rerank",
                {
                    "query": query,
                    "documents": [documents[index] for index in batch],
                    "model": self.rerank_model,
                },
            )
            for item in body.get("data", []):
                scores[batch[int(item["index"])]] = float(item["relevance_score"])
        return scores
