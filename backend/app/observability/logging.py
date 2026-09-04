"""Structured logging setup, shared by the API and worker processes.

Carries a redaction filter as a backstop: "never log a candidate's contact
details" (spec 18.1, 19) is a rule that holds only if it holds at every call
site, and a filter is the one place it can be enforced once. It is not a licence
to log document text — see app/domain/documents/redaction.py for what pattern
matching can and cannot remove.
"""

import logging

from app.domain.documents.redaction import redact


class RedactingFilter(logging.Filter):
    """Removes emails and phone numbers from log messages as they are emitted.

    Applied to the formatted message rather than to each argument, so it catches
    contact details wherever they entered the record — including from an
    exception's own text, which no call site controls.

    Rewrites `record.msg` and clears `record.args` because the two are formatted
    together downstream; redacting the message while leaving the arguments would
    put the details back at the next `%s`.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = redact(message)
        if redacted != message:
            record.msg = redacted
            record.args = None
        return True


def configure_logging(level: str) -> None:
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    redacting = RedactingFilter()
    for handler in logging.getLogger().handlers:
        handler.addFilter(redacting)
