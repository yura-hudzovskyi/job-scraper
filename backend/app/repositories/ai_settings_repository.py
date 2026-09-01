"""Persists UI-editable AI model overrides — see app/config/runtime_settings.py,
which layers these on top of the .env-derived Settings so a model changed through
the System page takes effect on the very next LLM call, no redeploy or restart
needed. Settings.* itself remains the zero-config default this always falls back
to. Stored in the same Redis instance as REDIS_URL (the app cache db, not
Celery's broker/backend — see app/api/routes/system.py for those), one hash so a
single HGETALL reads every override at once.

Reads fail open (empty dict) on a Redis error, same contract as
circuit_breaker.py and budget.py: an optional UI-config layer must never itself
block or crash a call the .env-configured default could still serve. Writes are
the opposite — an explicit "Save" from the System page that silently didn't
persist would be exactly the kind of invisible failure this whole cleanup is
meant to get rid of, so set_override lets a Redis error propagate; the route
turns it into a real error response instead of a false "saved."
"""

import redis.asyncio as redis

_KEY = "ai:model_overrides"

# Exactly the two "which model" knobs the pipeline actually runs on — provider
# *selection* (LLM_PROVIDER, EMBEDDING_PROVIDER) and API keys stay .env-only/out
# of Redis on purpose: those are infra/secrets decisions, not something to flip
# at runtime.
OVERRIDABLE_FIELDS = ("groq_model", "gemini_model")


class UnknownAiSettingField(ValueError):
    def __init__(self, field: str):
        super().__init__(f"unknown AI model field: {field!r} (expected one of {OVERRIDABLE_FIELDS})")


class AiSettingsRepository:
    def __init__(self, redis_client: redis.Redis):
        self._redis = redis_client

    async def get_overrides(self) -> dict[str, str]:
        try:
            raw = await self._redis.hgetall(_KEY)
        except redis.RedisError:
            return {}
        # redis-py's stubs type HGETALL as dict[bytes | str, bytes | str] regardless
        # of decode_responses=True (which does make every value a plain str at
        # runtime) — normalize explicitly rather than an unchecked type: ignore.
        return {str(field): str(model) for field, model in raw.items()}

    async def set_override(self, field: str, value: str | None) -> None:
        """None or "" clears that field's override (falls back to Settings again);
        any other string persists it."""
        if field not in OVERRIDABLE_FIELDS:
            raise UnknownAiSettingField(field)
        if not value:
            await self._redis.hdel(_KEY, field)
        else:
            await self._redis.hset(_KEY, field, value)
