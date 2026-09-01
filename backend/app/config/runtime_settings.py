"""Layers Redis-persisted, UI-editable model overrides (see
app/repositories/ai_settings_repository.py) on top of the .env-derived Settings.
Settings.get_settings() itself never changes at runtime and stays the zero-config
default — this returns a copy with just the overridden model fields replaced, so
every call site keeps building providers from a plain Settings exactly as before
(see app/integrations/ai/llm/factory.py, app/domain/matching/factory.py — neither
one needs to know overrides exist), while a model changed through the System page
still takes effect on the very next call, no redeploy or restart needed.
"""

import redis.asyncio as redis

from app.config.settings import Settings
from app.repositories.ai_settings_repository import AiSettingsRepository


async def get_effective_settings(settings: Settings) -> Settings:
    repository = AiSettingsRepository(redis.from_url(settings.redis_url, decode_responses=True))
    overrides = await repository.get_overrides()
    return settings.model_copy(update=overrides) if overrides else settings
