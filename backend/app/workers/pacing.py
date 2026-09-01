"""When to come back after the AI said "not now" — see docs/ai-pipeline-v3.md (6.3).

A worker that waits for a rate limit inside the task holds a process slot doing
nothing. Rescheduling with a countdown hands the slot back and lets the broker
redeliver the task when the provider is actually open again.

Two details that matter in practice:

- **Jitter.** Every task parked by the same daily quota would otherwise wake at
  exactly the same second and re-collide; a few seconds of spread avoids that
  thundering herd, and costs nothing.
- **A ceiling.** Celery keeps countdown/ETA tasks in the worker's memory rather
  than back in the broker, so scheduling one eight hours out is a memory leak
  with extra steps. A long wait becomes several shorter ones instead: the task
  re-checks hourly and gives up after a bounded number of attempts, leaving
  whatever the degraded path already produced in place.
"""

import random
from datetime import timedelta

MAX_COUNTDOWN_SECONDS = 3600
_MIN_COUNTDOWN_SECONDS = 5
_JITTER_SECONDS = 10


def retry_countdown(retry_after: timedelta | None, attempt: int = 0) -> int:
    """Seconds to wait before retrying. Uses the provider's own reset when the
    router knew it, and backs off exponentially when it didn't."""
    if retry_after is not None:
        base = retry_after.total_seconds()
    else:
        base = 60 * (2**attempt)
    bounded = max(_MIN_COUNTDOWN_SECONDS, min(base, MAX_COUNTDOWN_SECONDS))
    return int(bounded + random.uniform(0, _JITTER_SECONDS))
