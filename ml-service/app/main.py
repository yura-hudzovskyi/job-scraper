"""The understanding-model runtime, as one small HTTP service.

Why it is a separate container rather than an import in the Celery worker: the
weights are about 4 GB resident and take ten seconds to load, and the worker
runs prefork with four children, each of which would hold its own copy. The API
and the worker also share one image, so torch in that image would ride along on
every API deploy. Nothing here is about capacity — the VM has room — it is about
not paying for the model four times and not shipping it to processes that never
call it (spec 3.5.3).

It publishes no port. Reachable only from the compose network, like Postgres and
Redis, which is what stands in for authentication here.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from app.config import get_settings
from app.extractor import Gliner2Extractor, ModelNotLoaded
from app.schemas import (
    DocumentEntitiesResponse,
    EntityResponse,
    ExtractRequest,
    ExtractResponse,
    HealthResponse,
    InfoResponse,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

settings = get_settings()
extractor = Gliner2Extractor(settings)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    if settings.load_on_startup:
        # Blocking, on purpose. The container is not ready until the model is,
        # and a service that accepts requests it cannot serve is worse than one
        # that takes ten seconds to come up.
        extractor.load()
    yield


app = FastAPI(title="job-scraper ml-service", lifespan=lifespan)


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Liveness, and whether the weights are in memory.

    Deliberately not a 503 when unloaded: the process is alive and the answer
    is the useful part. A deploy watching this wants to tell "still loading"
    apart from "crashed", and a status code cannot say both.
    """
    return HealthResponse(status="ok", loaded=extractor.loaded)


@app.get("/info", response_model=InfoResponse)
async def info() -> InfoResponse:
    """Which weights this process actually holds.

    The backend writes a `model_registry` row naming the model and revision it
    believes is running (spec 7.4). This is how that belief can be checked
    against the process rather than against the config that was meant to
    produce it.
    """
    return InfoResponse(
        extractor_model_id=settings.extractor_model_id,
        extractor_revision=settings.extractor_revision,
        loaded=extractor.loaded,
        load_seconds=extractor.load_seconds,
        max_chars=settings.max_chars,
        torch_threads=settings.torch_threads,
    )


@app.post("/extract", response_model=ExtractResponse)
async def extract(request: ExtractRequest) -> ExtractResponse:
    """Find the requested labels in each document, with offsets and scores.

    Returns only spans that quote the text they came from. What is missing from
    a result is therefore either absent from the document or below threshold —
    never something the model found and this service failed to locate.
    """
    try:
        results = await extractor.extract(
            [document.text for document in request.documents],
            request.labels,
            request.threshold,
        )
    except ModelNotLoaded as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return ExtractResponse(
        extractor_model_id=settings.extractor_model_id,
        extractor_revision=settings.extractor_revision,
        documents=[
            DocumentEntitiesResponse(
                id=document.id,
                entities=[
                    EntityResponse(
                        label=entity.label,
                        text=entity.text,
                        start_char=entity.start_char,
                        end_char=entity.end_char,
                        confidence=entity.confidence,
                    )
                    for entity in result.entities
                ],
                rejected_spans=result.rejected_spans,
                truncated=result.truncated,
            )
            for document, result in zip(request.documents, results, strict=True)
        ],
    )
