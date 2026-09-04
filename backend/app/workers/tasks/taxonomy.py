"""Importing a taxonomy release, as a background task.

A task rather than an HTTP endpoint because the work is minutes of bulk inserts
against a file on disk, and a request that has to stay open for it is a request
that times out. Spec 15 lists `POST /admin/taxonomies:import`; when that arrives
it should queue this rather than do the work itself.

The archive is read from the filesystem the worker can see. That is deliberate:
ESCO has no direct download URL — it asks for an email and sends a link — so the
release is fetched once by a person, and the importer's job is to be
reproducible about which bytes it read, not to fetch them.
"""

import asyncio
import logging
import zipfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

from app.db.session import session_scope
from app.integrations.taxonomy import esco
from app.repositories.taxonomy_repository import TaxonomyRepository
from app.services.taxonomy_import_service import TaxonomyImportService
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


def _prepare(source: str) -> tuple[Path, Path | None]:
    """Resolve a path to (directory of CSVs, archive if there was one).

    Accepts either a zip as downloaded or an already-extracted directory, so a
    caller does not have to know which form the file is in.
    """
    path = Path(source)
    if path.is_dir():
        return path, None
    if not path.exists():
        raise FileNotFoundError(f"no taxonomy release at {source}")

    target = path.with_suffix("")
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path) as archive:
        archive.extractall(target)
    return target, path


async def _run(source: str, version: str, language: str, activate: bool) -> dict[str, Any]:
    directory, archive = _prepare(source)
    async with session_scope() as session:
        service = TaxonomyImportService(TaxonomyRepository(session))
        result = await service.import_release(
            directory,
            version=version,
            language=language,
            archive=archive,
            activate=activate,
        )
    return asdict(result)


@celery_app.task(name="taxonomy.import_release")
def import_release(
    source: str,
    version: str,
    language: str = "en",
    activate: bool = True,
) -> dict[str, Any]:
    """Import an ESCO release, or merge a language into one already imported."""
    return asyncio.run(_run(source, version, language, activate))


@celery_app.task(name="taxonomy.inspect_release")
def inspect_release(source: str) -> dict[str, Any]:
    """What a release on disk contains, without writing anything.

    Worth having separately: an import is a minutes-long write, and "did I
    download the right thing" should not require starting one.
    """
    directory, archive = _prepare(source)
    languages = esco.available_languages(directory)
    summary: dict[str, Any] = {
        "directory": str(directory),
        "languages": languages,
        "checksum": esco.checksum(archive) if archive else None,
    }
    for language in languages:
        missing = esco.missing_files(directory, language)
        if missing:
            summary[language] = {"missing": missing}
            continue
        concepts = esco.parse_concepts(directory, language)
        summary[language] = {
            "concepts": len(concepts),
            "relations": len(esco.parse_relations(directory, language)),
            "surface_forms": sum(len(forms) for c in concepts for forms in c.labels.values()),
        }
    return summary
