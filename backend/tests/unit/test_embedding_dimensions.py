"""A vector that cannot be compared is refused on write — spec 10.1.

The failure this prevents is quiet. A row of the wrong size does not raise when
it is stored; it simply never matches anything, so a corpus half-migrated to a
new model looks like a sudden quality regression with no error anywhere. 10.1
says a model change needs a background re-embed and dual-read, not an in-place
overwrite, and this is the cheap half of enforcing that.
"""

import pytest

from app.repositories.embedding_repository import DimensionMismatch


def test_the_error_names_both_sizes_and_the_model() -> None:
    """pgvector's own message comes from inside a cosine operator and says
    "different vector dimensions" without which document, which model, or what
    the two sizes were."""
    error = DimensionMismatch("voyage-4-large", expected=1024, received=512)

    message = str(error)
    assert "voyage-4-large" in message
    assert "1024" in message
    assert "512" in message


def test_the_error_says_what_to_do_about_it() -> None:
    """A dimension change means the model changed, and the fix is a re-embed —
    not a retry, which is what an unexplained write failure invites."""
    assert "re-embed" in str(DimensionMismatch("m", 1024, 768))


def test_it_is_a_value_error_so_a_batch_loop_can_catch_it() -> None:
    """The embedding job isolates one failed batch from the rest; that only
    works if this is catchable alongside the provider's own errors."""
    assert issubclass(DimensionMismatch, ValueError)


def test_raising_it_does_not_need_a_database() -> None:
    with pytest.raises(DimensionMismatch):
        raise DimensionMismatch("voyage-4-large", 1024, 1)
