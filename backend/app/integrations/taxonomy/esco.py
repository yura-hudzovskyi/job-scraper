"""Reading an ESCO classification release off disk.

Parsing only — no database, no session. The importer service does the writing;
this turns a directory of CSVs into plain records, which is what makes it
testable against a handful of fixture rows instead of a 10 MB archive.

Shape of a real v1.2.1 English release, measured (these are parsed rows, not
lines — descriptions contain newlines inside quoted fields, so `wc -l` reports
several times more):

    skills_en.csv           13 960  conceptType,conceptUri,skillType,…
    occupations_en.csv       3 043  conceptType,conceptUri,iscoGroup,…
    skillGroups_en.csv         640  the nodes skills hang under
    ISCOGroups_en.csv          619  the nodes occupations hang under
    broaderRelationsSkillPillar_en.csv   20 819 edges
    broaderRelationsOccPillar_en.csv      ~3 600 edges
    skillSkillRelations_en.csv            5 818 essential / optional

Skill groups and ISCO groups are imported as concepts rather than skipped. The
broader relations point at them, so leaving them out means either dropping most
of the hierarchy or writing edges whose target does not exist — and the
hierarchy is what spec 13.1 step 3 matches an ancestor or child against.

**Language.** ESCO's CSVs are monolingual: one archive per language, same
`conceptUri` throughout. So a language is not a different import, it is more
labels for concepts that already exist — see `ConceptRecord.labels`, keyed by
language, and `TaxonomyImporter.import_language` for the merge.
"""

import csv
import hashlib
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

# ESCO ships single cells holding a whole description; the stdlib default of
# 131 072 characters is not enough for every row in occupations_en.csv.
csv.field_size_limit(10_000_000)

NAMESPACE = "esco"

# Files a classification release must contain for the import to mean anything.
# A release missing one of these is a partial download, not a smaller taxonomy.
REQUIRED_FILES = ("skills", "occupations", "skillGroups")

_WHITESPACE = re.compile(r"\s+")


def normalize_label(label: str) -> str:
    """The form a label is matched on: case-folded, whitespace collapsed.

    Used for both sides of the alias lookup, so "Machine  Learning" in a vacancy
    finds "machine learning" in ESCO. Case folding rather than lowercasing,
    because the corpus is not only Latin script.
    """
    return _WHITESPACE.sub(" ", label).strip().casefold()


@dataclass
class ConceptRecord:
    """One ESCO concept, in the shape the database stores it.

    `labels` is `{language: [surface forms]}` and holds the preferred label plus
    every alternative, deduplicated. Merging a second language adds a key rather
    than a row — that is the whole reason it is a map.
    """

    external_id: str
    concept_type: str
    preferred_label: str
    labels: dict[str, list[str]] = field(default_factory=dict)
    description: str | None = None


@dataclass(frozen=True)
class RelationRecord:
    source_external_id: str
    target_external_id: str
    relation_type: str


def _split_labels(cell: str) -> list[str]:
    """ESCO packs alternative labels into one cell, newline-separated."""
    return [line.strip() for line in cell.replace("\r\n", "\n").split("\n") if line.strip()]


def _read(path: Path) -> Iterator[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        yield from csv.DictReader(handle)


def _concept(row: dict[str, str], language: str, concept_type: str) -> ConceptRecord | None:
    uri = (row.get("conceptUri") or "").strip()
    preferred = (row.get("preferredLabel") or "").strip()
    if not uri or not preferred:
        # A concept with no URI cannot be referenced and one with no preferred
        # label cannot be displayed. Both are malformed rather than merely empty.
        return None

    surface_forms = [preferred, *_split_labels(row.get("altLabels", ""))]
    seen: dict[str, None] = {}
    for form in surface_forms:
        seen.setdefault(form, None)

    return ConceptRecord(
        external_id=uri,
        concept_type=concept_type,
        preferred_label=preferred,
        labels={language: list(seen)},
        description=(row.get("description") or "").strip() or None,
    )


def merge_concepts(records: list[ConceptRecord]) -> list[ConceptRecord]:
    """Collapse records sharing a `conceptUri`, unioning their labels.

    Two reasons this is needed, and they want the same behaviour. A v1.2.1
    release repeats 25 concepts verbatim inside its own files, which would
    otherwise violate `unique(namespace, external_id, taxonomy_version)`. And a
    second language import is the same operation with a different key in
    `labels` — merge rather than insert.

    Order is preserved: the first record wins for scalar fields, so re-running
    an import cannot silently reword a concept.
    """
    merged: dict[str, ConceptRecord] = {}
    for record in records:
        existing = merged.get(record.external_id)
        if existing is None:
            merged[record.external_id] = ConceptRecord(
                external_id=record.external_id,
                concept_type=record.concept_type,
                preferred_label=record.preferred_label,
                labels={language: list(forms) for language, forms in record.labels.items()},
                description=record.description,
            )
            continue

        for language, forms in record.labels.items():
            known = existing.labels.setdefault(language, [])
            seen = set(known)
            for form in forms:
                if form not in seen:
                    known.append(form)
                    seen.add(form)
        if existing.description is None:
            existing.description = record.description
    return list(merged.values())


def parse_concepts(directory: Path, language: str) -> list[ConceptRecord]:
    """Every concept in a release: skills, occupations and skill groups.

    `skillType` decides whether a skill row is a competence or a knowledge item —
    ESCO's own distinction, carried through rather than flattened, because spec
    6.5 treats `domain_knowledge` and `professional_skill` as different
    categories. Five rows in v1.2.1 have it empty; they become plain skills
    rather than being dropped, since the label is still real.
    """
    concepts: list[ConceptRecord] = []

    for row in _read(directory / f"skills_{language}.csv"):
        skill_type = (row.get("skillType") or "").strip()
        concept_type = "knowledge" if skill_type == "knowledge" else "skill"
        if (record := _concept(row, language, concept_type)) is not None:
            concepts.append(record)

    for row in _read(directory / f"occupations_{language}.csv"):
        if (record := _concept(row, language, "occupation")) is not None:
            concepts.append(record)

    for row in _read(directory / f"skillGroups_{language}.csv"):
        if (record := _concept(row, language, "skill_group")) is not None:
            concepts.append(record)

    # The occupation hierarchy's own nodes. Optional because a release can be
    # downloaded without them, but their absence costs every occupation its
    # broader edge.
    isco = directory / f"ISCOGroups_{language}.csv"
    if isco.exists():
        for row in _read(isco):
            if (record := _concept(row, language, "occupation_group")) is not None:
                concepts.append(record)

    return merge_concepts(concepts)


def parse_relations(directory: Path, language: str) -> list[RelationRecord]:
    """The hierarchy and the skill-to-skill edges.

    Relations are language-independent — they join URIs, and the URIs are the
    same in every release language — so importing a second language adds no
    edges. Only the first import needs to read these.
    """
    relations: list[RelationRecord] = []

    broader = directory / f"broaderRelationsSkillPillar_{language}.csv"
    if broader.exists():
        for row in _read(broader):
            source, target = row.get("conceptUri"), row.get("broaderUri")
            if source and target:
                relations.append(RelationRecord(source.strip(), target.strip(), "broader"))

    occ_broader = directory / f"broaderRelationsOccPillar_{language}.csv"
    if occ_broader.exists():
        for row in _read(occ_broader):
            source, target = row.get("conceptUri"), row.get("broaderUri")
            if source and target:
                relations.append(RelationRecord(source.strip(), target.strip(), "broader"))

    skill_skill = directory / f"skillSkillRelations_{language}.csv"
    if skill_skill.exists():
        for row in _read(skill_skill):
            source, target = row.get("originalSkillUri"), row.get("relatedSkillUri")
            if not source or not target:
                continue
            # ESCO's own words for these are "essential" and "optional", which
            # spec 7.3 names essential_for / optional_for.
            kind = "essential_for" if row.get("relationType") == "essential" else "optional_for"
            relations.append(RelationRecord(source.strip(), target.strip(), kind))

    return relations


def available_languages(directory: Path) -> list[str]:
    """Which languages this directory holds, read off the filenames."""
    return sorted(
        path.stem.removeprefix("skills_")
        for path in directory.glob("skills_*.csv")
    )


def checksum(path: Path) -> str:
    """sha256 of the release archive, so an import names the exact bytes it read.

    "We imported v1.2.1" is not reproducible — the publisher can and does
    re-issue a version. This is what makes a re-import of the same file a
    detectable no-op.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def missing_files(directory: Path, language: str) -> list[str]:
    """Which required CSVs are absent — checked before anything is written, so a
    partial download fails the import instead of half-filling a version."""
    return [
        f"{name}_{language}.csv"
        for name in REQUIRED_FILES
        if not (directory / f"{name}_{language}.csv").exists()
    ]
