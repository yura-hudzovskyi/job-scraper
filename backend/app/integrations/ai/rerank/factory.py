"""Which rerank engines this deployment has, in the order to try them — see
docs/ai-pipeline-v3.md (D3).

Order is quality-first, and the list always ends with the local engine so that
reranking degrades in speed rather than disappearing. Like the LLM legs, an
engine whose credentials aren't configured is simply absent rather than one that
always fails.

The order is a hypothesis, not a measurement: a model moves up only after it
beats the current one on the CV/job validation set (phase 9).
"""

from app.config.settings import Settings
from app.integrations.ai.embeddings.factory import build_cross_encoder_provider
from app.integrations.ai.rerank.base import RerankEngine
from app.integrations.ai.rerank.providers import (
    CloudflareRerankEngine,
    LocalCrossEncoderRerankEngine,
    VoyageRerankEngine,
)


def rerank_engines(settings: Settings) -> list[RerankEngine]:
    engines: list[RerankEngine] = []

    if settings.voyage_api_key:
        engines.append(VoyageRerankEngine(settings.voyage_api_key, settings.voyage_rerank_model))

    if settings.cloudflare_account_id and settings.cloudflare_api_token:
        engines.append(
            CloudflareRerankEngine(
                settings.cloudflare_account_id,
                settings.cloudflare_api_token,
                settings.cloudflare_rerank_model,
            )
        )

    cross_encoder = build_cross_encoder_provider(settings)
    if cross_encoder is not None and settings.cross_encoder_model:
        engines.append(LocalCrossEncoderRerankEngine(cross_encoder, settings.cross_encoder_model))

    return engines
