# Notifications

Telegram is the first delivery channel, not a special case baked into core logic.
Business logic depends only on the `NotificationProvider` interface.

## Contract

```python
class NotificationProvider(Protocol):
    async def send_job_match(self, match: JobMatch) -> None: ...
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

```text
🔥 87% MATCH

Senior Full Stack Engineer — Acme Inc.

💰 $4,000–5,500   🌍 Remote / Europe   🧑‍💻 3+ years

Strong match: React, TypeScript, Python, product ownership
Gaps: AWS, NestJS

Requirement match: 76%    Practical fit: 87%

[Open vacancy] [Apply] [Save] [Not relevant]
```

Inline actions map to `UserJobAction`: 👍 relevant, 👎 not relevant, ⭐ save,
✅ applied, 🚫 hide company.

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
