"""Reading an ESCO release off disk.

The fixtures are small, but every quirk in them was found in the real v1.2.1
English release rather than imagined: alternative labels newline-separated
inside one cell, descriptions containing newlines (so the file has 104 065 lines
and 13 960 rows), five skills with no skillType, and 25 concepts repeated
verbatim by the publisher.

That last one is why `merge_concepts` exists — without it the import violates
`unique(namespace, external_id, taxonomy_version)` on real data, and it would
only be discovered against the 10 MB archive.
"""

from pathlib import Path

from app.integrations.taxonomy.esco import (
    ConceptRecord,
    available_languages,
    checksum,
    merge_concepts,
    missing_files,
    normalize_label,
    parse_concepts,
    parse_relations,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "esco"


def _by_uri(records: list[ConceptRecord]) -> dict[str, ConceptRecord]:
    return {record.external_id: record for record in records}


# --- concepts ----------------------------------------------------------------


def test_skills_occupations_and_both_group_kinds_are_all_imported() -> None:
    """The groups are not optional: the broader relations point at them, so
    skipping them means edges with no target."""
    concepts = parse_concepts(FIXTURES, "en")

    types = {record.concept_type for record in concepts}
    assert types == {"skill", "knowledge", "occupation", "skill_group", "occupation_group"}


def test_skill_type_separates_competences_from_knowledge() -> None:
    """Spec 6.5 treats domain_knowledge and professional_skill as different
    categories, so ESCO's own distinction is carried through rather than
    flattened."""
    concepts = _by_uri(parse_concepts(FIXTURES, "en"))

    assert concepts["http://data.europa.eu/esco/skill/aaa"].concept_type == "skill"
    assert concepts["http://data.europa.eu/esco/skill/bbb"].concept_type == "knowledge"


def test_a_skill_with_no_skill_type_is_kept_as_a_skill() -> None:
    """Five rows in the real release have it empty. The label is still real, so
    dropping them would lose concepts over a missing classification."""
    concepts = _by_uri(parse_concepts(FIXTURES, "en"))

    assert concepts["http://data.europa.eu/esco/skill/ccc"].concept_type == "skill"


def test_alternative_labels_are_split_out_of_their_single_cell() -> None:
    """ESCO packs them newline-separated into one field."""
    concept = _by_uri(parse_concepts(FIXTURES, "en"))["http://data.europa.eu/esco/skill/aaa"]

    assert concept.labels["en"] == [
        "manage musical staff",
        "manage music staff",
        "direct musical staff",
    ]


def test_the_preferred_label_comes_first_among_the_surface_forms() -> None:
    """The linker reports which form matched, and the preferred one is what a
    user should be shown."""
    concept = _by_uri(parse_concepts(FIXTURES, "en"))["http://data.europa.eu/esco/skill/bbb"]

    assert concept.labels["en"][0] == concept.preferred_label


def test_a_description_spanning_several_lines_is_read_as_one_field() -> None:
    """This is why the release's line count is seven times its row count."""
    concept = _by_uri(parse_concepts(FIXTURES, "en"))["http://data.europa.eu/esco/skill/aaa"]

    assert concept.description is not None
    assert "wc -l lies" in concept.description


def test_rows_that_cannot_be_referenced_or_displayed_are_dropped() -> None:
    """No URI means nothing can point at it; no preferred label means nothing can
    show it. Both are malformed rather than merely sparse."""
    concepts = parse_concepts(FIXTURES, "en")

    assert all(record.external_id for record in concepts)
    assert all(record.preferred_label for record in concepts)
    assert "http://data.europa.eu/esco/skill/ddd" not in _by_uri(concepts)


# --- deduplication and language merging --------------------------------------


def test_a_concept_the_publisher_repeated_is_stored_once() -> None:
    """25 concepts are duplicated verbatim in the real v1.2.1 release. Without
    this the import violates the identity constraint on real data."""
    concepts = parse_concepts(FIXTURES, "en")

    uris = [record.external_id for record in concepts]
    assert len(uris) == len(set(uris))


def test_a_repeated_concept_does_not_duplicate_its_labels() -> None:
    concept = _by_uri(parse_concepts(FIXTURES, "en"))["http://data.europa.eu/esco/skill/aaa"]

    assert len(concept.labels["en"]) == len(set(concept.labels["en"]))


def test_a_second_language_adds_labels_rather_than_a_second_concept() -> None:
    """ESCO's CSVs are monolingual, so adding Ukrainian later is a merge into
    concepts that already exist — the same operation as deduplication."""
    english = ConceptRecord(
        external_id="uri/a",
        concept_type="skill",
        preferred_label="project management",
        labels={"en": ["project management", "managing projects"]},
    )
    ukrainian = ConceptRecord(
        external_id="uri/a",
        concept_type="skill",
        preferred_label="управління проєктами",
        labels={"uk": ["управління проєктами"]},
    )

    merged = merge_concepts([english, ukrainian])

    assert len(merged) == 1
    assert merged[0].labels == {
        "en": ["project management", "managing projects"],
        "uk": ["управління проєктами"],
    }


def test_merging_keeps_the_first_preferred_label() -> None:
    """Re-running an import must not silently reword a concept."""
    first = ConceptRecord("uri/a", "skill", "original", {"en": ["original"]})
    second = ConceptRecord("uri/a", "skill", "reworded", {"en": ["reworded"]})

    assert merge_concepts([first, second])[0].preferred_label == "original"


def test_merging_fills_in_a_description_only_when_one_is_missing() -> None:
    without = ConceptRecord("uri/a", "skill", "a", {"en": ["a"]}, description=None)
    with_text = ConceptRecord("uri/a", "skill", "a", {"en": ["a"]}, description="explained")

    assert merge_concepts([without, with_text])[0].description == "explained"
    assert merge_concepts([with_text, without])[0].description == "explained"


# --- relations ---------------------------------------------------------------


def test_both_hierarchies_and_the_skill_graph_are_read() -> None:
    relations = parse_relations(FIXTURES, "en")

    kinds = {relation.relation_type for relation in relations}
    assert kinds == {"broader", "essential_for", "optional_for"}


def test_escos_essential_and_optional_map_to_the_schemas_relation_names() -> None:
    relations = parse_relations(FIXTURES, "en")
    by_source = {relation.source_external_id: relation for relation in relations if "skill/" in relation.source_external_id and relation.relation_type != "broader"}

    assert by_source["http://data.europa.eu/esco/skill/aaa"].relation_type == "essential_for"
    assert by_source["http://data.europa.eu/esco/skill/bbb"].relation_type == "optional_for"


def test_an_occupation_hangs_under_its_isco_group() -> None:
    """Without ISCOGroups imported, 2400 of these edges in the real release point
    at nothing."""
    relations = parse_relations(FIXTURES, "en")
    concepts = {record.external_id for record in parse_concepts(FIXTURES, "en")}

    occupation_edges = [
        relation
        for relation in relations
        if relation.source_external_id.endswith("occupation/xyz")
    ]
    assert occupation_edges
    assert occupation_edges[0].target_external_id in concepts


# --- release handling --------------------------------------------------------


def test_a_partial_download_is_named_before_anything_is_written(tmp_path: Path) -> None:
    """A release missing a required file is an incomplete download, not a
    smaller taxonomy — so it fails the import rather than half-filling it."""
    assert missing_files(FIXTURES, "en") == []
    assert set(missing_files(tmp_path, "en")) == {
        "skills_en.csv",
        "occupations_en.csv",
        "skillGroups_en.csv",
    }


def test_the_languages_present_are_read_off_the_filenames() -> None:
    assert available_languages(FIXTURES) == ["en"]


def test_the_checksum_identifies_the_exact_bytes(tmp_path: Path) -> None:
    """A version string is not reproducible — publishers re-issue releases. This
    is what makes a re-import of the same file a detectable no-op."""
    first = tmp_path / "a.zip"
    first.write_bytes(b"release contents")
    same = tmp_path / "b.zip"
    same.write_bytes(b"release contents")
    different = tmp_path / "c.zip"
    different.write_bytes(b"other contents")

    assert checksum(first) == checksum(same)
    assert checksum(first) != checksum(different)
    assert len(checksum(first)) == 64


# --- label normalization -----------------------------------------------------


def test_labels_normalize_for_matching_without_losing_their_original() -> None:
    assert normalize_label("  Machine   Learning ") == "machine learning"
    assert normalize_label("PYTHON") == "python"


def test_normalization_case_folds_beyond_ascii() -> None:
    """The corpus is largely Ukrainian, so lowercasing ASCII is not enough."""
    assert normalize_label("Розробник") == "розробник"
