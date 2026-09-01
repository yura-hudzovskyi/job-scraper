"""One router per capability, N legs, and a state store that remembers which of
them are currently worth calling — see docs/ai-pipeline-v3.md (5).

This replaces FallbackLLMProvider's fixed primary/fallback pair. The difference
that matters isn't the leg count: it's that the router *classifies* what went
wrong (errors.py), parks the leg for exactly as long as that failure warrants
(state.py), and tells the caller when there is genuinely no capacity left,
instead of every call site re-deriving "was that a 429?" for itself.

It implements the LLMProvider protocol, so services keep depending on "give me a
structured completion", never on a vendor.

Two behaviours are deliberate:

- **A schema failure retries the same leg once, then moves on.** The provider is
  healthy; its answer just didn't validate, and models are non-deterministic
  enough that one repair attempt usually lands. Parking a working provider for
  that would be wrong.
- **No capacity is an exception, not a silent None.** Callers differ in what they
  should do about it — the job pipeline degrades to rules, the reranker skips its
  verdict, an interactive request reschedules — and NoCapacity carries the reset
  so a Celery task can come back exactly when a leg reopens rather than guessing.
"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from enum import StrEnum

from app.integrations.ai.llm.base import LLMProvider, LLMResult, T
from app.integrations.ai.routing.errors import FailureKind, classify
from app.integrations.ai.routing.state import ProviderStateStore

logger = logging.getLogger(__name__)

_REPAIR_NOTE = (
    "\n\nYour previous answer could not be parsed. Reply with valid JSON matching "
    "the requested schema exactly, and nothing else."
)


class Capability(StrEnum):
    """What the call is *for*. Policy, budgets and quotas are all keyed on this
    rather than on the vendor, so swapping providers never touches a call site."""

    PROFILE_EXTRACTION = "profile_extraction"
    JOB_EXTRACTION = "job_extraction"
    MATCH_ENRICHMENT = "match_enrichment"


@dataclass(frozen=True)
class ModelLeg:
    provider: str
    model: str
    # Deferred so a leg that never runs doesn't pay for its SDK import.
    build: Callable[[], LLMProvider] = field(repr=False)

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.model}"


class NoCapacity(RuntimeError):
    """Every leg for this capability is unavailable right now. `retry_after` is
    the soonest any of them reopens, when that is known."""

    def __init__(self, capability: Capability, retry_after: timedelta | None = None):
        self.capability = capability
        self.retry_after = retry_after
        super().__init__(
            f"no LLM capacity for {capability.value}"
            + (f" — retry in {int(retry_after.total_seconds())}s" if retry_after else "")
        )


class CapacityBudget:
    """What a capability may spend today, checked before a call is made. See
    app/integrations/ai/quota/budget.py for the implementation the factory
    injects; the router only needs "may I spend one call"."""

    async def try_consume(self) -> bool: ...  # pragma: no cover - protocol

    async def retry_after(self) -> timedelta | None: ...  # pragma: no cover - protocol


class LlmRouter:
    def __init__(
        self,
        capability: Capability,
        legs: Sequence[ModelLeg],
        state: ProviderStateStore,
        budget: CapacityBudget | None = None,
    ):
        self._capability = capability
        self._legs = list(legs)
        self._state = state
        self._budget = budget

    async def structured_completion(self, prompt: str, schema: type[T]) -> LLMResult[T]:
        if self._budget is not None and not await self._budget.try_consume():
            logger.info("%s is out of budget for today", self._capability.value)
            raise NoCapacity(self._capability, await self._budget.retry_after())

        soonest: timedelta | None = None
        for leg in self._legs:
            state = await self._state.state(leg.key)
            if not state.available:
                soonest = _soonest(soonest, state.retry_after)
                continue

            result = await self._try_leg(leg, prompt, schema)
            if result is not None:
                await self._state.record_success(leg.key)
                return result

        raise NoCapacity(self._capability, soonest)

    async def _try_leg(self, leg: ModelLeg, prompt: str, schema: type[T]) -> LLMResult[T] | None:
        """None means "this leg didn't produce an answer" — it has already been
        parked for however long its failure warrants."""
        provider = leg.build()
        for attempt in range(2):
            try:
                return await provider.structured_completion(prompt, schema)
            except Exception as exc:
                failure = classify(exc)
                await self._state.record_failure(leg.key, failure)
                logger.warning(
                    "%s leg %s failed (%s, status=%s) — cooling down %ss",
                    self._capability.value,
                    leg.key,
                    failure.kind.value,
                    failure.status,
                    int(failure.cooldown.total_seconds()),
                    exc_info=failure.kind is FailureKind.FATAL,
                )
                if failure.kind is FailureKind.FATAL:
                    # Worth an operator's attention: nothing about a bad key or a
                    # retired model id gets better by trying again later.
                    logger.error(
                        "%s leg %s is misconfigured and has been disabled for now — "
                        "check the API key and model id on the System page",
                        self._capability.value,
                        leg.key,
                    )
                if failure.kind is not FailureKind.SCHEMA or attempt == 1:
                    return None
                prompt = f"{prompt}{_REPAIR_NOTE}"
        return None


def _soonest(current: timedelta | None, candidate: timedelta | None) -> timedelta | None:
    if candidate is None:
        return current
    if current is None:
        return candidate
    return min(current, candidate)
