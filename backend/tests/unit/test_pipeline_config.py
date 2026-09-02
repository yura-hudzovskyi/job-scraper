from dataclasses import fields

from app.domain.pipeline_config import BOUNDS, DEFAULTS, DESCRIPTIONS, PipelineConfig


def test_every_setting_is_explained_to_the_user() -> None:
    """The System page renders these descriptions verbatim. A field without one
    would ship as an unlabelled input box, which is exactly the opaque config
    this rewrite exists to remove."""
    missing = [field.name for field in fields(PipelineConfig) if not DESCRIPTIONS.get(field.name)]

    assert missing == []


def test_every_numeric_setting_is_bounded() -> None:
    """An unbounded number here reaches a provider as a request for 50,000
    documents, or a threshold of -3. The API enforces these; this is what keeps a
    newly-added field from slipping past that check."""
    unbounded = [
        field.name
        for field in fields(PipelineConfig)
        if field.type in ("int", "float") and field.name not in BOUNDS
    ]

    assert unbounded == []


def test_defaults_sit_inside_their_own_bounds() -> None:
    for name, (minimum, maximum) in BOUNDS.items():
        assert minimum <= float(getattr(DEFAULTS, name)) <= maximum, name


def test_consider_threshold_defaults_below_apply_threshold() -> None:
    assert DEFAULTS.consider_threshold <= DEFAULTS.apply_threshold


def test_replace_changes_one_field_and_leaves_the_rest_alone() -> None:
    updated = DEFAULTS.replace(rerank_top_k=5)

    assert updated.rerank_top_k == 5
    assert updated.embedding_model == DEFAULTS.embedding_model
    assert DEFAULTS.rerank_top_k != 5  # frozen: the original is untouched
