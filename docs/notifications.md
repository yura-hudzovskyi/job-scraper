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

## Message shape

Built by `_format_message` in `backend/app/integrations/notifications/telegram_provider.py`:

```text
87% MATCH · APPLY

Senior Full Stack Engineer — Acme Inc.

💰 4000–5500 USD
📍 Remote
🎓 Senior · 3+ yrs required

✅ React, TypeScript, Python
⚠️ AWS (required), NestJS

Requirement match: 76%    Practical fit: 87%

🔗 DOU · Djinni
```

The two link labels above are HTML hyperlinks (`<a href="...">DOU</a>`), one per
source this canonical job is known under (see
`JobRepository.list_source_links_for_canonical`) — a job posted on both DOU and
Djinni links out to both by name instead of showing one raw URL. Scraped text
(title, company, skill/gap labels) is HTML-escaped before being interpolated, since
the message is sent with `parse_mode: HTML`.

No inline action buttons for now — there's no callback-query webhook handler wired
up anywhere in the app yet, so `save`/`applied`/`not relevant` buttons would render
in Telegram but silently do nothing on tap. Re-introduce them (and the
`UserJobAction` mapping this section used to describe) once something actually
handles the callback.

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
