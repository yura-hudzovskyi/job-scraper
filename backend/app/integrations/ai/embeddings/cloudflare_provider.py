"""Cloudflare Workers AI embeddings — the durable lane in
docs/ai-pipeline-v3.md (C3): an open model (BGE-M3) on a recurring free daily
allowance, so the pipeline keeps a working vector space even when a one-off free
token pool runs out.

Same plain-httpx shape as the Voyage adapter. Cloudflare wraps everything in its
own envelope (`{"success": ..., "result": ..., "errors": [...]}`) and answers 200
even for some failures, so the envelope is checked rather than trusted.
"""

import httpx

DEFAULT_MODEL = "@cf/baai/bge-m3"
_TIMEOUT_SECONDS = 30.0


class CloudflareEmbeddingProvider:
    def __init__(
        self,
        account_id: str,
        api_token: str,
        model: str = DEFAULT_MODEL,
        client: httpx.AsyncClient | None = None,
    ):
        self._account_id = account_id
        self._api_token = api_token
        self._model = model
        self._client = client

    @property
    def _url(self) -> str:
        return f"https://api.cloudflare.com/client/v4/accounts/{self._account_id}/ai/run/{self._model}"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        payload = {"text": texts}
        headers = {"Authorization": f"Bearer {self._api_token}"}

        if self._client is not None:
            response = await self._client.post(self._url, json=payload, headers=headers)
        else:
            async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
                response = await client.post(self._url, json=payload, headers=headers)
        response.raise_for_status()

        body = response.json()
        if not body.get("success", False):
            # A 200 with success=false is still a failure; letting it through
            # would store an empty vector set as if it were an answer.
            raise httpx.HTTPStatusError(
                f"Cloudflare Workers AI rejected the request: {body.get('errors')}",
                request=response.request,
                response=response,
            )
        return list(body["result"]["data"])
