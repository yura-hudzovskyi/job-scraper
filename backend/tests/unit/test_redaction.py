"""Contact-detail redaction, and the log filter that backstops it.

Half of these test what redaction must *not* touch. A redactor that also eats
salaries, years and identifiers makes logs unreadable, and unreadable logs get
switched off — which is a worse outcome for privacy than a slightly leaky one,
because then nothing is reviewed at all.
"""

import logging

from app.domain.documents.redaction import (
    EMAIL_PLACEHOLDER,
    PHONE_PLACEHOLDER,
    preview,
    redact,
)
from app.observability.logging import RedactingFilter

# --- what must be removed ----------------------------------------------------


def test_an_email_address_is_replaced() -> None:
    assert redact("Contact me at oleh.example@gmail.com please") == (
        f"Contact me at {EMAIL_PLACEHOLDER} please"
    )


def test_an_email_with_a_subdomain_and_a_plus_tag_is_replaced() -> None:
    assert EMAIL_PLACEHOLDER in redact("hire+jobs@mail.company.co.uk")
    assert "hire" not in redact("hire+jobs@mail.company.co.uk")


def test_an_international_phone_number_is_replaced() -> None:
    for written in ("+380501234567", "+38 050 123 45 67", "+38 (050) 123-45-67", "00380501234567"):
        assert PHONE_PLACEHOLDER in redact(f"call {written} today"), written
        assert "1234" not in redact(f"call {written} today"), written


def test_a_locally_grouped_phone_number_is_replaced() -> None:
    for written in ("050 123 45 67", "(050) 123-45-67", "044.234.56.78"):
        assert PHONE_PLACEHOLDER in redact(f"phone {written}"), written


def test_a_telegram_bot_token_is_removed() -> None:
    """Found in production logs. Telegram carries the token in the URL path
    rather than a header, so httpx prints it on every call, and it grants full
    control of the bot to anyone who can read the logs."""
    line = (
        "HTTP Request: POST https://api.telegram.org/"
        "bot8882561835:AAH56ARM4-umg2yACZyigjotAdgjOlHTNrM/setWebhook"
    )

    redacted = redact(line)

    assert "AAH56ARM4" not in redacted
    assert "8882561835" not in redacted
    assert "api.telegram.org" in redacted, "the URL should stay readable"


def test_a_bot_token_is_not_half_eaten_by_the_phone_pattern() -> None:
    """Credentials are redacted before phone numbers on purpose — the other
    order takes the digit run and leaves the secret half behind."""
    redacted = redact("bot123456789:ABCdefGHIjklMNOpqrsTUVwxyz012345")

    assert "ABCdef" not in redacted
    assert PHONE_PLACEHOLDER not in redacted


def test_a_bearer_token_is_removed_but_the_scheme_stays() -> None:
    redacted = redact("Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9")

    assert "eyJhbGci" not in redacted
    assert "Bearer" in redacted


def test_a_secret_in_a_query_string_is_removed_but_the_parameter_stays() -> None:
    """The parameter name is what makes a log line useful; the value is what
    makes it dangerous."""
    redacted = redact("GET /v1/embed?model=voyage-4-large&api_key=sk-live-abc123def456")

    assert "sk-live-abc123def456" not in redacted
    assert "api_key=" in redacted
    assert "model=voyage-4-large" in redacted


def test_the_word_bot_in_ordinary_text_survives() -> None:
    assert redact("the bot replied") == "the bot replied"
    assert redact("robots.txt disallows it") == "robots.txt disallows it"


def test_several_details_in_one_string_all_go() -> None:
    redacted = redact("Oleh, oleh@example.com, +380501234567")

    assert "example.com" not in redacted
    assert "0501234567" not in redacted


def test_an_email_containing_digits_is_not_half_redacted() -> None:
    """Emails are handled before phones on purpose: the other order picks the
    digits out of `user2024@example.com` and leaves a dangling `@`."""
    redacted = redact("user2024@example.com")

    assert redacted == EMAIL_PLACEHOLDER


# --- what must survive -------------------------------------------------------


def test_a_salary_range_is_left_alone() -> None:
    assert redact("Salary 4000-6000 USD") == "Salary 4000-6000 USD"


def test_a_year_range_is_left_alone() -> None:
    assert redact("Worked there 2019-2024") == "Worked there 2019-2024"


def test_dates_are_left_alone() -> None:
    """A date has the same grouped shape as a phone number, so digit count is
    what separates them: a Ukrainian number is ten digits, a date at most eight.
    The first draft of this redacted every employment date in every CV."""
    for written in ("12.03.2024", "01-02-2023", "15/04/2025", "2024.03.12"):
        assert redact(f"from {written}") == f"from {written}", written


def test_a_date_range_keeps_both_ends() -> None:
    text = "Worked 12.03.2024 to 15.04.2025"

    assert redact(text) == text


def test_a_version_number_is_left_alone() -> None:
    assert redact("Python 3.12.1 and Postgres 16") == "Python 3.12.1 and Postgres 16"


def test_an_identifier_is_left_alone() -> None:
    assert redact("job_source_record 4821005") == "job_source_record 4821005"


def test_ordinary_prose_is_untouched() -> None:
    text = "We are looking for an engineer with 5 years of experience in distributed systems."

    assert redact(text) == text


def test_a_uuid_is_left_alone() -> None:
    """Log lines are full of these — redacting them would make every message
    about a revision useless."""
    uuid = "a4f1e6c7-3b28-4f1e-9c73-b28a4f1e6c73"

    assert redact(f"revision {uuid} failed") == f"revision {uuid} failed"


# --- previews ----------------------------------------------------------------


def test_a_preview_is_short_redacted_and_single_line() -> None:
    text = "Line one\nLine two with oleh@example.com\nLine three"

    result = preview(text, limit=200)

    assert "\n" not in result
    assert "oleh@example.com" not in result
    assert "Line one" in result


def test_a_long_preview_is_truncated_visibly() -> None:
    result = preview("word " * 200, limit=40)

    assert len(result) == 41
    assert result.endswith("…")


def test_a_short_preview_is_not_marked_truncated() -> None:
    assert preview("short text", limit=40) == "short text"


# --- the log filter ----------------------------------------------------------


def _record(message: str, *args: object) -> logging.LogRecord:
    return logging.LogRecord(
        name="test", level=logging.INFO, pathname=__file__, lineno=1,
        msg=message, args=args, exc_info=None,
    )


def test_the_filter_redacts_a_plain_message() -> None:
    record = _record("candidate wrote oleh@example.com")

    RedactingFilter().filter(record)

    assert "oleh@example.com" not in record.getMessage()


def test_the_filter_redacts_details_that_arrive_through_arguments() -> None:
    """The details are usually in the arguments, not the format string. Redacting
    only `msg` would put them straight back at the next %s."""
    record = _record("parsing failed for %s", "oleh@example.com")

    RedactingFilter().filter(record)

    assert "oleh@example.com" not in record.getMessage()


def test_the_filter_leaves_a_clean_message_and_its_arguments_intact() -> None:
    record = _record("embedded %d vacancies in %s", 40, "3.2s")

    RedactingFilter().filter(record)

    assert record.getMessage() == "embedded 40 vacancies in 3.2s"


def test_the_filter_always_lets_the_record_through() -> None:
    """It redacts; it never drops. A filter that swallowed records would hide the
    errors it was scanning."""
    assert RedactingFilter().filter(_record("anything at all")) is True
