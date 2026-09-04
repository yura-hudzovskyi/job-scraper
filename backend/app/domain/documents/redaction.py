"""Stripping contact details out of text before it is logged or embedded.

Two callers, one rule. Logs must never carry a candidate's contact details
(spec 18.1, 19), and neither must the text handed to an embedding or reranking
model (10.2) — a vector built from a phone number is a vector that can leak one
back, and a name in a reranker prompt is a protected attribute reaching the
score.

What this is honest about: it removes patterns, not identities. An email address
or a `+380…` number has a shape a regex can find. A person's *name* does not —
"Oleksandr Bondarenko" is indistinguishable from any other two capitalised words,
and the only reliable way not to log a name is not to log the document. So this
is a backstop for text that has to be logged, never a licence to log raw CVs.

Conservative on purpose. Every pattern here requires a marker that ordinary
prose and ordinary numbers do not have — an `@` between two words, an explicit
country prefix, or phone-style separators. A redactor that also ate order
numbers and dates would make logs unreadable, and unreadable logs get turned off.
"""

import re

EMAIL_PLACEHOLDER = "[email]"
PHONE_PLACEHOLDER = "[phone]"
CREDENTIAL_PLACEHOLDER = "[redacted]"

# name@host.tld — the `@` plus a dotted host is the marker. Deliberately does not
# try to match every RFC 5322 curiosity; it matches what people put in CVs.
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")

# A phone number written the way people write them for international use:
# an explicit + or 00 prefix, then 7-or-more digits with optional separators.
# The prefix requirement is what keeps this off six-digit order ids and years.
_INTERNATIONAL_PHONE = re.compile(r"(?:\+|\b00)\d[\d\s().‐-―-]{6,}\d")

# A local number written with phone-style grouping: (050) 123-45-67, 050 123 45 67.
# Grouping alone is not enough to tell one from a date — `12.03.2024` has exactly
# the same shape — so a match only counts once it carries at least
# MIN_PHONE_DIGITS digits. That is the discriminator: a Ukrainian number is ten
# digits, a day-month-year date is at most eight.
_GROUPED_PHONE = re.compile(r"\b\(?\d{2,4}\)?(?:[\s.‐-―-]\d{2,4}){2,3}\b")

# Below this, a grouped run of digits is a date, a version or a reference, not a
# phone number.
MIN_PHONE_DIGITS = 9

# A Telegram bot token, which the API carries in the URL path rather than in a
# header — so httpx's own request logging prints it in full on every call, and
# anyone with log access can then control the bot. Found in production logs, not
# imagined: this is why the filter covers credentials and not only personal
# details. A token is arguably the more sensitive of the two.
_BOT_TOKEN = re.compile(r"bot\d{6,}:[A-Za-z0-9_-]{20,}")

# Bearer tokens and api keys in query strings, the other two shapes a secret
# reaches a log line in. Deliberately narrow: each requires its own marker, so
# ordinary text cannot trip them.
_BEARER = re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/-]{16,}=*")
_QUERY_SECRET = re.compile(
    r"(?i)([?&](?:api[_-]?key|token|secret|access[_-]?token|password)=)[^&\s]+"
)

# How much of a document may appear in a log line at all. A preview exists to
# identify *which* document went wrong, not to reproduce it.
PREVIEW_CHARS = 120


def _redact_if_long_enough(match: re.Match[str]) -> str:
    """A grouped run of digits is only a phone number if there are enough of
    them. Without this, `12.03.2024` and `01-02-2023` are redacted as phone
    numbers, and every CV's employment dates disappear from the logs."""
    matched = match.group()
    digits = sum(character.isdigit() for character in matched)
    return PHONE_PLACEHOLDER if digits >= MIN_PHONE_DIGITS else matched


def redact(text: str) -> str:
    """Replace contact details and credentials with placeholders.

    Order matters twice. Credentials go before phone numbers, or the digit run in
    a bot token is eaten as a phone number and the rest of the secret is left
    behind. Emails go before phones for the same reason — the digits inside
    `user2024@example.com` would otherwise be picked out and the `@` left
    dangling.
    """
    redacted = _BOT_TOKEN.sub(f"bot{CREDENTIAL_PLACEHOLDER}", text)
    redacted = _BEARER.sub(rf"\1 {CREDENTIAL_PLACEHOLDER}", redacted)
    redacted = _QUERY_SECRET.sub(rf"\1{CREDENTIAL_PLACEHOLDER}", redacted)
    redacted = _EMAIL.sub(EMAIL_PLACEHOLDER, redacted)
    redacted = _INTERNATIONAL_PHONE.sub(PHONE_PLACEHOLDER, redacted)
    return _GROUPED_PHONE.sub(_redact_if_long_enough, redacted)


def preview(text: str, limit: int = PREVIEW_CHARS) -> str:
    """A short, redacted, single-line excerpt safe to put in a log line.

    Collapses newlines so one document cannot spread across a log file, and
    marks truncation so a reader does not mistake the excerpt for the whole
    document.
    """
    collapsed = " ".join(redact(text).split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[:limit] + "…"
