"""Voyage embeddings over their REST API — the quality lane candidate in
docs/ai-pipeline-v3.md (C3).

Plain httpx rather than a vendor SDK: the endpoint is one POST, and adding a
dependency to save six lines isn't a trade worth making. Failures are raised as
httpx status errors, which the caller's own retry/lane handling reads the same
way it reads any other provider.

`input_type` is deliberately not sent. Voyage uses it to distinguish a short
search query from a stored document, and this app compares one document (a CV
section) against another (a posting section) — symmetric, so tagging one side as
a "query" would skew it.
"""

import httpx

DEFAULT_MODEL = "voyage-4-large"
_URL = "https://api.voyageai.com/v1/embeddings"
_TIMEOUT_SECONDS = 30.0


class VoyageEmbeddingProvider:
    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        client: httpx.AsyncClient | None = None,
    ):
        self._api_key = api_key
        self._model = model
        self._client = client

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        payload = {"input": texts, "model": self._model}
        headers = {"Authorization": f"Bearer {self._api_key}"}

        if self._client is not None:
            response = await self._client.post(_URL, json=payload, headers=headers)
        else:
            async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
                response = await client.post(_URL, json=payload, headers=headers)
        response.raise_for_status()

        data = response.json().get("data", [])
        # The API documents results in request order; sorting by the index it
        # echoes back costs nothing and removes the assumption.
        ordered = sorted(data, key=lambda item: item.get("index", 0))
        return [item["embedding"] for item in ordered]
