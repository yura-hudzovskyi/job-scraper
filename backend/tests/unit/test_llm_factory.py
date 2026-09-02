"""Which legs a capability gets, in what order, from a given configuration —
policy in one place (app/integrations/ai/routing/policy.py), construction in
another (app/integrations/ai/llm/factory.py). No network, no SDK calls: legs
build their provider lazily, so these only look at what was selected.
"""

from app.config.settings import Settings
from app.integrations.ai.llm.factory import build_llm_router, legs_for
from app.integrations.ai.routing.router import Capability


def _settings(**overrides: object) -> Settings:
    """Every credential explicitly unset, so a real key in the ambient
    environment can't change what these tests build."""
    base: dict[str, object] = {
        "groq_api_key": None,
        "gemini_api_key": None,
        "openai_api_key": None,
        "anthropic_api_key": None,
        "llm_provider": None,
        "llm_model": None,
    }
    return Settings(**{**base, **overrides})  # type: ignore[arg-type]


def _keys(capability: Capability, settings: Settings) -> list[str]:
    return [leg.key for leg in legs_for(capability, settings)]


def test_nothing_configured_builds_no_router() -> None:
    settings = _settings()

    assert legs_for(Capability.JOB_EXTRACTION, settings) == []
    assert build_llm_router(Capability.JOB_EXTRACTION, settings) is None


def test_the_job_pipeline_leads_with_the_fast_provider() -> None:
    settings = _settings(groq_api_key="gsk_fake", gemini_api_key="AIza_fake")

    assert _keys(Capability.JOB_EXTRACTION, settings) == [
        "groq:llama-3.3-70b-versatile",
        "gemini:gemini-2.0-flash",
    ]


def test_cv_analysis_leads_with_the_quality_provider() -> None:
    # The reverse order of the job pipeline: profile extraction is rare and its
    # output is what every later match is built on.
    settings = _settings(groq_api_key="gsk_fake", gemini_api_key="AIza_fake")

    assert _keys(Capability.PROFILE_EXTRACTION, settings) == [
        "gemini:gemini-2.0-flash",
        "groq:llama-3.3-70b-versatile",
    ]


def test_a_provider_without_credentials_is_absent_not_a_failing_leg() -> None:
    settings = _settings(groq_api_key="gsk_fake")

    assert _keys(Capability.JOB_EXTRACTION, settings) == ["groq:llama-3.3-70b-versatile"]


def test_legs_use_the_currently_configured_model_names() -> None:
    # The System page writes overrides onto Settings before this runs, so a model
    # changed at runtime takes effect on the very next call.
    settings = _settings(groq_api_key="gsk_fake", groq_model="llama-3.1-8b-instant")

    assert _keys(Capability.JOB_EXTRACTION, settings) == ["groq:llama-3.1-8b-instant"]


def test_the_paid_leg_needs_a_provider_a_model_and_its_key() -> None:
    assert _keys(Capability.JOB_EXTRACTION, _settings(llm_provider="openai")) == []
    assert (
        _keys(Capability.JOB_EXTRACTION, _settings(llm_provider="openai", llm_model="gpt-4o-mini"))
        == []
    )

    settings = _settings(
        llm_provider="openai", llm_model="gpt-4o-mini", openai_api_key="sk-fake"
    )

    assert _keys(Capability.JOB_EXTRACTION, settings) == ["paid:gpt-4o-mini"]


def test_the_paid_leg_comes_after_both_free_tiers() -> None:
    settings = _settings(
        groq_api_key="gsk_fake",
        gemini_api_key="AIza_fake",
        llm_provider="anthropic",
        llm_model="claude-haiku-4-5-20251001",
        anthropic_api_key="sk-ant-fake",
    )

    assert _keys(Capability.MATCH_ENRICHMENT, settings)[-1] == "paid:claude-haiku-4-5-20251001"
