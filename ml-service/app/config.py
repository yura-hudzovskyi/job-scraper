"""Settings for the model runtime, all overridable from the environment.

The model id and revision are settings rather than constants because spec 17.4
requires a self-hosted model to be re-downloadable byte-identically: pinning the
revision here means the running service can say which weights it holds, and the
`model_registry` row the backend writes can be checked against it.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ML_", protected_namespaces=())

    # Chosen by measurement on the real corpus, not from the model card — see
    # docs/universal-job-matching-system-spec-v1.md 17.6. The English-only
    # variant Phase 0 benchmarked finds 12.9 entities per Ukrainian vacancy;
    # this one finds 18.6, with higher confidence and lower latency, and 36% of
    # the corpus is Ukrainian.
    extractor_model_id: str = "fastino/gliner2-multi-v1"
    extractor_revision: str = "c6296e25603e4d31f68ef8a9f4edb73421d1e45a"

    # Below this the model's own span scores are noise. It is the model's
    # threshold, not a domain policy: what counts as a *usable* competency is
    # the backend's call, made against the confidence this returns.
    default_threshold: float = 0.5

    # Documents longer than this are cut, and the response says so. The corpus
    # tops out near 7 900 characters (spec 17.2), so this is headroom rather
    # than a limit anything hits — but a document silently losing its tail is
    # exactly the failure that looks like a bad model.
    max_chars: int = 12_000

    # The VM has 4 cores and also runs Postgres, Redis, the API and the Celery
    # worker. Extraction is a background step; leaving two cores for everything
    # else keeps a scrape from stalling behind a batch of vacancies.
    torch_threads: int = 2

    # How many documents go through the model in one forward pass. Batching is
    # measurably cheaper than looping (4 documents batched took 0.66s against
    # 1.15s sequential), but every document in a batch is resident at once.
    batch_size: int = 4

    # Loading the weights takes about ten seconds and roughly 4 GB. Doing it
    # during startup rather than on the first request means a container that is
    # up is a container that can answer; `/health` reports which it is.
    load_on_startup: bool = True


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
