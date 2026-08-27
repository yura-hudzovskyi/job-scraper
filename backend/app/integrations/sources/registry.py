"""Registers available JobSourceAdapter implementations.

Adding a new source = implement JobSourceAdapter + register it here. No other module
should change. See docs/source-adapters.md.
"""

from app.integrations.sources.base import JobSourceAdapter
from app.integrations.sources.djinni.adapter import DjinniAdapter
from app.integrations.sources.dou.adapter import DouAdapter


class SourceRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, JobSourceAdapter] = {}

    def register(self, adapter: JobSourceAdapter) -> None:
        self._adapters[adapter.source_name] = adapter

    def get(self, source_name: str) -> JobSourceAdapter:
        return self._adapters[source_name]

    def all(self) -> list[JobSourceAdapter]:
        return list(self._adapters.values())


def build_default_registry() -> SourceRegistry:
    """Wire up the adapters enabled by default (DOU, Djinni)."""
    registry = SourceRegistry()
    registry.register(DouAdapter())
    registry.register(DjinniAdapter())
    return registry
