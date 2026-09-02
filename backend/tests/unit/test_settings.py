"""Configuration must not be able to take the app down over a line that used to
be valid. These are regression tests for a real deploy failure: a server whose
.env still said LLM_PROVIDER=ollama crashed alembic, the API and every worker at
import time.
"""

import pytest

from app.config.settings import Settings


def _settings(**env: str) -> Settings:
    # _env_file=None so a developer's own .env can't change the answer.
    return Settings(_env_file=None, **env)  # type: ignore[arg-type]


def test_a_retired_provider_name_is_ignored_rather_than_fatal() -> None:
    # The option is gone; the deployment is otherwise fine. Crashing on boot is
    # the worst possible way to say "that setting moved".
    assert _settings(llm_provider="ollama").llm_provider is None
    assert _settings(llm_provider="OLLAMA").llm_provider is None


def test_a_blank_provider_means_no_paid_leg() -> None:
    # .env.example documents `LLM_PROVIDER=`, which arrives as "" rather than as
    # absent — that has to mean "unset", not "invalid".
    assert _settings(llm_provider="").llm_provider is None
    assert _settings(llm_provider="   ").llm_provider is None


def test_a_real_provider_still_works() -> None:
    assert _settings(llm_provider="openai").llm_provider == "openai"
    assert _settings(llm_provider="anthropic").llm_provider == "anthropic"


def test_a_typo_still_fails_loudly() -> None:
    # Only names this app used to accept are swallowed; a misspelling is a
    # mistake worth surfacing.
    with pytest.raises(ValueError):
        _settings(llm_provider="opanai")


def test_a_blank_embedding_provider_falls_back_to_its_default() -> None:
    # This field has no "unset" state, so blank means the default — mapping it to
    # None would only produce a more confusing error than the blank did.
    assert _settings(embedding_provider="").embedding_provider == "sentence_transformers"
