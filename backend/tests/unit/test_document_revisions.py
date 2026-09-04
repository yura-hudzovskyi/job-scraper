"""The two decisions the revision layer makes, tested without a database.

`plan_revision` answers "does this content become a new version"; the transition
table answers "may a revision move here from there". Both are pure functions over
plain data precisely so these cases can be written out rather than mocked.
"""

import itertools

import pytest

from app.domain.documents.models import (
    ALLOWED_TRANSITIONS,
    BlockType,
    EntityKind,
    IllegalTransition,
    RevisionRef,
    RevisionStatus,
    can_transition,
    check_transition,
    plan_revision,
)

# --- plan_revision -----------------------------------------------------------


def test_first_ingest_of_a_document_is_revision_one() -> None:
    plan = plan_revision([], "abc")

    assert plan.revision_no == 1
    assert plan.is_new is True
    assert plan.reverted is False


def test_re_ingesting_identical_content_creates_no_revision() -> None:
    """The common case by a wide margin: a scrape re-reads the same vacancy every
    run, and that must cost nothing and store nothing."""
    history = [RevisionRef(revision_no=1, content_hash="abc")]

    plan = plan_revision(history, "abc")

    assert plan.is_new is False
    assert plan.revision_no == 1


def test_changed_content_becomes_the_next_revision() -> None:
    history = [RevisionRef(revision_no=1, content_hash="abc")]

    plan = plan_revision(history, "def")

    assert plan.is_new is True
    assert plan.revision_no == 2


def test_revision_numbers_continue_from_the_highest_seen() -> None:
    history = [
        RevisionRef(revision_no=1, content_hash="abc"),
        RevisionRef(revision_no=2, content_hash="def"),
        RevisionRef(revision_no=3, content_hash="ghi"),
    ]

    plan = plan_revision(history, "jkl")

    assert plan.revision_no == 4
    assert plan.is_new is True


def test_history_order_does_not_decide_which_revision_is_latest() -> None:
    """The repository does not order its history query, so the plan must not
    depend on row order — only on revision_no."""
    history = [
        RevisionRef(revision_no=3, content_hash="ghi"),
        RevisionRef(revision_no=1, content_hash="abc"),
        RevisionRef(revision_no=2, content_hash="def"),
    ]

    assert plan_revision(history, "ghi").is_new is False
    assert plan_revision(history, "new").revision_no == 4


def test_content_reverting_to_an_older_version_creates_nothing_and_says_so() -> None:
    """A -> B -> A. The bytes are not new, so there is no revision to create, and
    unique(owner, content_hash) would refuse one anyway. The flag exists so a
    caller tracking "which revision is current" knows the pointer moved back."""
    history = [
        RevisionRef(revision_no=1, content_hash="abc"),
        RevisionRef(revision_no=2, content_hash="def"),
    ]

    plan = plan_revision(history, "abc")

    assert plan.is_new is False
    assert plan.revision_no == 1
    assert plan.reverted is True


def test_matching_the_newest_revision_is_not_a_revert() -> None:
    history = [
        RevisionRef(revision_no=1, content_hash="abc"),
        RevisionRef(revision_no=2, content_hash="def"),
    ]

    assert plan_revision(history, "def").reverted is False


# --- the state machine -------------------------------------------------------


def test_the_forward_path_runs_end_to_end() -> None:
    path = [
        RevisionStatus.RECEIVED,
        RevisionStatus.PARSED,
        RevisionStatus.EXTRACTING,
        RevisionStatus.EXTRACTED,
        RevisionStatus.INDEXING,
        RevisionStatus.SEARCHABLE,
    ]

    for current, target in itertools.pairwise(path):
        check_transition(current, target)


def test_a_revision_cannot_skip_extraction_to_become_searchable() -> None:
    """The failure this guards is silent: a revision that jumps to `searchable`
    has no extracted profile, and nothing downstream would report that as an
    error — it would just match on an empty profile."""
    with pytest.raises(IllegalTransition):
        check_transition(RevisionStatus.RECEIVED, RevisionStatus.SEARCHABLE)


def test_every_working_state_can_fail() -> None:
    working = [
        RevisionStatus.RECEIVED,
        RevisionStatus.PARSED,
        RevisionStatus.EXTRACTING,
        RevisionStatus.EXTRACTED,
        RevisionStatus.INDEXING,
        RevisionStatus.SEARCHABLE,
    ]

    for status in working:
        assert can_transition(status, RevisionStatus.FAILED)


def test_a_retry_rewinds_to_the_input_state_of_the_failed_stage() -> None:
    for target in (RevisionStatus.RECEIVED, RevisionStatus.PARSED, RevisionStatus.EXTRACTED):
        assert can_transition(RevisionStatus.FAILED, target)


def test_a_failed_revision_cannot_be_declared_searchable() -> None:
    with pytest.raises(IllegalTransition):
        check_transition(RevisionStatus.FAILED, RevisionStatus.SEARCHABLE)


def test_reprocessing_a_searchable_revision_rewinds_to_its_parsed_text() -> None:
    """A reprocess re-extracts under a new model version. The raw text has not
    changed, so there is no new revision — it goes back to `parsed` and forward
    again."""
    assert can_transition(RevisionStatus.SEARCHABLE, RevisionStatus.PARSED)


def test_no_state_transitions_to_itself() -> None:
    for status, targets in ALLOWED_TRANSITIONS.items():
        assert status not in targets


def test_every_status_has_an_entry_in_the_transition_table() -> None:
    """A status missing from the table would raise KeyError inside
    can_transition rather than returning False — a crash, not a rejection."""
    assert set(ALLOWED_TRANSITIONS) == set(RevisionStatus)


def test_every_transition_target_is_a_real_status() -> None:
    for targets in ALLOWED_TRANSITIONS.values():
        assert targets <= set(RevisionStatus)


def test_illegal_transition_names_both_ends() -> None:
    error = IllegalTransition(RevisionStatus.RECEIVED, RevisionStatus.SEARCHABLE)

    assert error.current is RevisionStatus.RECEIVED
    assert error.target is RevisionStatus.SEARCHABLE
    assert "received" in str(error)
    assert "searchable" in str(error)


# --- enum values are persisted, so they are part of the schema ---------------


def test_enum_values_match_what_the_columns_store() -> None:
    assert [kind.value for kind in EntityKind] == ["job", "candidate"]
    assert [status.value for status in RevisionStatus] == [
        "received",
        "parsed",
        "extracting",
        "extracted",
        "indexing",
        "searchable",
        "failed",
    ]
    assert [block.value for block in BlockType] == [
        "title",
        "heading",
        "paragraph",
        "list_item",
        "table_cell",
        "metadata",
        "unknown",
    ]
