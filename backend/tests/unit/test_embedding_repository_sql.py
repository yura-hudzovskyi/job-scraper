"""The raw SQL in EmbeddingRepository, checked without a database.

These statements are the one place this app writes SQL by hand, and the failure
mode they had was invisible to every other kind of test: a bind parameter that
looks bound, isn't, and only fails when a real driver sees the leftover `:`.
"""

import inspect
import re

from sqlalchemy import text

from app.repositories.embedding_repository import EmbeddingRepository


def _statements(method: object) -> list[str]:
    """Every text(\"\"\"...\"\"\") literal in a method's source."""
    source = inspect.getsource(method)  # type: ignore[arg-type]
    return re.findall(r'text\(\s*"""(.*?)"""', source, re.DOTALL)


def test_search_binds_every_parameter_it_passes() -> None:
    """`:query::vector` is the trap: SQLAlchemy only treats `:name` as a bind
    parameter when it is *not* followed by another colon, so Postgres's `::` cast
    syntax silently binds `quer` and leaves `y::vector` as literal SQL. The
    statement still compiles here and still looks right in the source — it fails
    only against a real driver, as "syntax error at or near :"."""
    statements = _statements(EmbeddingRepository.search)
    assert statements, "search() no longer contains a text() statement to check"

    bound = set()
    for statement in statements:
        bound |= set(text(statement)._bindparams)

    assert bound == {"query", "document_type", "model", "limit"}


def test_no_hand_written_sql_uses_the_double_colon_cast() -> None:
    """Guards the whole class, not just today's query: `CAST(x AS type)` is the
    only cast form that survives text() parameter parsing."""
    offenders = [
        name
        for name, method in vars(EmbeddingRepository).items()
        if callable(method)
        for statement in _statements(method)
        if re.search(r":\w+::", statement)
    ]

    assert offenders == []
