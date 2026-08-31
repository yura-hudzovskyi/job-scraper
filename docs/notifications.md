# Notifications

Telegram is the first delivery channel, not a special case baked into core logic.
Business logic depends only on the `NotificationProvider` interface.

## Contract

```python
class NotificationProvider(Protocol):
    async def send_job_match(self, notification: JobMatchNotification) -> None: ...
```

Implementations live in `backend/app/integrations/notifications/`
(`telegram_provider.py` first; `email`, `discord`, `slack`, `push` are future adapters
behind the same interface).

## Delivery policy

Not every match is worth interrupting the user for. `NotificationPolicy`
(`backend/app/domain/notifications/policy.py`) decides, based on score bands:

```text
score >= 85              → notify immediately
score 75-84               → notify immediately if salary/location also match
score 65-74               → include in the daily digest
score < 65                → no notification
```

### Quiet hours

Notifications queued during quiet hours (e.g. 22:00–08:00) are held and delivered as a
morning summary instead of interrupting the user overnight.

## Message shape — a swipe card, not a report

Every match is a short, glanceable card with two buttons, deliberately modeled on a
dating app's yes/no rather than a document to read through. Built by
`_format_message` in `backend/app/integrations/notifications/telegram_provider.py`:

```text
87% MATCH · APPLY

Senior Full Stack Engineer — Acme Inc.
💰 4000–5500 USD · 📍 Remote · 🎓 Senior

✅ React, TypeScript, Python
⚠️ AWS (required), NestJS

🔗 DOU · Djinni

📊 12 pending · 5 approved · 3 rejected

[✅ Approve]  [❌ Reject]
```

The two link labels above are HTML hyperlinks (`<a href="...">DOU</a>`), one per
source this canonical job is known under (see
`JobRepository.list_source_links_for_canonical`) — a job posted on both DOU and
Djinni links out to both by name instead of showing one raw URL. Scraped text
(title, company, skill/gap labels) is HTML-escaped before being interpolated, since
the message is sent with `parse_mode: HTML`. There's no separate
"requirement match vs. practical fit" breakdown here — that level of detail lives
on the Job Details page; a swipe decision only needs the headline score.

The stats line (`📊 ...`) is the running total of every eligible match this user
has ever had, grouped by `MatchDecision` (`pending`/`approved`/`rejected`) — see
`MatchRepository.count_decisions`. It's the "how much is left, how am I doing"
signal a dating app shows, computed fresh on every send.

### Approve / Reject

Tapping a button records a `MatchDecision` on the underlying `JobMatch`
(`MatchRepository.set_decision`) — independent of and never overwritten by
`Recommendation` (the pipeline's own opinion), and never reset by a rescore
(`MatchRepository.upsert` deliberately excludes `decision` from what a rescore
overwrites). Reject behaves like a dating app's "pass": a rejected job is hidden
from the default Jobs list the same way a `SKIP` recommendation already is (see
`MatchRepository.list_skipped_canonical_job_ids`). Approve is currently
tracking-only — there's no application tracker yet to hand it off to (Phase 5, see
docs/roadmap.md).

**Why polling, not a webhook:** receiving button taps needs *something* to call
this app back — either Telegram calls a public HTTPS webhook, or this app polls
`getUpdates`. This deployment does have a public domain already (`API_DOMAIN`, see
docs/deployment.md), so a webhook was possible — polling was still the simpler
choice: no `secret_token` to generate/verify, no `setWebhook` registration step to
run once per deployment, and it works identically in local dev (no public URL at
all) and production without a code path difference. At this app's personal scale
(one shared bot, a handful of users), the few seconds of added latency from
polling is a non-issue. `workers/tasks/telegram_poll.py` runs a quick,
non-blocking `getUpdates` call (`timeout=0`) on a short Celery Beat interval
(`TELEGRAM_POLL_INTERVAL_SECONDS`, default 5s) and hands each `callback_query` to
`TelegramCallbackService`. The update-id cursor lives in Redis
(`telegram:update_offset`), not Postgres — it's polling mechanics, not domain data.

## Feedback → learning

Feedback is recorded, and the platform surfaces patterns — e.g. "you rejected 11/12
Angular-heavy positions" — as a **suggestion**, never an automatic change:

```text
You rejected 11/12 Angular-heavy positions.
Reduce Angular relevance weight?  [Yes] [No]
```

Preference weights are never silently mutated by the learning loop; the user always
confirms.

## Idempotency

Notification delivery must be safe to retry: `unique(notification_id, channel)` so a
worker retry or restart never sends the same match twice.
