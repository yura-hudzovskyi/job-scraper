"""The two ways an interactive AI call can fail, as domain errors rather than
whatever the vendor SDK raised.

Both CV analysis and preferences AI-fill are user-triggered and answer straight
back over HTTP, so their failures reach a public response. A provider's own
exception text is not safe to put there: it can carry account ids, rate-limit
headers, request ids and internal URLs (docs/ai-pipeline-v3.md, 9.3). These
carry a fixed, human message instead, and the real exception is logged
server-side where it belongs.

Background call sites don't use these — job skill extraction and the "should I
apply?" reranker degrade to a result without that layer instead of raising at
all (see job_skill_extraction_service.py and MatchingService.should_i_apply).
"""


class LlmNotConfigured(RuntimeError):
    """No provider is configured at all — a setup problem, safe to describe."""


class LlmCallFailed(RuntimeError):
    """A provider was configured but the call didn't produce a usable result:
    it was down, rate-limited, unauthorized, or answered something that didn't
    match the schema. Deliberately carries no provider text."""

    def __init__(self) -> None:
        super().__init__("the AI provider could not complete this request — try again shortly")
