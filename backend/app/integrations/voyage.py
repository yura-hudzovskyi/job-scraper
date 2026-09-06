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

import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

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

# Voyage also caps tokens per minute, separately from tokens per batch: 2 000 000
# for rerank-3. A first full pass over this corpus is about 2.9 million, so it
# hits the ceiling however neatly the batches are split — the limit is a rate,
# and no batch size makes a rate go away. Waiting is the whole remedy, and it is
# cheap: this is background work behind a queue, and after the first pass the
# cache means later runs rerank only what changed.
RATE_LIMIT_STATUS = 429
MAX_RATE_LIMIT_RETRIES = 6
# A TPM window is a minute, so a shorter wake-up just spends another request
# discovering the same thing.
RATE_LIMIT_WAIT_SECONDS = 65.0


class VoyageError(httpx.HTTPStatusError):
    """A Voyage error that carries what Voyage said about it."""


def _retry_after(response: httpx.Response, default: float) -> float:
    """How long the provider asked us to wait, if it said."""
    header = response.headers.get("retry-after")
    if header:
        try:
            return max(1.0, float(header))
        except ValueError:
            pass
    return default


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

    async def _send(self, path: str, payload: dict[str, Any]) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self._api_key}"}
        if self._client is not None:
            return await self._client.post(f"{_BASE_URL}{path}", json=payload, headers=headers)
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            return await client.post(f"{_BASE_URL}{path}", json=payload, headers=headers)

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """One call, waiting out a rate limit rather than failing on it.

        Only 429 is retried. Every other error is a fact about the request —
        a batch over the token limit, a bad model name, an expired key — and
        retrying it just asks the same question again more expensively.
        """
        response = await self._send(path, payload)
        for attempt in range(MAX_RATE_LIMIT_RETRIES):
            if response.status_code != RATE_LIMIT_STATUS:
                break
            wait = _retry_after(response, RATE_LIMIT_WAIT_SECONDS)
            logger.info(
                "rate limited by %s, waiting %.0fs (attempt %d of %d)",
                path,
                wait,
                attempt + 1,
                MAX_RATE_LIMIT_RETRIES,
            )
            await asyncio.sleep(wait)
            response = await self._send(path, payload)

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

    async def rerank(self, query: str, documents: list[str]) -> dict[int, float]:
        """Relevance of each document to `query`, in the order the documents were given.

        Split into batches that fit the provider's token limit. Splitting is safe
        for the scores themselves: a reranker returns a score per (query,
        document) pair, so a document's relevance does not depend on which other
        documents travelled with it. Batching changes the number of requests, not
        the answer.

        Returns a score per document *index* rather than a list, so a caller can
        tell "scored 0.0" from "not scored". A partial answer is a real outcome
        here: the batches after a failure are missing, and treating them as
        zeroes would rank them below every vacancy the reranker disliked.

        Voyage returns each batch ranked rather than in input order — the echoed
        index is what puts it back.
        """
        if not documents:
            return {}

        scores: dict[int, float] = {}
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
