"""Lists models actually pulled on the configured Ollama server — backs the model
picker in the "Rescore all vacancies" confirmation dialog (Jobs page), so a user
can only pick a model that's really available instead of typing a tag that isn't
pulled and having the bulk rescore fail job-by-job. Degrades to an empty list
(never an error) when Ollama isn't reachable or isn't the configured provider —
the frontend falls back to a free-text field, same "optional AI layer degrades
gracefully" policy as everywhere else in this app.
"""

import uuid

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import get_current_user_id
from app.config.settings import get_settings

router = APIRouter(prefix="/api/integrations/ollama", tags=["llm"])


class OllamaModelsResponse(BaseModel):
    models: list[str]


@router.get("/models", response_model=OllamaModelsResponse)
async def list_ollama_models(
    user_id: uuid.UUID = Depends(get_current_user_id),
) -> OllamaModelsResponse:
    base_url = get_settings().ollama_base_url
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{base_url.rstrip('/')}/api/tags")
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError):
        return OllamaModelsResponse(models=[])

    names = [model["name"] for model in payload.get("models", []) if "name" in model]
    return OllamaModelsResponse(models=sorted(names))
