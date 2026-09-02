"""Which embedding lanes this deployment has, and in what order retrieval should
prefer them — see docs/ai-pipeline-v3.md (C2, C3).

A lane is one model's vector space. Two roles exist:

- **quality**: the best model available, used when its coverage is high enough.
- **durable**: an always-available fallback, so retrieval keeps working when a
  one-off free token pool runs out or a hosted provider is down. The local
  sentence-transformers model fills this role with no key and no quota, which is
  why it is the default rather than a curiosity.

Lane ids are `provider:model:v1`. The plan writes them with the dimension baked
in ("voyage-4-large:1024:v1"); the dimension is recorded on the lane row instead,
observed from the first vector actually produced, because a hard-coded dimension
table is exactly the kind of thing that quietly goes stale when a provider ships
a new variant.

Nothing here builds a provider eagerly: a lane whose credentials aren't set is
simply absent, and one that is configured but never queried never pays for its
import.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

from app.config.settings import Settings
from app.integrations.ai.embeddings.base import EmbeddingProvider

QUALITY = "quality"
DURABLE = "durable"

LOCAL = "local"
CLOUDFLARE = "cloudflare"
VOYAGE = "voyage"


@dataclass(frozen=True)
class LaneSpec:
    provider: str
    model: str
    role: str
    build: Callable[[], EmbeddingProvider] = field(repr=False)

    @property
    def id(self) -> str:
        return f"{self.provider}:{self.model}:v1"


def _voyage_lane(settings: Settings) -> LaneSpec | None:
    if not settings.voyage_api_key:
        return None
    api_key, model = settings.voyage_api_key, settings.voyage_embedding_model

    def build() -> EmbeddingProvider:
        from app.integrations.ai.embeddings.voyage_provider import VoyageEmbeddingProvider

        return VoyageEmbeddingProvider(api_key, model)

    return LaneSpec(provider=VOYAGE, model=model, role=QUALITY, build=build)


def _cloudflare_lane(settings: Settings) -> LaneSpec | None:
    if not (settings.cloudflare_account_id and settings.cloudflare_api_token):
        return None
    account_id = settings.cloudflare_account_id
    token = settings.cloudflare_api_token
    model = settings.cloudflare_embedding_model

    def build() -> EmbeddingProvider:
        from app.integrations.ai.embeddings.cloudflare_provider import CloudflareEmbeddingProvider

        return CloudflareEmbeddingProvider(account_id, token, model)

    return LaneSpec(provider=CLOUDFLARE, model=model, role=DURABLE, build=build)


def _local_lane(settings: Settings) -> LaneSpec | None:
    if settings.embedding_provider != "sentence_transformers":
        return None
    model = settings.embedding_model

    def build() -> EmbeddingProvider:
        from app.integrations.ai.embeddings.factory import build_embedding_provider

        provider = build_embedding_provider(settings)
        assert provider is not None  # guarded by the branch above
        return provider

    return LaneSpec(provider=LOCAL, model=model, role=DURABLE, build=build)


def lanes_for(settings: Settings) -> list[LaneSpec]:
    """Every configured lane, quality first. More than one durable lane can
    exist (a hosted BGE-M3 and the local model); they stay separate lanes because
    "same model family" is not "same vectors" until someone has actually verified
    the numbers match (docs/ai-pipeline-v3.md, C3)."""
    candidates = [_voyage_lane(settings), _cloudflare_lane(settings), _local_lane(settings)]
    return [lane for lane in candidates if lane is not None]


def preferred_lane(lanes: list[LaneSpec]) -> LaneSpec | None:
    """The lane retrieval should use when nothing is known about coverage: the
    quality one if configured, else the first durable one."""
    return next((lane for lane in lanes if lane.role == QUALITY), None) or next(iter(lanes), None)
