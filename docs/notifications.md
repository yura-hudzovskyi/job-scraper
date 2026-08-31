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

### Receiving button taps: a webhook

Telegram calls this app back over a webhook rather than this app polling
`getUpdates` — a first iteration used polling (a Celery Beat tick calling
`getUpdates` every few seconds) to avoid standing up a public endpoint, but that
tradeoff didn't actually pay off here: this deployment already has a public HTTPS
domain (`API_DOMAIN`, fronted by Caddy — see docs/deployment.md) for the frontend
to reach the API at all, so "no public endpoint needed" was never true, and
polling only added latency and constant background chatter for no real benefit at
this scale either. A webhook is simpler once you already have the domain.

**Registration** (`backend/app/integrations/notifications/telegram_webhook.py`):
on every API startup (FastAPI lifespan, see `app/main.py`), if `TELEGRAM_BOT_TOKEN`
and `API_DOMAIN` are both set, the app calls Telegram's `setWebhook` with
`https://{API_DOMAIN}/api/integrations/telegram/webhook`. Idempotent and
best-effort — a transient failure (DNS not yet propagated, Telegram briefly down)
is logged and never blocks the API from starting. No-ops entirely in local dev,
where there's no public domain to register.

**Receiving** (`POST /api/integrations/telegram/webhook` in
`app/api/routes/telegram.py`): unauthenticated by JWT (Telegram calls this
directly, not a logged-in browser), so authenticity instead comes from the
`X-Telegram-Bot-Api-Secret-Token` header Telegram echoes back on every call,
checked against `TELEGRAM_WEBHOOK_SECRET` (or, if that's unset, a secret derived
from `SECRET_KEY` — see `resolve_webhook_secret` — so the endpoint is never left
open just because nobody set one more env var). A mismatched or missing header is
rejected with 401. Once validated, the update is handed to the same
`TelegramCallbackService` a polling-based design would have used — the
Approve/Reject business logic doesn't know or care which transport delivered it.

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
