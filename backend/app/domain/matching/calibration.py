"""Turning one model's raw relevance number into a comparable one — see
docs/ai-pipeline-v3.md (G3).

Raw rerank scores are not comparable across models: Voyage returns a 0-1
relevance, a BGE reranker returns an unbounded logit, and a cross-encoder returns
whatever its regression head produces. Blending or thresholding them without a
per-model mapping means a score that silently changes meaning when a provider
falls back.

These mappings are hand-tuned, and deliberately shaped rather than fitted: there
is no labelled data yet to fit them against (phase 9 builds it). That is why
CALIBRATION_VERSION is recorded with every result — when real calibration
arrives, the results produced under this version are identifiable rather than
quietly reinterpreted — and why anything calibrated this way carries reduced
confidence.
"""

import math

CALIBRATION_VERSION = "1"

# Providers that already return a bounded 0-1 relevance need no reshaping, only a
# clamp against the occasional out-of-range value. Voyage documents a relevance
# score; the local cross-encoder applies its own sigmoid before returning
# (app/integrations/ai/embeddings/cross_encoder_provider.py).
#
# This has to be decided per *model*, never per value: reshaping only the
# out-of-range numbers would map a raw -1 above a raw 0, which reverses two
# results while looking like a harmless special case.
_BOUNDED_PREFIXES = ("voyage:", "local:")


def _sigmoid(value: float) -> float:
    # Guard against overflow on the extreme logits some rerankers emit.
    if value >= 30:
        return 1.0
    if value <= -30:
        return 0.0
    return 1.0 / (1.0 + math.exp(-value))


def calibrate_relevance(model_id: str, raw: float) -> float:
    """Raw score -> 0-1 relevance for this model. Unknown models are treated the
    same as logit-producing ones: a sigmoid is monotone, so the *ranking* is
    always preserved even when the absolute value is only approximate."""
    if model_id.startswith(_BOUNDED_PREFIXES):
        return max(0.0, min(1.0, raw))
    return _sigmoid(raw)
