"""Which providers serve which capability, in what order, and how much each
capability may spend per day — see docs/ai-pipeline-v3.md (F5, 16).

This is the only module outside the adapters that mentions vendors by name, and
it is intentionally data, not control flow: reordering a chain or adding a
provider is an edit here, never a change to a call site.

The order is a hypothesis, not a truth (docs/ai-pipeline-v3.md, 12). Today it
says:

- CV analysis and preferences AI-fill lead with the quality model. They're rare,
  user-triggered, and their output is what every later match is built on.
- The job pipeline leads with the fast one. It runs per scraped job, and Gemini's
  small daily allowance is the scarce interactive resource this app protects.
- Match enrichment follows the job pipeline rather than the plan's
  quality-first order, because it currently runs on every CONSIDER+APPLY match:
  putting it on Gemini first would spend the interactive allowance on background
  work. Phase 7 makes enrichment selective, which is when that choice changes.
"""

from app.config.settings import Settings
from app.integrations.ai.routing.router import Capability

# Provider ids as the adapters know them; the factory turns each into a leg when
# its credentials are configured, and skips it otherwise.
GEMINI = "gemini"
GROQ = "groq"
PAID = "paid"  # the optional OpenAI/Anthropic leg behind LLM_PROVIDER + LLM_MODEL

_ORDER: dict[Capability, tuple[str, ...]] = {
    Capability.PROFILE_EXTRACTION: (GEMINI, GROQ, PAID),
    Capability.JOB_EXTRACTION: (GROQ, GEMINI, PAID),
    Capability.MATCH_ENRICHMENT: (GROQ, GEMINI, PAID),
}


def provider_order(capability: Capability) -> tuple[str, ...]:
    return _ORDER[capability]


def daily_limit(capability: Capability, settings: Settings) -> int:
    """Each capability's own ceiling. Kept in Settings (and so overridable per
    deployment) rather than hard-coded, because the right numbers depend on which
    free tiers a given account actually has."""
    limits = {
        Capability.PROFILE_EXTRACTION: settings.llm_daily_limit_profile_extraction,
        Capability.JOB_EXTRACTION: settings.llm_daily_limit_job_extraction,
        Capability.MATCH_ENRICHMENT: settings.llm_daily_limit_match_enrichment,
    }
    return limits[capability]
