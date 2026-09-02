"""Lane selection and the two hosted adapters. The adapters are covered against a
mocked transport rather than a live account: what's worth pinning is the request
shape and how a failure is recognised, not that the vendor is up.
"""

import httpx
import pytest

from app.config.settings import Settings
from app.integrations.ai.embeddings.cloudflare_provider import CloudflareEmbeddingProvider
from app.integrations.ai.embeddings.lanes import DURABLE, QUALITY, lanes_for, preferred_lane
from app.integrations.ai.embeddings.voyage_provider import VoyageEmbeddingProvider


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "voyage_api_key": None,
        "cloudflare_account_id": None,
        "cloudflare_api_token": None,
    }
    return Settings(**{**base, **overrides})  # type: ignore[arg-type]


def test_the_local_model_is_always_a_lane_so_retrieval_never_depends_on_a_key() -> None:
    lanes = lanes_for(_settings())

    assert [lane.id for lane in lanes] == ["local:all-MiniLM-L6-v2:v1"]
    assert lanes[0].role == DURABLE


def test_a_configured_quality_lane_is_preferred() -> None:
    lanes = lanes_for(_settings(voyage_api_key="pa-fake"))

    assert [lane.id for lane in lanes] == [
        "voyage:voyage-4-large:v1",
        "local:all-MiniLM-L6-v2:v1",
    ]
    chosen = preferred_lane(lanes)
    assert chosen is not None and chosen.role == QUALITY


def test_a_hosted_durable_lane_and_the_local_one_stay_separate() -> None:
    # Same model family is not the same vector space until someone has verified
    # the numbers match, so they never share a lane id.
    lanes = lanes_for(
        _settings(cloudflare_account_id="acct", cloudflare_api_token="cf-fake")
    )

    assert [lane.id for lane in lanes] == [
        "cloudflare:@cf/baai/bge-m3:v1",
        "local:all-MiniLM-L6-v2:v1",
    ]
    assert len({lane.id for lane in lanes}) == 2


def test_a_lane_without_credentials_is_absent_rather_than_a_failing_lane() -> None:
    lanes = lanes_for(_settings(cloudflare_account_id="acct"))  # token missing

    assert all(lane.provider != "cloudflare" for lane in lanes)


@pytest.mark.asyncio
async def test_voyage_sends_the_documented_request_and_orders_the_results() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = request.read().decode()
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [0.3, 0.4]},
                    {"index": 0, "embedding": [0.1, 0.2]},
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = VoyageEmbeddingProvider("pa-fake", "voyage-4-large", client=client)

    vectors = await provider.embed(["first", "second"])

    assert vectors == [[0.1, 0.2], [0.3, 0.4]]
    assert seen["url"] == "https://api.voyageai.com/v1/embeddings"
    assert seen["auth"] == "Bearer pa-fake"
    assert "voyage-4-large" in str(seen["body"])


@pytest.mark.asyncio
async def test_an_empty_batch_never_reaches_the_provider() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
        raise AssertionError("no request should be made for an empty batch")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    assert await VoyageEmbeddingProvider("pa-fake", client=client).embed([]) == []


@pytest.mark.asyncio
async def test_cloudflare_unwraps_its_envelope() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "accounts/acct/ai/run/@cf/baai/bge-m3" in str(request.url)
        return httpx.Response(200, json={"success": True, "result": {"data": [[0.5, 0.6]]}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = CloudflareEmbeddingProvider("acct", "cf-fake", client=client)

    assert await provider.embed(["text"]) == [[0.5, 0.6]]


@pytest.mark.asyncio
async def test_cloudflare_treats_a_200_with_success_false_as_a_failure() -> None:
    # Letting it through would store "no vectors" as if it were an answer.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": False, "errors": [{"message": "no neurons"}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = CloudflareEmbeddingProvider("acct", "cf-fake", client=client)

    with pytest.raises(httpx.HTTPStatusError):
        await provider.embed(["text"])
