"""The model itself: loaded once, called one batch at a time.

One instance, serialized behind a lock. A second concurrent forward pass would
double the resident memory of a 4 GB model on a 23 GB box shared with Postgres,
and buy nothing: the work is CPU-bound on the same two threads either way. The
queue that feeds this is a Celery worker, so waiting is free and thrashing is
not.
"""

import asyncio
import logging
import time
from typing import Any

from app.config import Settings
from app.entities import DocumentEntities, best_label_per_span, collect, truncate

logger = logging.getLogger(__name__)


class ModelNotLoaded(RuntimeError):
    """Raised instead of loading on demand inside a request.

    Loading takes ten seconds and 4 GB. A request that triggers it looks like a
    hung service, and ten concurrent ones look like an OOM.
    """


class Gliner2Extractor:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._model: Any = None
        self._lock = asyncio.Lock()
        self.load_seconds: float | None = None

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        """Load the pinned weights. Blocking and slow — call it during startup."""
        import gliner2
        import torch

        torch.set_num_threads(self._settings.torch_threads)

        started = time.perf_counter()
        self._model = gliner2.GLiNER2.from_pretrained(
            self._settings.extractor_model_id,
            revision=self._settings.extractor_revision,
        )
        self.load_seconds = time.perf_counter() - started
        logger.info(
            "loaded %s@%s in %.1fs on %d threads",
            self._settings.extractor_model_id,
            self._settings.extractor_revision[:12],
            self.load_seconds,
            self._settings.torch_threads,
        )

    def _extract_batch(
        self, texts: list[str], labels: list[str], threshold: float
    ) -> list[dict[str, object]]:
        schema = self._model.create_schema().entities(labels)
        # include_confidence and include_spans are the whole reason this service
        # is usable: without them the model returns label -> [string], and a
        # string cannot be pointed at a place in a document.
        raw = self._model.batch_extract(
            texts,
            schema,
            self._settings.batch_size,
            threshold,
            0,
            True,
            True,
            True,
        )
        return list(raw)

    async def extract(
        self, texts: list[str], labels: list[str], threshold: float | None = None
    ) -> list[DocumentEntities]:
        if self._model is None:
            raise ModelNotLoaded("the extractor has not finished loading")
        if not texts:
            return []

        prepared: list[str] = []
        was_truncated: list[bool] = []
        for text in texts:
            cut, truncated = truncate(text, self._settings.max_chars)
            prepared.append(cut)
            was_truncated.append(truncated)

        effective = self._settings.default_threshold if threshold is None else threshold

        async with self._lock:
            # Off the event loop: a forward pass is seconds of CPU, and holding
            # the loop for it would stall the health check the deployment uses
            # to decide whether this container is alive.
            raw_results = await asyncio.to_thread(self._extract_batch, prepared, labels, effective)

        results: list[DocumentEntities] = []
        for raw, text, truncated in zip(raw_results, prepared, was_truncated, strict=True):
            found, rejected = collect(raw, text)
            if rejected:
                logger.warning("%d span(s) did not quote the document they came from", rejected)
            results.append(
                DocumentEntities(
                    entities=best_label_per_span(found),
                    rejected_spans=rejected,
                    truncated=truncated,
                )
            )
        return results
