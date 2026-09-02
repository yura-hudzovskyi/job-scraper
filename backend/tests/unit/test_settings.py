"""Settings has one job now: read infrastructure out of .env without falling over
on the shapes a hand-edited .env actually produces."""

from app.config.settings import Settings


def test_a_blank_api_key_counts_as_unset() -> None:
    """`VOYAGE_API_KEY=` arrives as an empty string, not as absent. Without this
    the System page would report the key as configured and every run would fail
    with an auth error instead of the one clear "key is not set" blocker."""
    settings = Settings(voyage_api_key="", telegram_bot_token="  ")

    assert settings.voyage_api_key is None
    assert settings.telegram_bot_token is None


def test_cors_origins_accept_the_documented_comma_separated_form() -> None:
    settings = Settings(api_cors_origins="http://a.example, http://b.example")

    assert settings.api_cors_origins == ["http://a.example", "http://b.example"]


def test_cors_origins_accept_a_real_list_too() -> None:
    settings = Settings(api_cors_origins=["http://a.example"])

    assert settings.api_cors_origins == ["http://a.example"]


def test_unknown_env_vars_are_ignored_rather_than_fatal() -> None:
    """A .env left over from an older version still names settings this app no
    longer has. Those are stale config, not a reason to refuse to boot."""
    settings = Settings(llm_provider="groq", embedding_provider="sentence_transformers")

    assert not hasattr(settings, "llm_provider")
    assert settings.voyage_api_key is None
