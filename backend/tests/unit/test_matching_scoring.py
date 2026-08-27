"""Deterministic scoring must stay unit-testable without network/DB access —
see docs/matching-engine.md."""

import pytest


@pytest.mark.skip(reason="pending DeterministicScorer.score implementation")
def test_exact_skill_match_scores_higher_than_related_match() -> None:
    raise NotImplementedError
