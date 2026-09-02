"""The three rerank engines this app can actually run today — see
docs/ai-pipeline-v3.md (D3).

- **Voyage `rerank-3`**: the quality option while its free token pool lasts.
- **Cloudflare `@cf/baai/bge-reranker-base`**: an open model on a recurring daily
  allowance, so there is a hosted option that doesn't run out one day and stay
  out.
- **Local cross-encoder**: no key, no quota, no network. Slower per pair on CPU,
  but it is the reason reranking can't become unavailable — the same model the
  semantic scorer already loads, reused rather than duplicated.

Hosted adapters are plain httpx against one documented endpoint, and both raise
on failure: the caller's contract is that a failed set is rerun whole on the next
engine, never stitched together.
"""

import httpx

from app.integrations.ai.embeddings.base import CrossEncoderProvider

VOYAGE_DEFAULT_MODEL = "rerank-3"
CLOUDFLARE_DEFAULT_MODEL = "@cf/baai/bge-reranker-base"

_VOYAGE_URL = "https://api.voyageai.com/v1/rerank"
_TIMEOUT_SECONDS = 30.0


class VoyageRerankEngine:
    def __init__(
        self,
        api_key: str,
        model: str = VOYAGE_DEFAULT_MODEL,
        client: httpx.AsyncClient | None = None,
    ):
        self._api_key = api_key
        self._model = model
        self._client = client

    @property
    def model_id(self) -> str:
        return f"voyage:{self._model}"

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        if not documents:
            return []

        payload = {"query": query, "documents": documents, "model": self._model}
        headers = {"Authorization": f"Bearer {self._api_key}"}

        if self._client is not None:
            response = await self._client.post(_VOYAGE_URL, json=payload, headers=headers)
        else:
            async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
                response = await client.post(_VOYAGE_URL, json=payload, headers=headers)
        response.raise_for_status()

        # The API returns results ranked, not in input order; the echoed index is
        # what puts them back where the caller expects them.
        scores = [0.0] * len(documents)
        for item in response.json().get("data", []):
            scores[int(item["index"])] = float(item["relevance_score"])
        return scores


class CloudflareRerankEngine:
    def __init__(
        self,
        account_id: str,
        api_token: str,
        model: str = CLOUDFLARE_DEFAULT_MODEL,
        client: httpx.AsyncClient | None = None,
    ):
        self._account_id = account_id
        self._api_token = api_token
        self._model = model
        self._client = client

    @property
    def model_id(self) -> str:
        return f"cloudflare:{self._model}"

    @property
    def _url(self) -> str:
        return f"https://api.cloudflare.com/client/v4/accounts/{self._account_id}/ai/run/{self._model}"

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        if not documents:
            return []

        payload = {"query": query, "contexts": [{"text": document} for document in documents]}
        headers = {"Authorization": f"Bearer {self._api_token}"}

        if self._client is not None:
            response = await self._client.post(self._url, json=payload, headers=headers)
        else:
            async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
                response = await client.post(self._url, json=payload, headers=headers)
        response.raise_for_status()

        body = response.json()
        if not body.get("success", False):
            raise httpx.HTTPStatusError(
                f"Cloudflare Workers AI rejected the rerank: {body.get('errors')}",
                request=response.request,
                response=response,
            )

        scores = [0.0] * len(documents)
        for item in body["result"]["response"]:
            scores[int(item["id"])] = float(item["score"])
        return scores


class LocalCrossEncoderRerankEngine:
    """The cross-encoder the semantic scorer already loads, asked a different
    question: one query against many documents instead of one pair."""

    def __init__(self, cross_encoder: CrossEncoderProvider, model: str):
        self._cross_encoder = cross_encoder
        self._model = model

    @property
    def model_id(self) -> str:
        return f"local:{self._model}"

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        if not documents:
            return []
        return await self._cross_encoder.score([(query, document) for document in documents])
