"""Names of the events the document pipeline publishes.

One definition, imported by both the service that writes an event and the relay
that dispatches it. Two string literals that have to stay equal across a process
boundary is exactly the pair that drifts, and the failure is silent: the event
is written, the relay finds no handler for it, and everything reports success.
"""

# A new version of a document's text has been stored and parsed. Consumed by
# extraction (app/workers/tasks/outbox.py).
DOCUMENT_REVISION_CREATED = "document_revision_created"

# What `outbox_events.aggregate_type` carries for the events above.
DOCUMENT_REVISION_AGGREGATE = "document_revision"
