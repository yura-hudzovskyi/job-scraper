"""The wire contract between the backend and this service.

Field names match the backend's `EvidenceSpan` (`start_char`/`end_char`) on
purpose. The offsets cross a process boundary and end up in a database column
that other code indexes into; two names for the same number is how they drift.
"""

from pydantic import BaseModel, ConfigDict, Field


class ExtractDocument(BaseModel):
    # Echoed back on the matching result. The caller sends a batch and gets a
    # list; without an id it would be relying on order across an HTTP boundary.
    id: str
    text: str


class ExtractRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    documents: list[ExtractDocument] = Field(min_length=1, max_length=32)
    # What to look for, decided by the caller. The labels are a domain choice
    # (spec 8.2's bounded passes), and putting them here keeps this service a
    # model runtime rather than a second place where profile logic lives.
    labels: list[str] = Field(min_length=1, max_length=16)
    threshold: float | None = Field(default=None, ge=0.0, le=1.0)


class EntityResponse(BaseModel):
    label: str
    text: str
    start_char: int
    end_char: int
    # The model's own span score, not a placeholder — spec 3.5.2 wants a real
    # one so a low-confidence fact can be treated as low-confidence.
    confidence: float


class DocumentEntitiesResponse(BaseModel):
    id: str
    entities: list[EntityResponse]
    # Spans whose offsets did not quote the text they came from, dropped rather
    # than returned. Non-zero means the model or its tokeniser changed under us.
    rejected_spans: int
    truncated: bool


class ExtractResponse(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    # Travels with every response so a stored profile can name what produced it
    # (spec 2.6) without the backend having to trust its own configuration.
    extractor_model_id: str
    extractor_revision: str
    documents: list[DocumentEntitiesResponse]


class InfoResponse(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    extractor_model_id: str
    extractor_revision: str
    loaded: bool
    load_seconds: float | None
    max_chars: int
    torch_threads: int


class HealthResponse(BaseModel):
    status: str
    loaded: bool
