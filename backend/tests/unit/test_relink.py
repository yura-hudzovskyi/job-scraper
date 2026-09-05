"""Reading a stored profile back as linker input.

Re-linking exists because promoting a term changes what the linker can find,
while mentions already written do not move on their own. It is cheap only
because it reuses the model's decisions instead of re-making them: the spans
come out of the stored profile, so no forward pass runs.
"""

from app.domain.taxonomy.linking import ExtractedSpan
from app.workers.tasks.backfill import _spans_of


def test_a_competency_with_evidence_becomes_a_span() -> None:
    profile = {
        "competencies": [
            {
                "raw_text": "Terraform",
                "confidence": 0.94,
                "evidence": {"start_char": 10, "end_char": 19, "text": "Terraform"},
            }
        ]
    }

    assert _spans_of(profile) == [ExtractedSpan("Terraform", 10, 19, 0.94)]


def test_a_competency_without_evidence_is_skipped_not_guessed() -> None:
    """No span means no offset to link at. Searching the text for the word would
    reintroduce exactly the ambiguity evidence spans exist to remove."""
    profile = {"competencies": [{"raw_text": "Terraform", "confidence": 0.9, "evidence": None}]}

    assert _spans_of(profile) == []


def test_the_model_s_confidence_survives_the_round_trip() -> None:
    """Re-linking must not quietly upgrade a 0.6 guess to a certainty just
    because it passed through storage."""
    profile = {
        "competencies": [
            {
                "raw_text": "Go",
                "confidence": 0.61,
                "evidence": {"start_char": 0, "end_char": 2, "text": "Go"},
            }
        ]
    }

    assert _spans_of(profile)[0].confidence == 0.61


def test_a_structural_profile_yields_nothing_to_relink() -> None:
    """Profiles written before the model ran have no competencies. They fall
    back to the whole-text scan rather than linking an empty list."""
    assert _spans_of({"requirements": [{"kind": "experience"}]}) == []
    assert _spans_of({}) == []
